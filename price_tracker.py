#!/usr/bin/env python3
"""Local, deterministic price tracker.

Reads vendors.json, drives Brave (via Playwright) to read the current shelf price
off each enabled vendor page, tracks history in price_history.json, and writes a
snapshot to status.json. Run it on demand:

    python price_tracker.py

Exit codes: 0 = all vendors succeeded, 1 = one or more fetch/parse failures,
2 = fatal (bad config or browser launch failure).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDORS_FILE = ROOT / "vendors.json"
HISTORY_FILE = ROOT / "price_history.json"
STATUS_FILE = ROOT / "status.json"


# --- config / IO -----------------------------------------------------------

def load_config():
    with open(VENDORS_FILE) as f:
        return json.load(f)


def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def save_status(status):
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


def last_distinct_entry(history, product, vendor, current_price):
    """Most recent entry whose price differs from the current reading.

    Comparing against the last *distinct* price avoids noisy ~0% diffs when the
    script is run several times in a day without a real price move.
    """
    entries = history.get(product, {}).get(vendor, [])
    for entry in reversed(entries):
        if entry.get("price") != current_price:
            return entry
    return entries[-1] if entries else None


# --- price extraction ------------------------------------------------------

def normalize_price(text):
    """Pull a float out of a messy price string ('$3.49/ea', '1,299.00')."""
    if text is None:
        return None
    cleaned = str(text).replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _walk_jsonld_for_price(node):
    """Recursively search a parsed JSON-LD node for an offers price."""
    if isinstance(node, dict):
        offers = node.get("offers")
        if isinstance(offers, dict) and "price" in offers:
            return offers["price"]
        if isinstance(offers, list):
            for offer in offers:
                price = _walk_jsonld_for_price(offer)
                if price is not None:
                    return price
        if "price" in node and node.get("@type") in ("Offer", "Product", None):
            return node["price"]
        for value in node.values():
            price = _walk_jsonld_for_price(value)
            if price is not None:
                return price
    elif isinstance(node, list):
        for item in node:
            price = _walk_jsonld_for_price(item)
            if price is not None:
                return price
    return None


def extract_jsonld_price(page):
    """Try structured data first — most reliable on retail pages."""
    blocks = page.locator("script[type='application/ld+json']").all()
    found_any = False
    for block in blocks:
        found_any = True
        raw = block.inner_text()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        price = _walk_jsonld_for_price(data)
        if price is not None:
            return normalize_price(price), found_any
    return None, found_any


def extract_css_price(page, selectors):
    """Try each CSS selector in order; first that parses to a number wins."""
    for selector in selectors or []:
        loc = page.locator(selector).first
        if loc.count() == 0:
            continue
        text = loc.inner_text(timeout=2000)
        price = normalize_price(text)
        if price is not None:
            return price, selector
    return None, None


# --- per-vendor processing -------------------------------------------------

def process_vendor(page, product, unit_type, vendor, config, history):
    name = product
    vendor_name = vendor["vendor"]
    url = vendor["url"]
    package_count = vendor.get("package_count", 1)
    unit_size = vendor.get("unit_size", 1)

    result = {
        "product": name,
        "vendor": vendor_name,
        "url": url,
        "ok": False,
        "price": None,
        "price_per_base_unit": None,
        "unit_type": unit_type,
        "package_count": package_count,
        "unit_size": unit_size,
        "prev_price": None,
        "prev_price_per_base_unit": None,
        "change_pct": None,
        "alert": False,
        "error": None,
    }

    page.goto(url, timeout=config.get("nav_timeout_ms", 30000), wait_until="domcontentloaded")

    price, _jsonld_present = extract_jsonld_price(page)
    if price is None:
        price, _matched = extract_css_price(page, vendor.get("selectors"))

    if price is None:
        result["error"] = "Could not extract a price (JSON-LD and all selectors missed)"
        return result

    total_base_units = package_count * unit_size
    price_per_base_unit = round(price / total_base_units, 6) if total_base_units else None

    result["price"] = price
    result["price_per_base_unit"] = price_per_base_unit

    prev = last_distinct_entry(history, name, vendor_name, price)
    if prev:
        result["prev_price"] = prev.get("price")
        result["prev_price_per_base_unit"] = prev.get("price_per_base_unit")
        prev_ppu = prev.get("price_per_base_unit")
        if prev_ppu:
            change = (price_per_base_unit - prev_ppu) / prev_ppu * 100
            result["change_pct"] = round(change, 2)
            threshold = config.get("alert_threshold_pct", 5.0)
            if change <= -abs(threshold):
                result["alert"] = True

    result["ok"] = True
    return result


def append_history(history, result, timestamp):
    product = history.setdefault(result["product"], {})
    entries = product.setdefault(result["vendor"], [])
    entries.append({
        "timestamp": timestamp,
        "price": result["price"],
        "price_per_base_unit": result["price_per_base_unit"],
        "package_count": result["package_count"],
        "unit_size": result["unit_size"],
        "unit_type": result["unit_type"],
    })


# --- terminal summary ------------------------------------------------------

EXIT_MEANING = {
    0: "all vendors succeeded",
    1: "one or more fetch/parse failures",
    2: "fatal error",
}


def _change_str(change_pct):
    """Signed change with a direction arrow, e.g. '↓ -6.2%' or '— flat'."""
    if change_pct is None:
        return "— (no prior reading)"
    if change_pct < 0:
        return f"↓ {change_pct:+.2f}%"
    if change_pct > 0:
        return f"↑ {change_pct:+.2f}%"
    return "— flat"


def print_summary(results, exit_code):
    """Human-readable summary — the primary surface. Self-sufficient: the user
    should learn everything (prices, changes, alerts, failures) from here."""
    print()
    for r in results:
        if r["ok"]:
            mark = "🔻" if r["alert"] else "🟢"
            print(
                f"{mark} {r['product']} @ {r['vendor']}: "
                f"${r['price']:.2f}  "
                f"(${r['price_per_base_unit']:.4f}/{r['unit_type']})  "
                f"{_change_str(r['change_pct'])}"
                + ("  ** PRICE DROP **" if r["alert"] else "")
            )
        else:
            err = r["error"]
            reason = err.get("message") if isinstance(err, dict) else err
            print(f"🔴 {r['product']} @ {r['vendor']}: FAILED — {reason}")

    ok = sum(1 for r in results if r["ok"])
    failed = len(results) - ok
    alerts = [r for r in results if r.get("alert")]
    print()
    summary = f"{ok} ok, {failed} failed"
    if alerts:
        names = ", ".join(f"{a['product']} @ {a['vendor']}" for a in alerts)
        summary += f" — {len(alerts)} price drop(s): {names}"
    print(summary)
    print(f"exit {exit_code} ({EXIT_MEANING.get(exit_code, 'unknown')})")


# --- main ------------------------------------------------------------------

def main():
    try:
        config_doc = load_config()
    except (OSError, json.JSONDecodeError) as e:
        print(f"FATAL: could not read vendors.json: {e}", file=sys.stderr)
        return 2

    config = config_doc.get("config", {})
    products = config_doc.get("products", [])
    history = load_history()

    user_data_dir = os.path.expanduser(config.get("user_data_dir", "~/.price-tracker/brave-profile"))
    executable = os.path.expanduser(config.get("brave_executable", ""))
    profile_directory = config.get("profile_directory", "Default")

    results = []
    timestamp = datetime.now(timezone.utc).isoformat()
    had_failure = False

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                executable_path=executable,
                headless=config.get("headless", False),
                args=[f"--profile-directory={profile_directory}"],
            )
            page = context.new_page()
            for product in products:
                unit_type = product.get("unit_type", "unit")
                for vendor in product.get("vendors", []):
                    if not vendor.get("enabled", True):
                        continue
                    try:
                        r = process_vendor(page, product["name"], unit_type, vendor, config, history)
                    except Exception as e:  # one bad vendor never aborts the run
                        r = {
                            "product": product["name"], "vendor": vendor["vendor"],
                            "url": vendor["url"], "ok": False, "price": None,
                            "price_per_base_unit": None, "unit_type": unit_type,
                            "package_count": vendor.get("package_count", 1),
                            "unit_size": vendor.get("unit_size", 1),
                            "prev_price": None, "prev_price_per_base_unit": None,
                            "change_pct": None, "alert": False, "error": str(e),
                        }
                    if r["ok"]:
                        append_history(history, r, timestamp)
                    else:
                        had_failure = True
                    results.append(r)
            context.close()
    except Exception as e:
        print(f"FATAL: browser launch failed: {e}", file=sys.stderr)
        return 2

    save_history(history)
    exit_code = 1 if had_failure else 0
    save_status({
        "run_timestamp": timestamp,
        "exit_code": exit_code,
        "ok_count": sum(1 for r in results if r["ok"]),
        "fail_count": sum(1 for r in results if not r["ok"]),
        "results": results,
    })
    print_summary(results, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
