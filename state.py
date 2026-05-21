import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "state.db"


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                window_key TEXT NOT NULL UNIQUE,
                alerted_at TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS mismatch_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_count  INTEGER NOT NULL,
                ghl_count   INTEGER NOT NULL,
                diff        INTEGER NOT NULL,
                contacts    TEXT NOT NULL DEFAULT '[]',
                date        TEXT NOT NULL,
                detected_at TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ml_date ON mismatch_log(date)")


def window_key(window_start: datetime, window_end: datetime) -> str:
    raw = f"{window_start.isoformat()}|{window_end.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def already_alerted(key: str) -> bool:
    with _conn() as con:
        row = con.execute("SELECT 1 FROM alert_log WHERE window_key = ?", (key,)).fetchone()
    return row is not None


def record_alert(key: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO alert_log (window_key, alerted_at) VALUES (?, ?)",
            (key, datetime.now(timezone.utc).isoformat()),
        )


def record_mismatch(meta_count: int, ghl_count: int, contacts: list) -> None:
    now = datetime.now(timezone.utc)
    with _conn() as con:
        con.execute(
            "INSERT INTO mismatch_log (meta_count, ghl_count, diff, contacts, date, detected_at) VALUES (?,?,?,?,?,?)",
            (
                meta_count, ghl_count, meta_count - ghl_count,
                json.dumps(contacts),
                now.strftime("%Y-%m-%d"), now.isoformat(),
            ),
        )


def get_mismatches_for_date(date: str) -> list:
    with _conn() as con:
        rows = con.execute(
            "SELECT meta_count, ghl_count, diff, contacts, detected_at FROM mismatch_log WHERE date = ? ORDER BY detected_at",
            (date,),
        ).fetchall()
    result = []
    for r in rows:
        entry = dict(r)
        entry["contacts"] = json.loads(entry["contacts"])
        result.append(entry)
    return result


def get_mismatch_count_for_date(date: str) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) as cnt FROM mismatch_log WHERE date = ?", (date,)
        ).fetchone()
    return row["cnt"] if row else 0
