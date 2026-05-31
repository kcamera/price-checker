# price-checker

## Run
```sh
.venv/bin/python price_tracker.py
```
Exit codes: `0` all ok · `1` one or more vendor failures · `2` fatal (config/browser).  
Terminal output is the primary surface — the user reads it directly, no file needed in the happy path.

## Key files
| File | Role |
|---|---|
| `vendors.json` | Config + product/vendor list — edit here to add products or fix selectors |
| `price_history.json` | Append-only history — **gitignored, never commit** |
| `status.json` | Latest run snapshot — **gitignored, never commit** |
| `~/.price-tracker/brave-profile` | Dedicated Brave user-data-dir — don't touch manually |

## Selector work — subagent rule
**Always use a subagent to fetch and inspect a vendor page.** Do not load raw HTML or page source into the main context. The subagent returns: the working selector (or JSON-LD confirmation), one-line justification, and the normalized price it found. Nothing else comes back.

Try JSON-LD (`script[type="application/ld+json"]` → `offers.price`) before any CSS selector. If CSS is needed, prefer the narrowest stable selector (attribute selectors like `[itemprop="price"]` over brittle class chains).

## Git discipline
- Branch: `phase-1`
- Logical, well-formed commits with imperative subject lines
- **No push, no PR** — user opens the PR manually on GitHub

## Recovery pattern (broken selector)
When extraction fails, `status.json` has everything needed to diagnose without re-running:
- `error.stage` — `navigation` / `extraction` / `parse` / `runtime`
- `error.selectors_tried` — the ordered list that was attempted
- `error.page_title` + `error.final_url` — catches redirects, captcha, out-of-stock
- `error.raw_text` — the string that wouldn't normalize (parse-stage failures)
- `error.jsonld_present` — whether any JSON-LD blocks were found at all

Read `status.json` before touching the script.
