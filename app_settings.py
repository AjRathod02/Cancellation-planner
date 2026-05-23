"""Runtime application settings (in-memory, adjustable via API)."""

import os
from datetime import date, timedelta


_auto_post: bool = os.getenv("AUTO_POST", "true").strip().lower() in ("1", "true", "yes")


def _default_plan_dates() -> list[str]:
  """Today through N-1 days ahead; N from PLAN_DAYS (default 2)."""
  raw = os.getenv("PLAN_DAYS", "2").strip()
  try:
    days = max(1, int(raw))
  except ValueError:
    days = 2
  today = date.today()
  return [(today + timedelta(days=i)).isoformat() for i in range(days)]


def _parse_plan_dates_env() -> list[str]:
  raw = os.getenv("PLAN_DATES", "").strip()
  if raw:
    dates = [d.strip() for d in raw.split(",") if d.strip()]
    if dates:
      return _normalize_plan_dates(dates)
  return _default_plan_dates()


def _normalize_plan_dates(dates: list[str]) -> list[str]:
  normalized: list[str] = []
  seen: set[str] = set()
  for value in dates:
    try:
      iso = date.fromisoformat(value).isoformat()
    except ValueError as exc:
      raise ValueError(f"Invalid date: {value}") from exc
    if iso not in seen:
      seen.add(iso)
      normalized.append(iso)
  if not normalized:
    raise ValueError("At least one plan date is required")
  if len(normalized) > 30:
    raise ValueError("At most 30 plan dates are allowed")
  return sorted(normalized)


_plan_dates: list[str] = _parse_plan_dates_env()


def get_auto_post() -> bool:
  return _auto_post


def set_auto_post(enabled: bool) -> None:
  global _auto_post
  _auto_post = enabled


def get_plan_dates() -> list[str]:
  return list(_plan_dates)


def set_plan_dates(dates: list[str]) -> None:
  global _plan_dates
  _plan_dates = _normalize_plan_dates(dates)


def get_settings_dict() -> dict:
  return {"auto_post": _auto_post, "plan_dates": _plan_dates}
