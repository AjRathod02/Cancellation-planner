"""Runtime application settings (in-memory, adjustable via API)."""

import os

_auto_post: bool = os.getenv("AUTO_POST", "true").strip().lower() in ("1", "true", "yes")


def get_auto_post() -> bool:
  return _auto_post


def set_auto_post(enabled: bool) -> None:
  global _auto_post
  _auto_post = enabled


def get_settings_dict() -> dict:
  return {"auto_post": _auto_post}
