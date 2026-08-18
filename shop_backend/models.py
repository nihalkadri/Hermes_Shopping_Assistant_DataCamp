"""Pydantic models for the Hermes demo shop backend.

Covers the product catalog and a simple in-memory shopping cart used
for the local FastAPI shop portion of the demo.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class Product(BaseModel):
    """A single catalog item."""

    id: str = Field(..., description="Unique product identifier, e.g. 'shoe-001'")
    name: str = Field(..., description="Full product name")
    brand: str = Field(..., description="Brand name, e.g. 'Nike'")
    category: str = Field(..., description="Product category, e.g. 'running shoes'")
    price: float = Field(..., ge=0, description="Price in USD")
    sizes: List[int] = Field(..., description="Available sizes (US shoe sizes)")
    color: str = Field(..., description="Primary color")
    rating: float = Field(..., ge=0, le=5, description="Average rating out of 5")
    reviews_count: int = Field(..., ge=0, description="Number of reviews")
    in_stock: bool = Field(True, description="Whether the item is currently in stock")
    description: str = Field(..., description="Short marketing description")
    image_url: str = Field(..., description="Placeholder image URL")


class ProductListResponse(BaseModel):
    """Response envelope for product search/listing."""

    count: int
    products: List[Product]


class CartAddRequest(BaseModel):
    """Body for adding an item to the cart."""

    product_id: str = Field(..., description="ID of the product to add")
    size: Optional[int] = Field(None, description="Requested size, if applicable")
    quantity: int = Field(1, ge=1, description="Quantity to add")


class CartItem(BaseModel):
    """A line item stored in the cart."""

    item_id: str = Field(..., description="Unique cart line item id")
    product_id: str
    name: str
    brand: str
    price: float
    size: Optional[int] = None
    quantity: int
    added_at: datetime


class CartResponse(BaseModel):
    """Full cart contents plus computed totals."""

    items: List[CartItem]
    item_count: int
    subtotal: float


class HealthResponse(BaseModel):
    """Simple health check payload."""

    status: str
    product_count: int
    time: datetime
