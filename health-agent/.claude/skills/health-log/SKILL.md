---
name: health-log
description: Log food, meals, macros, or workouts to Google Health / Fitbit. Use whenever the user describes something they ate or drank ("had a shawarma", "two eggs and toast", "my usual breakfast") or exercise they did ("ran 7k", "45 min gym", "played padel"), or asks what they have logged today. Converts natural language into macro estimates and writes them via the healthlog CLI.
---

# Logging food and exercise to Google Health

The user's Fitbit Air syncs to Google Health, but Fitbit's Ask Coach is not
available in the UAE. This skill replaces the logging half of that: the user
describes a meal or workout in plain language, you estimate the macros, and
`healthlog` writes it through the Google Health API. Entries appear in their
Fitbit app within a minute or two.

## The one rule

**Always show your macro estimate and get confirmation before writing.**

These numbers become the user's actual health record, and an anonymous food log
cannot be edited after the fact — a wrong entry has to be deleted and redone. A
two-second confirmation is cheaper than that. The exception is a `--saved` food,
where the macros were already agreed when it was cached.

## Logging food

1. Check the cache first — `healthlog food list-saved`. Anything matching what
   they described is settled; skip estimation.
2. Otherwise estimate calories, protein, carbs and fat from the description.
   Use the portion the user implies, not a generic serving. "A shawarma" from a
   Dubai shop is not a USDA 100 g reference portion.
3. Show the estimate as one compact line and ask.
4. Write it:

```bash
healthlog food log --name "chicken shawarma" --meal LUNCH \
  --kcal 620 --protein 38 --carbs 55 --fat 26
```

Meal types: `BREAKFAST`, `LUNCH`, `DINNER`, `SNACK`. Infer from the time of day
or from what the user said; ask only when genuinely ambiguous.

`--at` accepts `now` (default), `HH:MM`, or an ISO timestamp. Use it whenever
the user is logging retroactively — "I had this at 8" means `--at 08:30`, not
now. Timestamps are written in Asia/Dubai.

For multi-item meals, prefer one entry per item. It reads better in the Fitbit
app and lets the user delete one thing without losing the whole meal.

### Recurring meals

When the user says something is their usual, or you notice the same meal a third
time, offer to cache it:

```bash
healthlog food save --name "usual breakfast" --kcal 420 --protein 28 --carbs 38 --fat 16
healthlog food log --saved "usual breakfast" --meal BREAKFAST
```

## Logging exercise

```bash
healthlog exercise log --type RUNNING --minutes 45 --kcal 480 --km 7.2 \
  --name "Marina loop"
```

Only `--type` and `--minutes` are required. Supply `--kcal`, `--km` or `--steps`
when the user gives them or when a calorie estimate is clearly useful — but say
that a burn figure is an estimate, since the watch measures this directly and
its number is better than yours.

Common types: `RUNNING`, `WALKING`, `BIKING`, `SWIMMING`, `STRENGTH_TRAINING`,
`HIIT`, `PADEL`, `TENNIS`, `YOGA`, `WORKOUT`. Full list in
`healthlog/payloads.py`. Fall back to `WORKOUT` rather than guessing an enum
that may not exist.

**Watch out for double counting.** If the Fitbit already auto-detected the
workout, logging it again duplicates it. For anything the watch was worn for,
ask before writing.

## Reading back

```bash
healthlog show --type food --days 1
healthlog show --type exercise --days 7
```

Summarise the totals in prose. Do not paste raw JSON at the user unless asked.

## When something breaks

- **`invalid_grant` / auth errors** — the OAuth consent screen has fallen back
  to Testing, where Google expires refresh tokens after 7 days. Tell the user to
  publish the app to Production, then `healthlog auth login`. This is the single
  most likely failure.
- **A write returns 4xx on nutrition specifically** — nutrition write support is
  the known open question in this project. Run `healthlog probe`, report exactly
  what failed, and do not silently retry.
- **Anything else** — surface the error text rather than paraphrasing it. The
  API's messages are specific and worth reading verbatim.

Add `--dry-run` to any command to see the exact request without sending it.
Useful when unsure about a payload.
