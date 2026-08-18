"""FastAPI REST API for the Hermes demo shop.

Run with:
    uvicorn shop_backend.main:app --reload --port 8000

Endpoints:
    GET  /health                 - service health check
    GET  /products                - search/filter the catalog
    GET  /products/{product_id}   - fetch a single product
    POST /cart/items               - add an item to the cart
    GET  /cart                    - view the cart
    DELETE /cart/items/{item_id}  - remove one cart line item
    DELETE /cart                  - clear the cart

The cart is a single in-memory cart (process-lifetime, not per-session)
by design: it is meant to persist across separate Hermes chat sessions
during the demo so "What's in my cart?" works from a fresh session.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from shop_backend.data import PRODUCTS, get_product_by_id, search_products
from shop_backend.models import (
    CartAddRequest,
    CartItem,
    CartResponse,
    HealthResponse,
    Product,
    ProductListResponse,
)

app = FastAPI(
    title="Hermes Demo Shop API",
    description="Local curated-catalog shop backend for the Hermes Agent demo.",
    version="0.1.0",
)

# Permissive CORS since this only ever runs locally for the demo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory cart storage: a single shared cart for the demo session.
_CART: List[CartItem] = []


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Basic health/readiness check."""
    return HealthResponse(
        status="ok",
        product_count=len(PRODUCTS),
        time=datetime.now(timezone.utc),
    )


@app.get("/products", response_model=ProductListResponse)
def list_products(
    q: Optional[str] = Query(None, description="Free-text search across name/brand/description"),
    brand: Optional[str] = Query(None, description="Exact brand match, e.g. 'Nike'"),
    category: Optional[str] = Query(None, description="Category substring match"),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    size: Optional[int] = Query(None, description="Required available size"),
    in_stock_only: bool = Query(False),
) -> ProductListResponse:
    """Search/filter the product catalog."""
    results = search_products(
        query=q,
        brand=brand,
        category=category,
        min_price=min_price,
        max_price=max_price,
        size=size,
        in_stock_only=in_stock_only,
    )
    return ProductListResponse(count=len(results), products=results)


@app.get("/products/{product_id}", response_model=Product)
def get_product(product_id: str) -> Product:
    """Fetch a single product by id."""
    product = get_product_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")
    return product


@app.post("/cart/items", response_model=CartResponse, status_code=201)
def add_to_cart(request: CartAddRequest) -> CartResponse:
    """Add a product to the cart."""
    product = get_product_by_id(request.product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{request.product_id}' not found")
    if request.size is not None and request.size not in product.sizes:
        raise HTTPException(
            status_code=400,
            detail=f"Size {request.size} not available for '{product.name}'. "
            f"Available sizes: {product.sizes}",
        )

    item = CartItem(
        item_id=str(uuid.uuid4())[:8],
        product_id=product.id,
        name=product.name,
        brand=product.brand,
        price=product.price,
        size=request.size,
        quantity=request.quantity,
        added_at=datetime.now(timezone.utc),
    )
    _CART.append(item)
    return _cart_response()


@app.get("/cart", response_model=CartResponse)
def view_cart() -> CartResponse:
    """View the current cart contents."""
    return _cart_response()


@app.delete("/cart/items/{item_id}", response_model=CartResponse)
def remove_cart_item(item_id: str) -> CartResponse:
    """Remove a single line item from the cart by its item_id."""
    global _CART
    before = len(_CART)
    _CART = [i for i in _CART if i.item_id != item_id]
    if len(_CART) == before:
        raise HTTPException(status_code=404, detail=f"Cart item '{item_id}' not found")
    return _cart_response()


@app.delete("/cart", response_model=CartResponse)
def clear_cart() -> CartResponse:
    """Empty the entire cart."""
    _CART.clear()
    return _cart_response()


def _cart_response() -> CartResponse:
    subtotal = round(sum(i.price * i.quantity for i in _CART), 2)
    return CartResponse(
        items=list(_CART),
        item_count=sum(i.quantity for i in _CART),
        subtotal=subtotal,
    )
