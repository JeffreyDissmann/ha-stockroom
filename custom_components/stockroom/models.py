"""Typed data models for Stockroom."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class StockroomStatistics:
    """Normalized statistics returned by ``GET /statistics``."""

    total: int
    value: float
    rooms: int
    containers: int
    items: int


@dataclass(slots=True, frozen=True)
class StockroomUser:
    """Authenticated user returned by ``GET /user``."""

    user_id: int
    name: str
    email: str


@dataclass(slots=True, frozen=True)
class LinkedItem:
    """A Stockroom item linked to a Home Assistant device."""

    item_id: int
    name: str
    location_path: str
    quantity: int
    url: str
