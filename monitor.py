import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

import ghl_alert
import ghl_client
import meta_client
import state

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def _workflow_ids() -> list[str]:
    raw = os.environ.get("GHL_WORKFLOW_IDS", "")
    ids = [i.strip() for i in raw.split(",") if i.strip()]
    if not ids:
        raise ValueError("GHL_WORKFLOW_IDS niet ingesteld in .env")
    return ids


def _is_match(meta_lead: dict, ghl_contacts: list[dict]) -> bool:
    email = meta_lead.get("email", "")
    phone = meta_lead.get("phone", "")
    for c in ghl_contacts:
        if email and c.get("email") == email:
            return True
        if phone and c.get("phone") == phone:
            return True
    return False


def _label(lead: dict) -> str:
    return lead.get("email") or lead.get("phone") or "onbekend"


def run_check() -> None:
    window_minutes = int(os.environ.get("WINDOW_MINUTES", 30))
    grace_minutes = int(os.environ.get("GRACE_MINUTES", 10))
    spike_threshold = int(os.environ.get("SPIKE_THRESHOLD", 2))

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=window_minutes)

    log.info("Check | venster: %s – %s UTC", window_start.strftime("%H:%M"), now.strftime("%H:%M"))

    # ── Spike check ───────────────────────────────────────────────────────────
    spike_since = now - timedelta(minutes=5)
    try:
        spike_contacts, spike_counts = ghl_client.get_all_workflow_contacts(_workflow_ids(), spike_since, now)
        spike_total = sum(spike_counts.values())
        if spike_total > spike_threshold:
            log.warning("SPIKE: %d leads in 5 min.", spike_total)
            ghl_alert.send_spike_sms(spike_counts, spike_total, spike_contacts, now)
        else:
            log.info("Spike OK: %d <= %d", spike_total, spike_threshold)
    except Exception as exc:
        log.error("Spike check fout: %s", exc)

    # ── Mismatch check ────────────────────────────────────────────────────────
    meta_until = now - timedelta(minutes=grace_minutes)
    if meta_until <= window_start:
        log.info("Grace period beslaat heel venster.")
        return

    window_key = state.window_key(window_start, meta_until)
    if state.already_alerted(window_key):
        log.info("Al gealerteerd voor dit venster.")
        return

    try:
        meta_leads = meta_client.get_leads(window_start, meta_until)
        log.info("Meta leads: %d", len(meta_leads))
    except Exception as exc:
        log.error("Meta API fout: %s", exc)
        return

    try:
        ghl_contacts, _ = ghl_client.get_all_workflow_contacts(_workflow_ids(), window_start, now)
        log.info("GHL contacten: %d", len(ghl_contacts))
    except Exception as exc:
        log.error("GHL API fout: %s", exc)
        return

    if not meta_leads:
        log.info("Geen Meta-leads in venster.")
        return

    today = now.strftime("%Y-%m-%d")
    missing = [l for l in meta_leads if not _is_match(l, ghl_contacts)]

    if not missing:
        log.info("OK — alle %d leads in GHL.", len(meta_leads))
        return

    log.warning("MISMATCH: %d van %d leads niet in GHL.", len(missing), len(meta_leads))
    try:
        ghl_alert.send_mismatch_sms(len(meta_leads), len(ghl_contacts), missing, now)
        state.record_alert(window_key)
        state.record_mismatch(len(meta_leads), len(ghl_contacts), missing)
    except Exception as exc:
        log.error("Mismatch SMS fout: %s", exc)
