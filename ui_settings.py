"""UI / runtime settings overlay (data/ui_settings.json) on top of .env."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import DATA_DIR, DISCORD_TIERS

DEFAULTS: dict[str, Any] = {
    "scan_interval_min": 25,
    "max_alerts_per_tier": 1,
    "enrich_on_alert": True,
    "no_repeat_same_day": True,
    "allow_cheaper_repeat": True,
    "min_discount": 50,
    "min_original_price": 8.99,
    # Credentials (UI overrides .env when non-empty)
    "keepa_api_key": "",
    "discord_webhook_50": "",
    "discord_webhook_60": "",
    "discord_webhook_70": "",
    "discord_webhook_80": "",
    # Channel on/off — unchecked = no alerts to that Discord group
    "channel_enabled_50": True,
    "channel_enabled_60": True,
    "channel_enabled_70": True,
    "channel_enabled_80": True,
}

_SECRET_KEYS = (
    "keepa_api_key",
    "discord_webhook_50",
    "discord_webhook_60",
    "discord_webhook_70",
    "discord_webhook_80",
)


def _settings_path() -> Path:
    return DATA_DIR / "ui_settings.json"


def load_ui_settings() -> dict[str, Any]:
    data = dict(DEFAULTS)
    path = _settings_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in DEFAULTS:
                    if k in raw:
                        data[k] = raw[k]
        except (OSError, json.JSONDecodeError):
            pass
    data["scan_interval_min"] = max(5, min(120, int(data["scan_interval_min"])))
    data["max_alerts_per_tier"] = max(1, min(5, int(data["max_alerts_per_tier"])))
    data["min_discount"] = max(40, min(95, int(data["min_discount"])))
    data["min_original_price"] = max(0.0, float(data["min_original_price"]))
    for k in _SECRET_KEYS:
        data[k] = str(data.get(k) or "").strip()
    for tier in DISCORD_TIERS:
        ck = f"channel_enabled_{tier}"
        data[ck] = bool(data.get(ck, True))
    return data


def save_ui_settings(updates: dict[str, Any]) -> dict[str, Any]:
    data = load_ui_settings()
    for k, v in updates.items():
        if k in DEFAULTS:
            data[k] = v
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return load_ui_settings()


def apply_ui_to_settings(settings) -> None:
    """Mutate Settings with UI overlay when ui_settings.json exists.

    If the file is missing, env/.env stays the source of truth (Render deploy).
    """
    path = _settings_path()
    if not path.is_file():
        return

    ui = load_ui_settings()
    settings.poll_interval_sec = int(ui["scan_interval_min"]) * 60
    settings.min_discount = int(ui["min_discount"])
    settings.min_original_price = float(ui["min_original_price"])
    settings.enrich_products = False
    settings.max_alerts_per_tier = int(ui["max_alerts_per_tier"])
    settings.enrich_on_alert = bool(ui["enrich_on_alert"])
    settings.no_repeat_same_day = bool(ui["no_repeat_same_day"])
    settings.allow_cheaper_repeat = bool(ui["allow_cheaper_repeat"])

    key = str(ui.get("keepa_api_key") or "").strip()
    if key:
        settings.keepa_api_key = key

    webhooks = dict(settings.webhooks or {})
    for tier in DISCORD_TIERS:
        wh = str(ui.get(f"discord_webhook_{tier}") or "").strip()
        if wh:
            webhooks[tier] = wh
    # Drop disabled channels so monitor won't alert / won't count them as active tiers
    settings.webhooks = {
        k: v
        for k, v in webhooks.items()
        if v and bool(ui.get(f"channel_enabled_{k}", True))
    }
    settings.channel_enabled = {  # type: ignore[attr-defined]
        t: bool(ui.get(f"channel_enabled_{t}", True)) for t in DISCORD_TIERS
    }
