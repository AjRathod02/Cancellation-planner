"""
Scheduler server: SharePoint -> cancellation logic -> Power Automate webhook.
No local file output.
"""

import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from config import get_settings, validate_settings
from job_runner import run_scheduled_job

load_dotenv()

_settings = get_settings()
logging.basicConfig(
  level=getattr(logging, _settings["log_level"], logging.INFO),
  format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scheduler_server")

scheduler = BackgroundScheduler(timezone=_settings["schedule_timezone"])


def _parse_cron() -> dict:
  cron_expr = _settings["cron_schedule"]
  if cron_expr:
    parts = cron_expr.split()
    if len(parts) != 5:
      raise ValueError("CRON_SCHEDULE must have 5 fields: minute hour day month day_of_week")
    return {
      "minute": parts[0],
      "hour": parts[1],
      "day": parts[2],
      "month": parts[3],
      "day_of_week": parts[4],
    }
  return {
    "minute": _settings["schedule_minute"],
    "hour": _settings["schedule_hour"],
    "day": "*",
    "month": "*",
    "day_of_week": "*",
  }


def _scheduled_job_wrapper():
  try:
    run_scheduled_job()
  except Exception:
    logger.exception("Scheduled job failed")


def _verify_run_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
  expected = get_settings()["run_api_key"]
  if expected and x_api_key != expected:
    raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@asynccontextmanager
async def lifespan(app: FastAPI):
  validate_settings()
  cron = _parse_cron()
  trigger = CronTrigger(
    minute=cron["minute"],
    hour=cron["hour"],
    day=cron["day"],
    month=cron["month"],
    day_of_week=cron["day_of_week"],
    timezone=scheduler.timezone,
  )
  scheduler.add_job(
    _scheduled_job_wrapper,
    trigger=trigger,
    id="cancellation_plan",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
  )
  scheduler.start()
  logger.info(
    "Scheduler started (cron: %s %s * * *, tz=%s)",
    cron["minute"],
    cron["hour"],
    scheduler.timezone,
  )
  yield
  scheduler.shutdown(wait=False)


app = FastAPI(
  title="Cancellation Plan Scheduler",
  description="Runs cancellation plan from SharePoint and posts results to Power Automate.",
  lifespan=lifespan,
)


@app.get("/health")
def health():
  job = scheduler.get_job("cancellation_plan")
  next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
  return {
    "status": "ok",
    "scheduler_running": scheduler.running,
    "next_run": next_run,
    "timezone": str(scheduler.timezone),
    "webhook_configured": bool(get_settings()["webhook_url"]),
    "sharepoint_configured": bool(get_settings()["sp_client_id"]),
  }


@app.api_route("/run", methods=["GET", "POST"], dependencies=[Depends(_verify_run_api_key)])
def run_now():
  """Manually trigger the job (same as scheduled run)."""
  try:
    result = run_scheduled_job()
    return JSONResponse(result)
  except Exception as exc:
    logger.exception("Manual run failed")
    raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
  import uvicorn

  validate_settings()
  uvicorn.run("scheduler_server:app", host="0.0.0.0", port=get_settings()["port"], reload=False)
