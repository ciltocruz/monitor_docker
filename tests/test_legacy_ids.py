"""legacy_ids must reproduce the entity_ids/names of the YAML-only 1.20 release.

The reference functions below are the exact formulas of monitor_docker 1.20
(sensor.py/switch.py/button.py on master), copied so the comparison does not
depend on the code under test.
"""

from __future__ import annotations

import re

import pytest

from homeassistant.util import slugify

from custom_components.monitor_docker import legacy_ids

# --- reference: monitor_docker 1.20 ------------------------------------------


def _find_rename(d, item):
    for k in d:
        if re.match(k, item):
            return d[k]
    return item


def _ref(config, instance, cname, sensor_name, kind):
    prefix = instance
    if config.get("prefix"):
        prefix = config["prefix"]
    alias_entityid = cname
    if config.get("rename_entity"):
        alias_entityid = _find_rename(config.get("rename", {}), cname)
    alias_name = _find_rename(config.get("rename", {}), cname)
    if kind == "docker_sensor":
        return slugify(f"{prefix}_{sensor_name}"), sensor_name
    if kind == "allinone":
        return (
            slugify(f"{prefix}_{alias_entityid}"),
            config.get("sensorname", "{name} {sensor}").format(
                name=alias_name, sensorname="", sensor=""
            ),
        )
    if kind == "container_sensor":
        return (
            slugify(f"{prefix}_{alias_entityid}_{sensor_name}"),
            config.get("sensorname", "{name} {sensor}").format(
                name=alias_name, sensorname=sensor_name, sensor=sensor_name
            ),
        )
    if kind == "switch":
        return (
            slugify(f"{prefix}_{alias_entityid}"),
            config.get("switchname", "{name}").format(name=alias_name),
        )
    if kind == "button":
        return (
            slugify(prefix + "_" + cname + "_restart"),
            config.get("buttonname", "{name} Restart").format(name=alias_name),
        )
    raise ValueError(kind)


CONFIGS = [
    pytest.param({}, id="defaults"),
    pytest.param({"prefix": "Pi"}, id="prefix"),
    pytest.param({"rename": {"homeassistant": "Home Assistant"}}, id="rename-name-only"),
    pytest.param(
        {"rename": {"homeassistant": "Home Assistant"}, "rename_entity": True},
        id="rename-entity",
    ),
    pytest.param(
        {"rename": {"^matter.*": "Matter"}, "rename_entity": True}, id="rename-regex"
    ),
    pytest.param(
        {
            "sensorname": "{name}: {sensorname}",
            "switchname": "Docker {name}",
            "buttonname": "{name} neu starten",
        },
        id="name-formats",
    ),
]
CNAMES = ["homeassistant", "matter-server", "nginx-proxy-manager-app"]


@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize("instance", ["Docker", "My Host"])
@pytest.mark.parametrize("cname", CNAMES)
def test_container_ids_match_1_20(config, instance, cname) -> None:
    """Container sensors, allinone, switch and button match 1.20."""
    assert (
        legacy_ids.container_sensor_object_id(config, instance, cname, "1CPU"),
        legacy_ids.container_sensor_name(config, cname, "1CPU"),
    ) == tuple(
        x.strip() for x in _ref(config, instance, cname, "1CPU", "container_sensor")
    )
    oid, name = _ref(config, instance, cname, None, "allinone")
    assert legacy_ids.container_sensor_object_id(config, instance, cname, None) == oid
    assert legacy_ids.container_sensor_name(config, cname, None) == name.strip()
    assert (
        legacy_ids.switch_object_id(config, instance, cname),
        legacy_ids.switch_name(config, cname),
    ) == _ref(config, instance, cname, None, "switch")
    assert (
        legacy_ids.button_object_id(config, instance, cname),
        legacy_ids.button_name(config, cname),
    ) == _ref(config, instance, cname, None, "button")


@pytest.mark.parametrize("config", CONFIGS)
def test_docker_sensor_ids_match_1_20(config) -> None:
    """Engine-wide sensors match 1.20."""
    oid, _ = _ref(config, "Docker", "", "1CPU", "docker_sensor")
    assert legacy_ids.docker_sensor_object_id(config, "Docker", "1CPU") == oid


def test_real_install() -> None:
    """Entity ids of a real 1.20 install (name: Docker, no prefix/rename_entity)."""
    config = {"rename": {"homeassistant": "Home Assistant"}}
    assert legacy_ids.container_sensor_object_id(
        config, "Docker", "esphome", "1CPU"
    ) == "docker_esphome_1cpu"
    assert legacy_ids.container_sensor_object_id(
        config, "Docker", "homeassistant", "Status"
    ) == "docker_homeassistant_status"
    assert legacy_ids.docker_sensor_object_id(config, "Docker", "1CPU") == "docker_1cpu"
    assert legacy_ids.switch_object_id(config, "Docker", "portainer") == "docker_portainer"
    assert legacy_ids.switch_name(config, "homeassistant") == "Home Assistant"
