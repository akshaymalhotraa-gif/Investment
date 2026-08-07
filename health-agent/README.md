# healthlog

Log food and exercise to Google Health (and therefore your Fitbit app) from the
terminal, or by talking to Claude.

Built because Fitbit's Ask Coach is unavailable in the UAE — the coaching layer
is region-gated, but the **data layer is not**. This writes directly to the
Google Health API v4, which is keyed to your Google account rather than your
location.

```
you → Claude (skill) → healthlog CLI → Google Health API v4 → Fitbit app
        macro estimate
```

## Why not the obvious alternatives

| Option | Why not |
|---|---|
| Fitbit Web API | Shuts down September 2026. Building on it now buys weeks. |
| Health Connect | Writes work, but it's an on-device Android API. Needs a sideloaded app; nothing in the cloud for Claude to call. |
| [`ghealth` CLI](https://github.com/Google-Health-API/google-health-cli) | Good tool, worth having for reads. Its docs list writable types as *exercise, sleep, weight, body-fat, height* — nutrition is absent, which is exactly half of what we need. Hence our own client. |

## Setup

### 1. Google Cloud project

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com)
2. **APIs & Services → Library →** enable **Google Health API**
3. **OAuth consent screen →** configure, add yourself as a user
4. **⚠️ Publish the app to "In Production."** Not optional — see below
5. **Credentials → Create credentials → OAuth client ID → Desktop app**, download the JSON
6. Save it:

```bash
mkdir -p ~/.config/healthlog
cp ~/Downloads/client_secret_*.json ~/.config/healthlog/client_secret.json
```

Scopes requested: `googlehealth.nutrition` and `googlehealth.activity_and_fitness`.

### 2. Install

```bash
pip install -e .
```

### 3. Authenticate

```bash
healthlog auth login          # opens a browser
healthlog auth login --manual # headless: prints a URL, paste the code back
healthlog auth status
```

### 4. Probe — do this before trusting anything

```bash
healthlog probe
```

Checks every read and write path against your real account, writes a 1-kcal test
entry labelled `healthlog probe`, and tells you precisely what the server
rejected if anything fails. It prints the delete command for its own test data.

## The token expiry trap

While your OAuth consent screen sits in **Testing**, Google hard-caps refresh
tokens at **7 days**. You would re-authenticate every week, forever. Publishing
the app to **In Production** removes the cap. You will see an "unverified app"
warning on the consent screen — click through it; verification only matters for
distributing to other people.

If you ever see `invalid_grant`, this is why. The CLI says so explicitly.

## Usage

```bash
# food
healthlog food log --name "chicken shawarma" --meal LUNCH \
  --kcal 620 --protein 38 --carbs 55 --fat 26
healthlog food log --name "flat white" --meal SNACK --kcal 120 --at 08:30

# recurring meals
healthlog food save --name "usual breakfast" --kcal 420 --protein 28 --carbs 38 --fat 16
healthlog food log --saved "usual breakfast" --meal BREAKFAST

# exercise
healthlog exercise log --type RUNNING --minutes 45 --kcal 480 --km 7.2 --name "Marina loop"
healthlog exercise log --type STRENGTH_TRAINING --minutes 60 --at 07:00

# read back
healthlog show --type food --days 1
healthlog show --type exercise --days 7

# inspect a request without sending it
healthlog --dry-run food log --name "test" --kcal 100 --protein 5 --carbs 10 --fat 3
```

Timestamps default to **Asia/Dubai**. Override with `HEALTHLOG_TZ`.

## Using it from Claude

`.claude/skills/health-log/SKILL.md` is a Claude skill. With this directory open
in Claude Code, say:

> had a chicken shawarma and a laban for lunch

Claude estimates the macros, shows them, waits for your confirmation, then logs.
The confirmation step is deliberate — anonymous food logs cannot be edited after
they are written, only deleted and recreated.

## Known unknown: nutrition writes

Google's nutrition data-type docs describe a `POST` to
`/users/me/dataTypes/nutrition-log/dataPoints` supporting both identified foods
(referencing a `Food` resource) and anonymous foods (display name plus macros).
That is what `payloads.py` builds.

But the `ghealth` CLI's own documentation omits nutrition from its writable
types. One of the two is stale, and this repo has no credentials with which to
settle it.

**`healthlog probe` settles it in about ten seconds.** If nutrition writes turn
out to be gated, exercise logging still works fully, and the fallback for food
is the Fitbit Web API's `/1/user/-/foods/log.json` — usable only until the
September 2026 shutdown, so it would be a stopgap while the Google endpoint
opens up.

## Layout

```
healthlog/
  config.py     endpoints, scopes, paths      ← edit here if the spec moves
  auth.py       OAuth loopback + refresh
  client.py     HTTP wrapper
  payloads.py   DataPoint construction        ← pure, unit-tested
  foods.py      local cache of usual meals
  cli.py        command line
tests/          payload shape tests, no network
.claude/skills/health-log/SKILL.md
```

```bash
pytest
```

Credentials live in `~/.config/healthlog/` (0600), never in the repo.
