import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

import ghl_alert
import ghl_client
import meta_client
import state
from utils import normalize_phone

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def _label(lead: dict) -> str:
    return lead.get("email") or lead.get("phone") or "onbekend"


def _is_in_events(lead: dict, events: list[dict]) -> bool:
    email = (lead.get("email") or "").lower().strip()
    phone = normalize_phone(lead.get("phone") or "")
    for e in events:
        if email and e.get("email") == email:
            return True
        if phone and normalize_phone(e.get("phone") or "") == phone:
            return True
    return False


def run_check() -> None:
    window_minutes = int(os.environ.get("WINDOW_MINUTES", 30))
    grace_minutes = int(os.environ.get("GRACE_MINUTES", 10))
    spike_threshold = int(os.environ.get("SPIKE_THRESHOLD", 4))
    spike_window = int(os.environ.get("SPIKE_WINDOW_MINUTES", 15))

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=window_minutes)

    log.info("Check | venster: %s – %s UTC", window_start.strftime("%H:%M"), now.strftime("%H:%M"))

    # ── Spike check (via webhook events) ─────────────────────────────────────
    spike_since = now - timedelta(minutes=spike_window)
    try:
        spike_events = state.get_workflow_events(spike_since, now)

        # Dedupleer op email/phone voor unieke leads
        seen: set[str] = set()
        unique_contacts: list[dict] = []
        for e in spike_events:
            key = e.get("email") or e.get("phone")
            if key and key not in seen:
                seen.add(key)
                unique_contacts.append(e)

        # Per-workflow breakdown — gebruik workflowNo als label indien beschikbaar
        counts: dict[str, int] = {}
        for e in spike_events:
            wf_no = e.get("workflow_no", "")
            wf_label = f"Flow {wf_no}" if wf_no else e.get("workflow_id", "unknown")[:8]
            counts[wf_label] = counts.get(wf_label, 0) + 1

        spike_total = len(unique_contacts)
        if spike_total > spike_threshold:
            log.warning("SPIKE: %d unieke leads in %d min.", spike_total, spike_window)
            ghl_alert.send_spike_sms(counts, spike_total, unique_contacts, now, spike_window)
        else:
            log.info("Spike OK: %d unieke leads <= %d (venster %d min)", spike_total, spike_threshold, spike_window)
    except Exception as exc:
        log.error("Spike check fout: %s", exc)

    # ── Mismatch check ────────────────────────────────────────────────────────
    meta_until = now - timedelta(minutes=grace_minutes)
    if meta_until <= window_start:
        log.info("Grace period beslaat heel venster.")
        return

    try:
        meta_leads = meta_client.get_leads(window_start, meta_until)
        log.info("Meta leads: %d", len(meta_leads))
    except Exception as exc:
        log.error("Meta API fout: %s", exc)
        return

    if not meta_leads:
        log.info("Geen Meta-leads in venster.")
        return

    # Haal workflow events op voor het volledige venster (inclusief grace)
    wf_events = state.get_workflow_events(window_start, now)
    wf_count = len({e.get("email") or e.get("phone") for e in wf_events if e.get("email") or e.get("phone")})
    log.info("Workflow events (uniek): %d", wf_count)

    # Primaire check: zit de lead in de ontvangen webhook events?
    missing = [l for l in meta_leads if not _is_in_events(l, wf_events)]

    if not missing:
        log.info("OK — alle %d leads in workflows.", len(meta_leads))
        return

    # Fallback: bestaat het contact al in GHL (bijv. oudere lead die opnieuw converteert)?
    truly_missing = []
    for lead in missing:
        if ghl_client.contact_exists(lead.get("email", ""), lead.get("phone", "")):
            log.info("Lead gevonden via directe GHL-lookup: %s", _label(lead))
        else:
            truly_missing.append(lead)

    if not truly_missing:
        log.info("OK — alle leads gevonden in GHL na directe verificatie.")
        return

    new_missing = state.filter_new_leads(truly_missing)
    if not new_missing:
        log.info("Mismatch bekende leads — al eerder gerapporteerd, overgeslagen.")
        return

    log.warning("MISMATCH: %d nieuwe leads niet in workflow (van %d totaal).", len(new_missing), len(meta_leads))
    try:
        ghl_alert.send_mismatch_sms(len(meta_leads), wf_count, new_missing, now)
        state.mark_leads_alerted(new_missing)
        state.record_mismatch(len(meta_leads), wf_count, new_missing)
    except Exception as exc:
        log.error("Mismatch SMS fout: %s", exc)
