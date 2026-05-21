import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

import state
from ghl_alert import send_daily_summary
from monitor import run_check

load_dotenv()
state.init_db()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

scheduler = BlockingScheduler(timezone="UTC")

# Elke 5 minuten: mismatch + spike check
scheduler.add_job(run_check, "interval", minutes=5, id="lead_monitor")

# Elke dag 08:00 NL: dagelijks SMS-overzicht
scheduler.add_job(
    send_daily_summary,
    "cron",
    hour=6,       # 08:00 Amsterdam = 06:00 UTC (zomer) / 07:00 UTC (winter)
    minute=0,
    timezone="Europe/Amsterdam",
    id="daily_summary",
)


def _shutdown(sig, frame):
    log.info("Stoppen...")
    scheduler.shutdown(wait=False)
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

if __name__ == "__main__":
    log.info("Lead Monitor gestart — check elke 5 min, dagelijks overzicht 08:00 NL.")
    run_check()  # directe eerste check
    scheduler.start()
