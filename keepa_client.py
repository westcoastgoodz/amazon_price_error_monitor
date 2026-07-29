"""Thin Keepa API wrapper.

Uses the paid Keepa REST API:
  * /deal    - the "Browsing Deals" feed (products that recently dropped)
  * /product - full product details for enrichment / accurate filters

Docs: https://keepa.com/#!discuss/t/browsing-deals/338 and
      https://keepa.com/#!discuss/t/product-object/116
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.keepa.com"
IMAGE_BASE = "https://m.media-amazon.com/images/I/"

# Dark Keepa chart (matches Black Box / our sample UI mockups — not the white default).
_GRAPH_DARK = (
    "cBackground=1E1F22"
    "&cFont=DBDEE1"
    "&cAmazon=FF9900"
    "&cNew=9B7EDE"
    "&cUsed=888888"
    "&cSales=2ECC71"
    "&cBB=E91E8C"
    "&cFBA=3498DB"
    "&cFBM=95A5A6"
)


def decode_keepa_image_name(image) -> str:
    """Keepa deal.image is often an int[] of US-ASCII codes, not a string.

    Example: [54,49,107,...] -> \"61k3Lay7JUL.jpg\"
    """
    if image is None:
        return ""
    if isinstance(image, str):
        return image.strip()
    if isinstance(image, (list, tuple)):
        try:
            return "".join(chr(int(c)) for c in image).strip()
        except (TypeError, ValueError):
            return ""
    return str(image).strip()


def amazon_image_url(
    image_id: str | list | None,
    *,
    size: int = 120,
) -> str:
    """Build a Discord-safe Amazon CDN product image URL from a Keepa image id.

    ``size`` inserts Amazon's ``._SLnnn_`` resize so Discord thumbnails stay small.
    """
    raw = decode_keepa_image_name(image_id)
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        if "/images/I/" in raw and "._SL" not in raw and size > 0:
            base, _, query = raw.partition("?")
            if base.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                stem, dot, ext = base.rpartition(".")
                raw = f"{stem}._SL{int(size)}_{dot}{ext}"
                if query:
                    raw = raw + "?" + query
                return raw
        return raw
    name = raw.split("/")[-1].split("?")[0]
    if not name:
        return ""
    if "." not in name:
        name = name + ".jpg"
    if size > 0 and "._SL" not in name:
        stem, dot, ext = name.rpartition(".")
        name = f"{stem}._SL{int(size)}_{dot}{ext}"
    return IMAGE_BASE + name


def product_image_url(product: dict | None) -> str:
    """Prefer Keepa product.images[].l / imagesCSV; empty if none."""
    if not product:
        return ""
    images = product.get("images")
    if isinstance(images, list) and images:
        first = images[0] or {}
        if isinstance(first, dict):
            name = first.get("l") or first.get("m") or ""
            built = amazon_image_url(name if isinstance(name, str) else None)
            if built:
                return built
    csv = product.get("imagesCSV")
    if csv:
        first = str(csv).split(",")[0].strip()
        built = amazon_image_url(first)
        if built:
            return built
    return ""


def graph_image_url(asin: str, domain_code: str, days: int = 90) -> str:
    """Public Keepa price-history PNG — dark theme like sample alerts (0 tokens)."""
    return (
        f"https://graph.keepa.com/pricehistory.png?"
        f"asin={asin}&domain={domain_code}"
        f"&amazon=1&new=1&bb=1&salesrank=1&fba=1"
        f"&range={days}&width=600&height=220"
        f"&{_GRAPH_DARK}"
    )


def graph_image_url_api(asin: str, api_key: str, domain: int = 1, days: int = 90) -> str:
    """Keepa API graphimage (uses tokens) — same dark style as public graph."""
    return (
        f"https://api.keepa.com/graphimage?"
        f"key={api_key}&domain={domain}&asin={asin}"
        f"&amazon=1&new=1&bb=1&salesrank=1&fba=1"
        f"&range={days}&width=600&height=220"
        f"&{_GRAPH_DARK}"
    )

# Keepa csv / stats indexes we care about.
IDX_RATING = 16          # value is stars * 10  (e.g. 45 -> 4.5)
IDX_REVIEW_COUNT = 17


@dataclass
class DealRaw:
    """Normalized view of a single Keepa deal row (deals.dr[i])."""
    asin: str
    title: str
    image_url: str
    root_cat: int
    categories: list[int]
    current: list[int]          # cents, indexed by price type (-1 = none)
    avg: list[list[int]]        # [dateRange][priceType] cents
    delta_percent: list[list[int]]  # [dateRange][priceType] percent
    raw: dict = field(default_factory=dict)

    def price(self, price_type: int) -> float | None:
        try:
            c = self.current[price_type]
        except (IndexError, TypeError):
            return None
        return round(c / 100.0, 2) if c is not None and c >= 0 else None

    def avg_price(self, date_range: int, price_type: int) -> float | None:
        try:
            a = self.avg[date_range][price_type]
        except (IndexError, TypeError):
            return None
        return round(a / 100.0, 2) if a is not None and a >= 0 else None

    def discount_percent(self, date_range: int, price_type: int) -> int | None:
        try:
            d = self.delta_percent[date_range][price_type]
        except (IndexError, TypeError):
            return None
        return int(d) if d is not None else None


class KeepaError(RuntimeError):
    pass


class KeepaClient:
    def __init__(self, api_key: str, domain: int = 1, timeout: float = 30.0):
        if not api_key:
            raise KeepaError("KEEPA_API_KEY is not set")
        self.api_key = api_key
        self.domain = domain
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": "APEM/1.0"})
        self.tokens_left: int | None = None
        self.refill_in_ms: int = 0

    def close(self) -> None:
        self._client.close()

    # -- deals -------------------------------------------------
    def get_deals(self, selection: dict, page: int = 0) -> list[DealRaw]:
        params = {
            "key": self.api_key,
            "selection": json.dumps({**selection, "page": page}, separators=(",", ":")),
        }
        data = self._request("/deal", params)
        deals_block = (data or {}).get("deals") or {}
        rows = deals_block.get("dr") or []
        return [self._normalize_deal(r) for r in rows if r]

    def _normalize_deal(self, r: dict) -> DealRaw:
        image_url = amazon_image_url(r.get("image"))
        return DealRaw(
            asin=r.get("asin", ""),
            title=r.get("title", "") or "",
            image_url=image_url,
            root_cat=int(r.get("rootCat") or 0),
            categories=list(r.get("categories") or []),
            current=list(r.get("current") or []),
            avg=list(r.get("avg") or []),
            delta_percent=list(r.get("deltaPercent") or []),
            raw=r,
        )

    # -- product enrichment -----------------------------------
    def get_product(self, asin: str, stats_days: int = 90) -> dict | None:
        params = {
            "key": self.api_key,
            "domain": self.domain,
            "asin": asin,
            "stats": stats_days,
            "buybox": 1,
        }
        data = self._request("/product", params)
        products = (data or {}).get("products") or []
        return products[0] if products else None

    # -- low level --------------------------------------------
    def _request(self, path: str, params: dict, retries: int = 2) -> dict:
        url = BASE_URL + path
        for attempt in range(retries + 1):
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise KeepaError(f"HTTP error calling {path}: {exc}") from exc

            if resp.status_code == 429:
                # Out of tokens - back off using refill hint if present.
                wait = 20 * (attempt + 1)
                logger.warning("Keepa 429 (rate/tokens). Waiting %ss", wait)
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                raise KeepaError(f"Keepa {path} returned {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            self.tokens_left = data.get("tokensLeft", self.tokens_left)
            self.refill_in_ms = data.get("refillIn", 0) or 0
            return data
        raise KeepaError(f"Keepa {path} failed after retries")
