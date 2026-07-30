"""Builds and sends Discord alerts."""
from __future__ import annotations

import json
import logging

import httpx

from alert_uis import EFC_LOGO_PATH, build_alert_ui
from config import Settings
from models import AlertItem

logger = logging.getLogger(__name__)


def build_embed(item: AlertItem, tier: int, *, style: str | None = None) -> dict:
    _, embed = build_alert_ui(item, tier, style)
    return embed


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _fetch_graph(url: str) -> bytes | None:
    """Download graph PNG. Reject tiny/blank-looking responses."""
    if not url:
        return None
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": _UA})
            if r.status_code != 200:
                return None
            ctype = (r.headers.get("content-type") or "").lower()
            if "image" not in ctype and not url.lower().endswith(".png"):
                # Keepa API graphimage sometimes omits content-type
                if not (r.content[:8].startswith(b"\x89PNG") or r.content[:2] == b"\xff\xd8"):
                    return None
            data = r.content
            # Blank/white Keepa charts are usually very small; real charts are larger.
            if len(data) < 2500:
                logger.warning("Graph image too small (%s bytes) — likely blank", len(data))
                return None
            return data
    except httpx.HTTPError:
        return None


def _logo_bytes() -> bytes | None:
    try:
        if EFC_LOGO_PATH.is_file():
            return EFC_LOGO_PATH.read_bytes()
    except OSError:
        return None
    return None


def send_alert(
    webhook_url: str,
    ping: str,
    item: AlertItem,
    tier: int,
    *,
    style: str | None = None,
) -> tuple[bool, str]:
    content, embed = build_alert_ui(item, tier, style)
    if ping and ping.strip():
        content = f"{ping.strip()}  {content}"[:2000]

    # Drop broken thumbnail URLs — Discord returns 400 {"embeds":["0"]} on bad images.
    thumb = (embed.get("thumbnail") or {}).get("url") or ""
    if thumb and not _url_ok(thumb):
        logger.warning("Dropping bad product image URL for %s: %s", item.asin, thumb[:80])
        embed.pop("thumbnail", None)

    graph_bytes = None
    if item.graph_url:
        graph_bytes = _fetch_graph(item.graph_url)
        if graph_bytes is None:
            from config import load_settings
            from keepa_client import graph_image_url_api

            s = load_settings()
            if s.keepa_api_key and item.asin:
                api_url = graph_image_url_api(item.asin, s.keepa_api_key, s.keepa_domain)
                graph_bytes = _fetch_graph(api_url)
                if graph_bytes:
                    logger.info("Used Keepa API graphimage for %s", item.asin)

    logo = _logo_bytes()
    # If logo missing, strip attachment:// icon so Discord doesn't 400.
    if not logo:
        author = embed.get("author") or {}
        if str(author.get("icon_url") or "").startswith("attachment://"):
            author.pop("icon_url", None)

    try:
        with httpx.Client(timeout=20.0) as client:
            files: list[tuple] = []
            if logo:
                files.append(("files[0]", ("efc_logo.png", logo, "image/png")))
            if graph_bytes:
                embed["image"] = {"url": "attachment://graph.png"}
                idx = len(files)
                files.append((f"files[{idx}]", ("graph.png", graph_bytes, "image/png")))

            if files:
                payload = {"content": content, "embeds": [embed]}
                resp = client.post(
                    webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files=files,
                )
            else:
                embed.pop("image", None)
                payload = {"content": content, "embeds": [embed]}
                resp = client.post(webhook_url, json=payload)

            if resp.status_code in (200, 204):
                return True, ""
            # Retry once without thumbnail if Discord rejected the embed.
            if resp.status_code == 400 and "thumbnail" in embed:
                embed.pop("thumbnail", None)
                if files:
                    if graph_bytes:
                        embed["image"] = {"url": "attachment://graph.png"}
                    payload = {"content": content, "embeds": [embed]}
                    resp = client.post(
                        webhook_url,
                        data={"payload_json": json.dumps(payload)},
                        files=files,
                    )
                else:
                    embed.pop("image", None)
                    resp = client.post(webhook_url, json={"content": content, "embeds": [embed]})
                if resp.status_code in (200, 204):
                    return True, "sent without thumbnail"
            return False, f"{resp.status_code}: {resp.text[:200]}"
    except httpx.HTTPError as exc:
        logger.warning("Discord send failed for %s: %s", item.asin, exc)
        return False, str(exc)


def _url_ok(url: str) -> bool:
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": _UA})
            if r.status_code >= 400:
                return False
            ctype = (r.headers.get("content-type") or "").lower()
            return "image" in ctype or r.content[:3] in (b"\xff\xd8\xff", b"\x89PN")
    except httpx.HTTPError:
        return False


def dispatch(item: AlertItem, tiers: list[int], settings: Settings) -> list[int]:
    delivered: list[int] = []
    for tier in tiers:
        url = settings.webhooks.get(tier)
        if not url:
            continue
        ok, err = send_alert(url, settings.pings.get(tier, ""), item, tier)
        if ok:
            delivered.append(tier)
            logger.info("Alerted %s (%s%%) -> Amazon-%s", item.asin, item.discount, tier)
        else:
            logger.warning("Failed Amazon-%s for %s: %s", tier, item.asin, err)
    return delivered
