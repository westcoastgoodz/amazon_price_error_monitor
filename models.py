"""Normalized alert item assembled from a Keepa deal (+ optional product)."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus, urlencode

from config import Settings
from keepa_client import (
    IDX_RATING,
    IDX_REVIEW_COUNT,
    DealRaw,
    amazon_image_url,
    graph_image_url,
    product_image_url,
)


@dataclass
class AlertItem:
    asin: str
    title: str
    image_url: str
    new_price: float
    old_price: float
    discount: int
    seller: str
    promotion: bool
    business_required: bool
    brand: str | None
    review_count: int | None
    rating: float | None
    categories: list[int]
    root_cat: int
    amazon_url: str
    keepa_url: str
    google_url: str
    sas_url: str
    ebay_url: str
    atc_url: str
    graph_url: str
    # Drop vs 7-day average (sudden drop signal for real price errors).
    recent_discount: int | None = None


def _extract_stats(product: dict | None) -> dict:
    if not product:
        return {}
    stats = product.get("stats") or {}
    current = stats.get("current") or []

    def at(idx: int) -> int | None:
        try:
            v = current[idx]
        except (IndexError, TypeError):
            return None
        return v if v is not None and v >= 0 else None

    rating_raw = at(IDX_RATING)
    review_count = at(IDX_REVIEW_COUNT)

    coupon = product.get("coupon")
    promo = bool(coupon and any(bool(c) for c in coupon)) or bool(product.get("promotions"))

    # buyBoxSellerId can live on stats OR latest entry of buyBoxSellerIdHistory.
    sid = stats.get("buyBoxSellerId")
    if not sid or str(sid) in ("-1", "-2", "null", "None"):
        sid = _last_buybox_seller_id(product)

    return {
        "brand": product.get("brand"),
        "rating": round(rating_raw / 10.0, 1) if rating_raw is not None else None,
        "review_count": review_count,
        "buy_box_seller_id": sid,
        "buy_box_is_fba": stats.get("buyBoxIsFBA"),
        "current": current,
        "promotion": promo,
        "business_required": bool(product.get("businessOnlyProduct") or product.get("isB2B")),
        "images_csv": product.get("imagesCSV"),
    }


def _last_buybox_seller_id(product: dict) -> str | None:
    """buyBoxSellerIdHistory = [keepaMin, sellerId, keepaMin, sellerId, ...]."""
    hist = product.get("buyBoxSellerIdHistory")
    if not isinstance(hist, (list, tuple)) or len(hist) < 2:
        return None
    # Walk newest → oldest for a real seller id.
    for i in range(len(hist) - 1, 0, -2):
        sid = hist[i]
        if sid is None:
            continue
        s = str(sid).strip()
        if s and s not in ("-1", "-2", "null", "None"):
            return s
    return None


def _seller_from_prices(current: list, deal_cents: int | None, amazon_seller_id: str) -> str | None:
    """Infer Amazon / FBA / FBM by matching live Keepa price indexes to the deal."""
    if not current:
        return None

    def cents(idx: int) -> int | None:
        try:
            v = current[idx]
        except (IndexError, TypeError):
            return None
        return int(v) if v is not None and v >= 0 else None

    amazon_p = cents(0)   # AMAZON
    new_p = cents(1)      # Marketplace NEW
    fbm_ship = cents(7)   # NEW_FBM_SHIPPING
    fba_p = cents(10)     # NEW_FBA
    bb_p = cents(18)      # BUY_BOX_SHIPPING

    target = deal_cents if deal_cents is not None and deal_cents >= 0 else new_p or bb_p

    def near(a: int | None, b: int | None, tol: int = 3) -> bool:
        return a is not None and b is not None and abs(a - b) <= tol

    if near(target, amazon_p):
        return "Amazon"
    if near(target, fba_p) or (fba_p is not None and near(fba_p, new_p) and near(target, new_p)):
        return "FBA"
    if near(target, fbm_ship) or (fbm_ship is not None and near(target, new_p) and amazon_p != new_p):
        # FBM shipping includes postage — allow a wider match vs New.
        if near(target, fbm_ship, tol=500) or (new_p is not None and amazon_p != new_p and fba_p is None):
            return "FBM"
    if amazon_p is not None and new_p is not None and amazon_p == new_p and near(target, new_p):
        return "Amazon"
    if fba_p is not None and near(target, new_p):
        return "FBA"
    if new_p is not None and near(target, new_p):
        return "FBA" if fba_p is not None else "Marketplace"
    return None


def _seller_label(
    stats: dict,
    amazon_seller_id: str,
    *,
    deal_price: float | None = None,
) -> str:
    sid = stats.get("buy_box_seller_id")
    if sid:
        sid = str(sid).strip()
    if sid and amazon_seller_id and sid == amazon_seller_id:
        return "Amazon"
    if stats.get("buy_box_is_fba") is True:
        return "FBA"
    if stats.get("buy_box_is_fba") is False and sid:
        return "FBM"

    deal_cents = int(round(deal_price * 100)) if deal_price is not None else None
    inferred = _seller_from_prices(list(stats.get("current") or []), deal_cents, amazon_seller_id)
    if inferred:
        return inferred

    # Seller id present but Keepa omitted FBA flag (needs offers=) — still show something.
    if sid:
        return "FBA"
    return "Marketplace"


def build_alert(
    deal: DealRaw,
    product: dict | None,
    settings: Settings,
) -> AlertItem | None:
    """Turn a raw deal (+ optional product) into a display-ready AlertItem.

    Returns None if we can't compute a valid new/old price for the chosen price type.
    """
    pt = settings.price_type
    dr = settings.date_range

    new_price = deal.price(pt)
    old_price = deal.avg_price(dr, pt)
    discount = deal.discount_percent(dr, pt)

    if new_price is None or old_price is None or old_price <= 0:
        return None
    if discount is None:
        discount = round((old_price - new_price) / old_price * 100)
    if discount <= 0:
        return None

    # 7-day average drop — filters "always cheap vs old 90d MSRP" Keepa noise.
    recent_discount: int | None = None
    week_avg = deal.avg_price(1, pt)
    if week_avg is not None and week_avg > 0:
        recent_discount = int(round((week_avg - new_price) / week_avg * 100))

    stats = _extract_stats(product)

    image_url = product_image_url(product) or deal.image_url
    if not image_url and deal.raw.get("image") is not None:
        image_url = amazon_image_url(deal.raw.get("image"))

    host = settings.domain_host
    title = deal.title or (product or {}).get("title") or deal.asin
    q = title or deal.asin

    sas_url = "https://sas.selleramp.com/sas/lookup?" + urlencode(
        {"search_term": deal.asin, "sas_cost_price": f"{new_price:.2f}"}
    )

    return AlertItem(
        asin=deal.asin,
        title=title,
        image_url=image_url,
        new_price=new_price,
        old_price=old_price,
        discount=int(discount),
        seller=_seller_label(stats, settings.amazon_seller_id, deal_price=new_price),
        promotion=bool(stats.get("promotion")),
        business_required=bool(stats.get("business_required")),
        brand=stats.get("brand"),
        review_count=stats.get("review_count"),
        rating=stats.get("rating"),
        categories=deal.categories,
        root_cat=deal.root_cat,
        amazon_url=f"https://{host}/dp/{deal.asin}",
        keepa_url=f"https://keepa.com/#!product/{settings.keepa_domain}-{deal.asin}",
        google_url="https://www.google.com/search?q=" + quote_plus(q),
        sas_url=sas_url,
        ebay_url="https://www.ebay.com/sch/i.html?_nkw=" + quote_plus(q),
        atc_url=f"https://{host}/gp/aws/cart/add.html?ASIN.1={deal.asin}&Quantity.1=1",
        graph_url=graph_image_url(deal.asin, settings.domain_code) if settings.include_graph else "",
        recent_discount=recent_discount,
    )
