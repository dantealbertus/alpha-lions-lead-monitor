import os
import re
from datetime import datetime
from typing import Optional

import requests

GHL_API = "https://rest.gohighlevel.com/v1"


def _headers() -> dict:
    key = os.environ.get("GHL_API_KEY", "")
    if not key:
        raise ValueError("GHL_API_KEY niet ingesteld in .env")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "31" + digits[1:]
    return "+" + digits if digits else ""


def _parse_date_ms(value: object) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            from datetime import timezone as tz
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except ValueError:
            pass
    return 0


def get_workflow_contacts(
    workflow_id: str,
    location_id: str,
    since: datetime,
    until: datetime,
) -> list[dict[str, str]]:
    """
    Haalt contacten op in het workflow en filtert client-side op tijdvenster.
    GHL v1 API ondersteunt geen paginering gecombineerd met workflowId filter,
    maar binnen een 30-min venster zijn nooit meer dan 100 leads.
    """
    since_ms = int(since.timestamp() * 1000)
    until_ms = int(until.timestamp() * 1000)

    resp = requests.get(
        f"{GHL_API}/contacts/",
        headers=_headers(),
        params={"locationId": location_id, "workflowId": workflow_id, "limit": 100},
        timeout=15,
    )
    # GHL geeft 404 terug als een workflow 0 contacten heeft — behandel als leeg
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    contacts = []

    for c in resp.json().get("contacts", []):
        created_ms = _parse_date_ms(c.get("dateAdded", 0))
        if since_ms <= created_ms <= until_ms:
            contacts.append({
                "email": (c.get("email") or "").lower().strip(),
                "phone": _normalize_phone(c.get("phone") or ""),
            })

    return contacts


def get_all_workflow_contacts(
    workflow_ids: list[str],
    since: datetime,
    until: datetime,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """
    Geeft alle contacten over alle workflows + count per workflow.
    Returns: (contacts_list, counts_per_workflow)
    """
    location_id = os.environ.get("GHL_LOCATION_ID", "")
    if not location_id:
        raise ValueError("GHL_LOCATION_ID niet ingesteld in .env")

    all_contacts: list[dict[str, str]] = []
    counts: dict[str, int] = {}

    for wf_id in workflow_ids:
        wf_id = wf_id.strip()
        c = get_workflow_contacts(wf_id, location_id, since, until)
        counts[wf_id[:8]] = len(c)
        all_contacts.extend(c)

    return all_contacts, counts


def get_contact_id_by_phone(phone: str) -> Optional[str]:
    """Zoekt een GHL contact op telefoonnummer via de search-endpoint."""
    location_id = os.environ.get("GHL_LOCATION_ID", "")
    # GHL v1 zoekt op telefoonnummer via /search met query
    digits = re.sub(r"\D", "", phone)
    resp = requests.get(
        f"{GHL_API}/contacts/search",
        headers=_headers(),
        params={"locationId": location_id, "query": digits, "limit": 5},
        timeout=15,
    )
    if resp.status_code == 404:
        # Fallback: zoek op alle contacten en match handmatig
        resp2 = requests.get(
            f"{GHL_API}/contacts/",
            headers=_headers(),
            params={"locationId": location_id, "limit": 100},
            timeout=15,
        )
        resp2.raise_for_status()
        for c in resp2.json().get("contacts", []):
            c_phone = re.sub(r"\D", "", c.get("phone") or "")
            if c_phone.endswith(digits[-9:]):
                return c["id"]
        return None
    resp.raise_for_status()
    contacts = resp.json().get("contacts", [])
    for c in contacts:
        c_phone = re.sub(r"\D", "", c.get("phone") or "")
        if c_phone.endswith(digits[-9:]):
            return c["id"]
    return None
