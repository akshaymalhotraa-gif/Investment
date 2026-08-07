"""Payload shape tests - no credentials, no network.

These lock in the request bodies so a refactor cannot silently change what gets
sent to Google. They cannot prove the server accepts these shapes; that is what
`healthlog probe` is for.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from healthlog import payloads

DUBAI = ZoneInfo("Asia/Dubai")
NOON = datetime(2026, 8, 7, 12, 30, tzinfo=DUBAI)


def test_anonymous_food_carries_macros_and_offset():
    point = payloads.nutrition_datapoint(
        start=NOON,
        meal_type="lunch",
        display_name="chicken shawarma",
        calories=620,
        protein_g=38,
        carbs_g=55,
        fat_g=26,
    )
    log = point["nutritionLog"]

    assert log["mealType"] == "LUNCH"
    assert log["foodDisplayName"] == "chicken shawarma"
    assert log["energy"] == {"calories": 620.0}
    assert log["totalCarbohydrate"] == {"grams": 55.0}
    assert log["totalFat"] == {"grams": 26.0}
    assert {"nutrient": "PROTEIN", "quantity": {"grams": 38.0}} in log["nutrients"]
    # +04:00 must survive, or the meal lands on the wrong Dubai day.
    assert log["interval"]["startTime"].endswith("+04:00")


def test_identified_food_defers_macros_to_the_catalogue():
    point = payloads.nutrition_datapoint(
        start=NOON,
        meal_type="BREAKFAST",
        food_ref="users/me/dataTypes/food/dataPoints/abc123",
        calories=999,  # should be ignored in favour of the referenced food
    )
    log = point["nutritionLog"]

    assert log["food"] == "users/me/dataTypes/food/dataPoints/abc123"
    assert "energy" not in log
    assert "foodDisplayName" not in log


def test_amend_strategy_reflects_anonymous_immutability():
    anon = payloads.nutrition_datapoint(
        start=NOON, meal_type="SNACK", display_name="dates", calories=90
    )
    identified = payloads.nutrition_datapoint(
        start=NOON, meal_type="SNACK", food_ref="users/me/dataTypes/food/dataPoints/x"
    )
    assert payloads.amend_strategy(anon) == "delete_and_recreate"
    assert payloads.amend_strategy(identified) == "patch"


def test_naive_datetime_is_refused():
    with pytest.raises(ValueError, match="naive datetime"):
        payloads.nutrition_datapoint(
            start=datetime(2026, 8, 7, 12, 30),
            meal_type="LUNCH",
            display_name="anything",
        )


def test_food_needs_a_name_or_a_reference():
    with pytest.raises(ValueError, match="food_ref"):
        payloads.nutrition_datapoint(start=NOON, meal_type="LUNCH")


def test_bad_meal_type_rejected_locally():
    with pytest.raises(ValueError, match="meal_type"):
        payloads.nutrition_datapoint(
            start=NOON, meal_type="BRUNCH", display_name="eggs"
        )


def test_exercise_duration_drives_end_time_and_active_duration():
    point = payloads.exercise_datapoint(
        start=NOON,
        duration_minutes=45,
        exercise_type="running",
        display_name="Marina loop",
        calories=480,
        distance_km=7.2,
        steps=8400,
    )
    session = point["exercise"]

    assert session["exerciseType"] == "RUNNING"
    assert session["activeDuration"] == "2700s"
    assert session["interval"]["endTime"] == (NOON + timedelta(minutes=45)).isoformat()
    assert session["metricsSummary"]["caloriesKcal"] == 480.0
    # 7.2 km expressed in millimetres, integer, no float drift.
    assert session["metricsSummary"]["distanceMillimeters"] == 7_200_000
    assert session["metricsSummary"]["steps"] == 8400


def test_exercise_omits_metrics_block_when_nothing_measured():
    point = payloads.exercise_datapoint(
        start=NOON, duration_minutes=30, exercise_type="YOGA"
    )
    assert "metricsSummary" not in point["exercise"]


def test_exercise_rejects_zero_duration():
    with pytest.raises(ValueError, match="positive"):
        payloads.exercise_datapoint(
            start=NOON, duration_minutes=0, exercise_type="WALKING"
        )


def test_unknown_nutrient_fails_loudly():
    with pytest.raises(ValueError, match="Unknown nutrient"):
        payloads.nutrition_datapoint(
            start=NOON,
            meal_type="SNACK",
            display_name="mystery",
            extra_nutrients={"vitamin_q": 5},
        )
