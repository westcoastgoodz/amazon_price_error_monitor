"""Configurable filtering to cut noise, per the client's requirements.

The most important rule (client): drop anything whose ORIGINAL (old) price is
below MIN_ORIGINAL_PRICE (default $4), regardless of sale price.

Live-price verify still skips OOS / already-recovered deals. Promo, B2B,
and 7-day-drop gates are off by default so Keepa Deals items can post.
"""
from __future__ import annotations

from config import Settings
from models import AlertItem

# Obvious junk Keepa often lists — never alert these.
_SPAM_TITLE_BITS = (
    "for parts",
    "not working",
    "does not work",
    "doesn't work",
    "wholesale lot",
    "no returns",
    "parts only",
    "as is",
)

# US Amazon / Keepa root browse nodes for books & ebooks.
BOOK_CATEGORY_IDS = (
    283155,       # Books
    133140011,    # Kindle Store / eBooks
    11260432011,  # Audible Audiobooks (common node)
)

# Title clues when Keepa category path is incomplete.
_BOOK_TITLE_BITS = (
    "paperback",
    "hardcover",
    "mass market paperback",
    "kindle edition",
    "kindle store",
    "audiobook",
    "audio cd",
    "large print",
    "board book",
)


def _is_book_item(item: AlertItem) -> bool:
    cats = set(item.categories) | ({item.root_cat} if item.root_cat else set())
    if cats & set(BOOK_CATEGORY_IDS):
        return True
    title_l = (item.title or "").lower()
    return any(bit in title_l for bit in _BOOK_TITLE_BITS)


def passes_filters(item: AlertItem, s: Settings) -> tuple[bool, str]:
    # Original (not sale) price floor.
    if item.old_price < s.min_original_price:
        return False, f"original price ${item.old_price:.2f} < ${s.min_original_price:.2f}"

    # Minimum discount (lowest tier gate).
    if item.discount < s.min_discount:
        return False, f"discount {item.discount}% < {s.min_discount}%"

    # No 90%+ alerts (Amazon-90 removed; hard reject).
    if item.discount >= 90:
        return False, f"discount {item.discount}% >= 90 (disabled)"

    title_l = (item.title or "").lower()
    if any(bit in title_l for bit in _SPAM_TITLE_BITS):
        return False, "spam / parts-only title"
    if bool(getattr(s, "exclude_books", True)) and _is_book_item(item):
        return False, "books / kindle / audiobook excluded"
    if s.title_keywords and not any(k in title_l for k in s.title_keywords):
        return False, "title missing required keyword"
    if s.exclude_keywords and any(k in title_l for k in s.exclude_keywords):
        return False, "title has excluded keyword"

    # Category include / exclude (root + full path).
    cats = set(item.categories) | ({item.root_cat} if item.root_cat else set())
    exclude_cats = set(s.exclude_categories)
    if bool(getattr(s, "exclude_books", True)):
        exclude_cats |= set(BOOK_CATEGORY_IDS)
    if s.include_categories and not (cats & set(s.include_categories)):
        return False, "category not in include list"
    if exclude_cats and (cats & exclude_cats):
        return False, "category in exclude list"

    # The remaining filters need enriched product data to be meaningful.
    if s.enrich_products:
        if s.min_review_count > 0:
            if item.review_count is None or item.review_count < s.min_review_count:
                return False, f"reviews {item.review_count} < {s.min_review_count}"
        if s.min_rating > 0:
            if item.rating is None or item.rating < s.min_rating:
                return False, f"rating {item.rating} < {s.min_rating}"

        brand_l = (item.brand or "").lower()
        if s.include_brands and brand_l not in s.include_brands:
            return False, "brand not in include list"
        if s.exclude_brands and brand_l in s.exclude_brands:
            return False, "brand in exclude list"

        if s.seller_type not in ("any", "all", ""):
            seller_l = item.seller.lower()
            if s.seller_type == "fba" and seller_l not in ("fba", "amazon"):
                return False, f"seller {item.seller} != FBA"
            if s.seller_type == "fbm" and seller_l != "fbm":
                return False, f"seller {item.seller} != FBM"
            if s.seller_type == "amazon" and seller_l != "amazon":
                return False, f"seller {item.seller} != Amazon"

    return True, "ok"


def passes_price_error_quality(
    item: AlertItem,
    product: dict | None,
    s: Settings,
) -> tuple[bool, str]:
    """Stricter gates so we post real Amazon price errors, not Keepa deal junk."""

    # Sudden drop vs last 7 days. Missing 7d data must not kill a Keepa deal.
    min_recent = int(getattr(s, "min_recent_discount", 0) or 0)
    if min_recent > 0:
        recent = item.recent_discount
        if recent is not None and recent < min_recent:
            return False, (
                f"not a sudden drop (7d disc {recent}% < {min_recent}%)"
            )

    if bool(getattr(s, "reject_business", False)) and item.business_required:
        return False, "business-only product"

    if bool(getattr(s, "reject_promotions", False)) and item.promotion:
        return False, "promotion/coupon"

    seller_mode = (getattr(s, "seller_type", "any") or "any").lower()
    seller_l = (item.seller or "").lower()
    if seller_mode not in ("", "any", "all"):
        if seller_mode == "amazon" and seller_l != "amazon":
            return False, f"seller {item.seller} != Amazon"
        if seller_mode == "fba" and seller_l not in ("fba", "amazon"):
            return False, f"seller {item.seller} != FBA/Amazon"
        if seller_mode == "fbm" and seller_l != "fbm":
            return False, f"seller {item.seller} != FBM"

    # Live Keepa /product must still show a similar buyable price (stale deal skip).
    if bool(getattr(s, "verify_live_price", True)) and product:
        live = _best_live_price(product, int(getattr(s, "price_type", 1) or 1))
        if live is None:
            return False, "live price missing / OOS"
        if live > item.new_price * 1.20 and (live - item.new_price) > 1.0:
            return False, f"live ${live:.2f} >> deal ${item.new_price:.2f}"

    return True, "ok"


def _best_live_price(product: dict, price_type: int) -> float | None:
    """Prefer the configured price type; fall back to New / Buy Box / Amazon / FBA."""
    stats = product.get("stats") or {}
    current = stats.get("current") or []
    seen: set[int] = set()
    for idx in (price_type, 1, 18, 0, 10):
        if idx in seen:
            continue
        seen.add(idx)
        try:
            cents = current[idx]
        except (IndexError, TypeError):
            continue
        if cents is not None and cents >= 0:
            return cents / 100.0
    return None


def target_tiers(discount: int, s: Settings) -> list[int]:
    """Which Discord tier channel(s) a deal should be posted to."""
    eligible = [t for t in s.tiers if discount >= t]
    if not eligible:
        return []
    if s.route_mode == "cascade":
        return eligible
    # highest: single best tier
    return [max(eligible)]
