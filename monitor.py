"""Main monitoring loop: smart Keepa scan → best deal per Discord tier."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from config import DATA_DIR, Settings
from discord_notifier import send_alert
from filters import passes_filters, target_tiers
from keepa_client import KeepaClient, KeepaError
from models import AlertItem, build_alert
from storage import Storage
from ui_settings import apply_ui_to_settings

logger = logging.getLogger(__name__)

INT_MAX = 2147483647


def build_selection(s: Settings) -> dict:
    selection: dict = {
        "domainId": s.keepa_domain,
        "priceTypes": [s.price_type],
        "dateRange": s.date_range,
        "sortType": 4,
        "deltaPercentRange": [s.min_discount, INT_MAX],
        "isFilterEnabled": True,
        "isRangeEnabled": True,
    }
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

    def run_once(self) -> int:
        self.reload_settings()
        try:
            deals = self.keepa.get_deals(self.selection)
        except KeepaError as exc:
            logger.error("Keepa deal fetch failed: %s", exc)
            return 0

        logger.info(
            "Fetched %d deals (tokens left: %s) | scan every %ss",
            len(deals),
            self.keepa.tokens_left,
            self.s.poll_interval_sec,
        )

        # Build candidates WITHOUT enriching every row (saves tokens).
        by_tier: dict[int, list[AlertItem]] = defaultdict(list)
        for deal in deals:
            if not deal.asin:
                continue
            item = build_alert(deal, None, self.s)
            if item is None:
                continue
            self._apply_rolling_reference(item)
            ok, reason = passes_filters(item, self.s)
            if not ok:
                continue
            tiers = target_tiers(item.discount, self.s)
            if not tiers:
                continue
            if not self.store.should_alert(
                item.asin,
                item.new_price,
                cooldown_hours=self.s.alert_cooldown_hours,
                no_repeat_same_day=bool(getattr(self.s, "no_repeat_same_day", True)),
                allow_if_cheaper=bool(getattr(self.s, "allow_cheaper_repeat", True)),
            ):
                continue
            # highest mode → one tier; cascade → list (still pick best per tier below)
            for t in tiers:
                by_tier[t].append(item)

        max_per = int(getattr(self.s, "max_alerts_per_tier", 1) or 1)
        enrich = bool(getattr(self.s, "enrich_on_alert", True))
        alerted = 0
        sent_asins: set[str] = set()

        # Process high tiers first so a 90% deal goes to Amazon-90, not also treated as filler.
        for tier in sorted(by_tier.keys(), reverse=True):
            pool = by_tier[tier]
            # Unique ASINs, best score first
            best_by_asin: dict[str, AlertItem] = {}
            for it in pool:
                prev = best_by_asin.get(it.asin)
                if prev is None or _deal_score(it) > _deal_score(prev):
                    best_by_asin[it.asin] = it
            ranked = sorted(best_by_asin.values(), key=_deal_score, reverse=True)

            picked = 0
            for item in ranked:
                if picked >= max_per:
                    break
                if item.asin in sent_asins:
                    continue

                # Enrich only this winner (seller / business / better image).
                if enrich:
                    try:
                        product = self.keepa.get_product(item.asin)
                    except KeepaError as exc:
                        logger.debug("Enrich failed %s: %s", item.asin, exc)
                        product = None
                    if product:
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
                        from keepa_client import product_image_url

                        built = product_image_url(product)
                        if built:
                            item.image_url = built

                webhook = self.s.webhooks.get(tier)
                if not webhook:
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
                        "Alerted %s (%s%%) -> Amazon-%s | seller=%s",
                        item.asin, item.discount, tier, item.seller,
                    )
                else:
                    logger.warning("Failed Amazon-%s for %s: %s", tier, item.asin, err)

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
        while not self._stop:
            try:
                self.reload_settings()
                n = self.run_once()
                wait = self.s.poll_interval_sec
                logger.info("Cycle done. %d alert(s). Next scan in %ss (%s min).", n, wait, wait // 60)
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error in monitor cycle")
                wait = self.s.poll_interval_sec
            # Sleep in chunks so stop / interval change can apply sooner
            end = time.time() + wait
            while time.time() < end and not self._stop:
                time.sleep(min(5.0, end - time.time()))

    def close(self) -> None:
        self.keepa.close()
        self.store.close()
