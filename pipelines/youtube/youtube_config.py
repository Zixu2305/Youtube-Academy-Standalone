import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_args, **_kwargs):
        return False


REQUEST_TIMEOUT_SECONDS = 30
MAX_ERROR_DETAILS = 20
RECENT_ROWS_LIMIT = 10
DEFAULT_DAILY_QUOTA_LIMIT = 10000
DEFAULT_QUOTA_WARNING_THRESHOLD = 8000


load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
