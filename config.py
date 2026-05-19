"""Environment configuration and startup validation."""

import os
import sys
from functools import lru_cache


def _get(name: str, default: str = "") -> str:
  return os.getenv(name, default).strip()


@lru_cache
def get_settings() -> dict:
  tenant_id = _get("SP_TENANT_ID")
  return {
    "webhook_url": _get("WEBHOOK_URL") or _get("POWER_AUTOMATE_WEBHOOK_URL"),
    "sp_client_id": _get("SP_CLIENT_ID"),
    "sp_client_secret": _get("SP_CLIENT_SECRET"),
    "sp_tenant_id": tenant_id,
    "sp_authority": _get("SP_AUTHORITY") or (
      f"https://login.microsoftonline.com/{tenant_id}" if tenant_id else ""
    ),
    "sp_site_id": _get("SP_SITE_ID"),
    "schedule_timezone": _get("SCHEDULE_TIMEZONE", "America/Los_Angeles"),
    "schedule_hour": _get("SCHEDULE_HOUR", "22"),
    "schedule_minute": _get("SCHEDULE_MINUTE", "50"),
    "cron_schedule": _get("CRON_SCHEDULE"),
    "port": int(_get("PORT", "8000")),
    "run_api_key": _get("RUN_API_KEY"),
    "log_level": _get("LOG_LEVEL", "INFO").upper(),
  }


def validate_settings() -> None:
  """Fail fast when required production settings are missing."""
  settings = get_settings()
  missing = []

  if not settings["webhook_url"]:
    missing.append("WEBHOOK_URL")
  if not settings["sp_client_id"]:
    missing.append("SP_CLIENT_ID")
  if not settings["sp_client_secret"]:
    missing.append("SP_CLIENT_SECRET")
  if not settings["sp_tenant_id"]:
    missing.append("SP_TENANT_ID")
  if not settings["sp_site_id"]:
    missing.append("SP_SITE_ID")

  if missing:
    raise RuntimeError(
      "Missing required environment variables: "
      + ", ".join(missing)
      + ". Copy .env.example to .env and configure values."
    )
