#!/usr/bin/env python3
"""Standalone price-drop watcher for the Shopify store, run on a schedule by
Hermes's cron toolset (not an MCP server - Hermes cron just runs this as a
plain script and forwards its stdout to the Telegram home channel).

Usage:
    # Manage the watchlist:
    python mcp_server/price_watch.py --add "Nike Pegasus" --target 80
    python mcp_server/price_watch.py --list
    python mcp_server/price_watch.py --remove 0

    # Test the alert path without hitting the real store or waiting for a
    # real price drop:
    python mcp_server/price_watch.py --dry-run

    # What cron actually runs daily - checks every watch, prints an alert
    # line per drop, stays silent (on stdout) if nothing changed:
    python mcp_server/price_watch.py

Watchlist file: ~/.hermes_price_watch.json
    {"watches": [{"query": "Nike Pegasus", "product_id": "gid://...",
                  "target_price": 80.0, "last_seen_price": 89.99,
                  "added": "2026-08-13"}]}

Design note on stdout noise: Hermes cron forwards every line of stdout to
Telegram. Printing a status line on every no-op daily run would page the
user for nothing every single day. So the default (no-flag) run is silent
on stdout unless there's an actual alert - diagnostics go to stderr instead,
same "stdout is sacred" convention as the MCP servers in this project, just
for a different reason (notification hygiene instead of protocol purity).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse the already-validated Shopify JSON-RPC/normalization logic instead
# of duplicating it - both files live in mcp_server/, and Python puts a
# directly-run script's own directory on sys.path[0] automatically.
import shopify_mcp_server as shopify

WATCHLIST_FILE = Path.home() / ".hermes_price_watch.json"


def _load_watchlist() -> List[Dict[str, Any]]:
    try:
        data = json.loads(WATCHLIST_FILE.read_text())
        return data.get("watches", [])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_watchlist(watches: List[Dict[str, Any]]) -> None:
    WATCHLIST_FILE.write_text(json.dumps({"watches": watches}, indent=2))


async def _resolve_product(query: str) -> Optional[Dict[str, Any]]:
    """Search the store for `query` and return the first result, or None."""
    results = await shopify._search_products(query=query, limit=1)
    products = results["products"]
    return products[0] if products else None


async def cmd_add(query: str, target: float) -> int:
    print(f"Searching store for '{query}'...")
    try:
        product = await _resolve_product(query)
    except Exception as exc:
        print(f"FAIL: could not search the store: {exc}")
        return 1

    if product is None:
        print(f"FAIL: no product found matching '{query}' - not added.")
        return 1

    watches = _load_watchlist()
    watches.append(
        {
            "query": query,
            "product_id": product["product_id"],
            "target_price": target,
            "last_seen_price": product["price_min"],
            "added": datetime.now(timezone.utc).date().isoformat(),
        }
    )
    _save_watchlist(watches)
    print(
        f"Added watch: '{query}' -> {product['name']} "
        f"(currently ${product['price_min']}, target ${target})"
    )
    return 0


def cmd_list() -> int:
    watches = _load_watchlist()
    if not watches:
        print("No watches configured.")
        return 0
    for i, w in enumerate(watches):
        print(
            f"[{i}] '{w['query']}' target=${w['target_price']} "
            f"last_seen=${w['last_seen_price']} added={w['added']} "
            f"product_id={w['product_id']}"
        )
    return 0


def cmd_remove(index: int) -> int:
    watches = _load_watchlist()
    if not (0 <= index < len(watches)):
        print(f"FAIL: no watch at index {index} (have {len(watches)}: 0..{len(watches) - 1})")
        return 1
    removed = watches.pop(index)
    _save_watchlist(watches)
    print(f"Removed watch [{index}]: '{removed['query']}'")
    return 0


def _format_alert(query: str, current: float, target: float, last_seen: float) -> str:
    return (
        f"\U0001f53b Price alert: '{query}' is now ${current:.2f} "
        f"(target ${target:.2f}, was ${last_seen:.2f})"
    )


def cmd_dry_run() -> int:
    """Simulate a price drop so the alert-formatting/delivery path can be
    tested without waiting for a real one or touching the network."""
    fake_query = "Nike Pegasus (dry-run)"
    fake_target = 80.0
    fake_last_seen = 89.99
    fake_current = 74.99
    print(_format_alert(fake_query, fake_current, fake_target, fake_last_seen))
    print("(dry-run: no real store lookup was made, no watchlist changes were saved)", file=sys.stderr)
    return 0


async def cmd_check(alert_on_any_drop: bool) -> int:
    """The real cron run: check every watch, alert on drops, stay silent on
    stdout otherwise."""
    watches = _load_watchlist()
    if not watches:
        print("[price_watch] no watches configured", file=sys.stderr)
        return 0

    alerts = 0
    for w in watches:
        try:
            product = await shopify._get_product(w["product_id"])
            current_price = product["price_min"]
        except Exception as exc:
            print(f"[price_watch] check failed for '{w['query']}': {exc}", file=sys.stderr)
            continue

        if current_price is None:
            print(f"[price_watch] '{w['query']}': no price returned, skipping", file=sys.stderr)
            continue

        target = w["target_price"]
        last_seen = w.get("last_seen_price", current_price)

        dropped_below_target = current_price < target
        dropped_at_all = current_price < last_seen

        if dropped_below_target or (alert_on_any_drop and dropped_at_all):
            print(_format_alert(w["query"], current_price, target, last_seen))
            alerts += 1
        else:
            print(
                f"[price_watch] '{w['query']}': ${current_price} (target ${target}, no alert)",
                file=sys.stderr,
            )

        w["last_seen_price"] = current_price

    _save_watchlist(watches)
    print(f"[price_watch] checked {len(watches)} watch(es), {alerts} alert(s)", file=sys.stderr)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Shopify price-drop watcher")
    parser.add_argument("--add", metavar="QUERY", help="Add a new watch for QUERY")
    parser.add_argument("--target", type=float, help="Target price for --add (required with --add)")
    parser.add_argument("--list", action="store_true", help="List all watches")
    parser.add_argument("--remove", type=int, metavar="INDEX", help="Remove the watch at INDEX")
    parser.add_argument("--dry-run", action="store_true", help="Simulate a price drop to test the alert path")
    parser.add_argument(
        "--alert-on-any-drop",
        action="store_true",
        help="Also alert when price drops at all (not just below target)",
    )
    # parse_known_args for the same reason as the MCP servers in this project:
    # be tolerant of unexpected extra argv from whatever launches this script.
    args, _unknown = parser.parse_known_args()

    if args.add is not None:
        if args.target is None:
            print("FAIL: --add requires --target <price>")
            sys.exit(1)
        sys.exit(asyncio.run(cmd_add(args.add, args.target)))

    if args.list:
        sys.exit(cmd_list())

    if args.remove is not None:
        sys.exit(cmd_remove(args.remove))

    if args.dry_run:
        sys.exit(cmd_dry_run())

    sys.exit(asyncio.run(cmd_check(args.alert_on_any_drop)))


if __name__ == "__main__":
    main()
