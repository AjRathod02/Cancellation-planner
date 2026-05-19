"""Fetch SharePoint inputs, run cancellation logic, deliver results via webhook."""

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from cancellation_processor import (
  OUTPUT_COLUMNS,
  build_summary_message,
  process_cancellation_plan,
)
from config import get_settings
from sharepoint import load_sharepoint_inputs

logger = logging.getLogger(__name__)

_WEBHOOK_TIMEOUT = 120
_WEBHOOK_RETRIES = 3


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


def run_scheduled_job() -> dict[str, Any]:
  """SharePoint -> process -> Power Automate (no local file output)."""
  logger.info("Starting cancellation plan job")
  inputs = load_sharepoint_inputs()

  result_df = process_cancellation_plan(
    inputs["inventory_df"],
    inputs["confirmed_df"],
    inputs["credit_df"],
    inputs["mm_is_df"],
  )

  summary = build_summary_message(result_df)
  payload = build_result_payload(result_df, summary)
  webhook = send_result_to_webhook(payload)

  logger.info("Job completed: %s", summary)
  return {
    "success": True,
    "summary": summary,
    "row_count": len(result_df),
    "webhook": webhook,
  }
