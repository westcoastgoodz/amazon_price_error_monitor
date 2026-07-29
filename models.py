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

    return {
        "brand": product.get("brand"),
        "rating": round(rating_raw / 10.0, 1) if rating_raw is not None else None,
        "review_count": review_count,
        "buy_box_seller_id": stats.get("buyBoxSellerId"),
        "buy_box_is_fba": stats.get("buyBoxIsFBA"),
        "promotion": promo,
        "business_required": bool(product.get("businessOnlyProduct") or product.get("isB2B")),
        "images_csv": product.get("imagesCSV"),
    }


def _seller_label(stats: dict, amazon_seller_id: str) -> str:
    sid = stats.get("buy_box_seller_id")
    if sid and amazon_seller_id and sid == amazon_seller_id:
        return "Amazon"
    if stats.get("buy_box_is_fba"):
        return "FBA"
    if sid:
        return "FBM"
    return "—"


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
        seller=_seller_label(stats, settings.amazon_seller_id),
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
    )
