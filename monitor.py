"""Main monitoring loop: smart Keepa scan → best deal per Discord tier."""
from __future__ import annotations

import logging
import time

from config import DATA_DIR, Settings
from discord_notifier import send_alert
from filters import passes_filters, passes_price_error_quality, target_tiers
from keepa_client import KeepaClient, KeepaError
from models import AlertItem, build_alert
from storage import Storage
from ui_settings import apply_ui_to_settings

logger = logging.getLogger(__name__)

INT_MAX = 2147483647

# Keepa page is sorted by high discount — one wide fetch floods top tiers and
# starves lower ones. Fetch each band separately so every channel can alert.
# Bands stop at 89% — no Amazon-90 channel and no 90%+ alerts.
TIER_BANDS: dict[int, tuple[int, int]] = {
    80: (80, 89),
    70: (70, 79),
    60: (60, 69),
    50: (50, 59),
}


def build_selection(
    s: Settings,
    *,
    delta_percent_range: tuple[int, int] | None = None,
) -> dict:
    # Hard cap: never request 90%+ deals.
    lo, hi = delta_percent_range or (s.min_discount, 89)
    hi = min(int(hi), 89)
    lo = min(int(lo), hi)
    selection: dict = {
        "domainId": s.keepa_domain,
        "priceTypes": [s.price_type],
        "dateRange": s.date_range,
        "sortType": 4,
        "deltaPercentRange": [lo, hi],
        "isFilterEnabled": True,
        "isRangeEnabled": True,
        "filterErotic": True,
        "singleVariation": True,
    }
    # Prefer historical lows — closer to real price errors than random sale %.
    if bool(getattr(s, "require_lowest", True)):
        selection["isLowest"] = True
    if s.include_categories:
        selection["includeCategories"] = s.include_categories
    if s.exclude_categories:
        selection["excludeCategories"] = s.exclude_categories
    if s.min_rating > 0:
        selection["minRating"] = int(round(s.min_rating * 10))
    if s.title_keywords:
        selection["titleSearch"] = " ".join(s.title_keywords)
    return selection


def _deal_score(item: AlertItem) -> tuple:
    """Higher is better: discount %, then dollars saved, then lower new price."""
    saved = max(0.0, item.old_price - item.new_price)
    return (item.discount, saved, -item.new_price)


def _tier_band(tier: int, min_discount: int) -> tuple[int, int] | None:
    """Discount window for a Discord tier, clipped to min_discount."""
    if tier not in TIER_BANDS:
        return None
    lo, hi = TIER_BANDS[tier]
    lo = max(lo, int(min_discount))
    if lo > hi:
        return None
    return lo, hi


class Monitor:
    def __init__(self, settings: Settings):
        apply_ui_to_settings(settings)
        self.s = settings
        self.keepa = KeepaClient(settings.keepa_api_key, settings.keepa_domain)
        self.store = Storage(DATA_DIR / "monitor.db")
        self.selection = build_selection(settings)
        self._stop = False

    def reload_settings(self, settings: Settings | None = None) -> None:
        if settings is not None:
            self.s = settings
        apply_ui_to_settings(self.s)
        self.selection = build_selection(self.s)

    def request_stop(self) -> None:
        self._stop = True

    def _apply_rolling_reference(self, item: AlertItem) -> None:
        self.store.add_price(item.asin, item.new_price)
        if self.s.reference_mode != "rolling_30d":
            return
        ref = self.store.rolling_reference(item.asin, 30)
        if ref and ref > item.new_price:
            item.old_price = round(ref, 2)
            item.discount = int(round((ref - item.new_price) / ref * 100))

    def _candidates_from_deals(self, deals: list, tier: int) -> list[AlertItem]:
        """Filter deals that route to this tier under current settings."""
        out: list[AlertItem] = []
        for deal in deals:
            if not deal.asin:
                continue
            item = build_alert(deal, None, self.s)
            if item is None:
                continue
            self._apply_rolling_reference(item)
            ok, _reason = passes_filters(item, self.s)
            if not ok:
                continue
            tiers = target_tiers(item.discount, self.s)
            if tier not in tiers:
                continue
            if not self.store.should_alert(
                item.asin,
                item.new_price,
                cooldown_hours=self.s.alert_cooldown_hours,
                no_repeat_same_day=bool(getattr(self.s, "no_repeat_same_day", True)),
                allow_if_cheaper=bool(getattr(self.s, "allow_cheaper_repeat", True)),
            ):
                continue
            out.append(item)
        return out

    def _enrich(self, item: AlertItem) -> dict | None:
        try:
            product = self.keepa.get_product(item.asin)
        except KeepaError as exc:
            logger.debug("Enrich failed %s: %s", item.asin, exc)
            return None
        if not product:
            return None
        from keepa_client import product_image_url
        from models import _extract_stats, _seller_label

        stats = _extract_stats(product)
        item.seller = _seller_label(stats, self.s.amazon_seller_id)
        item.promotion = bool(stats.get("promotion"))
        item.business_required = bool(stats.get("business_required"))
        item.brand = stats.get("brand")
        item.review_count = stats.get("review_count")
        item.rating = stats.get("rating")
        title = (product.get("title") or "").strip()
        if title:
            item.title = title
        built = product_image_url(product)
        if built:
            item.image_url = built
        return product

    def run_once(self) -> int:
        self.reload_settings()
        max_per = int(getattr(self.s, "max_alerts_per_tier", 1) or 1)
        # Quality gates need /product — always enrich before send.
        enrich = True
        alerted = 0
        sent_asins: set[str] = set()

        # High tiers first; each band is its own Keepa /deal call.
        active_tiers = [t for t in (80, 70, 60, 50) if self.s.webhooks.get(t)]
        for i, tier in enumerate(active_tiers):
            if self._stop:
                break
            band = _tier_band(tier, self.s.min_discount)
            if band is None:
                continue
            selection = build_selection(self.s, delta_percent_range=band)
            try:
                deals = self.keepa.get_deals(selection)
            except KeepaError as exc:
                logger.error("Keepa deal fetch failed for Amazon-%s: %s", tier, exc)
                continue

            logger.info(
                "Amazon-%s band %s–%s: %d deals (tokens left: %s)",
                tier,
                band[0],
                "max" if band[1] >= 89 and band[0] >= 80 else band[1],
                len(deals),
                self.keepa.tokens_left,
            )

            candidates = self._candidates_from_deals(deals, tier)
            best_by_asin: dict[str, AlertItem] = {}
            for it in candidates:
                prev = best_by_asin.get(it.asin)
                if prev is None or _deal_score(it) > _deal_score(prev):
                    best_by_asin[it.asin] = it
            ranked = sorted(best_by_asin.values(), key=_deal_score, reverse=True)

            picked = 0
            webhook = self.s.webhooks.get(tier)
            if not webhook:
                continue

            for item in ranked:
                if picked >= max_per:
                    break
                if item.asin in sent_asins:
                    continue

                # Cheap pre-check (no Keepa tokens): sudden 7d drop, etc.
                ok_pre, reason_pre = passes_price_error_quality(item, None, self.s)
                if not ok_pre and "sudden drop" in reason_pre:
                    logger.info("Skip %s: %s", item.asin, reason_pre)
                    continue

                product = self._enrich(item) if enrich else None
                # Re-check seller filters after enrich.
                ok_basic, reason_basic = passes_filters(item, self.s)
                if not ok_basic:
                    logger.info("Skip %s after enrich: %s", item.asin, reason_basic)
                    continue
                ok_q, reason_q = passes_price_error_quality(item, product, self.s)
                if not ok_q:
                    logger.info("Skip %s (not real price error): %s", item.asin, reason_q)
                    continue

                ok, err = send_alert(
                    webhook,
                    self.s.pings.get(tier, ""),
                    item,
                    tier,
                )
                if ok:
                    self.store.record_alert(item.asin, item.new_price, item.discount)
                    sent_asins.add(item.asin)
                    alerted += 1
                    picked += 1
                    logger.info(
                        "Alerted %s (%s%%, 7d=%s%%) -> Amazon-%s | seller=%s",
                        item.asin,
                        item.discount,
                        item.recent_discount,
                        tier,
                        item.seller,
                    )
                else:
                    logger.warning("Failed Amazon-%s for %s: %s", tier, item.asin, err)

            # Brief pause between band calls (token refill / Keepa politeness).
            if i < len(active_tiers) - 1 and not self._stop:
                time.sleep(1.5)

        self.store.prune_history(45)
        return alerted

    def run_forever(self) -> None:
        self.reload_settings()
        logger.info(
            "Monitor started. tiers=%s interval=%ss min_discount=%s%% no_repeat_day=%s",
            self.s.tiers,
            self.s.poll_interval_sec,
            self.s.min_discount,
            getattr(self.s, "no_repeat_same_day", True),
        )
        first = True
        while not self._stop:
            try:
                self.reload_settings()
                n = self.run_once()
                wait = self.s.poll_interval_sec
                if first:
                    logger.info(
                        "First scan done (%d alert(s)). Next in %ss (%s min).",
                        n, wait, wait // 60,
                    )
                    first = False
                else:
                    logger.info(
                        "Cycle done. %d alert(s). Next scan in %ss (%s min).",
                        n, wait, wait // 60,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error in monitor cycle")
                wait = self.s.poll_interval_sec
            cycle_start = time.time()
            wait = self.s.poll_interval_sec
            end = cycle_start + wait
            while time.time() < end and not self._stop:
                time.sleep(min(5.0, end - time.time()))
                self.reload_settings()
                if self.s.poll_interval_sec != wait:
                    wait = self.s.poll_interval_sec
                    end = cycle_start + wait

    def close(self) -> None:
        self.keepa.close()
        self.store.close()
