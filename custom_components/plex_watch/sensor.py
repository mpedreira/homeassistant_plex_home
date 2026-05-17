from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PlexNowPlayingSensor(coordinator, entry.entry_id),
        PlexLatestAddedSensor(coordinator, entry.entry_id),
        PlexNewSeriesDetectedSensor(coordinator, entry.entry_id),
        PlexNewEpisodesSensor(coordinator, entry.entry_id),
        PlexServerStatusSensor(coordinator, entry.entry_id),
        PlexOnDeckSensor(coordinator, entry.entry_id),
    ])


class PlexNowPlayingSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "Plex Now Playing"
        self._attr_icon = "mdi:play-circle"
        self._attr_unique_id = f"{entry_id}_now_playing"

    @property
    def state(self):
        return len(self.coordinator.data.get("sessions", []))

    @property
    def extra_state_attributes(self):
        sessions = self.coordinator.data.get("sessions", [])
        return {
            "sessions": [
                {
                    "title": s.get("title"),
                    "show": s.get("grandparent_title"),
                    "season": s.get("season_index"),
                    "episode": s.get("episode_index"),
                    "user": s.get("user"),
                    "player": s.get("player_title"),
                    "state": s.get("state"),
                    "progress_pct": s.get("progress_pct"),
                    "type": s.get("media_type"),
                }
                for s in sessions
            ]
        }


class PlexLatestAddedSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "Plex Latest Added"
        self._attr_icon = "mdi:movie-plus"
        self._attr_unique_id = f"{entry_id}_latest_added"

    @property
    def state(self):
        items = self.coordinator.data.get("recently_added", [])
        if not items:
            return None
        item = items[0]
        if item.get("media_type") == "episode" and item.get("grandparent_title"):
            return f"{item['grandparent_title']} - {item['title']}"
        return item.get("title")

    @property
    def extra_state_attributes(self):
        items = self.coordinator.data.get("recently_added", [])
        if not items:
            return {}
        item = items[0]
        return {
            "type": item.get("media_type"),
            "show": item.get("grandparent_title"),
            "season": item.get("season_index"),
            "episode": item.get("episode_index"),
            "added_at": item.get("added_at"),
            "library_section": item.get("library_section_title"),
            "rating_key": item.get("rating_key"),
        }


class PlexNewSeriesDetectedSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "Plex New Series Detected"
        self._attr_icon = "mdi:television-classic"
        self._attr_unique_id = f"{entry_id}_new_series_detected"

    @property
    def state(self):
        return self.coordinator.data.get("new_series_detected", False)

    @property
    def extra_state_attributes(self):
        return {}


class PlexNewEpisodesSensor(CoordinatorEntity, SensorEntity):
    """Total unwatched episodes across all series configured in Options."""

    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "Plex New Episodes"
        self._attr_icon = "mdi:television-play"
        self._attr_unique_id = f"{entry_id}_new_episodes"

    @property
    def state(self):
        counts: dict = self.coordinator.data.get("unwatched_counts", {})
        return sum(counts.values())

    @property
    def extra_state_attributes(self):
        counts: dict = self.coordinator.data.get("unwatched_counts", {})
        return {
            "by_series": counts,
            "total": sum(counts.values()),
        }


class PlexServerStatusSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "Plex Server Status"
        self._attr_icon = "mdi:server"
        self._attr_unique_id = f"{entry_id}_server_status"

    @property
    def state(self):
        return "online" if self.coordinator.data.get("server_online") else "offline"

    @property
    def extra_state_attributes(self):
        return {"base_url": self.coordinator.base_url}


class PlexOnDeckSensor(CoordinatorEntity, SensorEntity):
    """What you are watching right now (my_session), or the top in-progress item (on_deck)."""

    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "Plex On Deck"
        self._attr_icon = "mdi:play-box-multiple"
        self._attr_unique_id = f"{entry_id}_on_deck"

    def _source(self):
        """Return (item, is_live) — prefer active session, fallback to on_deck queue."""
        my_session = self.coordinator.data.get("my_session")
        if my_session:
            return my_session, True
        items = self.coordinator.data.get("on_deck", [])
        return (items[0] if items else None), False

    def _format_episode(self, item) -> str | None:
        if item.get("media_type") == "episode" and item.get("grandparent_title"):
            season = item.get("season_index", "?")
            ep = item.get("episode_index", "?")
            try:
                s_str = f"{int(season):02d}"
                e_str = f"{int(ep):02d}"
            except (TypeError, ValueError):
                s_str, e_str = str(season), str(ep)
            return f"{item['grandparent_title']} S{s_str}E{e_str}"
        return item.get("title")

    @property
    def state(self):
        item, _ = self._source()
        if not item:
            return None
        return self._format_episode(item)

    @property
    def extra_state_attributes(self):
        item, is_live = self._source()
        if not item:
            return {"playing": False}
        attrs = {
            "playing": is_live,
            "title": item.get("title"),
            "show": item.get("grandparent_title"),
            "season": item.get("season_index"),
            "episode": item.get("episode_index"),
            "progress_pct": item.get("progress_pct"),
            "type": item.get("media_type"),
        }
        if is_live:
            attrs["player"] = item.get("player_title")
            attrs["user"] = item.get("user")
        return attrs
