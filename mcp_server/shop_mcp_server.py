#!/usr/bin/env python3
"""MCP server wrapping the Hermes demo shop FastAPI backend.

Exposes the local shop (shop_backend) as MCP tools so the Hermes Agent
can search products and manage a cart.

Usage:
    # Run as a stdio MCP server (what Hermes launches via `hermes mcp add`):
    python mcp_server/shop_mcp_server.py

    # Run a standalone connectivity + tool smoke test against a running
    # backend (does NOT require an MCP client):
    python mcp_server/shop_mcp_server.py --selftest

Environment variables:
    SHOP_API_URL - base URL of the shop_backend FastAPI service
                   (default: http://localhost:8000)
"""

from __future__ import annotations

import warnings

# MCP stdio servers MUST NOT write anything to stdout except protocol
# messages: any stray text (a warning, a print, a BOM) corrupts the
# JSON-RPC stream the client is parsing and shows up as an opaque
# "Connection closed" on the Hermes side, typically after Hermes has
# waited a few seconds for a handshake that never arrives cleanly.
# The 'mcp' package's own dependency (pydantic_settings) emits an
# IncompleteFieldDefinitionWarning the moment FastMCP(...) is
# constructed - harmless in --selftest mode (stdout isn't a protocol
# channel there), but a real risk on the actual stdio run. Silence all
# warnings unconditionally, before importing anything that could ever
# want to fire one.
warnings.simplefilter("ignore")

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import httpx

SHOP_API_URL = os.environ.get("SHOP_API_URL", "http://localhost:8000")
DEFAULT_TIMEOUT = 10.0


def _client() -> httpx.AsyncClient:
    """Build an httpx client for talking to the local shop backend.

    trust_env=False disables picking up HTTP_PROXY/ALL_PROXY/etc. from the
    environment: the backend is always a same-machine service, and a
    misconfigured or SOCKS-only system proxy should never break local
    tool calls.
    """
    return httpx.AsyncClient(base_url=SHOP_API_URL, timeout=DEFAULT_TIMEOUT, trust_env=False)


# ---------------------------------------------------------------------------
# Core tool logic (plain async functions, independent of the MCP framework).
# Kept separate from the @mcp.tool() wrappers below so --selftest can call
# them directly without needing a live MCP client/transport.
# ---------------------------------------------------------------------------


async def _search_products(
    query: str = "",
    brand: str = "",
    category: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    size: Optional[int] = None,
    in_stock_only: bool = False,
) -> Dict[str, Any]:
    """Search the shop catalog by free text and/or filters."""
    params: Dict[str, Any] = {}
    if query:
        params["q"] = query
    if brand:
        params["brand"] = brand
    if category:
        params["category"] = category
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if size is not None:
        params["size"] = size
    if in_stock_only:
        params["in_stock_only"] = in_stock_only

    async with _client() as client:
        resp = await client.get("/products", params=params)
        resp.raise_for_status()
        return resp.json()


async def _get_product(product_id: str) -> Dict[str, Any]:
    """Fetch full details for a single product by id."""
    async with _client() as client:
        resp = await client.get(f"/products/{product_id}")
        resp.raise_for_status()
        return resp.json()


async def _add_to_cart(
    product_id: str, size: Optional[int] = None, quantity: int = 1
) -> Dict[str, Any]:
    """Add a product (with optional size) to the shared demo cart."""
    payload = {"product_id": product_id, "size": size, "quantity": quantity}
    async with _client() as client:
        resp = await client.post("/cart/items", json=payload)
        resp.raise_for_status()
        return resp.json()


async def _view_cart() -> Dict[str, Any]:
    """Return the current contents of the cart."""
    async with _client() as client:
        resp = await client.get("/cart")
        resp.raise_for_status()
        return resp.json()


async def _remove_from_cart(item_id: str) -> Dict[str, Any]:
    """Remove a single line item from the cart by its item_id."""
    async with _client() as client:
        resp = await client.delete(f"/cart/items/{item_id}")
        resp.raise_for_status()
        return resp.json()


async def _clear_cart() -> Dict[str, Any]:
    """Empty the entire cart."""
    async with _client() as client:
        resp = await client.delete("/cart")
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# MCP tool registration. FastMCP is only required to actually run the
# stdio server (the else-branch below); --selftest works without it.
# ---------------------------------------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP

    _MCP_IMPORT_ERROR: Optional[Exception] = None
except ImportError as exc:  # pragma: no cover - exercised only when mcp isn't installed
    FastMCP = None  # type: ignore[assignment]
    _MCP_IMPORT_ERROR = exc


def build_mcp_server() -> "FastMCP":
    """Construct the FastMCP server instance and register all shop tools."""
    if FastMCP is None:
        raise RuntimeError(
            "The 'mcp' package is required to run the stdio server. "
            "Install it with: pip install mcp"
        ) from _MCP_IMPORT_ERROR

    mcp = FastMCP("hermes-demo-shop")

    @mcp.tool()
    async def search_products(
        query: str = "",
        brand: str = "",
        category: str = "",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        size: Optional[int] = None,
        in_stock_only: bool = False,
    ) -> Dict[str, Any]:
        """Search the shop catalog. Filter by brand, category, price range, and size."""
        return await _search_products(
            query, brand, category, min_price, max_price, size, in_stock_only
        )

    @mcp.tool()
    async def get_product(product_id: str) -> Dict[str, Any]:
        """Get full details for one product by its id (e.g. 'shoe-001')."""
        return await _get_product(product_id)

    @mcp.tool()
    async def add_to_cart(
        product_id: str, size: Optional[int] = None, quantity: int = 1
    ) -> Dict[str, Any]:
        """Add a product to the shopping cart, optionally with a size and quantity."""
        return await _add_to_cart(product_id, size, quantity)

    @mcp.tool()
    async def view_cart() -> Dict[str, Any]:
        """View everything currently in the shopping cart."""
        return await _view_cart()

    @mcp.tool()
    async def remove_from_cart(item_id: str) -> Dict[str, Any]:
        """Remove a single item from the cart by its cart item_id."""
        return await _remove_from_cart(item_id)

    @mcp.tool()
    async def clear_cart() -> Dict[str, Any]:
        """Empty the entire shopping cart."""
        return await _clear_cart()

    return mcp


# ---------------------------------------------------------------------------
# Self-test: exercises the core tool functions directly against a running
# shop_backend instance, without needing an MCP client/transport.
# ---------------------------------------------------------------------------


async def _run_selftest() -> bool:
    print(f"[selftest] SHOP_API_URL = {SHOP_API_URL}")
    ok = True

    # 1. Backend reachability
    try:
        async with _client() as client:
            resp = await client.get("/health")
            resp.raise_for_status()
            health = resp.json()
        print(f"[selftest] PASS: backend healthy -> {health}")
    except Exception as exc:
        print(f"[selftest] FAIL: backend not reachable at {SHOP_API_URL}: {exc}")
        print(
            "[selftest] Start it first with: "
            "uvicorn shop_backend.main:app --reload --port 8000"
        )
        return False

    # 2. Search
    try:
        results = await _search_products(brand="Nike", max_price=100, size=10)
        products = results.get("products", [])
        assert len(products) > 0, "expected at least one Nike result under $100 in size 10"
        print(f"[selftest] PASS: search_products -> {len(products)} result(s)")
        first_id = products[0]["id"]
    except Exception as exc:
        print(f"[selftest] FAIL: search_products: {exc}")
        return False

    # 3. Get product
    try:
        product = await _get_product(first_id)
        assert product["id"] == first_id
        print(f"[selftest] PASS: get_product -> {product['name']}")
    except Exception as exc:
        print(f"[selftest] FAIL: get_product: {exc}")
        ok = False

    # 4. Add to cart
    try:
        cart = await _add_to_cart(first_id, size=10, quantity=1)
        assert cart["item_count"] >= 1
        print(f"[selftest] PASS: add_to_cart -> item_count={cart['item_count']}")
        item_id = cart["items"][-1]["item_id"]
    except Exception as exc:
        print(f"[selftest] FAIL: add_to_cart: {exc}")
        return False

    # 5. View cart
    try:
        cart = await _view_cart()
        assert any(i["item_id"] == item_id for i in cart["items"])
        print(f"[selftest] PASS: view_cart -> {cart['item_count']} item(s), subtotal ${cart['subtotal']}")
    except Exception as exc:
        print(f"[selftest] FAIL: view_cart: {exc}")
        ok = False

    # 6. Remove item, then clear cart (cleanup)
    try:
        cart = await _remove_from_cart(item_id)
        print(f"[selftest] PASS: remove_from_cart -> item_count={cart['item_count']}")
    except Exception as exc:
        print(f"[selftest] FAIL: remove_from_cart: {exc}")
        ok = False

    try:
        cart = await _clear_cart()
        assert cart["item_count"] == 0
        print("[selftest] PASS: clear_cart -> cart empty")
    except Exception as exc:
        print(f"[selftest] FAIL: clear_cart: {exc}")
        ok = False

    # 7. MCP package availability (informational, not fatal for selftest)
    if FastMCP is None:
        print(
            "[selftest] WARN: 'mcp' package not installed - stdio server won't start. "
            "Install with: pip install mcp"
        )
    else:
        print("[selftest] PASS: 'mcp' package importable, FastMCP server can be built")
        try:
            build_mcp_server()
            print("[selftest] PASS: MCP tool registration succeeded")
        except Exception as exc:
            print(f"[selftest] FAIL: MCP tool registration: {exc}")
            ok = False

    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes demo shop MCP server")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Run a standalone smoke test against a running shop_backend and exit",
    )
    # parse_known_args, not parse_args: Hermes's own `hermes mcp add ... --args
    # <path> --env KEY=VAL` CLI has a quirk where it appends the --env flag and
    # its value as literal extra argv to the spawned process, on top of (also,
    # separately, correctly) setting it as an actual environment variable. A
    # strict parse_args() sees that unrecognized "--env KEY=VAL" tail, prints
    # an error to stderr, and calls sys.exit(2) - killing the server before it
    # ever reaches mcp.run(). Hermes then retries several times, each dying
    # the same way, which is what shows up as an opaque ~15s "Connection
    # closed" with no useful error surfaced anywhere. parse_known_args()
    # ignores anything it doesn't recognize instead of exiting, which is the
    # right behavior for a stdio server that has no business validating
    # exactly what its host process chooses to pass on the command line.
    args, _unknown_args = parser.parse_known_args()

    if args.selftest:
        passed = asyncio.run(_run_selftest())
        if passed:
            print("\n[selftest] ALL CHECKS PASSED")
            sys.exit(0)
        else:
            print("\n[selftest] SOME CHECKS FAILED")
            sys.exit(1)

    # Normal mode: run the MCP stdio server (this is what Hermes launches).
    mcp = build_mcp_server()
    mcp.run()


if __name__ == "__main__":
    main()
