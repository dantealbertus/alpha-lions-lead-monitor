import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import state

log = logging.getLogger(__name__)
AMS = ZoneInfo("Europe/Amsterdam")


def _label(lead: dict) -> str:
    return lead.get("email") or lead.get("phone") or "onbekend"


def _format_leads(leads: list, max_n: int = 10) -> str:
    lines = [f"• {_label(l)}" for l in leads[:max_n]]
    if len(leads) > max_n:
        lines.append(f"• ...en {len(leads) - max_n} meer")
    return "\n".join(lines)


def _ams_time(dt: datetime) -> str:
    return dt.astimezone(AMS).strftime("%H:%M")


def _send_sms(message: str) -> None:
    webhook_url = os.environ.get("GHL_WEBHOOK_URL", "").strip()
    if not webhook_url or webhook_url == "VUL_IN":
        raise ValueError("GHL_WEBHOOK_URL niet ingesteld in .env")
    resp = requests.post(webhook_url, json={"message": message}, timeout=15)
    resp.raise_for_status()
    log.info("Bericht verstuurd: %s", message[:60])


def send_mismatch_sms(meta_count: int, ghl_count: int, missing_leads: list, window_end: datetime) -> None:
    diff = meta_count - ghl_count
    message = (
        f"⚠️ *LEAD MISMATCH*\n"
        f"🕐 {_ams_time(window_end)}\n"
        f"\n"
        f"*Meta:* {meta_count} leads\n"
        f"*GHL WhatsApp Flow:* {ghl_count} leads\n"
        f"*Verschil:* {diff} lead(s) missen\n"
        f"\n"
        f"*Ontbrekend in GHL:*\n"
        f"{_format_leads(missing_leads)}"
    )
    _send_sms(message)


def send_spike_sms(counts_per_workflow: dict, total: int, contacts: list, window_end: datetime, window_minutes: int = 15) -> None:
    flow_parts = "\n".join(
        f"• Flow {i+1}: {v} leads"
        for i, (_, v) in enumerate(counts_per_workflow.items()) if v > 0
    )
    message = (
        f"⚡ *SPIKE GEDETECTEERD*\n"
        f"🕐 {_ams_time(window_end)}\n"
        f"\n"
        f"*{total} nieuwe leads in {window_minutes} minuten toegevoegd aan de WhatsApp flows*\n"
        f"\n"
        f"*Per workflow:*\n"
        f"{flow_parts}\n"
        f"\n"
        f"*Contacten:*\n"
        f"{_format_leads(contacts)}"
    )
    _send_sms(message)


def send_daily_summary() -> None:
    today = datetime.now(AMS).strftime("%d-%m-%Y")
    date_key = datetime.now(AMS).strftime("%Y-%m-%d")
    mismatches = state.get_mismatches_for_date(date_key)

    if not mismatches:
        _send_sms(
            f"✅ *DAGOVERZICHT {today}*\n"
            f"\n"
            f"Geen mismatches gevonden.\n"
            f"Alle leads zijn correct verwerkt!"
        )
        return

    all_missing = []
    for m in mismatches:
        all_missing.extend(m.get("contacts", []))

    seen = set()
    unique = []
    for l in all_missing:
        key = _label(l)
        if key not in seen:
            seen.add(key)
            unique.append(l)

    message = (
        f"📋 *DAGOVERZICHT {today}*\n"
        f"\n"
        f"*{len(mismatches)}* mismatch-alert(s)\n"
        f"*{len(unique)}* unieke ontbrekende leads\n"
        f"\n"
        f"*Leads niet in GHL:*\n"
        f"{_format_leads(unique, max_n=15)}"
    )
    _send_sms(message)
