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
    # Household-wide maintenance counters. Default to 0 so a Stockroom server
    # without the maintenance API still parses cleanly.
    maintenance_overdue: int = 0
    maintenance_due_soon: int = 0


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


@dataclass(slots=True, frozen=True)
class BatteryLinkTarget:
    """A Stockroom item linked to HA, as a candidate for battery syncing.

    Built from ``GET /home-assistant-links`` (the server's view of the link),
    already filtered to this Home Assistant instance. ``battery_type`` is the
    value Stockroom currently holds, used to avoid redundant PATCHes.
    """

    item_id: int
    ha_device_id: str | None
    ha_entity_id: str | None
    instance_id: str | None
    battery_type: str | None
