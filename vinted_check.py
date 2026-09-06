#!/usr/bin/env python3
"""
Vinted new-listing watcher.

Reads a Vinted catalog search URL, hits Vinted's internal API for that
same search, works out which listed items are new since the last run,
and pushes a notification for each new one via ntfy.sh.

State (which item IDs have already been seen) is kept in seen_items.json
in this repo, so the GitHub Actions workflow commits it back after every run.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

import requests

STATE_FILE = Path(__file__).parent / "seen_items.json"

# Vinted's public-facing catalog page and its internal JSON API share almost
# all query params; the catalog[] param on the page maps to catalog_ids on
# the API, and search_id is just a tracking value we can drop.
PARAM_RENAMES = {
    "catalog[]": "catalog_ids",
    "brand_ids[]": "brand_ids",
    "material_ids[]": "material_ids",
    "color_ids[]": "color_ids",
    "size_ids[]": "size_ids",
    "status_ids[]": "status_ids",
}
PARAMS_TO_DROP = {"search_id"}


def build_api_url(catalog_search_url: str) -> str:
    parsed = urlparse(catalog_search_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    query = parse_qs(parsed.query)

    api_params = {}
    for key, values in query.items():
        if key in PARAMS_TO_DROP:
            continue
        new_key = PARAM_RENAMES.get(key, key)
        # parse_qs gives lists; keep multiple values (e.g. color_ids) as a list
        if new_key in api_params:
            api_params[new_key].extend(values)
        else:
            api_params[new_key] = list(values)

    api_params.setdefault("order", ["newest_first"])
    api_params.setdefault("per_page", ["30"])

    # requests needs a flat list of tuples to preserve repeated keys like color_ids
    flat_pairs = []
    for key, values in api_params.items():
        for v in values:
            flat_pairs.append((key, v))

    return f"{domain}/api/v2/catalog/items?{urlencode(flat_pairs)}"


def fetch_items(catalog_search_url: str) -> list[dict]:
    api_url = build_api_url(catalog_search_url)
    domain = f"{urlparse(catalog_search_url).scheme}://{urlparse(catalog_search_url).netloc}"

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }
    )

    # Vinted requires a valid session/cookie before the API will respond;
    # visiting the homepage first picks up the cookies it sets.
    session.get(domain, timeout=20)

    resp = session.get(api_url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def load_seen_ids() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except (json.JSONDecodeError, ValueError):
            return set()
    return set()


def save_seen_ids(ids: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(ids), indent=2))


def notify(topic: str, item: dict) -> None:
    title = item.get("title", "New Vinted listing")
    price_obj = item.get("price") or {}
    amount = price_obj.get("amount")
    currency = price_obj.get("currency_code", "")
    price_str = f"{amount} {currency}".strip() if amount else "price n/a"
    item_url = item.get("url", "")

    requests.post(
        f"https://ntfy.sh/{topic}",
        data=f"{title} — {price_str}".encode("utf-8"),
        headers={
            "Title": "New Vinted listing".encode("utf-8"),
            "Click": item_url,
            "Priority": "default",
        },
        timeout=15,
    )


def main() -> int:
    search_url = os.environ.get("SEARCH_URL")
    ntfy_topic = os.environ.get("NTFY_TOPIC")

    if not search_url or not ntfy_topic:
        print("SEARCH_URL and NTFY_TOPIC environment variables are required.", file=sys.stderr)
        return 1

    items = fetch_items(search_url)
    current_ids = {str(item["id"]) for item in items if "id" in item}

    seen_ids = load_seen_ids()
    first_run = not STATE_FILE.exists()

    new_ids = current_ids - seen_ids

    if first_run:
        print(f"First run: recording {len(current_ids)} existing listings without notifying.")
    elif new_ids:
        print(f"Found {len(new_ids)} new listing(s). Sending notifications...")
        for item in items:
            if str(item.get("id")) in new_ids:
                notify(ntfy_topic, item)
    else:
        print("No new listings.")

    save_seen_ids(current_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
