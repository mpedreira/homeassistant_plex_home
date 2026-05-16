import logging
from typing import Any, Dict
import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN, CONF_SERVER_ID, CONF_SERVER_NAME, CONF_BASE_URL, CONF_TOKEN, CONF_USE_LOCAL, CONF_WATCHED_SERIES
from .plex_api import PlexAPI

_LOGGER = logging.getLogger(__name__)

class PlexWatchOptionsFlowHandler(config_entries.OptionsFlow):
    # No __init__ needed: self.config_entry is provided as a read-only property
    # by the OptionsFlow base class in modern Home Assistant.

    async def async_step_init(self, user_input: Dict[str, Any] = None):
        errors = {}
        token: str = self.config_entry.data[CONF_TOKEN]
        current_use_local: bool = self.config_entry.options.get(
            CONF_USE_LOCAL, self.config_entry.data.get(CONF_USE_LOCAL, False)
        )
        current_watched: str = self.config_entry.options.get(
            CONF_WATCHED_SERIES, self.config_entry.data.get(CONF_WATCHED_SERIES, "")
        )
        api = PlexAPI(token)
        servers = await api.get_resources(include_local=current_use_local)
        await api.close()
        if not servers:
            errors["base"] = "no_servers"
            return self.async_show_form(step_id="init", errors=errors)
        server_options = {s["id"]: s["name"] for s in servers}
        if user_input is not None:
            use_local = user_input.get(CONF_USE_LOCAL, False)
            # Re-fetch with updated local preference so base_url is correct
            api2 = PlexAPI(token)
            servers2 = await api2.get_resources(include_local=use_local)
            await api2.close()
            server = next((s for s in servers2 if s["id"] == user_input[CONF_SERVER_ID]), None)
            if not server:
                errors["base"] = "invalid_server"
            else:
                return self.async_create_entry(
                    title=server["name"],
                    data={
                        CONF_SERVER_ID: server["id"],
                        CONF_SERVER_NAME: server["name"],
                        CONF_BASE_URL: server["base_url"],
                        CONF_USE_LOCAL: use_local,
                        CONF_WATCHED_SERIES: user_input.get(CONF_WATCHED_SERIES, ""),
                        "access_token": server.get("access_token"),
                    },
                )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SERVER_ID): vol.In(server_options),
                vol.Optional(CONF_USE_LOCAL, default=current_use_local): bool,
                vol.Optional(CONF_WATCHED_SERIES, default=current_watched): str,
            }),
            errors=errors,
        )
