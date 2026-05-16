from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, CONF_TOKEN, CONF_SERVER_ID, CONF_SERVER_NAME, CONF_BASE_URL, CONF_USE_LOCAL
from .plex_api import PlexAPI, PlexAuthFlow

_LOGGER = logging.getLogger(__name__)


class PlexWatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._token: str = ""
        self._servers: list[dict[str, Any]] = []
        self._auth_flow: PlexAuthFlow | None = None
        self._pin_id: int | None = None
        self._auth_url: str = ""
        self._pin_code: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> PlexWatchOptionsFlowHandler:
        from .options_flow import PlexWatchOptionsFlowHandler
        return PlexWatchOptionsFlowHandler()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Start PIN-based Plex authentication."""
        errors: dict[str, str] = {}

        if self._auth_flow is not None:
            await self._auth_flow.close()

        self._auth_flow = PlexAuthFlow()
        try:
            pin_id, pin_code = await self._auth_flow.create_pin()
            self._pin_id = pin_id
            self._auth_url = self._auth_flow.get_auth_url()
            self._pin_code = pin_code
        except Exception as err:
            _LOGGER.error("Error creating Plex PIN: %s", err)
            await self._auth_flow.close()
            self._auth_flow = None
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        return await self.async_step_auth_wait()

    async def async_step_auth_wait(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show auth URL and wait for user to authorize."""
        errors: dict[str, str] = {}

        if user_input is not None and self._auth_flow and self._pin_id:
            token = await self._auth_flow.check_pin(self._pin_id)
            if not token:
                errors["base"] = "auth_not_completed"
            else:
                self._token = token
                await self._auth_flow.close()
                self._auth_flow = None
                api = PlexAPI(self._token)
                try:
                    self._servers = await api.get_resources()
                finally:
                    await api.close()
                if not self._servers:
                    errors["base"] = "no_servers"
                else:
                    return await self.async_step_select_server()

        # Show the URL as a read-only text field so it is always visible
        auth_url = getattr(self, "_auth_url", "")
        pin_code = getattr(self, "_pin_code", "")

        return self.async_show_form(
            step_id="auth_wait",
            data_schema=vol.Schema({
                vol.Optional("plex_url", default=auth_url): str,
            }),
            description_placeholders={
                "auth_url": auth_url,
                "pin_code": pin_code,
            },
            errors=errors,
        )

    async def async_step_select_server(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Let the user pick which Plex server to monitor."""
        errors: dict[str, str] = {}
        server_options = {s["id"]: f"{s['name']} {'(tuyo)' if s['owned'] else '(compartido)'}" for s in self._servers}

        if user_input is not None:
            server_id = user_input[CONF_SERVER_ID]
            use_local: bool = user_input.get(CONF_USE_LOCAL, False)
            server = next((s for s in self._servers if s["id"] == server_id), None)
            if not server:
                errors["base"] = "invalid_server"
            else:
                await self.async_set_unique_id(server_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=server["name"],
                    data={
                        CONF_TOKEN: self._token,
                        CONF_SERVER_ID: server["id"],
                        CONF_SERVER_NAME: server["name"],
                        CONF_BASE_URL: server["base_url"],
                        CONF_USE_LOCAL: use_local,
                        "access_token": server.get("access_token"),
                    },
                )

        return self.async_show_form(
            step_id="select_server",
            data_schema=vol.Schema({
                vol.Required(CONF_SERVER_ID): vol.In(server_options),
                vol.Optional(CONF_USE_LOCAL, default=False): bool,
            }),
            errors=errors,
        )

