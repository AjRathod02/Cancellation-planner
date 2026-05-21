"""
Scheduler server: SharePoint -> cancellation logic -> Power Automate webhook.
No local file output.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app_settings import get_settings_dict, set_auto_post
from config import get_settings, validate_settings
from job_runner import (
  get_last_job_result,
  post_draft_to_teams,
  run_scheduled_job,
  update_draft_rows,
)

load_dotenv()

_settings = get_settings()
logging.basicConfig(
  level=getattr(logging, _settings["log_level"], logging.INFO),
  format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scheduler_server")

scheduler = BackgroundScheduler(timezone=_settings["schedule_timezone"])

# Runtime schedule (overrides .env after POST /schedule)
_current_schedule: dict[str, str] = {
  "hour": _settings["schedule_hour"],
  "minute": _settings["schedule_minute"],
  "timezone": _settings["schedule_timezone"],
}


class ScheduleUpdate(BaseModel):
  hour: int = Field(ge=0, le=23, description="Hour in 24h format (0-23)")
  minute: int = Field(ge=0, le=59, description="Minute (0-59)")
  timezone: str | None = Field(
    default=None,
    description="IANA timezone e.g. America/Los_Angeles (optional)",
  )


class AppSettingsUpdate(BaseModel):
  auto_post: bool


class DraftRowsUpdate(BaseModel):
  rows: list[dict[str, Any]]


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
    "minute": _current_schedule["minute"],
    "hour": _current_schedule["hour"],
    "day": "*",
    "month": "*",
    "day_of_week": "*",
  }


def _build_trigger() -> CronTrigger:
  cron = _parse_cron()
  tz = _current_schedule["timezone"]
  return CronTrigger(
    minute=cron["minute"],
    hour=cron["hour"],
    day=cron["day"],
    month=cron["month"],
    day_of_week=cron["day_of_week"],
    timezone=tz,
  )


def _format_time_12h(hour: int, minute: int) -> str:
  period = "AM" if hour < 12 else "PM"
  hour_12 = hour % 12 or 12
  return f"{hour_12}:{minute:02d} {period}"


def _get_schedule_status() -> dict:
  job = scheduler.get_job("cancellation_plan")
  hour = int(_current_schedule["hour"])
  minute = int(_current_schedule["minute"])
  tz = _current_schedule["timezone"]
  next_run_dt = job.next_run_time if job else None

  return {
    "scheduled_time": {
      "hour": hour,
      "minute": minute,
      "time_24h": f"{hour:02d}:{minute:02d}",
      "time_12h": _format_time_12h(hour, minute),
      "timezone": tz,
    },
    "cron": f"{minute} {hour} * * *",
    "frequency": "daily",
    "scheduler_running": scheduler.running,
    "job_id": "cancellation_plan",
    "next_run": next_run_dt.isoformat() if next_run_dt else None,
    "next_run_utc": next_run_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if next_run_dt
    else None,
  }


def update_schedule(hour: int, minute: int, timezone: str | None = None) -> dict:
  """Reschedule the job at runtime."""
  if not scheduler.running:
    raise RuntimeError("Scheduler is not running")

  _current_schedule["hour"] = str(hour)
  _current_schedule["minute"] = str(minute)
  if timezone:
    _current_schedule["timezone"] = timezone

  trigger = _build_trigger()
  scheduler.reschedule_job("cancellation_plan", trigger=trigger)

  logger.info(
    "Schedule updated to %02d:%02d %s",
    hour,
    minute,
    _current_schedule["timezone"],
  )
  return _get_schedule_status()


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
  trigger = _build_trigger()
  scheduler.add_job(
    _scheduled_job_wrapper,
    trigger=trigger,
    id="cancellation_plan",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
  )
  scheduler.start()
  status = _get_schedule_status()
  logger.info(
    "Scheduler started (cron: %s, tz=%s, next_run=%s)",
    status["cron"],
    status["scheduled_time"]["timezone"],
    status["next_run"],
  )
  yield
  scheduler.shutdown(wait=False)


app = FastAPI(
  title="Cancellation Plan Scheduler",
  description="Runs cancellation plan from SharePoint and posts results to Power Automate.",
  lifespan=lifespan,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
  return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
  schedule = _get_schedule_status()
  last = get_last_job_result()
  return {
    "status": "ok",
    "scheduler_running": scheduler.running,
    "schedule": schedule,
    "settings": get_settings_dict(),
    "webhook_configured": bool(get_settings()["webhook_url"]),
    "sharepoint_configured": bool(get_settings()["sp_client_id"]),
    "last_result_posted": last.get("posted") if last else None,
  }


def _settings_get():
  return get_settings_dict()


def _settings_post(body: AppSettingsUpdate):
  set_auto_post(body.auto_post)
  logger.info("auto_post set to %s", body.auto_post)
  return get_settings_dict()


for _settings_path in ("/settings", "/settings/"):
  app.add_api_route(_settings_path, _settings_get, methods=["GET"], tags=["settings"])
  app.add_api_route(_settings_path, _settings_post, methods=["POST"], tags=["settings"])


def _schedule_get():
  """Return current scheduled time details and next run."""
  return _get_schedule_status()


def _schedule_post(body: ScheduleUpdate):
  """Update daily run time; returns updated schedule details."""
  try:
    return update_schedule(body.hour, body.minute, body.timezone)
  except Exception as exc:
    logger.exception("Failed to update schedule")
    raise HTTPException(status_code=400, detail=str(exc)) from exc


for _path in ("/schedule", "/schedule/"):
  app.add_api_route(
    _path,
    _schedule_get,
    methods=["GET"],
    dependencies=[Depends(_verify_run_api_key)],
    tags=["schedule"],
  )
  app.add_api_route(
    _path,
    _schedule_post,
    methods=["POST"],
    dependencies=[Depends(_verify_run_api_key)],
    tags=["schedule"],
  )


@app.get("/results/latest")
def latest_results():
  """Return rows from the most recent job run (manual or scheduled)."""
  result = get_last_job_result()
  if not result:
    return {
      "success": True,
      "summary": "No job has run yet.",
      "row_count": 0,
      "generated_at_utc": None,
      "rows": [],
      "posted": False,
      "auto_post": get_settings_dict()["auto_post"],
    }
  return result


@app.put("/results/draft", dependencies=[Depends(_verify_run_api_key)])
def save_draft(body: DraftRowsUpdate):
  """Save edited plan rows before posting (when auto_post is disabled)."""
  try:
    return update_draft_rows(body.rows)
  except RuntimeError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/results/post", dependencies=[Depends(_verify_run_api_key)])
def post_results_to_teams():
  """Post the current draft to Power Automate / Teams."""
  try:
    return post_draft_to_teams()
  except RuntimeError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


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
  uvicorn.run("app:app", host="0.0.0.0", port=get_settings()["port"], reload=False)
