"""SQLite: alert de-duplication (no same-day repeats) + rolling price history."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                asin           TEXT PRIMARY KEY,
                last_price     REAL,
                last_discount  INTEGER,
                last_alert_ts  INTEGER,
                last_alert_day TEXT
            );
            CREATE TABLE IF NOT EXISTS price_history (
                asin  TEXT,
                ts    INTEGER,
                price REAL
            );
            CREATE INDEX IF NOT EXISTS idx_hist_asin_ts ON price_history(asin, ts);
            """
        )
        # Older DBs may lack last_alert_day
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(alerts)").fetchall()}
        if "last_alert_day" not in cols:
            self.conn.execute("ALTER TABLE alerts ADD COLUMN last_alert_day TEXT DEFAULT ''")
        self.conn.commit()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def should_alert(
        self,
        asin: str,
        price: float,
        *,
        cooldown_hours: int = 24,
        no_repeat_same_day: bool = True,
        allow_if_cheaper: bool = True,
    ) -> bool:
        row = self.conn.execute(
            "SELECT last_price, last_alert_ts, last_alert_day FROM alerts WHERE asin = ?",
            (asin,),
        ).fetchone()
        if row is None:
            return True

        now = int(time.time())
        # Same calendar day (UTC) — do not re-alert unless price dropped further.
        if no_repeat_same_day and (row["last_alert_day"] or "") == self._today():
            if allow_if_cheaper and price < (row["last_price"] or 1e12):
                return True
            return False

        if allow_if_cheaper and price < (row["last_price"] or 1e12):
            return True
        if now - (row["last_alert_ts"] or 0) >= cooldown_hours * 3600:
            return True
        return False

    def record_alert(self, asin: str, price: float, discount: int) -> None:
        self.conn.execute(
            """
            INSERT INTO alerts (asin, last_price, last_discount, last_alert_ts, last_alert_day)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(asin) DO UPDATE SET
                last_price = excluded.last_price,
                last_discount = excluded.last_discount,
                last_alert_ts = excluded.last_alert_ts,
                last_alert_day = excluded.last_alert_day
            """,
            (asin, price, discount, int(time.time()), self._today()),
        )
        self.conn.commit()

    def add_price(self, asin: str, price: float) -> None:
        self.conn.execute(
            "INSERT INTO price_history (asin, ts, price) VALUES (?, ?, ?)",
            (asin, int(time.time()), price),
        )
        self.conn.commit()

    def rolling_reference(self, asin: str, days: int = 30) -> float | None:
        cutoff = int(time.time()) - days * 86400
        row = self.conn.execute(
            "SELECT MAX(price) AS hi FROM price_history WHERE asin = ? AND ts >= ?",
            (asin, cutoff),
        ).fetchone()
        return row["hi"] if row and row["hi"] is not None else None

    def prune_history(self, days: int = 45) -> None:
        cutoff = int(time.time()) - days * 86400
        self.conn.execute("DELETE FROM price_history WHERE ts < ?", (cutoff,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
