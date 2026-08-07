"""DataPoint construction for the Google Health API v4.

Deliberately kept free of I/O so the request shapes can be unit-tested without
credentials, and so a spec correction is a small edit in one place.

SHAPE CONFIDENCE
----------------
These bodies follow Google's published nutrition/exercise data-type docs, but
they have not been executed against a live account in this repo (no token
here). `healthlog probe` exercises every one of them end-to-end and reports
exactly which field the server rejects. Treat the first probe run as part of
setup, not as an afterthought.

Nutrition supports two modes, per the docs:

  identified - `food` references an existing Food resource; the server fills in
               nutrients, energy and macros from the catalogue entry.
  anonymous  - `foodDisplayName` plus macros supplied by us. Note the documented
               catch: anonymous logs CANNOT be updated after creation. Amending
               one means delete + recreate, which `amend_strategy` encodes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

MEAL_TYPES = ["BREAKFAST", "LUNCH", "DINNER", "SNACK"]

# Exercise types the API recognises. Not exhaustive - the server owns the real
# enum - but these cover ordinary logging and give the CLI something to validate
# against so typos fail locally instead of after a round trip.
EXERCISE_TYPES = [
    "WALKING", "RUNNING", "BIKING", "SWIMMING", "HIKING", "ELLIPTICAL",
    "ROWING", "STAIR_CLIMBING", "STRENGTH_TRAINING", "WEIGHTLIFTING",
    "YOGA", "PILATES", "HIIT", "CIRCUIT_TRAINING", "TENNIS", "PADEL",
    "BASKETBALL", "FOOTBALL", "CRICKET", "BOXING", "MARTIAL_ARTS",
    "DANCING", "SKIING", "SNOWBOARDING", "GOLF", "WORKOUT",
]

# Nutrient identifiers used in the `nutrients` array. Energy and the two headline
# macros also appear as dedicated top-level fields per the docs; protein and the
# rest ride in the array.
NUTRIENT_KEYS = {
    "protein": "PROTEIN",
    "fiber": "DIETARY_FIBER",
    "sugar": "SUGARS",
    "sodium": "SODIUM",
    "saturated_fat": "SATURATED_FAT",
    "cholesterol": "CHOLESTEROL",
}


def _rfc3339(moment: datetime) -> str:
    """RFC-3339 with a real offset. Naive datetimes are rejected outright."""
    if moment.tzinfo is None:
        raise ValueError(
            "Refusing to send a naive datetime - an ambiguous timestamp lands "
            "the meal on the wrong day. Attach a tzinfo first."
        )
    return moment.isoformat()


def _interval(start: datetime, end: datetime | None = None) -> dict[str, str]:
    # A meal is a point in time; the API models everything as an interval, so a
    # zero-length one is the honest representation.
    return {"startTime": _rfc3339(start), "endTime": _rfc3339(end or start)}


def nutrition_datapoint(
    *,
    start: datetime,
    meal_type: str,
    display_name: str | None = None,
    food_ref: str | None = None,
    calories: float | None = None,
    protein_g: float | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    servings: float = 1.0,
    extra_nutrients: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build a nutrition-log DataPoint.

    Pass `food_ref` for an identified food, or `display_name` + macros for an
    anonymous one. Supplying neither is a programming error.
    """
    meal_type = meal_type.upper()
    if meal_type not in MEAL_TYPES:
        raise ValueError(f"meal_type must be one of {MEAL_TYPES}, got {meal_type!r}")
    if not food_ref and not display_name:
        raise ValueError("Need either food_ref (identified) or display_name (anonymous)")

    log: dict[str, Any] = {
        "interval": _interval(start),
        "mealType": meal_type,
        "serving": {"amount": float(servings)},
    }

    if food_ref:
        # Identified food: the server derives macros, so sending our own
        # estimates would only fight the catalogue.
        log["food"] = food_ref
        return {"nutritionLog": log}

    log["foodDisplayName"] = display_name
    if calories is not None:
        log["energy"] = {"calories": float(calories)}
    if carbs_g is not None:
        log["totalCarbohydrate"] = {"grams": float(carbs_g)}
    if fat_g is not None:
        log["totalFat"] = {"grams": float(fat_g)}

    nutrients = []
    if protein_g is not None:
        nutrients.append({"nutrient": NUTRIENT_KEYS["protein"],
                          "quantity": {"grams": float(protein_g)}})
    for name, grams in (extra_nutrients or {}).items():
        key = NUTRIENT_KEYS.get(name)
        if key is None:
            raise ValueError(
                f"Unknown nutrient {name!r}; known: {sorted(NUTRIENT_KEYS)}"
            )
        nutrients.append({"nutrient": key, "quantity": {"grams": float(grams)}})
    if nutrients:
        log["nutrients"] = nutrients

    return {"nutritionLog": log}


def amend_strategy(datapoint: dict[str, Any]) -> str:
    """Whether a logged meal can be patched, or must be replaced.

    Anonymous nutrition logs are immutable once written, so the CLI deletes and
    recreates instead of issuing a doomed PATCH.
    """
    log = datapoint.get("nutritionLog", {})
    return "patch" if log.get("food") else "delete_and_recreate"


def exercise_datapoint(
    *,
    start: datetime,
    duration_minutes: float,
    exercise_type: str,
    display_name: str | None = None,
    calories: float | None = None,
    distance_km: float | None = None,
    steps: int | None = None,
) -> dict[str, Any]:
    """Build an exercise DataPoint."""
    exercise_type = exercise_type.upper()
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive")

    end = start + timedelta(minutes=duration_minutes)
    session: dict[str, Any] = {
        "exerciseType": exercise_type,
        "interval": _interval(start, end),
        # Durations go over the wire as protobuf-style second strings.
        "activeDuration": f"{int(duration_minutes * 60)}s",
    }
    if display_name:
        session["displayName"] = display_name

    metrics: dict[str, Any] = {}
    if calories is not None:
        metrics["caloriesKcal"] = float(calories)
    if distance_km is not None:
        metrics["distanceMillimeters"] = int(round(distance_km * 1_000_000))
    if steps is not None:
        metrics["steps"] = int(steps)
    if metrics:
        session["metricsSummary"] = metrics

    return {"exercise": session}
