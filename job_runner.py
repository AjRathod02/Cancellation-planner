"""Fetch SharePoint inputs, run cancellation logic, deliver results via webhook."""

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app_settings import get_auto_post, get_plan_dates
from cancellation_processor import (
  OUTPUT_COLUMNS,
  build_summary_message,
  process_cancellation_plan,
)
from config import get_settings
from sharepoint import load_sharepoint_inputs

logger = logging.getLogger(__name__)

_last_job_result: dict[str, Any] | None = None

_WEBHOOK_TIMEOUT = 120
_WEBHOOK_RETRIES = 3
_AUTO_POST_EXCLUDE_ACTION = "Perfect date:"


def _serialize_cell(value: Any) -> Any:
  if pd.isna(value):
    return ""
  if isinstance(value, pd.Timestamp):
    return value.strftime("%Y-%m-%d")
  if hasattr(value, "isoformat"):
    try:
      return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
      return str(value)
  if isinstance(value, (int, float, bool)):
    return value
  return str(value)


def build_result_payload(df: pd.DataFrame, summary: str) -> dict[str, Any]:
  rows = [
    {col: _serialize_cell(row.get(col)) for col in OUTPUT_COLUMNS}
    for _, row in df.iterrows()
  ]
  return {
    "summary": summary,
    "row_count": len(df),
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "rows": rows,
  }


def _payload_for_webhook(result: dict[str, Any]) -> dict[str, Any]:
  return {
    "summary": result["summary"],
    "row_count": result["row_count"],
    "generated_at_utc": result["generated_at_utc"],
    "rows": result["rows"],
  }


def _is_perfect_date_row(row: dict[str, Any]) -> bool:
  return _AUTO_POST_EXCLUDE_ACTION in str(row.get("action", ""))


def rows_for_auto_post(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
  """Rows to include on auto-post; excludes actions containing 'Perfect date:'."""
  filtered = [r for r in rows if not _is_perfect_date_row(r)]
  return filtered, len(rows) - len(filtered)


def build_auto_post_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
  """Build webhook payload for auto-post, or None if no rows remain after filtering."""
  post_rows, excluded = rows_for_auto_post(payload["rows"])
  if not post_rows:
    return None

  summary = payload["summary"]
  if excluded:
    summary = (
      f"{summary} ({len(post_rows)} posted, "
      f"{excluded} excluded with Perfect date)"
    )

  return {
    "summary": summary,
    "row_count": len(post_rows),
    "generated_at_utc": payload["generated_at_utc"],
    "rows": post_rows,
  }


def _webhook_session() -> requests.Session:
  session = requests.Session()
  retry = Retry(
    total=_WEBHOOK_RETRIES,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=["POST"],
  )
  session.mount("https://", HTTPAdapter(max_retries=retry))
  return session


def send_result_to_webhook(payload: dict[str, Any]) -> dict[str, Any]:
  webhook_url = get_settings()["webhook_url"]
  session = _webhook_session()
  response = session.post(webhook_url, json=payload, timeout=_WEBHOOK_TIMEOUT)

  if not response.ok:
    logger.error("Webhook failed %s: %s", response.status_code, response.text[:500])
    response.raise_for_status()

  logger.info("Posted %s row(s) to Power Automate (HTTP %s)", payload["row_count"], response.status_code)
  return {"status_code": response.status_code}


def _store_job_result(
  payload: dict[str, Any],
  summary: str,
  posted: bool,
  webhook: dict[str, Any] | None = None,
  *,
  excluded_perfect_date: int = 0,
  posted_row_count: int | None = None,
) -> dict[str, Any]:
  global _last_job_result
  _last_job_result = {
    "success": True,
    "summary": summary,
    "row_count": payload["row_count"],
    "generated_at_utc": payload["generated_at_utc"],
    "rows": payload["rows"],
    "posted": posted,
    "auto_post": get_auto_post(),
    "webhook": webhook,
    "excluded_perfect_date": excluded_perfect_date,
    "posted_row_count": posted_row_count if posted_row_count is not None else (payload["row_count"] if posted else 0),
  }
  return _last_job_result


def run_scheduled_job() -> dict[str, Any]:
  """SharePoint -> process -> optional auto-post to Power Automate."""
  logger.info("Starting cancellation plan job (auto_post=%s)", get_auto_post())
  inputs = load_sharepoint_inputs()

  plan_dates = get_plan_dates()

  result_df = process_cancellation_plan(
    inputs["inventory_df"],
    inputs["confirmed_df"],
    inputs["credit_df"],
    inputs["mm_is_df"],
    plan_dates=plan_dates,
  )

  summary = build_summary_message(result_df, plan_dates=plan_dates)
  payload = build_result_payload(result_df, summary)

  posted = False
  webhook = None
  excluded = 0
  posted_row_count = 0

  if get_auto_post():
    post_payload = build_auto_post_payload(payload)
    _, excluded = rows_for_auto_post(payload["rows"])
    if post_payload:
      webhook = send_result_to_webhook(post_payload)
      posted = True
      posted_row_count = post_payload["row_count"]
      logger.info(
        "Job completed and posted %s row(s) (%s excluded Perfect date): %s",
        posted_row_count,
        excluded,
        post_payload["summary"],
      )
    else:
      logger.info(
        "Auto-post skipped: all %s row(s) excluded (Perfect date in action)",
        payload["row_count"],
      )
  else:
    logger.info("Job completed (draft, not posted): %s", summary)

  display_summary = summary
  if get_auto_post() and excluded:
    display_summary = (
      f"{summary} ({posted_row_count} posted, "
      f"{excluded} excluded: Perfect date)"
    )

  return _store_job_result(
    payload,
    display_summary,
    posted=posted,
    webhook=webhook,
    excluded_perfect_date=excluded,
    posted_row_count=posted_row_count,
  )


def update_draft_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
  """Save edited rows before posting to Teams."""
  global _last_job_result
  if _last_job_result is None:
    raise RuntimeError("No plan to edit. Run the job first.")

  if _last_job_result.get("posted"):
    raise RuntimeError("Plan was already posted and cannot be edited.")

  normalized = []
  for row in rows:
    normalized.append({col: _serialize_cell(row.get(col, "")) for col in OUTPUT_COLUMNS})

  df = pd.DataFrame(normalized, columns=OUTPUT_COLUMNS) if normalized else pd.DataFrame(columns=OUTPUT_COLUMNS)
  summary = build_summary_message(df, plan_dates=get_plan_dates())

  payload = {
    "summary": summary,
    "row_count": len(normalized),
    "generated_at_utc": _last_job_result.get("generated_at_utc")
    or datetime.now(timezone.utc).isoformat(),
    "rows": normalized,
  }

  return _store_job_result(
    payload,
    summary,
    posted=False,
    webhook=None,
  )


def post_draft_to_teams() -> dict[str, Any]:
  """Post the current draft to Power Automate / Teams thread."""
  global _last_job_result
  if _last_job_result is None:
    raise RuntimeError("No plan to post. Run the job first.")

  if _last_job_result.get("posted"):
    raise RuntimeError("Plan was already posted.")

  webhook = send_result_to_webhook(_payload_for_webhook(_last_job_result))
  _last_job_result["posted"] = True
  _last_job_result["webhook"] = webhook
  logger.info("Draft posted to Teams: %s", _last_job_result["summary"])
  return _last_job_result


def get_last_job_result() -> dict[str, Any] | None:
  return _last_job_result
