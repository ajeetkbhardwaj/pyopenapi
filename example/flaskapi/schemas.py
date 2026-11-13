from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ItemStatus(str, Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class ItemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    price: float = Field(..., gt=0)
    status: ItemStatus = ItemStatus.draft
    tags: list[str] = Field(default_factory=list)


class Item(ItemCreate):
    id: int
    owner_id: int


class User(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    is_active: bool = True


class Order(BaseModel):
    id: int
    user_id: int
    item_ids: list[int]
    total: float
    note: Optional[str] = None
