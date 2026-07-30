"""Configurable filtering to cut noise, per the client's requirements.

The most important rule (client): drop anything whose ORIGINAL (old) price is
below MIN_ORIGINAL_PRICE (default $8.99), regardless of sale price.

Extra "real price error" gates reject common Keepa deal-feed junk:
marketplace ghosts, coupons, B2B-only, and slow 90-day MSRP drops.
"""
from __future__ import annotations

from config import Settings
from models import AlertItem


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
    if s.title_keywords and not any(k in title_l for k in s.title_keywords):
        return False, "title missing required keyword"
    if s.exclude_keywords and any(k in title_l for k in s.exclude_keywords):
        return False, "title has excluded keyword"

    # Category include / exclude (root + full path).
    cats = set(item.categories) | ({item.root_cat} if item.root_cat else set())
    if s.include_categories and not (cats & set(s.include_categories)):
        return False, "category not in include list"
    if s.exclude_categories and (cats & set(s.exclude_categories)):
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

        if s.seller_type != "any":
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

    # Sudden drop vs last 7 days — skips "always discounted vs old 90d average".
    min_recent = int(getattr(s, "min_recent_discount", 0) or 0)
    if min_recent > 0:
        recent = item.recent_discount
        if recent is None or recent < min_recent:
            return False, (
                f"not a sudden drop (7d disc {recent}% < {min_recent}%)"
            )

    if bool(getattr(s, "reject_business", True)) and item.business_required:
        return False, "business-only product"

    if bool(getattr(s, "reject_promotions", True)) and item.promotion:
        return False, "promotion/coupon"

    seller_mode = (getattr(s, "seller_type", "any") or "any").lower()
    seller_l = (item.seller or "").lower()
    if seller_mode == "amazon" and seller_l != "amazon":
        return False, f"seller {item.seller} != Amazon"
    if seller_mode == "fba" and seller_l not in ("fba", "amazon"):
        return False, f"seller {item.seller} != FBA/Amazon"

    # Live Keepa /product must still show a similar buyable price (stale deal skip).
    if bool(getattr(s, "verify_live_price", True)) and product:
        stats = product.get("stats") or {}
        current = stats.get("current") or []
        pt = int(getattr(s, "price_type", 0) or 0)
        try:
            live_cents = current[pt]
        except (IndexError, TypeError):
            live_cents = None
        if live_cents is None or live_cents < 0:
            return False, "live price missing / OOS"
        live = live_cents / 100.0
        # If live recovered a lot above the deal price, not a buyable error anymore.
        if live > item.new_price * 1.20 and (live - item.new_price) > 1.0:
            return False, f"live ${live:.2f} >> deal ${item.new_price:.2f}"

    return True, "ok"


def target_tiers(discount: int, s: Settings) -> list[int]:
    """Which Discord tier channel(s) a deal should be posted to."""
    eligible = [t for t in s.tiers if discount >= t]
    if not eligible:
        return []
    if s.route_mode == "cascade":
        return eligible
    # highest: single best tier
    return [max(eligible)]
