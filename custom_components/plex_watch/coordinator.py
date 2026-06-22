from __future__ import annotations

import logging
import re
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    DEFAULT_SCAN_INTERVAL,
    CONF_USE_LOCAL,
    CONF_WATCHED_SERIES,
    CONF_WATCHLIST_RSS_URL,
    CONF_TITLE_LANGUAGE,
    MAX_DASHBOARD_ITEMS,
)
from .plex_api import PlexAPI
from .rss_watchlist import PlexWatchlistRSS
from .storage import PlexWatchStorage

_LOGGER = logging.getLogger(__name__)

# Matches plex.direct hostnames OR raw IPs that encode private ranges (- and . separators)
_LOCAL_IP_RE = re.compile(
    r"https?://"
    r"(192[-.]168"
    r"|10[-.]"
    r"|172[-.]((1[6-9])|(2[0-9])|(3[01]))[-.]\d"
    r")"
)


def _is_local_url(url: str) -> bool:
    """Return True if url points to a private/LAN IP."""
    return bool(_LOCAL_IP_RE.search(url))


class PlexDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Plex Watch ({entry.data.get('server_name', entry.entry_id)})",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        server_token: str = entry.data.get("access_token") or entry.data["token"]
        self.api = PlexAPI(entry.data["token"], server_token=server_token)
        self.base_url: str = entry.data["base_url"]
        self.storage = PlexWatchStorage(hass, entry.entry_id)
        self._rss = PlexWatchlistRSS(self.api._session, max_dashboard_items=MAX_DASHBOARD_ITEMS)
        self._loaded = False
        self._persisted: dict = {}
        self._connection_verified = False

    async def _rediscover_base_url(self) -> bool:
        """
        Fetch server list from plex.tv and update self.base_url in memory.
        Returns True if a valid non-local URL was found.
        Does NOT call async_update_entry — avoids triggering a reload during setup.
        """
        _LOGGER.warning("[plex_watch] Rediscovering Plex server URL (current: %s)", self.base_url)
        use_local: bool = self.entry.options.get(
            CONF_USE_LOCAL, self.entry.data.get(CONF_USE_LOCAL, False)
        )
        _LOGGER.warning("[plex_watch] Rediscovery: include_local=%s", use_local)
        try:
            servers = await self.api.get_resources(include_local=use_local)
        except Exception as err:
            _LOGGER.error("[plex_watch] get_resources raised unexpectedly: %s", err)
            return False

        server_id = self.entry.data.get("server_id")
        _LOGGER.warning("[plex_watch] Looking for server_id=%s among %d servers returned", server_id, len(servers))
        server = next((s for s in servers if s["id"] == server_id), None)

        if server and server.get("base_url"):
            new_url = server["base_url"]
            if _is_local_url(new_url) and not use_local:
                _LOGGER.error(
                    "[plex_watch] Rediscovery returned a LAN URL (%s) but use_local=False — ignoring",
                    new_url,
                )
                return False
            _LOGGER.warning(
                "[plex_watch] Rediscovery OK for '%s': %s (was: %s)",
                server.get("name"), new_url, self.base_url,
            )
            self.base_url = new_url
            # Update server-specific token (needed for non-owned servers returning HTTP 401)
            if server.get("access_token"):
                self.api._server_token = server["access_token"]
                _LOGGER.warning("[plex_watch] Server token updated for '%s'", server.get("name"))
            return True

        _LOGGER.error(
            "[plex_watch] Rediscovery failed — servers_returned=%d, server_matched=%s, ids_available=%s",
            len(servers), server is not None, [s['id'] for s in servers],
        )
        return False

    async def _async_update_data(self) -> dict:
        """Main coordinator update. Never raises UpdateFailed from network errors."""
        # Apply preferred language for Plex metadata titles.
        title_language_opt = self.entry.options.get(
            CONF_TITLE_LANGUAGE,
            self.entry.data.get(CONF_TITLE_LANGUAGE, "auto"),
        )
        title_language = (title_language_opt or "auto").strip().lower()
        if title_language == "auto":
            title_language = self.hass.config.language
        self.api.set_language(title_language or None)

        # Load persistent state once
        if not self._loaded:
            self._loaded = True
            try:
                self._persisted = await self.storage.async_load() or {}
            except Exception as err:
                _LOGGER.error("Failed to load storage: %s", err)
                self._persisted = {}

        # On first run, ensure we have a reachable non-local URL.
        # _connection_verified is only set True when we actually confirm a good URL.
        if not self._connection_verified:
            if _is_local_url(self.base_url):
                _LOGGER.warning(
                    "Stored base_url is a LAN address (%s) — forcing rediscovery",
                    self.base_url,
                )
                ok = await self._rediscover_base_url()
            else:
                ok = await self.api._test_connection(self.base_url)
                if not ok:
                    _LOGGER.warning(
                        "base_url unreachable (%s) — attempting rediscovery",
                        self.base_url,
                    )
                    ok = await self._rediscover_base_url()

            if ok:
                self._connection_verified = True
                _LOGGER.warning("[plex_watch] Connection verified, using: %s", self.base_url)
            else:
                # Don't try to use the (likely unreachable) stored URL — return empty and retry
                _LOGGER.warning("[plex_watch] Could not verify connection — returning empty data, will retry on next poll")
                return {
                    "sessions": [],
                    "recently_added": [],
                    "on_deck": [],
                    "unwatched_counts": {},
                    "my_session": None,
                    "new_series_detected": False,
                    "server_online": False,
                    "watchlist_rss": {},
                }

        raw_sessions = await self.api.get_sessions(self.base_url)
        sessions_ok: bool = raw_sessions is not None
        sessions: list = raw_sessions if sessions_ok else []
        recently_added = await self.api.get_library_recently_added(self.base_url)
        on_deck = await self.api.get_on_deck(self.base_url)

        server_online = self._connection_verified

        # Filter sessions to find the current user's own playback
        my_username: str = self.entry.data.get("username", "").lower()
        my_session = None
        for s in sessions:
            session_user = (s.get("user") or "").lower()
            if not my_username or session_user == my_username:
                my_session = s
                break

        # Unwatched episode counts for each watched series
        watched_raw: str = self.entry.options.get(
            CONF_WATCHED_SERIES, self.entry.data.get(CONF_WATCHED_SERIES, "")
        )
        watched: set[str] = {s.strip() for s in watched_raw.split(",") if s.strip()}
        unwatched_counts: dict[str, int] = {}
        if watched:
            unwatched_counts = await self.api.get_unwatched_counts(self.base_url, watched)
            _LOGGER.debug("Unwatched counts: %s", unwatched_counts)

        new_episodes: list[dict] = []

        # Detect brand new series (shows added for the first time)
        known_series: set[str] = set(self._persisted.get("known_series", []))
        current_shows: set[str] = set()
        for item in recently_added:
            if item.get("media_type") == "episode":
                gp = item.get("grandparent_title")
                if gp:
                    current_shows.add(gp)
            elif item.get("media_type") in ("show", "season"):
                t = item.get("title")
                if t:
                    current_shows.add(t)
        new_series = current_shows - known_series
        new_series_detected = bool(new_series)

        if new_series:
            known_series.update(new_series)
            self._persisted["known_series"] = list(known_series)
            try:
                await self.storage.async_save(self._persisted)
            except Exception as err:
                _LOGGER.error("Failed to save storage: %s", err)
            _LOGGER.info("New series detected: %s", new_series)

        watchlist_rss: dict = {}
        rss_url: str = self.entry.options.get(CONF_WATCHLIST_RSS_URL, "").strip()
        if rss_url:
            watchlist_rss = await self._rss.fetch_and_build(rss_url)
            watchlist_items = watchlist_rss.get("watchlist_items", [])
            resolved_pending = await self.api.resolve_watchlist_pending(self.base_url, watchlist_items)
            watchlist_rss.update(resolved_pending)

        return {
            "sessions": sessions,
            "sessions_ok": sessions_ok,
            "recently_added": recently_added,
            "on_deck": on_deck,
            "my_session": my_session,
            "unwatched_counts": unwatched_counts,
            "new_series_detected": new_series_detected,
            "server_online": server_online,
            "watchlist_rss": watchlist_rss,
        }

