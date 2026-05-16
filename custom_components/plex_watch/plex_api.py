from __future__ import annotations

import ipaddress
import logging
import ssl
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


def _is_private_address(address: str) -> bool:
    """Return True if address is a private/LAN IP (192.168.x.x, 10.x.x.x, 172.16-31.x.x, etc.)."""
    try:
        return ipaddress.ip_address(address).is_private
    except ValueError:
        return False

PLEX_TV_BASE = "https://plex.tv"

# Headers identifying our client to Plex
_PLEX_CLIENT_ID = "homeassistant-plex-watch-v1"
_PLEX_BASE_HEADERS = {
    "X-Plex-Product": "Plex Watch HA",
    "X-Plex-Version": "1.0.0",
    "X-Plex-Client-Identifier": _PLEX_CLIENT_ID,
    "X-Plex-Platform": "Home Assistant",
    "Accept": "application/json",
}

# SSL context that skips verification (plex.tv uses valid certs, but some HA setups have issues)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _make_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
    return aiohttp.ClientSession(connector=connector)


class PlexAuthFlow:
    """Manages the Plex PIN-based OAuth flow to obtain a valid user auth token."""

    def __init__(self) -> None:
        self._session = _make_session()
        self._pin_id: int | None = None
        self._pin_code: str | None = None

    async def close(self) -> None:
        await self._session.close()

    async def create_pin(self) -> tuple[int, str]:
        """Create a new Plex PIN. Returns (pin_id, pin_code)."""
        url = f"{PLEX_TV_BASE}/api/v2/pins"
        headers = {**_PLEX_BASE_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}
        try:
            async with self._session.post(url, headers=headers, data="strong=true", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    raise RuntimeError(f"PIN creation failed HTTP {resp.status}: {body[:200]}")
                data = await resp.json(content_type=None)
                self._pin_id = data["id"]
                self._pin_code = data["code"]
                _LOGGER.debug("Created Plex PIN id=%s code=%s", self._pin_id, self._pin_code)
                return self._pin_id, self._pin_code
        except RuntimeError:
            raise
        except Exception as err:
            raise RuntimeError(f"Network error creating Plex PIN: {err}") from err

    def get_auth_url(self) -> str:
        """Return the Plex auth URL the user must open in a browser."""
        if not self._pin_code:
            raise RuntimeError("PIN not created yet. Call create_pin() first.")
        params = urllib.parse.urlencode({
            "clientID": _PLEX_CLIENT_ID,
            "code": self._pin_code,
            "context[device][product]": "Plex Watch HA",
        })
        return f"https://app.plex.tv/auth#?{params}"

    async def check_pin(self, pin_id: int) -> str | None:
        """Poll Plex for the auth token. Returns the token if authorized, else None."""
        url = f"{PLEX_TV_BASE}/api/v2/pins/{pin_id}"
        try:
            async with self._session.get(url, headers=_PLEX_BASE_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    _LOGGER.debug("check_pin HTTP %s", resp.status)
                    return None
                data = await resp.json(content_type=None)
                token = data.get("authToken")
                if token:
                    _LOGGER.debug("Plex PIN claimed, token obtained")
                return token or None
        except Exception as err:
            _LOGGER.warning("Error checking Plex PIN: %s", err)
            return None


class PlexAPI:
    """Client for the Plex Media Server API."""

    def __init__(self, token: str, server_token: str | None = None) -> None:
        self._token = token          # account token — used for plex.tv API calls
        self._server_token = server_token or token  # server-specific token — used for direct server calls
        self._session = _make_session()

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        """Headers for plex.tv API calls (account token)."""
        return {**_PLEX_BASE_HEADERS, "X-Plex-Token": self._token, "Accept": accept}

    def _server_headers(self, accept: str = "application/json") -> dict[str, str]:
        """Headers for direct Plex server calls (server-specific token)."""
        return {**_PLEX_BASE_HEADERS, "X-Plex-Token": self._server_token, "Accept": accept}

    async def close(self) -> None:
        await self._session.close()

    async def validate_token(self) -> bool:
        """Return True if the token is valid."""
        url = f"{PLEX_TV_BASE}/api/v2/user"
        try:
            async with self._session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                _LOGGER.debug("validate_token HTTP %s", resp.status)
                return resp.status == 200
        except Exception as err:
            _LOGGER.error("Network error validating token: %s", err)
            return False

    async def _test_connection(self, uri: str) -> bool:
        """Return True if the Plex server at uri is reachable."""
        try:
            test_url = f"{uri}/identity"
            async with self._session.get(
                test_url,
                headers=self._headers("application/xml"),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                ok = resp.status in (200, 401)  # 401 = reachable but auth needed
                _LOGGER.debug("_test_connection %s → HTTP %s (%s)", uri, resp.status, 'OK' if ok else 'FAIL')
                return ok
        except Exception as err:
            _LOGGER.debug("_test_connection %s → unreachable: %s", uri, err)
            return False

    @staticmethod
    def _conn_uri(conn: dict) -> str | None:
        """Return URI using raw IP to bypass plex.direct DNS resolution issues."""
        address = conn.get("address")
        port = conn.get("port")
        protocol = conn.get("protocol", "https")
        # Relay connections use plex.tv relay infra — keep their original URI
        if conn.get("relay"):
            return conn.get("uri")
        # Direct connections: use raw IP to avoid DNS failures in Docker
        if address and port:
            return f"{protocol}://{address}:{port}"
        return conn.get("uri")

    async def get_resources(self, include_local: bool = False) -> list[dict[str, Any]]:
        """Return accessible Plex servers from plex.tv account."""
        url = f"{PLEX_TV_BASE}/api/v2/resources?includeHttps=1&includeRelay=1"
        try:
            async with self._session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=15)) as resp:
                _LOGGER.debug("get_resources HTTP %s", resp.status)
                if resp.status == 401:
                    _LOGGER.error("Plex token invalid (401)")
                    return []
                if resp.status != 200:
                    _LOGGER.error("get_resources HTTP %s: %s", resp.status, (await resp.text())[:200])
                    return []
                data = await resp.json(content_type=None)
        except Exception as err:
            _LOGGER.error("Network error in get_resources: %s", err)
            return []

        servers: list[dict[str, Any]] = []
        for device in data:
            provides = device.get("provides") or ""
            if "server" not in provides:
                continue
            connections: list[dict[str, Any]] = device.get("connections") or []
            # Filter connections based on include_local setting.
            # Double-check: exclude any connection whose address is a private IP,
            # regardless of the local flag (plex.tv can mark some as local=False
            # even though the address is still a LAN IP).
            def _keep(c: dict) -> bool:
                if include_local:
                    return True
                if c.get("local", False):
                    _LOGGER.warning(
                        "Skipping local=True connection: %s", c.get("uri", c.get("address"))
                    )
                    return False
                addr = str(c.get("address", ""))
                if addr and _is_private_address(addr):
                    _LOGGER.warning(
                        "Skipping private-IP connection (local=False but addr is private): %s", addr
                    )
                    return False
                return True

            non_local = sorted(
                [c for c in connections if _keep(c)],
                key=lambda c: (c.get("relay", False), c.get("protocol") != "https"),
            )
            if not non_local:
                _LOGGER.warning("No non-local connections for server '%s'", device.get("name"))
                continue
            # Pick best non-local connection (non-relay HTTPS first). No probe — fast.
            base_url: str | None = None
            for conn in non_local:
                uri = self._conn_uri(conn)
                if uri:
                    base_url = uri
                    _LOGGER.warning(
                        "Server '%s' → selected URL: %s  (relay=%s)",
                        device.get("name"), uri, conn.get("relay", False),
                    )
                    break
            if not base_url:
                _LOGGER.warning("Could not build URI for server '%s'", device.get("name"))
                continue
            servers.append({
                "id": device["clientIdentifier"],
                "name": device.get("name", "Unnamed Server"),
                "base_url": base_url,
                "owned": device.get("owned", False),
                "access_token": device.get("accessToken"),
            })
            _LOGGER.warning(
                "Server '%s' (owned=%s) accessToken present: %s",
                device.get("name"), device.get("owned"), device.get("accessToken") is not None,
            )

        _LOGGER.debug("Reachable Plex servers: %d", len(servers))
        return servers

    async def get_sessions(self, base_url: str) -> list[dict[str, Any]]:
        """Fetch active playback sessions."""
        url = f"{base_url}/status/sessions"
        try:
            async with self._session.get(url, headers=self._server_headers("application/xml"), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 403:
                    _LOGGER.warning("get_sessions HTTP 403 — endpoint restricted for non-owner users; sessions will be empty")
                    return []
                if resp.status != 200:
                    _LOGGER.warning("get_sessions HTTP %s", resp.status)
                    return []
                text = await resp.text()
        except Exception as err:
            _LOGGER.warning("Network error in get_sessions: %s", err)
            return []

        sessions: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(text)
            for video in list(root.findall("Video")) + list(root.findall("Track")):
                duration = video.attrib.get("duration")
                view_offset = video.attrib.get("viewOffset")
                remaining = (int(duration) - int(view_offset)) if duration and view_offset else None
                progress_pct = None
                if duration and view_offset and int(duration) > 0:
                    progress_pct = round(int(view_offset) / int(duration) * 100, 1)
                user_elem = video.find("User")
                user = user_elem.attrib.get("title") if user_elem is not None else None
                player_elem = video.find("Player")
                player_title = player_elem.attrib.get("title") if player_elem is not None else None
                player_device = player_elem.attrib.get("device") if player_elem is not None else None
                sessions.append({
                    "title": video.attrib.get("title"),
                    "media_type": video.attrib.get("type"),
                    "grandparent_title": video.attrib.get("grandparentTitle"),
                    "parent_title": video.attrib.get("parentTitle"),
                    "season_index": video.attrib.get("parentIndex"),
                    "episode_index": video.attrib.get("index"),
                    "user": user,
                    "player_title": player_title,
                    "player_device": player_device,
                    "state": video.attrib.get("state"),
                    "view_offset": view_offset,
                    "duration": duration,
                    "remaining": remaining,
                    "progress_pct": progress_pct,
                    "rating_key": video.attrib.get("ratingKey"),
                })
        except ET.ParseError as err:
            _LOGGER.error("Error parsing sessions XML: %s", err)
        return sessions

    async def get_library_recently_added(self, base_url: str) -> list[dict[str, Any]]:
        """Fetch recently added library items."""
        url = f"{base_url}/library/recentlyAdded"
        try:
            async with self._session.get(url, headers=self._server_headers("application/xml"), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    _LOGGER.warning("get_library_recently_added HTTP %s", resp.status)
                    return []
                text = await resp.text()
        except Exception as err:
            _LOGGER.warning("Network error in get_library_recently_added: %s", err)
            return []

        items: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(text)
            for item in root:
                items.append({
                    "title": item.attrib.get("title"),
                    "media_type": item.attrib.get("type"),
                    "grandparent_title": item.attrib.get("grandparentTitle"),
                    "parent_title": item.attrib.get("parentTitle"),
                    "season_index": item.attrib.get("parentIndex"),
                    "episode_index": item.attrib.get("index"),
                    "added_at": item.attrib.get("addedAt"),
                    "library_section_title": item.attrib.get("librarySectionTitle"),
                    "rating_key": item.attrib.get("ratingKey"),
                })
        except ET.ParseError as err:
            _LOGGER.error("Error parsing recently added XML: %s", err)
        return items

    async def get_on_deck(self, base_url: str) -> list[dict[str, Any]]:
        """Fetch 'On Deck' (continue watching) items."""
        url = f"{base_url}/library/onDeck"
        try:
            async with self._session.get(url, headers=self._server_headers("application/xml"), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status in (401, 403):
                    _LOGGER.warning("get_on_deck HTTP %s — likely wrong/missing server token (accessToken)", resp.status)
                    return []
                if resp.status != 200:
                    _LOGGER.warning("get_on_deck HTTP %s", resp.status)
                    return []
                text = await resp.text()
        except Exception as err:
            _LOGGER.warning("Network error in get_on_deck: %s", err)
            return []

        items: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(text)
            for item in root:
                duration = item.attrib.get("duration")
                view_offset = item.attrib.get("viewOffset")
                progress_pct = None
                if duration and view_offset and int(duration) > 0:
                    progress_pct = round(int(view_offset) / int(duration) * 100, 1)
                items.append({
                    "title": item.attrib.get("title"),
                    "media_type": item.attrib.get("type"),
                    "grandparent_title": item.attrib.get("grandparentTitle"),
                    "parent_title": item.attrib.get("parentTitle"),
                    "season_index": item.attrib.get("parentIndex"),
                    "episode_index": item.attrib.get("index"),
                    "progress_pct": progress_pct,
                    "rating_key": item.attrib.get("ratingKey"),
                })
        except ET.ParseError as err:
            _LOGGER.error("Error parsing on_deck XML: %s", err)
        return items

    async def get_show_sections(self, base_url: str) -> list[str]:
        """Return section IDs (keys) for TV-show libraries."""
        url = f"{base_url}/library/sections"
        try:
            async with self._session.get(url, headers=self._server_headers("application/xml"), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    _LOGGER.warning("get_show_sections HTTP %s", resp.status)
                    return []
                text = await resp.text()
        except Exception as err:
            _LOGGER.warning("Network error in get_show_sections: %s", err)
            return []
        sections: list[str] = []
        try:
            root = ET.fromstring(text)
            for d in root:
                if d.attrib.get("type") == "show":
                    key = d.attrib.get("key", "")
                    if key:
                        sections.append(key)
        except ET.ParseError as err:
            _LOGGER.error("Error parsing sections XML: %s", err)
        return sections

    async def get_unwatched_counts(self, base_url: str, watched: set[str]) -> dict[str, int]:
        """Return {show_title: unwatched_episode_count} for each title in watched.

        Uses leafCount - viewedLeafCount so that partially-watched episodes are
        counted as "still to watch", matching what the user sees in Plex.
        """
        if not watched:
            return {}
        sections = await self.get_show_sections(base_url)
        if not sections:
            return {s: 0 for s in watched}

        counts: dict[str, int] = {s: 0 for s in watched}
        for section_id in sections:
            for show_title in watched:
                url = (
                    f"{base_url}/library/sections/{section_id}/all"
                    f"?type=2&title={urllib.parse.quote(show_title)}"
                )
                try:
                    async with self._session.get(url, headers=self._server_headers("application/xml"), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            continue
                        text = await resp.text()
                except Exception as err:
                    _LOGGER.warning("Network error fetching shows for '%s': %s", show_title, err)
                    continue
                try:
                    root = ET.fromstring(text)
                    for item in root:
                        if item.attrib.get("title", "").lower() == show_title.lower():
                            leaf = int(item.attrib.get("leafCount", 0))
                            viewed = int(item.attrib.get("viewedLeafCount", 0))
                            counts[show_title] += max(0, leaf - viewed)
                            break
                except ET.ParseError as err:
                    _LOGGER.error("Error parsing show list XML for '%s': %s", show_title, err)
        return counts

    async def play_series(self, base_url: str, series_rating_key: str, client_identifier: str) -> bool:
        """Send a play command to a Plex client."""
        params = urllib.parse.urlencode({
            "key": f"/library/metadata/{series_rating_key}",
            "offset": 0,
            "machineIdentifier": client_identifier,
        })
        url = f"{base_url}/player/playback/playMedia?{params}"
        try:
            async with self._session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
        except Exception as err:
            _LOGGER.error("Network error in play_series: %s", err)
            return False

    async def get_current_series(self, base_url: str) -> str:
        """Return title of the currently playing episode, if any."""
        for session in await self.get_sessions(base_url):
            if session.get("type") == "episode":
                return session.get("title") or ""
        return ""


def _best_connection(connections: list[dict[str, Any]]) -> str | None:
    """Select the best URI from a list of Plex connections."""
    for local, https, no_relay in [
        (True, True, True),
        (False, True, True),
        (True, True, False),
        (False, True, False),
        (None, False, None),
    ]:
        for conn in connections:
            if not conn.get("uri"):
                continue
            if local is not None and bool(conn.get("local")) != local:
                continue
            if https and conn.get("protocol") != "https":
                continue
            if no_relay and conn.get("relay"):
                continue
            return conn["uri"]
    return None

