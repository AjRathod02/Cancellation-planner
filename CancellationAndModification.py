"""
Cancellation plan CLI — SharePoint inputs, webhook delivery only (no local files).
"""

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main() -> int:
  from config import validate_settings
  from job_runner import run_scheduled_job

  try:
    validate_settings()
    result = run_scheduled_job()
    print(result["summary"])
    print(f"Posted {result['row_count']} row(s) to Power Automate (HTTP {result['webhook']['status_code']})")
    return 0
  except Exception as exc:
    print(f"Error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
