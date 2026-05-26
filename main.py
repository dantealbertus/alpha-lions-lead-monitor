import logging
import os
import signal
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, request

import state
from ghl_alert import send_daily_summary
from monitor import run_check
from utils import normalize_phone

load_dotenv()
state.init_db()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
scheduler = BackgroundScheduler(timezone="UTC")

scheduler.add_job(run_check, "interval", minutes=5, id="lead_monitor")
scheduler.add_job(
    send_daily_summary,
    "cron",
    hour=8,
    minute=0,
    timezone="Europe/Amsterdam",
    id="daily_summary",
)


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/webhook/workflow", methods=["POST"])
def workflow_webhook():
    secret = request.args.get("secret", "")
    expected = os.environ.get("WEBHOOK_SECRET", "")
    if expected and secret != expected:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True) or {}
    workflow_id = data.get("workflowId", "unknown")
    email = (data.get("email") or "").lower().strip()
    phone = normalize_phone(data.get("phone") or "")

    if not email and not phone:
        return jsonify({"error": "email of phone vereist"}), 400

    state.record_workflow_event(workflow_id, email, phone)
    log.info("Webhook ontvangen | workflow=%s contact=%s", workflow_id, email or phone)
    return jsonify({"ok": True})


def _shutdown(sig, frame):
    log.info("Stoppen...")
    scheduler.shutdown(wait=False)
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

if __name__ == "__main__":
    log.info("Lead Monitor gestart — check elke 5 min, dagelijks overzicht 08:00 NL.")
    scheduler.start()
    run_check()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
