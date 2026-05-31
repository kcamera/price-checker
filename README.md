# price-checker

A small, **deterministic, local** price-tracking tool. It loads a list of products and
vendor URLs from `vendors.json`, drives your real **Brave** browser via Playwright to read
the current shelf price off each page, tracks history over time, and tells you when something
drops below your threshold.

No AI inference at runtime, no cloud service, no daemon — it's a fixed script that produces
the same result every run. You run it yourself when you want to know.

## Why Brave + a dedicated profile

Retail sites block obvious bots. To look like a normal shopper, the tool drives your
day-to-day **Brave** browser (headful) rather than a stock headless Chromium. It uses a
**dedicated Brave profile** reserved for this tool, kept separate from your personal browsing
so its cookies/trackers don't mix with yours — and so it doesn't collide with a Brave window
you already have open.

## Prerequisites

```sh
pip install -r requirements.txt   # installs playwright
playwright install chromium        # Playwright needs its driver even though we point it at Brave
```

You also need Brave installed. On macOS the executable is:

```
/Applications/Brave Browser.app/Contents/MacOS/Brave Browser
```

### Dedicated profile setup

`vendors.json` → `config` controls the browser:

- `brave_executable` — path to the Brave binary (above).
- `user_data_dir` — a **dedicated** directory for this tool's profile, e.g.
  `~/.price-tracker/brave-profile`. Created on first run. Do **not** point this at your
  personal Brave data dir (that would mix cookies and can fail with a profile-lock error if
  Brave is already open).
- `profile_directory` — the profile within that data dir (default `Default`).
- `headless` — keep `false`; a visible browser is more blocker-resistant.

## Usage

Run it on demand from your terminal (a shell alias is handy):

```sh
python price_tracker.py
```

A readable summary prints to the terminal — that's the primary surface. You learn everything
(prices, changes, alerts, failures) without opening any file.

**Exit codes:** `0` all vendors succeeded · `1` one or more fetch/parse failures · `2` fatal
(bad config or browser launch failure).

## Output files

- `price_history.json` — append-only history, keyed `product → vendor → [entries]`. Local only.
- `status.json` — the latest run snapshot: per-vendor prices, per-unit values, change vs. the
  previous reading, alert flags, and structured error details when something failed. Local only.

Both are git-ignored; only `vendors.json` (your config) is committed.

## When a page breaks (selector drift)

Retail pages change their markup often, which breaks price extraction. When that happens the
run records a structured `error` on that vendor in `status.json` — including which stage
failed, which selectors were tried, the page title / final URL (catches redirects and
"are you a robot" pages), and any raw text that wouldn't parse.

That file is designed to be the recovery artifact: open Claude Code and say
**"read status.json and fix the script"** — it has enough detail to diagnose and patch the
drifted selector.

## Scope

This is Phase 1: the local collector. Possible later additions (a menu-bar widget, unattended
scheduled runs, a phone-viewable page) are intentionally deferred until the selectors prove
stable against real vendor pages.
