"""Local cache of recurring meals.

The point is to stop re-deriving macros for the same breakfast every morning.
"My usual" resolves here, offline, before anything hits the network.
"""

from __future__ import annotations

import json
from typing import Any

from . import config


def _read() -> dict[str, Any]:
    if not config.FOOD_CACHE_PATH.exists():
        return {}
    return json.loads(config.FOOD_CACHE_PATH.read_text())


def _write(entries: dict[str, Any]) -> None:
    config.ensure_config_dir()
    config.FOOD_CACHE_PATH.write_text(json.dumps(entries, indent=2, sort_keys=True))


def save(
    name: str,
    *,
    calories: float,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
    food_ref: str | None = None,
) -> None:
    entries = _read()
    entries[name.lower()] = {
        "display_name": name,
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "food_ref": food_ref,
    }
    _write(entries)


def get(name: str) -> dict[str, Any] | None:
    return _read().get(name.lower())


def all_entries() -> dict[str, Any]:
    return _read()


def remove(name: str) -> bool:
    entries = _read()
    if name.lower() not in entries:
        return False
    del entries[name.lower()]
    _write(entries)
    return True
