import os
import re
from datetime import datetime
from typing import Any

import requests

GRAPH_API = "https://graph.facebook.com/v19.0"
PAGE_ID = "396715913528745"

_page_token_cache: str = ""


def _system_token() -> str:
    token = os.environ.get("META_ACCESS_TOKEN", "")
    if not token:
        raise ValueError("META_ACCESS_TOKEN niet ingesteld in .env")
    return token


def _page_token() -> str:
    global _page_token_cache
    if _page_token_cache:
        return _page_token_cache
    resp = requests.get(
        f"{GRAPH_API}/{PAGE_ID}",
        params={"access_token": _system_token(), "fields": "access_token"},
        timeout=15,
    )
    resp.raise_for_status()
    _page_token_cache = resp.json()["access_token"]
    return _page_token_cache


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "31" + digits[1:]
    return "+" + digits if digits else ""


def _get_forms() -> list[dict]:
    resp = requests.get(
        f"{GRAPH_API}/{PAGE_ID}/leadgen_forms",
        params={"access_token": _page_token(), "fields": "id,name", "limit": 100},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _extract_lead(field_data: list, form_name: str) -> dict:
    lead: dict = {"email": "", "phone": "", "form": form_name}
    for field in field_data:
        name = field.get("name", "").lower()
        values = field.get("values", [])
        value = values[0] if values else ""
        if name == "email":
            lead["email"] = value.lower().strip()
        elif name in ("phone_number", "phone", "telefoon", "telefoonnummer"):
            lead["phone"] = _normalize_phone(value)
    return lead


def get_leads(since: datetime, until: datetime) -> list[dict]:
    """Haalt individuele leads op (email + telefoon) uit alle forms."""
    forms = _get_forms()
    all_leads: list[dict] = []

    for form in forms:
        params: dict[str, Any] = {
            "access_token": _page_token(),
            "fields": "field_data,created_time",
            "filtering": (
                f'[{{"field":"time_created","operator":"GREATER_THAN","value":{int(since.timestamp())}}},'
                f'{{"field":"time_created","operator":"LESS_THAN","value":{int(until.timestamp())}}}]'
            ),
            "limit": 100,
        }
        url: str | None = f"{GRAPH_API}/{form['id']}/leads"

        while url:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for entry in data.get("data", []):
                lead = _extract_lead(entry.get("field_data", []), form["name"])
                all_leads.append(lead)
            url = data.get("paging", {}).get("next")
            params = {}

    return all_leads


def get_lead_count(since: datetime, until: datetime) -> int:
    """Telt leads via Ads Insights API als fallback (geen individuele data)."""
    ad_account_id = os.environ.get("META_AD_ACCOUNT_ID", "")
    params = {
        "access_token": _system_token(),
        "fields": "actions",
        "time_range": f'{{"since":"{since.strftime("%Y-%m-%d")}","until":"{until.strftime("%Y-%m-%d")}"}}',
        "level": "account",
        "limit": 1,
    }
    resp = requests.get(f"{GRAPH_API}/{ad_account_id}/insights", params=params, timeout=15)
    resp.raise_for_status()
    total = 0
    for record in resp.json().get("data", []):
        for action in record.get("actions", []):
            if action.get("action_type") == "lead":
                total += int(action.get("value", 0))
    return total
