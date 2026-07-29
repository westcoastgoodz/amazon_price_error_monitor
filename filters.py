"""Configurable filtering to cut noise, per the client's requirements.

The most important rule (client): drop anything whose ORIGINAL (old) price is
below MIN_ORIGINAL_PRICE (default $8.99), regardless of sale price.
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


def target_tiers(discount: int, s: Settings) -> list[int]:
    """Which Discord tier channel(s) a deal should be posted to."""
    eligible = [t for t in s.tiers if discount >= t]
    if not eligible:
        return []
    if s.route_mode == "cascade":
        return eligible
    # highest: single best tier
    return [max(eligible)]
