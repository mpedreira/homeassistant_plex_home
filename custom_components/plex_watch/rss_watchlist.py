from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import re
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import aiohttp
from homeassistant.util import dt as dt_util


NAMESPACE_ATOM = "{http://www.w3.org/2005/Atom}"
TITLE_YEAR_RE = re.compile(r"\((\d{4})\)\s*$")


@dataclass
class WatchlistItem:
    title: str
    category: str
    year: int | None
    link: str
    release: datetime | None
    source: str
    guid: str | None = None


class PlexWatchlistRSS:
    """Fetch and transform Plex watchlist RSS into sensor-friendly aggregates."""

    def __init__(self, session: aiohttp.ClientSession, max_dashboard_items: int = 10) -> None:
        self._session = session
        self._max_dashboard_items = max_dashboard_items

    async def fetch_and_build(self, feed_url: str) -> dict[str, Any]:
        """Return watchlist aggregates for sensors.

        Output keys are consumed by coordinator and later enriched against Plex server.
        """
        now_local = dt_util.now()
        payload = {
            "watchlist_pending_total": 0,
            "watchlist_next_release_in_days": None,
            "series_pending_episodes": {},
            "watchlist_items_without_date": 0,
            "top_10_pending_by_date": [],
            "pending_calendar": [],
            "next_release": None,
            "weekly_new_items": 0,
            "watchlist_items": [],
            "feed_items_total": 0,
            "plex_items_total": 0,
            "skipped_non_supported_items": 0,
            "category_counts": {},
            "excluded_non_plex_items": 0,
            "feed_status": "ok",
            "feed_error": None,
            "last_update": now_local.isoformat(),
        }

        try:
            async with self._session.get(
                feed_url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    payload["feed_status"] = "error"
                    payload["feed_error"] = f"HTTP {resp.status}"
                    return payload
                xml_text = await resp.text()
        except Exception as err:  # network errors should not break coordinator
            payload["feed_status"] = "error"
            payload["feed_error"] = str(err)
            return payload

        (
            watchlist_items,
            excluded_non_plex,
            feed_items_total,
            plex_items_total,
            skipped_non_supported_items,
            category_counts,
        ) = self._parse_feed(xml_text)
        payload["excluded_non_plex_items"] = excluded_non_plex
        payload["feed_items_total"] = feed_items_total
        payload["plex_items_total"] = plex_items_total
        payload["skipped_non_supported_items"] = skipped_non_supported_items
        payload["category_counts"] = category_counts
        payload["watchlist_items"] = [self._to_watchlist_item(item) for item in watchlist_items]

        without_date = [item for item in watchlist_items if item.release is None]
        future = [item for item in watchlist_items if item.release is not None and item.release > now_local]
        future.sort(key=lambda item: item.release or now_local)
        weekly = [item for item in watchlist_items if item.release is not None]

        payload["watchlist_items_without_date"] = len(without_date)

        if future:
            nxt = future[0]
            delta = nxt.release.date() - now_local.date() if nxt.release else timedelta(days=0)
            payload["watchlist_next_release_in_days"] = max(delta.days, 0)
            payload["next_release"] = self._to_calendar_item(nxt, pending_count=0)

        payload["weekly_new_items"] = self._count_week_releases(weekly, now_local)

        if feed_items_total > 0 and plex_items_total > 0 and not watchlist_items:
            payload["feed_status"] = "ok_no_supported_items"

        if payload["feed_error"]:
            payload["feed_status"] = "error"

        return payload

    def _parse_feed(self, xml_text: str) -> tuple[list[WatchlistItem], int, int, int, int, dict[str, int]]:
        """Parse RSS/Atom entries and keep only Plex-source show/movie items."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return [], 0, 0, 0, 0, {}

        entries: list[dict[str, str]] = []

        channel = root.find("channel")
        if channel is not None:
            for item in channel.findall("item"):
                entries.append(
                    {
                        "title": (item.findtext("title") or "").strip(),
                        "link": (item.findtext("link") or "").strip(),
                        "description": (item.findtext("description") or "").strip(),
                        "source": (item.findtext("source") or "").strip(),
                        "category": (item.findtext("category") or "").strip(),
                        "pub_date": (item.findtext("pubDate") or "").strip(),
                        "guid": (item.findtext("guid") or "").strip(),
                    }
                )
        else:
            for entry in root.findall(f"{NAMESPACE_ATOM}entry"):
                link_value = ""
                for link in entry.findall(f"{NAMESPACE_ATOM}link"):
                    if link.get("rel") in (None, "alternate"):
                        link_value = (link.get("href") or "").strip()
                        break
                entries.append(
                    {
                        "title": (entry.findtext(f"{NAMESPACE_ATOM}title") or "").strip(),
                        "link": link_value,
                        "description": (entry.findtext(f"{NAMESPACE_ATOM}summary") or "").strip(),
                        "source": "",
                        "category": (entry.findtext(f"{NAMESPACE_ATOM}category") or "").strip(),
                        "pub_date": (entry.findtext(f"{NAMESPACE_ATOM}published") or entry.findtext(f"{NAMESPACE_ATOM}updated") or "").strip(),
                    }
                )

        watchlist_items: list[WatchlistItem] = []
        excluded_non_plex = 0
        plex_items_total = 0
        skipped_non_supported_items = 0
        category_counts: dict[str, int] = {}

        for raw in entries:
            if not self._is_plex_source(raw):
                excluded_non_plex += 1
                continue
            plex_items_total += 1

            category = (raw.get("category") or "unknown").lower()
            category_counts[category] = category_counts.get(category, 0) + 1

            if category not in ("show", "movie"):
                skipped_non_supported_items += 1
                continue

            title = self._normalize_title(raw["title"] or "Unknown item")
            year = self._extract_year(raw.get("title", ""))
            release = self._parse_date(raw.get("pub_date", ""))
            source = raw.get("source") or "plex"
            guid = self._normalize_guid(raw.get("guid", ""))

            watchlist_items.append(
                WatchlistItem(
                    title=title,
                    category=category,
                    year=year,
                    link=raw.get("link", ""),
                    release=release,
                    source=source,
                    guid=guid,
                )
            )

        return (
            watchlist_items,
            excluded_non_plex,
            len(entries),
            plex_items_total,
            skipped_non_supported_items,
            category_counts,
        )

    def _is_plex_source(self, item: dict[str, str]) -> bool:
        source = (item.get("source") or "").lower()
        title = (item.get("title") or "").lower()
        description = (item.get("description") or "").lower()
        link = (item.get("link") or "").strip()

        if "plex" in source:
            return True

        if link:
            host = (urlparse(link).hostname or "").lower()
            if host.endswith("plex.tv") or host.endswith("plex.direct"):
                return True

        metadata = " ".join([title, description])
        # Guardrail: keep only entries that explicitly mention Plex in metadata.
        return "plex" in metadata

    def _normalize_title(self, raw_title: str) -> str:
        title = raw_title.strip()
        # Strip ALL trailing (YYYY) patterns — handles "Show (2022) (2022)" cases
        while True:
            stripped = TITLE_YEAR_RE.sub("", title).strip()
            if stripped == title:
                break
            title = stripped
        return title

    def _normalize_guid(self, raw_guid: str) -> str | None:
        """Return guid if it looks like a tvdb:// or imdb:// identifier."""
        g = raw_guid.strip()
        if g.startswith(("tvdb://", "imdb://", "tmdb://")):
            return g
        return None

    def _extract_year(self, raw_title: str) -> int | None:
        match = TITLE_YEAR_RE.search(raw_title.strip())
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _parse_date(self, value: str) -> datetime | None:
        if not value:
            return None

        try:
            dt_value = parsedate_to_datetime(value)
            return dt_util.as_local(dt_value)
        except (TypeError, ValueError):
            pass

        cleaned = value.strip().replace("Z", "+00:00")
        try:
            dt_value = datetime.fromisoformat(cleaned)
            if dt_value.tzinfo is None:
                dt_value = dt_value.replace(tzinfo=dt_util.UTC)
            return dt_util.as_local(dt_value)
        except ValueError:
            return None

    def _to_watchlist_item(self, item: WatchlistItem) -> dict[str, Any]:
        release_local = item.release.isoformat() if item.release else None
        return {
            "title": item.title,
            "category": item.category,
            "year": item.year,
            "release": release_local,
            "link": item.link,
            "source": item.source,
            "guid": item.guid,
        }

    def _to_calendar_item(self, item: WatchlistItem, pending_count: int) -> dict[str, Any]:
        release_local = item.release.isoformat() if item.release else None
        return {
            "series": item.title if item.category == "show" else None,
            "episode": item.title if item.category == "movie" else None,
            "title": item.title,
            "category": item.category,
            "year": item.year,
            "pending": pending_count,
            "release": release_local,
            "release_day": item.release.date().isoformat() if item.release else None,
            "source": item.source,
            "link": item.link,
        }

    def _count_week_releases(self, items: list[WatchlistItem], now_local: datetime) -> int:
        start_of_week = (now_local - timedelta(days=now_local.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        end_of_week = start_of_week + timedelta(days=7)

        return sum(
            1
            for item in items
            if item.release is not None and start_of_week <= item.release < end_of_week
        )
