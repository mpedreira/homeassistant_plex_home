from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PlexNowPlayingSensor(coordinator, entry.entry_id),
        PlexLatestAddedSensor(coordinator, entry.entry_id),
        PlexNewSeriesDetectedSensor(coordinator, entry.entry_id),
        PlexServerStatusSensor(coordinator, entry.entry_id),
        PlexOnDeckSensor(coordinator, entry.entry_id),
        PlexWatchlistPendingTotalSensor(coordinator, entry.entry_id),
        PlexWatchlistNextReleaseInDaysSensor(coordinator, entry.entry_id),
        PlexSeriesPendingEpisodesSensor(coordinator, entry.entry_id),
        PlexWatchlistItemsWithoutDateSensor(coordinator, entry.entry_id),
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
    """Deprecated legacy sensor.

    This sensor is kept only for backward compatibility in code references.
    It is no longer registered in async_setup_entry.
    """

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
    """Shows what you are watching right now, or None if you are not watching anything.

    Falls back to the top on_deck item only when sessions cannot be read (e.g. 403).
    Shows HA 'unknown' only when neither source can be reached.
    """

    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "Plex On Deck"
        self._attr_icon = "mdi:play-box-multiple"
        self._attr_unique_id = f"{entry_id}_on_deck"

    def _resolve(self):
        """Return (item, is_live, known) tuple.

        is_live=True  → item is a current active session
        is_live=False → item is from on_deck queue (fallback)
        known=False   → both sources failed; sensor should return unknown
        """
        sessions_ok: bool = self.coordinator.data.get("sessions_ok", False)
        my_session = self.coordinator.data.get("my_session")

        if sessions_ok:
            # We got a real answer from the server: either playing or definitively idle
            return my_session, True, True

        # sessions failed (403/401) — fall back to on_deck queue
        items = self.coordinator.data.get("on_deck", [])
        if items:
            return items[0], False, True

        return None, False, False

    def _format_episode(self, item) -> str:
        if item.get("media_type") == "episode" and item.get("grandparent_title"):
            season = item.get("season_index", "?")
            ep = item.get("episode_index", "?")
            try:
                s_str = f"{int(season):02d}"
                e_str = f"{int(ep):02d}"
            except (TypeError, ValueError):
                s_str, e_str = str(season), str(ep)
            return f"{item['grandparent_title']} S{s_str}E{e_str}"
        return item.get("title") or ""

    @property
    def state(self):
        item, is_live, known = self._resolve()
        if not known:
            return None          # → HA "unknown"
        if item is None:
            return "none"        # sessions OK but not watching
        return self._format_episode(item)

    @property
    def extra_state_attributes(self):
        item, is_live, known = self._resolve()
        if not known or item is None:
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


class PlexWatchlistPendingTotalSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "watchlist_pending_total"
        self._attr_icon = "mdi:playlist-clock"
        self._attr_unique_id = f"{entry_id}_watchlist_pending_total"

    @property
    def state(self):
        rss = self.coordinator.data.get("watchlist_rss", {})
        return rss.get("watchlist_pending_total", 0)

    @property
    def extra_state_attributes(self):
        rss = self.coordinator.data.get("watchlist_rss", {})
        return {
            "pending_calendar": rss.get("pending_calendar", []),
            "top_10_pending_by_date": rss.get("top_10_pending_by_date", []),
            "weekly_new_items": rss.get("weekly_new_items", 0),
            "feed_items_total": rss.get("feed_items_total", 0),
            "plex_items_total": rss.get("plex_items_total", 0),
            "skipped_non_supported_items": rss.get("skipped_non_supported_items", 0),
            "category_counts": rss.get("category_counts", {}),
            "excluded_non_plex_items": rss.get("excluded_non_plex_items", 0),
            "pending_movies": rss.get("pending_movies", {}),
            "watchlist_matches": rss.get("watchlist_matches", 0),
            "watchlist_items_checked": rss.get("watchlist_items_checked", 0),
            "watchlist_unmatched": rss.get("watchlist_unmatched", []),
            "feed_status": rss.get("feed_status", "unknown"),
            "feed_error": rss.get("feed_error"),
            "last_update": rss.get("last_update"),
        }


class PlexWatchlistNextReleaseInDaysSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "watchlist_next_release_in_days"
        self._attr_icon = "mdi:calendar-clock"
        self._attr_unique_id = f"{entry_id}_watchlist_next_release_in_days"

    @property
    def state(self):
        rss = self.coordinator.data.get("watchlist_rss", {})
        return rss.get("watchlist_next_release_in_days")

    @property
    def extra_state_attributes(self):
        rss = self.coordinator.data.get("watchlist_rss", {})
        return {
            "next_release": rss.get("next_release"),
            "feed_status": rss.get("feed_status", "unknown"),
            "feed_error": rss.get("feed_error"),
        }


class PlexSeriesPendingEpisodesSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "series_pending_episodes"
        self._attr_icon = "mdi:television-classic"
        self._attr_unique_id = f"{entry_id}_series_pending_episodes"

    @property
    def state(self):
        rss = self.coordinator.data.get("watchlist_rss", {})
        by_series = rss.get("series_pending_episodes", {})
        return len([series for series, count in by_series.items() if count > 0])

    @property
    def extra_state_attributes(self):
        rss = self.coordinator.data.get("watchlist_rss", {})
        by_series = rss.get("series_pending_episodes", {})
        pending_movies = rss.get("pending_movies", {})
        return {
            "by_series": by_series,
            "pending_movies": pending_movies,
            "total_pending": sum(by_series.values()) + sum(pending_movies.values()),
            "top_10_pending_by_date": rss.get("top_10_pending_by_date", []),
            "weekly_new_items": rss.get("weekly_new_items", 0),
        }


class PlexWatchlistItemsWithoutDateSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "watchlist_items_without_date"
        self._attr_icon = "mdi:calendar-remove"
        self._attr_unique_id = f"{entry_id}_watchlist_items_without_date"

    @property
    def state(self):
        rss = self.coordinator.data.get("watchlist_rss", {})
        return rss.get("watchlist_items_without_date", 0)

    @property
    def extra_state_attributes(self):
        rss = self.coordinator.data.get("watchlist_rss", {})
        return {
            "feed_status": rss.get("feed_status", "unknown"),
            "feed_error": rss.get("feed_error"),
            "feed_items_total": rss.get("feed_items_total", 0),
            "plex_items_total": rss.get("plex_items_total", 0),
            "skipped_non_supported_items": rss.get("skipped_non_supported_items", 0),
            "category_counts": rss.get("category_counts", {}),
            "excluded_non_plex_items": rss.get("excluded_non_plex_items", 0),
        }
