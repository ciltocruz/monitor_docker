"""Config flow for integration."""

from __future__ import annotations

import copy
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    Mapping,
)
from homeassistant.const import (
    CONF_MONITORED_CONDITIONS,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_URL,
)
from homeassistant.helpers import issue_registry as ir, selector

from .const import (
    API,
    CONF_BUTTONENABLED,
    CONF_CERTPATH,
    CONF_CONTAINERS,
    CONF_CONTAINERS_EXCLUDE,
    CONF_MEMORYCHANGE,
    CONF_MONITORED_CONTAINER_CONDITIONS,
    CONF_MONITORED_DOCKER_CONDITIONS,
    CONF_PRECISION_CPU,
    CONF_PRECISION_DISK_MB,
    CONF_PRECISION_MEMORY_MB,
    CONF_PRECISION_MEMORY_PERCENTAGE,
    CONF_PRECISION_NETWORK_KB,
    CONF_PRECISION_NETWORK_MB,
    CONF_RETRY,
    CONF_VERSION,
    CONF_SWITCHENABLED,
    CONTAINER_MONITOR_LIST,
    CONTAINER_PRE_SELECTION,
    DEFAULT_NAME,
    DEFAULT_RETRY,
    DEFAULT_SCAN_INTERVAL,
    DOCKER_MONITOR_LIST,
    DOCKER_PRE_SELECTION,
    DOMAIN,
    PRECISION,
)
from .helpers import DockerAPI

_LOGGER = logging.getLogger(__name__)


# Container selection modes for the containers step (not stored in entry data)
FIELD_CONTAINERS_MODE = "containers_mode"
FIELD_CONTAINERS_MODE_EXCLUSION = CONF_CONTAINERS_EXCLUDE
MODE_SELECTED = "selected"
MODE_ALL_EXCEPT = "all_except"

DEFAULT_ENTRY_DATA: dict[str, Any] = {
    # User
    CONF_NAME: DEFAULT_NAME,
    CONF_URL: "",
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    CONF_CERTPATH: "",
    CONF_RETRY: DEFAULT_RETRY,
    CONF_VERSION: "auto",
    # Containers
    CONF_CONTAINERS: [],
    CONF_CONTAINERS_EXCLUDE: [],  # Not relevant as all are selected
    # Conditions
    CONF_MONITORED_CONDITIONS: [],
    CONF_SWITCHENABLED: True,
    CONF_BUTTONENABLED: False,
    CONF_MEMORYCHANGE: 100,
    CONF_PRECISION_CPU: PRECISION,
    CONF_PRECISION_DISK_MB: PRECISION,
    CONF_PRECISION_MEMORY_MB: PRECISION,
    CONF_PRECISION_MEMORY_PERCENTAGE: PRECISION,
    CONF_PRECISION_NETWORK_KB: PRECISION,
    CONF_PRECISION_NETWORK_MB: PRECISION,
}


class DockerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Docker config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Start every flow with its own copy of the defaults."""
        self.data: dict[str, Any] = copy.deepcopy(DEFAULT_ENTRY_DATA)

    options = None
    _docker_api = None
    _containers_mode: str | None = None
    # True when this flow opened its own test connection (closed when done)
    _owns_docker_api = False
    _config_entry: ConfigEntry | None = None
    _docker_conditions = DOCKER_PRE_SELECTION
    _container_conditions = CONTAINER_PRE_SELECTION

    async def async_step_user(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user step."""
        errors = {}

        if user_input is not None:
            self.data.update(user_input)

            # Convert some user_input data as preparation to calling API
            if user_input[CONF_URL] == "":
                user_input[CONF_URL] = None
            user_input[CONF_MEMORYCHANGE] = self.data[CONF_MEMORYCHANGE]

            # Test connection to Docker
            await self._async_close_docker_api()
            try:
                self._docker_api = DockerAPI(self.hass, user_input)
                self._owns_docker_api = True
                await self._docker_api.init()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Could not connect to Docker in user step")
                errors["base"] = "invalid_connection"

            # Unless re-authorization, check and abort if name already exists
            if self.source != SOURCE_REAUTH:
                if (
                    DOMAIN in self.hass.data
                    and user_input[CONF_NAME] in self.hass.data[DOMAIN]
                ):
                    errors[CONF_NAME] = "name_exists"

                await self.async_set_unique_id(user_input[CONF_NAME])
                if not self._config_entry:
                    self._abort_if_unique_id_configured()

            if not errors:
                if self.source == SOURCE_REAUTH:
                    await self._async_close_docker_api()
                    return self.async_update_reload_and_abort(
                        self._config_entry,
                        data=self.data,
                    )
                return await self.async_step_containers()

        user_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=self.data[CONF_NAME]): str,
                vol.Optional(CONF_URL, default=self.data[CONF_URL]): str,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=self.data[CONF_SCAN_INTERVAL]
                ): int,
                vol.Optional(CONF_CERTPATH, default=self.data[CONF_CERTPATH]): str,
                vol.Required(CONF_RETRY, default=self.data[CONF_RETRY]): int,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=user_schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfigure step."""
        self._config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._config_entry is None:
            return self.async_abort(reason="reconfigure_failed")
        self.data = {**self._config_entry.data}
        self._docker_api = self.hass.data[DOMAIN][self._config_entry.data[CONF_NAME]][
            API
        ]
        # Preselect what the entry monitors today instead of the defaults
        monitored = self.data.get(CONF_MONITORED_CONDITIONS) or []
        self._docker_conditions = [c for c in monitored if c in DOCKER_MONITOR_LIST]
        self._container_conditions = [
            c for c in monitored if c in CONTAINER_MONITOR_LIST
        ]

        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["containers", "conditions"],
        )

    async def async_step_reauth(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        self._config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_user(user_input)

    async def async_step_containers(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the containers to monitor, by inclusion or exclusion.

        Two steps within one step id:
        1. ask the selection mode (only selected / all except excluded),
        2. show the matching multi-select and store it.

        The entry data keeps the backend contract:
        - inclusion: containers=[...] (empty list means every container)
        - exclusion: containers=[] and containers_exclude=[...]
        """
        errors = {}

        if user_input is not None:
            # Phase 1 result: the selection mode
            if FIELD_CONTAINERS_MODE in user_input:
                self._containers_mode = user_input[FIELD_CONTAINERS_MODE]
            # Phase 2 result, exclusion mode (all except excluded)
            elif FIELD_CONTAINERS_MODE_EXCLUSION in user_input:
                self.data[CONF_CONTAINERS] = []
                self.data[CONF_CONTAINERS_EXCLUDE] = user_input[
                    FIELD_CONTAINERS_MODE_EXCLUSION
                ]
                return await self._containers_done()
            # Phase 2 result, inclusion mode (only the selected ones)
            elif CONF_CONTAINERS in user_input:
                self.data[CONF_CONTAINERS] = user_input[CONF_CONTAINERS]
                return await self._containers_done()

        lang = (self.hass.config.language or "en")[:2]

        if self._containers_mode is not None:
            if self._containers_mode == MODE_SELECTED:
                container_schema = vol.Schema(
                    {
                        vol.Optional(
                            CONF_CONTAINERS, default=self.data[CONF_CONTAINERS]
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                mode="list",
                                # Every container on the host, plus selected ones
                                # that do not exist right now
                                options=sorted(
                                    set(self._docker_api.list_all_containers())
                                    | set(self.data[CONF_CONTAINERS])
                                ),
                                multiple=True,
                            ),
                        )
                    }
                )
            else:
                container_schema = vol.Schema(
                    {
                        vol.Optional(
                            FIELD_CONTAINERS_MODE_EXCLUSION,
                            default=self.data.get(CONF_CONTAINERS_EXCLUDE) or [],
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                mode="list",
                                options=sorted(
                                    set(self._docker_api.list_all_containers())
                                    | set(self.data.get(CONF_CONTAINERS_EXCLUDE) or [])
                                ),
                                multiple=True,
                            ),
                        )
                    }
                )

            return self.async_show_form(
                step_id="containers",
                data_schema=container_schema,
                errors=errors,
            )

        mode_labels = {
            MODE_SELECTED: {
                "es": "Solo los contenedores seleccionados",
            }.get(lang, "Only the selected containers"),
            MODE_ALL_EXCEPT: {
                "es": "Todos excepto los excluidos",
            }.get(lang, "All containers except the excluded ones"),
        }

        # Derived default: keep what the entry does today
        mode_default = MODE_SELECTED
        if (
            not self.data.get(CONF_CONTAINERS)
            and (self.data.get(CONF_CONTAINERS_EXCLUDE) or [])
        ):
            mode_default = MODE_ALL_EXCEPT

        mode_schema = vol.Schema(
            {
                vol.Required(
                    FIELD_CONTAINERS_MODE, default=mode_default
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": MODE_SELECTED, "label": mode_labels[MODE_SELECTED]},
                            {
                                "value": MODE_ALL_EXCEPT,
                                "label": mode_labels[MODE_ALL_EXCEPT],
                            },
                        ]
                    ),
                )
            }
        )

        return self.async_show_form(
            step_id="containers",
            data_schema=mode_schema,
            errors=errors,
        )

    async def _containers_done(self) -> ConfigFlowResult:
        """Finish the containers step: save on reconfigure, continue otherwise."""
        if self.source == SOURCE_RECONFIGURE:
            # self.async_set_unique_id(self.data[CONF_NAME])
            # self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._config_entry,
                data=self.data,
            )
        return await self.async_step_conditions()

    async def async_step_conditions(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user step."""
        errors = {}

        if user_input is not None:
            self._docker_conditions = user_input.pop(CONF_MONITORED_DOCKER_CONDITIONS)
            self._container_conditions = user_input.pop(
                CONF_MONITORED_CONTAINER_CONDITIONS
            )
            self.data.update(user_input)

            if not errors:
                self.data[CONF_MONITORED_CONDITIONS] = (
                    self._docker_conditions + self._container_conditions
                )
                if self.source == SOURCE_RECONFIGURE:
                    # self.async_set_unique_id(self.data[CONF_NAME])
                    # self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._config_entry,
                        data=self.data,
                        reason="reconfigure_successful",
                    )
                await self._async_close_docker_api()
                return self.async_create_entry(
                    title=self.data[CONF_NAME], data=self.data
                )

        conditions_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MONITORED_DOCKER_CONDITIONS, default=self._docker_conditions
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(DOCKER_MONITOR_LIST),
                        multiple=True,
                    ),
                ),
                vol.Optional(
                    CONF_MONITORED_CONTAINER_CONDITIONS,
                    default=self._container_conditions,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(CONTAINER_MONITOR_LIST),
                        multiple=True,
                    ),
                ),
                vol.Required(
                    CONF_SWITCHENABLED, default=self.data[CONF_SWITCHENABLED]
                ): bool,
                vol.Required(
                    CONF_BUTTONENABLED, default=self.data[CONF_BUTTONENABLED]
                ): bool,
                vol.Required(
                    CONF_MEMORYCHANGE, default=self.data[CONF_MEMORYCHANGE]
                ): int,
                vol.Required(
                    CONF_PRECISION_CPU, default=self.data[CONF_PRECISION_CPU]
                ): int,
                vol.Required(
                    CONF_PRECISION_MEMORY_MB,
                    default=self.data[CONF_PRECISION_MEMORY_MB],
                ): int,
                vol.Required(
                    CONF_PRECISION_MEMORY_PERCENTAGE,
                    default=self.data[CONF_PRECISION_MEMORY_PERCENTAGE],
                ): int,
                vol.Required(
                    CONF_PRECISION_NETWORK_KB,
                    default=self.data[CONF_PRECISION_NETWORK_KB],
                ): int,
                vol.Required(
                    CONF_PRECISION_NETWORK_MB,
                    default=self.data[CONF_PRECISION_NETWORK_MB],
                ): int,
            }
        )

        return self.async_show_form(
            step_id="conditions",
            data_schema=conditions_schema,
            errors=errors,
        )

    async def _async_close_docker_api(self) -> None:
        """Close the test connection opened by this flow (never the live one)."""
        if self._docker_api is not None and self._owns_docker_api:
            try:
                await self._docker_api.destroy()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug("Closing the test connection failed", exc_info=True)
        if self._owns_docker_api:
            self._docker_api = None
        self._owns_docker_api = False

    async def async_step_import(
        self, import_info: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Import config from configuration.yaml."""
        _LOGGER.debug("Starting async_step_import - %s", import_info)
        if import_info[CONF_URL] == "":
            import_info[CONF_URL] = None
        await self.async_set_unique_id(import_info[CONF_NAME])
        ir.async_create_issue(
            hass=self.hass,
            domain=DOMAIN,
            issue_id=f"remove_configuration_yaml_{import_info[CONF_NAME]}",
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="remove_configuration_yaml",
            translation_placeholders={
                "domain": DOMAIN,
                "integration_title": import_info[CONF_NAME],
            },
        )
        self._abort_if_unique_id_configured()
        # Keep every YAML key: prefix, rename, rename_entity and the name formats
        # are needed to keep the entity_ids of existing installs unchanged.
        # containers_exclude is kept as-is (folding it into an empty
        # "containers" list would silently monitor the excluded containers).
        self.data.update(copy.deepcopy(dict(import_info)))
        return self.async_create_entry(title=self.data[CONF_NAME], data=self.data)
