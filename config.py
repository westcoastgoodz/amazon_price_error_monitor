"""Configuration loader for the Amazon Price Error Monitor.

All settings come from a local .env file (see .env.example). Nothing is
hard-coded so the client can tune everything without touching the code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Discord channels: Amazon-50 / 60 / 70 / 80 (highest first).
DISCORD_TIERS = (80, 70, 60, 50)

load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


# Persist DB / UI settings here. On Render set DATA_DIR=/var/data (disk mount).
_data_raw = _get("DATA_DIR")
DATA_DIR = Path(_data_raw) if _data_raw else (BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_int(name: str, default: int) -> int:
    try:
        return int(float(_get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    val = _get(name, str(default)).lower()
    return val in ("1", "true", "yes", "y", "on")


def _get_list_int(name: str) -> list[int]:
    raw = _get(name)
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def _get_list_str(name: str) -> list[str]:
    raw = _get(name)
    return [p.strip().lower() for p in raw.replace(";", ",").split(",") if p.strip()]


# Keepa domain id -> country code used by the public price-history graph.
DOMAIN_CODE = {
    1: "com", 2: "co.uk", 3: "de", 4: "fr", 5: "co.jp",
    6: "ca", 8: "it", 9: "es", 10: "in", 11: "com.mx",
}

# Keepa domain id -> Amazon storefront host for product / cart links.
DOMAIN_HOST = {
    1: "www.amazon.com", 2: "www.amazon.co.uk", 3: "www.amazon.de",
    4: "www.amazon.fr", 5: "www.amazon.co.jp", 6: "www.amazon.ca",
    8: "www.amazon.it", 9: "www.amazon.es", 10: "www.amazon.in",
    11: "www.amazon.com.mx",
}

# US Amazon seller id (used to label the buy box as "Amazon").
AMAZON_SELLER_IDS = {
    1: "ATVPDKIKX0DER", 2: "A3P5ROKL5A1OLE", 3: "A3JWKAKR8XB7XF",
    6: "A3DWYIK6Y9EEQB", 11: "AVDBXBAVVSXLQ",
}


@dataclass
class Settings:
    keepa_api_key: str = ""
    keepa_domain: int = 1
    price_type: int = 1
    date_range: int = 3

    webhooks: dict[int, str] = field(default_factory=dict)
    pings: dict[int, str] = field(default_factory=dict)
    route_mode: str = "highest"

    poll_interval_sec: int = 1500  # 25 min — Keepa Pro friendly
    min_discount: int = 50
    alert_cooldown_hours: int = 24
    reference_mode: str = "keepa_avg"  # keepa_avg | rolling_30d

    max_alerts_per_tier: int = 2
    enrich_on_alert: bool = True
    no_repeat_same_day: bool = True
    allow_cheaper_repeat: bool = True

    min_original_price: float = 4.0
    min_review_count: int = 0
    min_rating: float = 0.0
    include_categories: list[int] = field(default_factory=list)
    exclude_categories: list[int] = field(default_factory=list)
    include_brands: list[str] = field(default_factory=list)
    exclude_brands: list[str] = field(default_factory=list)
    seller_type: str = "any"
    title_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    enrich_products: bool = True

    # Real price-error quality (vs raw Keepa deal junk)
    require_lowest: bool = False
    min_recent_discount: int = 20
    reject_promotions: bool = False
    reject_business: bool = False
    verify_live_price: bool = True

    include_graph: bool = True
    log_level: str = "INFO"

    @property
    def tiers(self) -> list[int]:
        """Discount tiers that actually have a webhook configured, high -> low."""
        return sorted((t for t in DISCORD_TIERS if self.webhooks.get(t)), reverse=True)

    @property
    def domain_code(self) -> str:
        return DOMAIN_CODE.get(self.keepa_domain, "com")

    @property
    def domain_host(self) -> str:
        return DOMAIN_HOST.get(self.keepa_domain, "www.amazon.com")

    @property
    def amazon_seller_id(self) -> str:
        return AMAZON_SELLER_IDS.get(self.keepa_domain, "")


def load_settings() -> Settings:
    webhooks = {
        50: _get("DISCORD_WEBHOOK_50"),
        60: _get("DISCORD_WEBHOOK_60"),
        70: _get("DISCORD_WEBHOOK_70"),
        80: _get("DISCORD_WEBHOOK_80"),
    }
    # CHANNEL_ENABLED_50=false → skip that tier even if webhook is set.
    channel_on = {
        50: _get_bool("CHANNEL_ENABLED_50", True),
        60: _get_bool("CHANNEL_ENABLED_60", True),
        70: _get_bool("CHANNEL_ENABLED_70", True),
        80: _get_bool("CHANNEL_ENABLED_80", True),
    }
    pings = {
        50: _get("DISCORD_PING_50"),
        60: _get("DISCORD_PING_60"),
        70: _get("DISCORD_PING_70"),
        80: _get("DISCORD_PING_80"),
    }
    s = Settings(
        keepa_api_key=_get("KEEPA_API_KEY"),
        keepa_domain=_get_int("KEEPA_DOMAIN", 1),
        price_type=_get_int("PRICE_TYPE", 1),
        date_range=_get_int("DATE_RANGE", 3),
        webhooks={k: v for k, v in webhooks.items() if v and channel_on.get(k, True)},
        pings={k: v for k, v in pings.items() if v},
        route_mode=_get("ROUTE_MODE", "highest").lower() or "highest",
        poll_interval_sec=_get_int("POLL_INTERVAL_SEC", 1500),
        min_discount=_get_int("MIN_DISCOUNT", 50),
        alert_cooldown_hours=_get_int("ALERT_COOLDOWN_HOURS", 24),
        reference_mode=_get("REFERENCE_MODE", "keepa_avg").lower() or "keepa_avg",
        max_alerts_per_tier=max(1, min(5, _get_int("MAX_ALERTS_PER_TIER", 2))),
        enrich_on_alert=_get_bool("ENRICH_ON_ALERT", True),
        no_repeat_same_day=_get_bool("NO_REPEAT_SAME_DAY", True),
        allow_cheaper_repeat=_get_bool("ALLOW_CHEAPER_REPEAT", True),
        min_original_price=_get_float("MIN_ORIGINAL_PRICE", 4.0),
        min_review_count=_get_int("MIN_REVIEW_COUNT", 0),
        min_rating=_get_float("MIN_RATING", 0.0),
        include_categories=_get_list_int("INCLUDE_CATEGORIES"),
        exclude_categories=_get_list_int("EXCLUDE_CATEGORIES"),
        include_brands=_get_list_str("INCLUDE_BRANDS"),
        exclude_brands=_get_list_str("EXCLUDE_BRANDS"),
        seller_type=_get("SELLER_TYPE", "any").lower() or "any",
        title_keywords=_get_list_str("TITLE_KEYWORDS"),
        exclude_keywords=_get_list_str("EXCLUDE_KEYWORDS"),
        enrich_products=_get_bool("ENRICH_PRODUCTS", False),
        require_lowest=_get_bool("REQUIRE_LOWEST", False),
        min_recent_discount=_get_int("MIN_RECENT_DISCOUNT", 20),
        reject_promotions=_get_bool("REJECT_PROMOTIONS", False),
        reject_business=_get_bool("REJECT_BUSINESS", False),
        verify_live_price=_get_bool("VERIFY_LIVE_PRICE", True),
        include_graph=_get_bool("INCLUDE_GRAPH", True),
        log_level=_get("LOG_LEVEL", "INFO").upper() or "INFO",
    )
    # Only overlay when UI file exists (env stays source of truth on Render).
    from ui_settings import apply_ui_to_settings

    apply_ui_to_settings(s)
    return s
