#!/usr/bin/env python3
"""MCP server wrapping a real Shopify store via two of Shopify's own
agent-facing JSON-RPC 2.0 APIs:

    https://{domain}/api/ucp/mcp  - UCP (Universal Commerce Protocol, ucp.dev).
                                     Used here for search_catalog/get_product.
    https://{domain}/api/mcp      - legacy Storefront MCP. Used here for cart
                                     operations (get_cart, update_cart) and
                                     search_shop_policies_and_faqs. Shopify is
                                     deprecating this endpoint; its cart tools
                                     stop working 2026-08-31.

Cart operations run on the legacy endpoint instead of UCP's own create_cart/
get_cart/update_cart because UCP's cart tools have a live bug on this store:
they silently zero the quantity of any line item and report
merchandise_out_of_stock, even for variants independently confirmed in-stock
and purchasable through the real storefront checkout (verified across many
products and fresh carts on 2026-08-18). The legacy endpoint adds the
identical variants successfully. Cart ids are shared between both endpoints,
so this is a routing choice, not a hard cutover - see _normalize_cart (kept,
currently unused) and _add_to_cart to move cart operations back to UCP once
Shopify fixes the bug, or before the 2026-08-31 deadline forces the legacy
path to stop working either way.

Usage:
    python mcp_server/shopify_mcp_server.py              # stdio MCP server
    python mcp_server/shopify_mcp_server.py --selftest    # smoke test, no MCP client needed

Environment variables:
    SHOPIFY_STORE_DOMAIN - the store's *.myshopify.com domain (required)

Request-shape notes (not documented by UCP itself - reverse-engineered from
live validation errors, since UCP's schemas describe the *resource* shape
but not the *request envelope*, which differs per operation):
  - search_catalog's filters.price.{min,max} are integers in minor units
    (cents) - confirmed via ucp.dev's price_filter.json schema.
  - The shapes below are only used by the kept-but-inactive UCP cart code in
    _normalize_cart, for a future revert:
      - create_cart: {meta, cart: {line_items: [{item: {id: variant_gid}, quantity}]}}.
        Server generates the line item's own id/totals - don't send them.
      - get_cart: {meta, id: cart_id} - id is TOP-LEVEL, unlike create_cart.
      - update_cart: {meta, id: cart_id, cart: {line_items: [...]}} - both the
        top-level id AND the nested cart.line_items are required. line_items
        is a FULL REPLACEMENT array, not incremental.
      - Cart line items carry item.price as a raw integer (cents), unlike
        every other endpoint's {amount, currency} dict.
"""

from __future__ import annotations

import warnings

# See shop_mcp_server.py for why this must run before any import that could
# ever emit a warning: a stdio MCP server must never write anything but
# protocol JSON to stdout, and pydantic_settings (an 'mcp' package dependency)
# emits one at FastMCP(...) construction time.
warnings.simplefilter("ignore")

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

STORE_DOMAIN = os.environ.get("SHOPIFY_STORE_DOMAIN", "YOUR-STORE.myshopify.com")
UCP_ENDPOINT = f"https://{STORE_DOMAIN}/api/ucp/mcp"
LEGACY_ENDPOINT = f"https://{STORE_DOMAIN}/api/mcp"
UCP_AGENT_PROFILE = (
    "https://shopify.dev/ucp/agent-profiles/examples/2026-04-08/valid-with-capabilities.json"
)
DEFAULT_TIMEOUT = 20.0
# Lives under ~/.hermes/ (not bare home) so it survives container restarts on the
# Oracle deployment, where only ~/.hermes is bind-mounted as persistent storage -
# a bare-home file would silently reset to empty on every container recreate.
CART_CACHE_FILE = Path.home() / ".hermes" / "shopify_cart.json"


def _client() -> httpx.AsyncClient:
    """httpx client for talking to Shopify. trust_env=False for the same
    reason as shop_mcp_server.py: never let a stray system proxy break a
    tool call silently."""
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, trust_env=False)


def _ucp_meta() -> Dict[str, Any]:
    return {"ucp-agent": {"profile": UCP_AGENT_PROFILE}}


# ---------------------------------------------------------------------------
# JSON-RPC transport
# ---------------------------------------------------------------------------


class ShopifyToolError(RuntimeError):
    """Raised when a Shopify MCP tool call fails (protocol- or tool-level)."""


async def _jsonrpc_call(endpoint: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """POST a JSON-RPC 2.0 tools/call request and return the normalized
    result payload (a plain dict). Deprecation notices are logged to stderr
    (never stdout - see module docstring) rather than surfaced to the model
    on every call."""
    body = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {"name": tool_name, "arguments": arguments},
    }
    async with _client() as client:
        resp = await client.post(endpoint, json=body)
        resp.raise_for_status()
        envelope = resp.json()

    if "error" in envelope:
        raise ShopifyToolError(f"{tool_name}: JSON-RPC error: {envelope['error']}")

    result = envelope.get("result", {})
    content_items = result.get("content", [])

    for item in content_items:
        text = item.get("text", "") if isinstance(item, dict) else ""
        if text.startswith("DEPRECATION NOTICE"):
            print(f"[shopify-mcp] {text}", file=sys.stderr)

    if result.get("isError"):
        messages = [
            item.get("text", "")
            for item in content_items
            if isinstance(item, dict) and not item.get("text", "").startswith("DEPRECATION NOTICE")
        ]
        raise ShopifyToolError(f"{tool_name}: {'; '.join(messages) or 'tool returned isError=true'}")

    if "structuredContent" in result:
        return result["structuredContent"]

    for item in content_items:
        text = item.get("text", "") if isinstance(item, dict) else ""
        if not text or text.startswith("DEPRECATION NOTICE"):
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw_text": text}

    return {}


# ---------------------------------------------------------------------------
# Cart-id persistence (~/.hermes_shopify_cart.json, keyed by store domain) -
# mirrors how the local demo shop's cart survives across Hermes sessions,
# except here Shopify's cart is server-side and only the id needs saving.
# ---------------------------------------------------------------------------


def _load_cart_id() -> Optional[str]:
    try:
        data = json.loads(CART_CACHE_FILE.read_text())
        return data.get(STORE_DOMAIN)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_cart_id(cart_id: str) -> None:
    try:
        data = json.loads(CART_CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    CART_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data[STORE_DOMAIN] = cart_id
    CART_CACHE_FILE.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Normalization - flatten Shopify's nested JSON into plain dicts so the
# model isn't parsing price_range.min.amount-style nesting on every turn.
# ---------------------------------------------------------------------------


def _cents_to_amount(minor_units: Any, currency: str) -> Optional[float]:
    if minor_units is None:
        return None
    return round(int(minor_units) / 100, 2)


def _normalize_variant(v: Dict[str, Any]) -> Dict[str, Any]:
    price = v.get("price", {})
    return {
        "variant_id": v.get("id"),
        "label": v.get("title"),
        "price": _cents_to_amount(price.get("amount"), price.get("currency", "USD")),
        "currency": price.get("currency"),
        "available": (v.get("availability") or {}).get("available"),
        "options": {o.get("name"): o.get("label") for o in v.get("options", [])},
        "checkout_url": v.get("checkout_url"),
    }


def _normalize_product(p: Dict[str, Any]) -> Dict[str, Any]:
    price_range = p.get("price_range", {})
    min_amount = (price_range.get("min") or {}).get("amount")
    max_amount = (price_range.get("max") or {}).get("amount")
    currency = (price_range.get("min") or {}).get("currency", "USD")
    variants = [_normalize_variant(v) for v in p.get("variants", [])]
    handle = p.get("handle")
    return {
        "product_id": p.get("id"),
        "name": p.get("title"),
        "product_url": f"https://{STORE_DOMAIN}/products/{handle}" if handle else None,
        "price_min": _cents_to_amount(min_amount, currency),
        "price_max": _cents_to_amount(max_amount, currency),
        "currency": currency,
        "any_available": any(v["available"] for v in variants) if variants else None,
        "variants": variants,
    }


def _normalize_cart(cart: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a UCP cart resource (see module docstring for the request
    shapes that produce this response). Currently unused - cart operations
    moved to the legacy endpoint; kept so reverting later only needs the
    call sites restored, not a rewrite."""
    currency = cart.get("currency", "USD")
    line_items = cart.get("line_items", [])
    items = []
    for li in line_items:
        item = li.get("item", {})
        # Cart line items carry price as a raw integer (cents) directly,
        # NOT the {amount, currency} dict used in catalog/product responses -
        # confirmed via a live crash, not the docs (KNOWN DEVIATIONS #8).
        price = item.get("price")
        price_amount = price.get("amount") if isinstance(price, dict) else price
        items.append(
            {
                "line_item_id": li.get("id"),
                "variant_id": item.get("id"),
                "name": item.get("title"),
                "quantity": li.get("quantity"),
                "unit_price": _cents_to_amount(price_amount, currency),
            }
        )
    totals_by_type = {t.get("type"): t.get("amount") for t in cart.get("totals", [])}
    messages = [m.get("content") for m in cart.get("messages", []) if m.get("content")]
    return {
        "cart_id": cart.get("id"),
        "items": items,
        "item_count": sum(i["quantity"] or 0 for i in items),
        "subtotal": _cents_to_amount(totals_by_type.get("subtotal"), currency),
        "total": _cents_to_amount(totals_by_type.get("total"), currency),
        "currency": currency,
        "checkout_url": cart.get("continue_url"),
        "messages": messages,
    }


def _normalize_legacy_cart(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a legacy Storefront MCP cart response (get_cart/update_cart on
    LEGACY_ENDPOINT) into the same shape _normalize_cart produces for UCP, so
    callers never need to know which endpoint actually served the cart. Unlike
    UCP, amounts here are decimal strings (e.g. "64.99"), not integer cents."""
    cart = payload.get("cart", {}) or {}
    errors = payload.get("errors") or []
    items = []
    for line in cart.get("lines", []):
        merch = line.get("merchandise", {}) or {}
        product = merch.get("product", {}) or {}
        variant_title = merch.get("title") or ""
        product_title = product.get("title") or ""
        name = f"{product_title} - {variant_title}" if variant_title else product_title
        quantity = line.get("quantity") or 0
        line_total = (line.get("cost", {}) or {}).get("total_amount", {}) or {}
        try:
            unit_price = round(float(line_total.get("amount")) / quantity, 2) if quantity else None
        except (TypeError, ValueError):
            unit_price = None
        items.append(
            {
                "line_item_id": line.get("id"),
                "variant_id": merch.get("id"),
                "name": name,
                "quantity": quantity,
                "unit_price": unit_price,
            }
        )

    def _amount(key: str) -> Optional[float]:
        try:
            return float((cart.get("cost", {}) or {}).get(key, {}).get("amount"))
        except (TypeError, ValueError, AttributeError):
            return None

    return {
        "cart_id": cart.get("id"),
        "items": items,
        "item_count": cart.get("total_quantity") or 0,
        "subtotal": _amount("subtotal_amount"),
        "total": _amount("total_amount"),
        "currency": ((cart.get("cost", {}) or {}).get("total_amount", {}) or {}).get("currency", "USD"),
        "checkout_url": cart.get("checkout_url"),
        "messages": [str(e) for e in errors],
    }


# ---------------------------------------------------------------------------
# Core tool logic - plain async functions, independent of the MCP framework,
# so --selftest can call them directly without a live MCP client/transport.
# ---------------------------------------------------------------------------


async def _search_products(
    query: str = "",
    max_price: Optional[float] = None,
    min_price: Optional[float] = None,
    country: str = "US",
    limit: int = 10,
) -> Dict[str, Any]:
    """Search the store's catalog by free text, with optional price bounds."""
    catalog: Dict[str, Any] = {
        "query": query,
        "context": {"address_country": country},
        "pagination": {"limit": limit},
    }
    if min_price is not None or max_price is not None:
        price_filter: Dict[str, Any] = {}
        if min_price is not None:
            price_filter["min"] = round(min_price * 100)
        if max_price is not None:
            price_filter["max"] = round(max_price * 100)
        catalog["filters"] = {"price": price_filter}

    payload = await _jsonrpc_call(
        UCP_ENDPOINT, "search_catalog", {"meta": _ucp_meta(), "catalog": catalog}
    )
    products = [_normalize_product(p) for p in payload.get("products", [])]
    return {"products": products, "count": len(products)}


async def _get_product(product_id: str, size: Optional[str] = None) -> Dict[str, Any]:
    """Fetch full details for one product by its Shopify gid, optionally
    pre-selecting a Size variant."""
    catalog: Dict[str, Any] = {"id": product_id}
    if size is not None:
        catalog["selected"] = [{"name": "Size", "label": str(size)}]
    payload = await _jsonrpc_call(
        UCP_ENDPOINT, "get_product", {"meta": _ucp_meta(), "catalog": catalog}
    )
    return _normalize_product(payload["product"]) if "product" in payload else payload


_NO_CART_RESPONSE = {"cart_id": None, "items": [], "item_count": 0, "message": "No cart yet - add something first."}


async def _add_to_cart(
    variant_id: str, quantity: int = 1, cart_id: Optional[str] = None
) -> Dict[str, Any]:
    """Add a variant to the cart via the legacy Storefront MCP's update_cart
    (see module docstring for why cart operations use the legacy endpoint
    instead of UCP).

    A cart that has ever had a variant rejected as sold out keeps rejecting
    that same variant on every later add, even via this endpoint and even
    though a brand-new cart accepts the identical variant immediately
    (confirmed repeatedly on 2026-08-18) - the rejection is state on the
    cart object itself, not the request. A cached cart_id can also simply
    have expired (a separate "cart does not exist" failure). Either way, a
    reused cart_id that fails is retried once against a fresh cart, but only
    when the reused cart had nothing real in it yet - if the existence check
    itself fails (cart truly gone) that counts as nothing to lose too; if it
    already holds other items, abandoning it would silently drop them, so
    the original error is raised instead."""
    resolved_cart_id = cart_id or _load_cart_id()
    args: Dict[str, Any] = {"add_items": [{"product_variant_id": variant_id, "quantity": quantity}]}
    if resolved_cart_id:
        args["cart_id"] = resolved_cart_id
    try:
        payload = await _jsonrpc_call(LEGACY_ENDPOINT, "update_cart", args)
    except ShopifyToolError:
        if not resolved_cart_id:
            raise
        try:
            current = await _jsonrpc_call(LEGACY_ENDPOINT, "get_cart", {"cart_id": resolved_cart_id})
        except ShopifyToolError:
            current = {}
        if (current.get("cart", {}) or {}).get("total_quantity"):
            raise
        fresh_args = {"add_items": [{"product_variant_id": variant_id, "quantity": quantity}]}
        payload = await _jsonrpc_call(LEGACY_ENDPOINT, "update_cart", fresh_args)
    cart = _normalize_legacy_cart(payload)
    if cart.get("cart_id"):
        _save_cart_id(cart["cart_id"])
    return cart


async def _view_cart(cart_id: Optional[str] = None) -> Dict[str, Any]:
    """View the cart via the legacy Storefront MCP's get_cart (see module
    docstring for why). Uses the persisted cart id if none is given, so the
    model doesn't need to remember it across turns/sessions."""
    resolved_cart_id = cart_id or _load_cart_id()
    if not resolved_cart_id:
        return dict(_NO_CART_RESPONSE)
    payload = await _jsonrpc_call(LEGACY_ENDPOINT, "get_cart", {"cart_id": resolved_cart_id})
    return _normalize_legacy_cart(payload)


async def _remove_from_cart(line_item_id: str, cart_id: Optional[str] = None) -> Dict[str, Any]:
    """Remove a line item from the cart via the legacy Storefront MCP's
    update_cart (see module docstring for why). Unlike UCP, this takes the
    line id to remove directly via remove_line_ids - no need to fetch and
    resubmit the full cart."""
    resolved_cart_id = cart_id or _load_cart_id()
    if not resolved_cart_id:
        raise ShopifyToolError("No cart_id available - nothing to remove from.")
    payload = await _jsonrpc_call(
        LEGACY_ENDPOINT, "update_cart", {"cart_id": resolved_cart_id, "remove_line_ids": [line_item_id]}
    )
    return _normalize_legacy_cart(payload)


async def _ask_store_policy(query: str) -> Dict[str, Any]:
    """Search the store's policies/FAQs (returns, shipping, etc.)."""
    payload = await _jsonrpc_call(LEGACY_ENDPOINT, "search_shop_policies_and_faqs", {"query": query})
    if isinstance(payload, list):
        return {"results": payload}
    if "_raw_text" in payload:
        return {"results": payload["_raw_text"]}
    return payload


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP

    _MCP_IMPORT_ERROR: Optional[Exception] = None
except ImportError as exc:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]
    _MCP_IMPORT_ERROR = exc


def build_mcp_server() -> "FastMCP":
    if FastMCP is None:
        raise RuntimeError(
            "The 'mcp' package is required to run the stdio server. "
            "Install it with: pip install mcp"
        ) from _MCP_IMPORT_ERROR

    mcp = FastMCP("hermes-shopify-store")

    @mcp.tool()
    async def search_products(
        query: str = "",
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
        country: str = "US",
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Search the real Shopify store's catalog. Prices are in dollars, not cents.
        No images - each result has product_url (the real product page) and each
        variant has checkout_url (a direct add-to-cart link). Include both as plain
        text links when showing products, not images."""
        return await _search_products(query, max_price, min_price, country, limit)

    @mcp.tool()
    async def get_product(product_id: str, size: Optional[str] = None) -> Dict[str, Any]:
        """Get full details for one product by its Shopify product id (gid://...), optionally selecting a size.
        No images - include product_url (the product page) and each variant's
        checkout_url (direct add-to-cart link) as plain text links."""
        return await _get_product(product_id, size)

    @mcp.tool()
    async def add_to_cart(variant_id: str, quantity: int = 1, cart_id: Optional[str] = None) -> Dict[str, Any]:
        """Add a product variant to the cart. Returns the cart including its checkout_url."""
        return await _add_to_cart(variant_id, quantity, cart_id)

    @mcp.tool()
    async def view_cart(cart_id: Optional[str] = None) -> Dict[str, Any]:
        """View the current cart, including its checkout_url for completing a real purchase."""
        return await _view_cart(cart_id)

    @mcp.tool()
    async def remove_from_cart(line_item_id: str, cart_id: Optional[str] = None) -> Dict[str, Any]:
        """Remove a line item from the cart (line_item_id comes from view_cart's items[].line_item_id)."""
        return await _remove_from_cart(line_item_id, cart_id)

    @mcp.tool()
    async def ask_store_policy(query: str) -> Dict[str, Any]:
        """Ask about the store's policies or FAQs (returns, shipping, payment, etc.)."""
        return await _ask_store_policy(query)

    return mcp


# ---------------------------------------------------------------------------
# Self-test: exercises the core tool functions against the real store.
# ---------------------------------------------------------------------------


async def _run_selftest() -> bool:
    print(f"[selftest] SHOPIFY_STORE_DOMAIN = {STORE_DOMAIN}")
    print(f"[selftest] UCP endpoint    = {UCP_ENDPOINT}")
    print(f"[selftest] legacy endpoint = {LEGACY_ENDPOINT}")

    def restricted_hint(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in (401, 403, 404):
                return (
                    f" (HTTP {code} - this store may be restricting Storefront MCP access; "
                    "check Settings > Apps > Storefront API access, or that the domain is correct)"
                )
        return ""

    # 1. Store reachability + catalog search (broad query so this doesn't
    # depend on specific demo products existing yet).
    try:
        results = await _search_products(query="", limit=5)
        products = results["products"]
        assert len(products) > 0, "catalog search returned zero products - is the store empty, or are products unpublished from the Online Store sales channel?"
        print(f"[selftest] PASS: search_products -> {len(products)} result(s)")
        target = next((p for p in products if p["variants"]), products[0])
        first_product_id = target["product_id"]
    except Exception as exc:
        print(f"[selftest] FAIL: search_products: {exc}{restricted_hint(exc)}")
        return False

    # 2. Get product
    try:
        product = await _get_product(first_product_id)
        assert product["product_id"] == first_product_id
        print(f"[selftest] PASS: get_product -> {product['name']}")
        variants = product["variants"]
        assert variants, "product has no variants to add to cart"
        first_variant_id = variants[0]["variant_id"]
    except Exception as exc:
        print(f"[selftest] FAIL: get_product: {exc}{restricted_hint(exc)}")
        return False

    ok = True

    # 3. Add to cart
    try:
        cart = await _add_to_cart(first_variant_id, quantity=1)
        assert cart["item_count"] >= 1, f"cart came back empty - store messages: {cart.get('messages')}"
        print(f"[selftest] PASS: add_to_cart -> item_count={cart['item_count']}")
    except Exception as exc:
        print(f"[selftest] FAIL: add_to_cart: {exc}{restricted_hint(exc)}")
        return False

    # 4. View cart + surface checkout URL
    line_item_id = None
    try:
        cart = await _view_cart()
        matching = [i for i in cart["items"] if i["variant_id"] == first_variant_id]
        assert matching, "just-added variant not found in cart"
        line_item_id = matching[0]["line_item_id"]
        print(f"[selftest] PASS: view_cart -> {cart['item_count']} item(s), total {cart.get('total')} {cart.get('currency')}")
        print(f"[selftest] CHECKOUT URL: {cart.get('checkout_url')}")
    except Exception as exc:
        print(f"[selftest] FAIL: view_cart: {exc}{restricted_hint(exc)}")
        ok = False

    # 5. Remove item (cleanup)
    try:
        assert line_item_id, "no line_item_id from view_cart step - can't test removal"
        cart = await _remove_from_cart(line_item_id)
        print(f"[selftest] PASS: remove_from_cart -> item_count={cart['item_count']}")
    except Exception as exc:
        print(f"[selftest] FAIL: remove_from_cart: {exc}{restricted_hint(exc)}")
        ok = False

    # 6. Policy search (informational - not fatal if the store has none configured)
    try:
        policy = await _ask_store_policy("return policy")
        print(f"[selftest] PASS: ask_store_policy -> {policy}")
    except Exception as exc:
        print(f"[selftest] WARN: ask_store_policy: {exc}{restricted_hint(exc)}")

    # 7. MCP package availability
    if FastMCP is None:
        print("[selftest] WARN: 'mcp' package not installed - stdio server won't start.")
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
    parser = argparse.ArgumentParser(description="Hermes Shopify store MCP server")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Run a standalone smoke test against the real store and exit",
    )
    # parse_known_args, not parse_args - see shop_mcp_server.py for why:
    # `hermes mcp add ... --env KEY=VAL` appends that flag as literal argv
    # to the spawned process too, which a strict parser would reject and
    # exit(2) on before the server ever starts.
    args, _unknown_args = parser.parse_known_args()

    if args.selftest:
        passed = asyncio.run(_run_selftest())
        if passed:
            print("\n[selftest] ALL CHECKS PASSED")
            sys.exit(0)
        else:
            print("\n[selftest] SOME CHECKS FAILED")
            sys.exit(1)

    mcp = build_mcp_server()
    mcp.run()


if __name__ == "__main__":
    main()
