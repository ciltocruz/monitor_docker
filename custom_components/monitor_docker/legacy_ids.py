"""Entity ids and names of the YAML-only releases (up to 1.20).

Those releases had no unique_ids and forced the entity_ids below. Config
entries (created in the UI or imported from configuration.yaml) suggest
exactly the same entity_ids and names, so existing dashboards, automations
and history keep working after the switch to a config entry.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from homeassistant.util import slugify

from .const import (
    CONF_BUTTONNAME,
    CONF_PREFIX,
    CONF_RENAME,
    CONF_RENAME_ENITITY,
    CONF_SENSORNAME,
    CONF_SWITCHNAME,
    DEFAULT_BUTTONNAME,
    DEFAULT_SENSORNAME,
    DEFAULT_SWITCHNAME,
)


def find_rename(rename: Mapping[str, str] | None, item: str) -> str:
    """Return the alias of the first rename pattern matching item."""
    for pattern, alias in (rename or {}).items():
        if re.match(pattern, item):
            return alias
    return item


def prefix(config: Mapping[str, Any], instance: str) -> str:
    """Entity_id prefix: the configured prefix, else the instance name."""
    return config.get(CONF_PREFIX) or instance


def alias_entityid(config: Mapping[str, Any], cname: str) -> str:
    """Container part of the entity_id (renamed only with rename_entity)."""
    if config.get(CONF_RENAME_ENITITY):
        return find_rename(config.get(CONF_RENAME), cname)
    return cname


def alias_name(config: Mapping[str, Any], cname: str) -> str:
    """Container part of the friendly name."""
    return find_rename(config.get(CONF_RENAME), cname)


def docker_sensor_object_id(
    config: Mapping[str, Any], instance: str, sensor_name: str
) -> str:
    """Object id of an engine-wide sensor, e.g. docker_containers_running."""
    return slugify(f"{prefix(config, instance)}_{sensor_name}")


def container_sensor_object_id(
    config: Mapping[str, Any], instance: str, cname: str, sensor_name: str | None
) -> str:
    """Object id of a container sensor (sensor_name None for allinone)."""
    object_id = f"{prefix(config, instance)}_{alias_entityid(config, cname)}"
    if sensor_name:
        object_id = f"{object_id}_{sensor_name}"
    return slugify(object_id)


def container_sensor_name(
    config: Mapping[str, Any], cname: str, sensor_name: str | None
) -> str:
    """Friendly name of a container sensor (sensor_name None for allinone)."""
    name_format = config.get(CONF_SENSORNAME) or DEFAULT_SENSORNAME
    sensor = sensor_name or ""
    return name_format.format(
        name=alias_name(config, cname), sensorname=sensor, sensor=sensor
    ).strip()


def switch_object_id(config: Mapping[str, Any], instance: str, cname: str) -> str:
    """Object id of a container switch."""
    return slugify(f"{prefix(config, instance)}_{alias_entityid(config, cname)}")


def switch_name(config: Mapping[str, Any], cname: str) -> str:
    """Friendly name of a container switch."""
    name_format = config.get(CONF_SWITCHNAME) or DEFAULT_SWITCHNAME
    return name_format.format(name=alias_name(config, cname))


def button_object_id(config: Mapping[str, Any], instance: str, cname: str) -> str:
    """Object id of a container restart button (never renamed)."""
    return slugify(f"{prefix(config, instance)}_{cname}_restart")


def button_name(config: Mapping[str, Any], cname: str) -> str:
    """Friendly name of a container restart button."""
    name_format = config.get(CONF_BUTTONNAME) or DEFAULT_BUTTONNAME
    return name_format.format(name=alias_name(config, cname))
