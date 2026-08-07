"""Endpoints, scopes and on-disk locations.

Everything the Google Health API might rename lives here, so a spec change is a
one-file edit rather than a hunt through the client.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

API_ROOT = "https://health.googleapis.com/v4"

# Data type ids as they appear in the /dataTypes/{id}/dataPoints path.
NUTRITION_TYPE = "nutrition-log"
FOOD_TYPE = "food"
EXERCISE_TYPE = "exercise"

# Google split the health scopes into explicit read/write variants in 2026. The
# unsuffixed scope covers add/edit/delete *and* read; `.readonly` is read-only.
# We ask for the full scope on both families because we write to both.
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.nutrition",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
]

OAUTH_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"

CONFIG_DIR = Path(
    os.environ.get("HEALTHLOG_CONFIG_DIR", Path.home() / ".config" / "healthlog")
)
CLIENT_SECRET_PATH = CONFIG_DIR / "client_secret.json"
TOKEN_PATH = CONFIG_DIR / "token.json"
FOOD_CACHE_PATH = CONFIG_DIR / "foods.json"

# Logs are timestamped in your local wall clock, not the server's. Dubai by
# default since that is where the meals are actually being eaten.
DEFAULT_TZ = os.environ.get("HEALTHLOG_TZ", "Asia/Dubai")


def local_tz() -> ZoneInfo:
    return ZoneInfo(DEFAULT_TZ)


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    return CONFIG_DIR
