"""Sensor platform for Stockroom."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Final

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import StockroomConfigEntry, StockroomDataUpdateCoordinator
from .linking import get_link_maps

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class StockroomSensorEntityDescription(SensorEntityDescription):
    """Describe a Stockroom statistics sensor."""

    value_key: str


SENSOR_DESCRIPTIONS: Final[tuple[StockroomSensorEntityDescription, ...]] = (
    StockroomSensorEntityDescription(
        key="total_items",
        value_key="total",
        translation_key="total_items",
        icon="mdi:archive",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StockroomSensorEntityDescription(
        key="total_value",
        value_key="value",
        translation_key="total_value",
        icon="mdi:cash-multiple",
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StockroomSensorEntityDescription(
        key="rooms",
        value_key="rooms",
        translation_key="rooms",
        icon="mdi:floor-plan",
        native_unit_of_measurement="rooms",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StockroomSensorEntityDescription(
        key="containers",
        value_key="containers",
        translation_key="containers",
        icon="mdi:package-variant-closed",
        native_unit_of_measurement="containers",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    StockroomSensorEntityDescription(
        key="items",
        value_key="items",
        translation_key="items",
        icon="mdi:cube-outline",
        native_unit_of_measurement="items",
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StockroomConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Stockroom sensor entities from a config entry."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        StockroomStatisticsSensor(
            coordinator,
            entry.entry_id,
            entry.data[CONF_HOST],
            entry.data.get(CONF_NAME, DEFAULT_NAME),
            description,
        )
        for description in SENSOR_DESCRIPTIONS
    ]

    device_registry = dr.async_get(hass)
    ha_device_to_item, _ = get_link_maps(entry)
    for ha_device_id, item_id in ha_device_to_item.items():
        if linked_device := device_registry.async_get(ha_device_id):
            entities.append(
                StockroomLinkedItemSensor(
                    coordinator,
                    entry.entry_id,
                    ha_device_id,
                    item_id,
                    linked_device,
                )
            )

    async_add_entities(entities)


class StockroomStatisticsSensor(
    CoordinatorEntity[StockroomDataUpdateCoordinator], SensorEntity
):
    """A Stockroom inventory statistics sensor."""

    entity_description: StockroomSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StockroomDataUpdateCoordinator,
        config_entry_id: str,
        host: str,
        display_name: str,
        description: StockroomSensorEntityDescription,
    ) -> None:
        """Initialize the statistics sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{config_entry_id}_{description.key}"
        self._attr_suggested_object_id = f"stockroom_{description.key}"
        self._attr_native_value = self._resolve_native_value()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry_id)},
            name=display_name or DEFAULT_NAME,
            manufacturer="Stockroom",
            configuration_url=host,
        )

    def _resolve_native_value(self) -> int | float | None:
        """Return the current value for this statistics sensor."""
        if self.coordinator.data is None:
            return None
        return getattr(
            self.coordinator.data.statistics, self.entity_description.value_key
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self._resolve_native_value()
        self.async_write_ha_state()


class StockroomLinkedItemSensor(
    CoordinatorEntity[StockroomDataUpdateCoordinator], SensorEntity
):
    """Diagnostic sensor exposing the linked Stockroom item for one HA device."""

    _attr_icon = "mdi:link-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "linked_item"

    def __init__(
        self,
        coordinator: StockroomDataUpdateCoordinator,
        config_entry_id: str,
        ha_device_id: str,
        item_id: int,
        linked_device: dr.DeviceEntry,
    ) -> None:
        """Initialize the linked-item sensor."""
        super().__init__(coordinator)
        self._ha_device_id = ha_device_id
        self._item_id = item_id
        self._attr_unique_id = f"{config_entry_id}_{ha_device_id}_linked_item"
        self._attr_native_value = item_id

        device_info: DeviceInfo = DeviceInfo()
        if linked_device.identifiers:
            device_info["identifiers"] = set(linked_device.identifiers)
        if linked_device.connections:
            device_info["connections"] = set(linked_device.connections)
        self._attr_device_info = device_info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return linked Stockroom item details for automations."""
        attributes: dict[str, Any] = {
            "ha_device_id": self._ha_device_id,
            "stockroom_item_id": self._item_id,
            "url": self.coordinator.api.get_item_url(self._item_id),
        }
        if self.coordinator.data is not None:
            linked = self.coordinator.data.linked_items.get(self._ha_device_id)
            if linked is not None:
                attributes["item_name"] = linked.name
                attributes["location_path"] = linked.location_path
                attributes["quantity"] = linked.quantity
        return attributes
