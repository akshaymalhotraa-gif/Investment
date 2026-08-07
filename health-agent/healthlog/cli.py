"""Command line entry point.

    healthlog auth login
    healthlog probe
    healthlog food log --name "chicken shawarma" --kcal 620 --protein 38 --carbs 55 --fat 26
    healthlog exercise log --type RUNNING --minutes 45 --kcal 480 --km 7.2
    healthlog show --days 1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from . import auth, config, foods, payloads
from .client import ApiError, HealthClient


def _parse_when(value: str | None) -> datetime:
    """Accept 'now', an ISO timestamp, or 'HH:MM' meaning today at that time."""
    tz = config.local_tz()
    if value in (None, "now"):
        return datetime.now(tz)
    try:
        if len(value) == 5 and ":" in value:
            hour, minute = (int(part) for part in value.split(":"))
            return datetime.now(tz).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
    except ValueError:
        raise SystemExit(
            f"Could not read --at {value!r}. Use 'now', '08:30', or "
            "'2026-08-07T08:30'."
        )


def _emit(result: dict, as_json: bool, human: str) -> None:
    print(json.dumps(result, indent=2) if as_json else human)


# ---- commands -----------------------------------------------------------


def cmd_auth(args: argparse.Namespace) -> int:
    if args.auth_cmd == "login":
        if args.manual:
            print("Open this URL, approve, then run:")
            print("  healthlog auth complete <code>\n")
            print(auth.login_manual_url())
        else:
            auth.login_loopback(open_browser=not args.no_browser)
    elif args.auth_cmd == "complete":
        auth.login_manual_complete(args.code)
    elif args.auth_cmd == "status":
        print(json.dumps(auth.status(), indent=2))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Verify what this account can actually read and write.

    Nutrition write support is the open question in this whole design, so the
    probe writes a real (tiny, clearly-labelled) entry and deletes it again.
    """
    client = HealthClient(dry_run=False)
    tz = config.local_tz()
    now = datetime.now(tz)
    results: list[tuple[str, str, str]] = []

    def check(label: str, fn) -> None:
        try:
            fn()
            results.append((label, "OK", ""))
        except ApiError as exc:
            detail = exc.body.strip().replace("\n", " ")[:220]
            results.append((label, f"FAIL {exc.status}", detail))
        except Exception as exc:  # noqa: BLE001 - probe reports, never raises
            results.append((label, "ERROR", str(exc)[:220]))

    check("read exercise", lambda: client.list(config.EXERCISE_TYPE, page_size=1))
    check("read nutrition-log", lambda: client.list(config.NUTRITION_TYPE, page_size=1))
    check("read food catalogue", lambda: client.list(config.FOOD_TYPE, page_size=1))

    created: dict[str, str] = {}

    def write_nutrition() -> None:
        point = payloads.nutrition_datapoint(
            start=now,
            meal_type="SNACK",
            display_name="healthlog probe - safe to delete",
            calories=1,
            protein_g=0,
            carbs_g=0,
            fat_g=0,
        )
        resp = client.create(config.NUTRITION_TYPE, point)
        if isinstance(resp, dict) and resp.get("name"):
            created["nutrition"] = resp["name"]

    def write_exercise() -> None:
        point = payloads.exercise_datapoint(
            start=now - timedelta(minutes=1),
            duration_minutes=1,
            exercise_type="WALKING",
            display_name="healthlog probe - safe to delete",
            calories=1,
        )
        resp = client.create(config.EXERCISE_TYPE, point)
        if isinstance(resp, dict) and resp.get("name"):
            created["exercise"] = resp["name"]

    check("WRITE nutrition-log", write_nutrition)
    check("WRITE exercise", write_exercise)

    width = max(len(label) for label, _, _ in results)
    print("\nGoogle Health API capability probe")
    print("=" * (width + 34))
    for label, verdict, detail in results:
        print(f"  {label.ljust(width)}  {verdict}")
        if detail:
            print(f"  {' ' * width}  -> {detail}")

    if created:
        print(
            "\nProbe wrote test entries. They are labelled 'healthlog probe' and "
            "are visible in your Fitbit app - delete them there, or with:"
        )
        for kind, name in created.items():
            print(f"  healthlog delete --type {kind} --id {name.rsplit('/', 1)[-1]}")

    failed_writes = [r for r in results if r[0].startswith("WRITE") and r[1] != "OK"]
    if failed_writes:
        print(
            "\nA write failed. If it was nutrition-log, that confirms the gap "
            "flagged in the README: exercise logging still works, food logging "
            "needs the fallback path. Send me the error text above."
        )
        return 1
    print("\nBoth write paths work. The agent is fully operational.")
    return 0


def cmd_food(args: argparse.Namespace) -> int:
    client = HealthClient(dry_run=args.dry_run)

    if args.food_cmd == "save":
        foods.save(
            args.name,
            calories=args.kcal,
            protein_g=args.protein,
            carbs_g=args.carbs,
            fat_g=args.fat,
        )
        print(f"Cached '{args.name}'. Log it later with: healthlog food log --saved \"{args.name}\"")
        return 0

    if args.food_cmd == "list-saved":
        entries = foods.all_entries()
        if not entries:
            print("No saved foods yet.")
            return 0
        for key, item in sorted(entries.items()):
            print(
                f"  {item['display_name']:<32} {item['calories']:>6.0f} kcal  "
                f"P{item['protein_g']:.0f} C{item['carbs_g']:.0f} F{item['fat_g']:.0f}"
            )
        return 0

    if args.food_cmd == "forget":
        print("Removed." if foods.remove(args.name) else f"No saved food named {args.name!r}.")
        return 0

    # food log
    if args.saved:
        entry = foods.get(args.saved)
        if not entry:
            print(f"No saved food named {args.saved!r}.", file=sys.stderr)
            return 1
        name, kcal = entry["display_name"], entry["calories"]
        protein, carbs, fat = entry["protein_g"], entry["carbs_g"], entry["fat_g"]
        food_ref = entry.get("food_ref")
    else:
        if args.name is None:
            print("Need --name or --saved.", file=sys.stderr)
            return 1
        name, kcal = args.name, args.kcal
        protein, carbs, fat = args.protein, args.carbs, args.fat
        food_ref = args.food_ref

    point = payloads.nutrition_datapoint(
        start=_parse_when(args.at),
        meal_type=args.meal,
        display_name=None if food_ref else name,
        food_ref=food_ref,
        calories=kcal,
        protein_g=protein,
        carbs_g=carbs,
        fat_g=fat,
        servings=args.servings,
    )
    try:
        result = client.create(config.NUTRITION_TYPE, point)
    except ApiError as exc:
        print(f"Failed to log food.\n{exc}", file=sys.stderr)
        return 1

    macros = f"{kcal:.0f} kcal, P{protein:.0f} C{carbs:.0f} F{fat:.0f}" if kcal else name
    _emit(result, args.json, f"Logged {args.meal.lower()}: {name} ({macros})")
    return 0


def cmd_exercise(args: argparse.Namespace) -> int:
    client = HealthClient(dry_run=args.dry_run)
    point = payloads.exercise_datapoint(
        start=_parse_when(args.at),
        duration_minutes=args.minutes,
        exercise_type=args.type,
        display_name=args.name,
        calories=args.kcal,
        distance_km=args.km,
        steps=args.steps,
    )
    try:
        result = client.create(config.EXERCISE_TYPE, point)
    except ApiError as exc:
        print(f"Failed to log exercise.\n{exc}", file=sys.stderr)
        return 1

    summary = f"Logged {args.minutes:.0f} min {args.type.lower()}"
    if args.km:
        summary += f", {args.km} km"
    if args.kcal:
        summary += f", {args.kcal:.0f} kcal"
    _emit(result, args.json, summary)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    client = HealthClient()
    tz = config.local_tz()
    end = datetime.now(tz)
    start = end - timedelta(days=args.days)
    data_type = {
        "food": config.NUTRITION_TYPE,
        "exercise": config.EXERCISE_TYPE,
    }[args.type]
    try:
        result = client.list(data_type, start=start, end=end)
    except ApiError as exc:
        print(f"Failed to read {args.type}.\n{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    client = HealthClient(dry_run=args.dry_run)
    data_type = {"food": config.NUTRITION_TYPE, "exercise": config.EXERCISE_TYPE}[args.type]
    try:
        client.delete(data_type, args.id)
    except ApiError as exc:
        print(f"Failed to delete.\n{exc}", file=sys.stderr)
        return 1
    print(f"Deleted {args.type} entry {args.id}")
    return 0


# ---- parser -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="healthlog", description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit raw API response")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the request without sending it"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="authenticate with Google")
    auth_sub = p_auth.add_subparsers(dest="auth_cmd", required=True)
    p_login = auth_sub.add_parser("login")
    p_login.add_argument("--manual", action="store_true", help="headless: print URL")
    p_login.add_argument("--no-browser", action="store_true")
    auth_sub.add_parser("status")
    p_complete = auth_sub.add_parser("complete")
    p_complete.add_argument("code")
    p_auth.set_defaults(func=cmd_auth)

    p_probe = sub.add_parser("probe", help="check which reads and writes work")
    p_probe.set_defaults(func=cmd_probe)

    p_food = sub.add_parser("food", help="nutrition logging")
    food_sub = p_food.add_subparsers(dest="food_cmd", required=True)

    p_flog = food_sub.add_parser("log")
    p_flog.add_argument("--name")
    p_flog.add_argument("--saved", help="use a cached food by name")
    p_flog.add_argument("--meal", default="SNACK", choices=payloads.MEAL_TYPES)
    p_flog.add_argument("--kcal", type=float)
    p_flog.add_argument("--protein", type=float)
    p_flog.add_argument("--carbs", type=float)
    p_flog.add_argument("--fat", type=float)
    p_flog.add_argument("--servings", type=float, default=1.0)
    p_flog.add_argument("--food-ref", help="Food resource name for an identified food")
    p_flog.add_argument("--at", help="'now', 'HH:MM', or ISO timestamp")

    p_fsave = food_sub.add_parser("save", help="cache a recurring meal")
    p_fsave.add_argument("--name", required=True)
    p_fsave.add_argument("--kcal", type=float, required=True)
    p_fsave.add_argument("--protein", type=float, required=True)
    p_fsave.add_argument("--carbs", type=float, required=True)
    p_fsave.add_argument("--fat", type=float, required=True)

    food_sub.add_parser("list-saved")
    p_fforget = food_sub.add_parser("forget")
    p_fforget.add_argument("name")
    p_food.set_defaults(func=cmd_food)

    p_ex = sub.add_parser("exercise", help="log a workout")
    ex_sub = p_ex.add_subparsers(dest="exercise_cmd", required=True)
    p_exlog = ex_sub.add_parser("log")
    p_exlog.add_argument("--type", required=True, help=f"e.g. {', '.join(payloads.EXERCISE_TYPES[:6])}")
    p_exlog.add_argument("--minutes", type=float, required=True)
    p_exlog.add_argument("--name", help="display name, e.g. 'Marina loop'")
    p_exlog.add_argument("--kcal", type=float)
    p_exlog.add_argument("--km", type=float)
    p_exlog.add_argument("--steps", type=int)
    p_exlog.add_argument("--at", help="start time: 'now', 'HH:MM', or ISO")
    p_ex.set_defaults(func=cmd_exercise)

    p_show = sub.add_parser("show", help="read back what is logged")
    p_show.add_argument("--type", default="food", choices=["food", "exercise"])
    p_show.add_argument("--days", type=int, default=1)
    p_show.set_defaults(func=cmd_show)

    p_del = sub.add_parser("delete")
    p_del.add_argument("--type", required=True, choices=["food", "exercise"])
    p_del.add_argument("--id", required=True)
    p_del.set_defaults(func=cmd_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except auth.AuthError as exc:
        print(f"\nAuth problem:\n{exc}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
