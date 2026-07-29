"""Discord alert embed (single layout)."""
from __future__ import annotations

from datetime import datetime, timezone

from models import AlertItem

TIER_COLORS = {
    60: 0x3498DB,
    70: 0x2ECC71,
    80: 0xE67E22,
    90: 0xE74C3C,
}

_AMAZON_ICON = (
    "https://upload.wikimedia.org/wikipedia/commons/d/de/Amazon_icon.png"
)


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_alert_ui(item: AlertItem, tier: int, style: str | None = None) -> tuple[str, dict]:
    """Build content + embed. ``style`` is ignored (single layout only)."""
    _ = style
    saved = max(0.0, item.old_price - item.new_price)
    fields = [
        {"name": "Current", "value": f"**${item.new_price:.2f}**", "inline": True},
        {"name": "90d Avg", "value": f"${item.old_price:.2f}", "inline": True},
        {"name": "You save", "value": f"**${saved:.2f}** ({item.discount}%)", "inline": True},
        {"name": "Seller", "value": item.seller or "\u2014", "inline": True},
        {"name": "Promo", "value": str(item.promotion).lower(), "inline": True},
        {"name": "Biz", "value": str(item.business_required).lower(), "inline": True},
        {"name": "ASIN", "value": f"[`{item.asin}`]({item.amazon_url})", "inline": True},
        {"name": "Tier", "value": f"`Amazon-{tier}`", "inline": True},
        {"name": "\u200b", "value": "\u200b", "inline": True},
        {
            "name": "Research",
            "value": (
                f"[GOOGLE]({item.google_url}) | [KEEPA]({item.keepa_url}) | "
                f"[SAS]({item.sas_url}) | [EBAY]({item.ebay_url})"
                f" · **[🛒\u00a0Add\u00a0to\u00a0Cart]({item.atc_url})**"
            ),
            "inline": False,
        },
    ]
    embed = {
        "author": {
            "name": "Amazon Price Error Monitor",
            "icon_url": _AMAZON_ICON,
            "url": "https://www.amazon.com",
        },
        "title": _truncate(item.title, 240),
        "url": item.amazon_url,
        "color": TIER_COLORS.get(tier, 0x2B2D31),
        "fields": fields,
        "footer": {"text": f"Keepa 90-day \u2022 Amazon-{tier}"},
        "timestamp": _stamp(),
    }
    if item.image_url:
        embed["thumbnail"] = {"url": item.image_url}
    if item.graph_url:
        embed["image"] = {"url": item.graph_url}
    content = f"**{item.discount}% OFF**"
    return content, embed
