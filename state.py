import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).parent / "state.db")))
ALERTED_LEAD_TTL_DAYS = 7


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS alerted_leads (
                lead_key   TEXT NOT NULL PRIMARY KEY,
                alerted_at TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS workflow_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id  TEXT NOT NULL DEFAULT '',
                workflow_no  TEXT NOT NULL DEFAULT '',
                email        TEXT NOT NULL DEFAULT '',
                phone        TEXT NOT NULL DEFAULT '',
                received_at  TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_we_received_at ON workflow_events(received_at)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS spike_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                alerted_at TEXT NOT NULL
            )
        """)
        # Migratie: workflow_no kolom toevoegen als die nog niet bestaat
        existing = {row[1] for row in con.execute("PRAGMA table_info(workflow_events)").fetchall()}
        if "workflow_no" not in existing:
            con.execute("ALTER TABLE workflow_events ADD COLUMN workflow_no TEXT NOT NULL DEFAULT ''")
    purge_old_alerted_leads()
    purge_old_workflow_events()


def purge_old_alerted_leads() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ALERTED_LEAD_TTL_DAYS)).isoformat()
    with _conn() as con:
        con.execute("DELETE FROM alerted_leads WHERE alerted_at < ?", (cutoff,))


def purge_old_workflow_events() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ALERTED_LEAD_TTL_DAYS)).isoformat()
    with _conn() as con:
        con.execute("DELETE FROM workflow_events WHERE received_at < ?", (cutoff,))


def get_last_spike_alerted_at() -> "datetime | None":
    with _conn() as con:
        row = con.execute(
            "SELECT alerted_at FROM spike_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return datetime.fromisoformat(row["alerted_at"])


def record_spike_alerted() -> None:
    with _conn() as con:
        con.execute("INSERT INTO spike_log (alerted_at) VALUES (?)", (datetime.now(timezone.utc).isoformat(),))


def record_workflow_event(workflow_id: str, workflow_no: str, email: str, phone: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO workflow_events (workflow_id, workflow_no, email, phone, received_at) VALUES (?,?,?,?,?)",
            (workflow_id, workflow_no, email, phone, now),
        )


def get_workflow_events(since: datetime, until: datetime) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT workflow_id, workflow_no, email, phone, received_at FROM workflow_events "
            "WHERE received_at >= ? AND received_at <= ? ORDER BY received_at",
            (since.isoformat(), until.isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]


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


def _lead_key(lead: dict) -> "str | None":
    return lead.get("email") or lead.get("phone") or None


def filter_new_leads(leads: list[dict]) -> list[dict]:
    if not leads:
        return []
    keys = [k for k in (_lead_key(l) for l in leads) if k]
    if not keys:
        return leads
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ALERTED_LEAD_TTL_DAYS)).isoformat()
    placeholders = ",".join("?" * len(keys))
    with _conn() as con:
        seen = {
            row[0]
            for row in con.execute(
                f"SELECT lead_key FROM alerted_leads WHERE lead_key IN ({placeholders}) AND alerted_at > ?",
                keys + [cutoff],
            ).fetchall()
        }
    return [l for l in leads if _lead_key(l) not in seen]


def mark_leads_alerted(leads: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = [(k, now) for l in leads if (k := _lead_key(l))]
    if not rows:
        return
    with _conn() as con:
        con.executemany(
            "INSERT OR REPLACE INTO alerted_leads (lead_key, alerted_at) VALUES (?, ?)", rows
        )
