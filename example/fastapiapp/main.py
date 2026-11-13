from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, status

from models import (
    Customer,
    Order,
    OrderItem,
    Product,
    ProductCreate,
    ProductStatus,
    ProductUpdate,
)

app = FastAPI(title="FastAPI Shop Example", version="0.1.0")
products_router = APIRouter(prefix="/products", tags=["products"])
customers_router = APIRouter(prefix="/customers", tags=["customers"])
orders_router = APIRouter(prefix="/orders", tags=["orders"])


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Service health check."""
    return {"status": "ok"}


@products_router.get("", response_model=list[Product])
def list_products(
    status_filter: Optional[ProductStatus] = Query(None, alias="status"),
    q: Optional[str] = Query(None, description="Search by name"),
    limit: int = Query(20, ge=1, le=100),
) -> list[Product]:
    """List products with filters."""
    return []


@products_router.post("", response_model=Product, status_code=status.HTTP_201_CREATED)
def create_product(product: ProductCreate) -> Product:
    """Create a product."""
    return Product(id=1, **product.model_dump())


@products_router.get("/{product_id}", response_model=Product)
def get_product(product_id: int) -> Product:
    """Get a product by id."""
    return Product(id=product_id, sku="SKU-1", name="Sample", price=10.0)


@products_router.patch("/{product_id}", response_model=Product)
def update_product(product_id: int, patch: ProductUpdate) -> Product:
    """Partially update a product."""
    return Product(id=product_id, sku="SKU-1", name=patch.name or "Sample", price=patch.price or 10.0)


@products_router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int) -> None:
    """Delete a product."""
    return None


@customers_router.get("/{customer_id}", response_model=Customer)
def get_customer(customer_id: int) -> Customer:
    """Get a customer profile."""
    return Customer(id=customer_id, email="customer@example.com", full_name="Customer One")


@customers_router.get("/{customer_id}/orders", response_model=list[Order])
def list_customer_orders(customer_id: int, include_draft: bool = Query(False)) -> list[Order]:
    """List orders for a customer."""
    return [
        Order(
            id=1,
            customer_id=customer_id,
            items=[OrderItem(product_id=1, quantity=2, unit_price=10.0)],
            total=20.0,
        )
    ]


@orders_router.post("", response_model=Order, status_code=status.HTTP_201_CREATED)
def create_order(
    order: Order,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> Order:
    """Create an order."""
    if not order.items:
        raise HTTPException(status_code=400, detail="Order must contain at least one item")
    return order


@orders_router.get("/{order_id}", response_model=Order)
def get_order(order_id: int) -> Order:
    """Get an order by id."""
    return Order(
        id=order_id,
        customer_id=1,
        items=[OrderItem(product_id=1, quantity=1, unit_price=10.0)],
        total=10.0,
    )


app.include_router(products_router)
app.include_router(customers_router)
app.include_router(orders_router)
