from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProductStatus(str, Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=1000)
    price: float = Field(..., gt=0)
    status: ProductStatus = ProductStatus.draft
    tags: list[str] = Field(default_factory=list)


class ProductCreate(ProductBase):
    sku: str = Field(..., min_length=3, max_length=40)


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(None, gt=0)
    status: Optional[ProductStatus] = None
    tags: Optional[list[str]] = None


class Product(ProductBase):
    id: int
    sku: str


class Customer(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    active: bool = True


class OrderItem(BaseModel):
    product_id: int
    quantity: int = Field(..., ge=1)
    unit_price: float = Field(..., gt=0)


class Order(BaseModel):
    id: int
    customer_id: int
    items: list[OrderItem]
    total: float
    note: Optional[str] = None
