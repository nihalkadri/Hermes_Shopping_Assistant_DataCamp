"""Curated product catalog for the Hermes demo shop.

10 running-shoe products spanning a few brands and price points so the
"Find Nike running shoes under $100, size 10" style demo prompts have
real matches (and real non-matches) to reason over.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from shop_backend.models import Product

PRODUCTS: List[Product] = [
    Product(
        id="shoe-001",
        name="Nike Air Zoom Pegasus 40",
        brand="Nike",
        category="running shoes",
        price=89.99,
        sizes=[7, 8, 9, 10, 11, 12],
        color="Black/White",
        rating=4.6,
        reviews_count=1820,
        in_stock=True,
        description="A responsive daily trainer with Nike React foam for road running.",
        image_url="https://example.com/images/shoe-001.jpg",
    ),
    Product(
        id="shoe-002",
        name="Nike Revolution 6",
        brand="Nike",
        category="running shoes",
        price=64.99,
        sizes=[6, 7, 8, 9, 10, 11],
        color="Grey/Volt",
        rating=4.3,
        reviews_count=942,
        in_stock=True,
        description="Lightweight, budget-friendly trainer with breathable mesh.",
        image_url="https://example.com/images/shoe-002.jpg",
    ),
    Product(
        id="shoe-003",
        name="Nike Air Max 270",
        brand="Nike",
        category="running shoes",
        price=139.99,
        sizes=[8, 9, 10, 11, 12, 13],
        color="White/Blue",
        rating=4.5,
        reviews_count=2310,
        in_stock=True,
        description="Lifestyle runner with Nike's largest heel Air unit for all-day comfort.",
        image_url="https://example.com/images/shoe-003.jpg",
    ),
    Product(
        id="shoe-004",
        name="Nike Free RN 5.0",
        brand="Nike",
        category="running shoes",
        price=79.99,
        sizes=[7, 8, 9, 10, 11],
        color="Black/Grey",
        rating=4.2,
        reviews_count=615,
        in_stock=False,
        description="Flexible, barefoot-feel trainer for natural motion running.",
        image_url="https://example.com/images/shoe-004.jpg",
    ),
    Product(
        id="shoe-005",
        name="Adidas Ultraboost 22",
        brand="Adidas",
        category="running shoes",
        price=149.99,
        sizes=[8, 9, 10, 11, 12],
        color="Core Black",
        rating=4.7,
        reviews_count=3105,
        in_stock=True,
        description="Premium energy-return trainer with Boost midsole cushioning.",
        image_url="https://example.com/images/shoe-005.jpg",
    ),
    Product(
        id="shoe-006",
        name="Adidas Duramo SL",
        brand="Adidas",
        category="running shoes",
        price=59.99,
        sizes=[7, 8, 9, 10, 11, 12],
        color="Blue/White",
        rating=4.1,
        reviews_count=488,
        in_stock=True,
        description="Everyday value trainer with a cushioned Cloudfoam midsole.",
        image_url="https://example.com/images/shoe-006.jpg",
    ),
    Product(
        id="shoe-007",
        name="Adidas Adizero Adios Pro 3",
        brand="Adidas",
        category="running shoes",
        price=224.99,
        sizes=[9, 10, 11, 12],
        color="Solar Red",
        rating=4.8,
        reviews_count=276,
        in_stock=True,
        description="Carbon-plated racing shoe built for marathon PRs.",
        image_url="https://example.com/images/shoe-007.jpg",
    ),
    Product(
        id="shoe-008",
        name="New Balance Fresh Foam X 1080v12",
        brand="New Balance",
        category="running shoes",
        price=164.99,
        sizes=[8, 9, 10, 11, 12, 13],
        color="Grey/Orange",
        rating=4.6,
        reviews_count=940,
        in_stock=True,
        description="Plush daily trainer with a wide, stable base for long miles.",
        image_url="https://example.com/images/shoe-008.jpg",
    ),
    Product(
        id="shoe-009",
        name="ASICS Gel-Nimbus 25",
        brand="ASICS",
        category="running shoes",
        price=154.99,
        sizes=[7, 8, 9, 10, 11],
        color="Black/Silver",
        rating=4.7,
        reviews_count=1188,
        in_stock=True,
        description="Max-cushion neutral trainer with Gel technology for shock absorption.",
        image_url="https://example.com/images/shoe-009.jpg",
    ),
    Product(
        id="shoe-010",
        name="Puma Velocity Nitro 2",
        brand="Puma",
        category="running shoes",
        price=94.99,
        sizes=[8, 9, 10, 11],
        color="White/Purple",
        rating=4.3,
        reviews_count=352,
        in_stock=True,
        description="Nitro-foam trainer that balances speed and everyday comfort.",
        image_url="https://example.com/images/shoe-010.jpg",
    ),
]

_PRODUCTS_BY_ID: Dict[str, Product] = {p.id: p for p in PRODUCTS}


def get_product_by_id(product_id: str) -> Optional[Product]:
    """Look up a single product by id, or None if not found."""
    return _PRODUCTS_BY_ID.get(product_id)


def search_products(
    query: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    size: Optional[int] = None,
    in_stock_only: bool = False,
) -> List[Product]:
    """Filter the catalog by the given criteria (all filters are AND'd, case-insensitive)."""
    results = PRODUCTS

    if query:
        q = query.lower()
        results = [
            p
            for p in results
            if q in p.name.lower() or q in p.brand.lower() or q in p.description.lower()
        ]
    if brand:
        b = brand.lower()
        results = [p for p in results if p.brand.lower() == b]
    if category:
        c = category.lower()
        results = [p for p in results if c in p.category.lower()]
    if min_price is not None:
        results = [p for p in results if p.price >= min_price]
    if max_price is not None:
        results = [p for p in results if p.price <= max_price]
    if size is not None:
        results = [p for p in results if size in p.sizes]
    if in_stock_only:
        results = [p for p in results if p.in_stock]

    return results
