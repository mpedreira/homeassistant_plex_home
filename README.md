# Plex Watch — Home Assistant Custom Integration

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![HA version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-brightgreen)](https://www.home-assistant.io/)
[![hacs_badge](https://img.shields.io/badge/HACS-Manual-orange.svg)](https://hacs.xyz/)

Monitor your [Plex Media Server](https://www.plex.tv/) from Home Assistant. Track what is playing right now, which shows have new episodes, your continue-watching queue, and server health — all as native HA sensors ready to use in automations and dashboards.

---

## Table of contents

- [Features](#features)
- [Sensors](#sensors)
- [Services](#services)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Automations examples](#automation-examples)
- [Common errors](#common-errors)
- [Known limitations](#known-limitations)
- [License](#license)

---

## Features

- **PIN-based OAuth** — no passwords stored; uses the official Plex auth flow
- **Multi-server** — add one entry per Plex server
- **Owned and shared servers** — works on servers you do not own (with graceful degradation)
- **Auto-rediscovery** — detects when the server IP changes and reconnects automatically
- **Configurable poll interval** — default 30 s
- **Local/remote toggle** — optionally prefer LAN connections
- **Watched series tracking** — track unfinished episodes per show
- **HA Storage** — remembers detected series across restarts

---

## Sensors

Six sensors are created per configured server.

| Entity | What it shows | Attributes |
|---|---|---|
| `sensor.plex_now_playing` | Number of active streams on the server | `sessions` list with show, user, player, progress % |
| `sensor.plex_latest_added` | Most recently added episode | `grandparent_title`, `season`, `episode`, `added_at` |
| `sensor.plex_new_series_detected` | `True` / `False` — a brand-new show appeared in the library | — |
| `sensor.plex_new_episodes` | Total **unwatched** episodes across your tracked series | `by_series` dict with per-show count |
| `sensor.plex_server_status` | `online` / `offline` | — |
| `sensor.plex_on_deck` | What **you** are watching right now (or `none` if idle) | `playing`, `show`, `season`, `episode`, `progress_pct`, `player` |

> **Note:** `plex_now_playing` requires admin access on the server. On shared (non-owned) servers it will always show `0` — this is a Plex server restriction, not a bug.

---

## Services

### `plex_watch.play_series`

Send a play command to a Plex client.

| Field | Required | Description |
|---|---|---|
| `series_rating_key` | ✅ | Plex `ratingKey` of the series (visible in the Plex web URL) |
| `client_identifier` | ✅ | `machineIdentifier` of the target Plex client |

```yaml
service: plex_watch.play_series
data:
  series_rating_key: "12345"
  client_identifier: "abc123def456"
```

### `plex_watch.get_current_series`

Refreshes the state `plex_watch.current_series` with the title of the currently playing episode.

```yaml
service: plex_watch.get_current_series
```

---

## Installation

### Manual (recommended until HACS listing)

1. Download or clone this repository.
2. Copy the `custom_components/plex_watch/` folder into your HA config directory:
   ```
   /config/custom_components/plex_watch/
   ```
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add integration** and search for **Plex Watch**.

### Via HACS (custom repository)

1. In HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/<your-user>/plex_watch` as type **Integration**.
3. Install **Plex Watch** and restart HA.

---

## Configuration

1. **Settings → Devices & Services → Add integration → Plex Watch**
2. A Plex PIN is generated. Visit the displayed URL on any device logged in to your Plex account.
3. After authorisation, select the server you want to monitor.
4. Optionally enable **Use local connection** to prefer your LAN IP.

The integration does **not** store your Plex password. It stores only the OAuth token returned by Plex.

---

## Options

After setup, click **Configure** on the integration card to change:

| Option | Description |
|---|---|
| **Server** | Switch to a different server without re-authenticating |
| **Use local connection** | Prefer LAN address (useful if HA and Plex are on the same network) |
| **Watched series** | Comma-separated list of show titles to track (`Berlin, The Bear, Severance`) |

---

## Automation examples

### Notify when a new episode of a tracked show is added

```yaml
automation:
  - alias: "Plex — new episode alert"
    trigger:
      - platform: state
        entity_id: sensor.plex_new_episodes
    condition:
      - condition: template
        value_template: "{{ trigger.to_state.state | int > trigger.from_state.state | int }}"
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: >
            {{ state_attr('sensor.plex_new_episodes', 'by_series') | to_json }}
```

### Turn on the TV when you start watching

```yaml
automation:
  - alias: "Plex — turn on TV on playback"
    trigger:
      - platform: state
        entity_id: sensor.plex_on_deck
    condition:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.playing == true }}"
    action:
      - service: media_player.turn_on
        target:
          entity_id: media_player.living_room_tv
```

### Alert when the Plex server goes offline

```yaml
automation:
  - alias: "Plex — server offline alert"
    trigger:
      - platform: state
        entity_id: sensor.plex_server_status
        to: "offline"
    action:
      - service: notify.notify
        data:
          message: "Plex server is offline!"
```

---

## Common errors

### `unknown` on all sensors after setup

**Cause:** HA cannot reach the Plex server URL stored during setup (usually a LAN address that is not accessible from the Docker container).

**Fix:** The integration auto-rediscovers the public URL on the first poll. Wait one poll cycle (30 s). If it persists, check your HA logs for `[plex_watch] Rediscovery` messages.

---

### `sensor.plex_now_playing` always shows `0`

**Cause:** The `/status/sessions` endpoint requires Plex server admin rights. Shared (non-owned) servers return HTTP 403.

**Fix:** This is a Plex restriction — not a bug. The sensor will always be `0` on servers you do not own. If you are the owner, verify that the stored `accessToken` is correct by re-adding the integration.

---

### `sensor.plex_on_deck` shows `unknown` instead of the last episode

**Cause:** Both the sessions endpoint (HTTP 403) and the on-deck endpoint (HTTP 401) failed. This usually means the server-specific `accessToken` was not obtained during setup (it can be `null` for shared servers depending on the owner's sharing settings).

**Fix:**
1. Check HA logs for `Server 'X' accessToken present: False`. If false, the Plex server owner has not enabled token sharing.
2. Re-add the integration to force a fresh token fetch.
3. Ask the server owner to check **Settings → Sharing** on their Plex server.

---

### `sensor.plex_new_episodes` shows `0` or never updates

**Cause:** The show titles in **Watched Series** must match exactly what Plex stores as the series title.

**Fix:** Check the exact title in Plex (e.g. `The Bear`, not `bear` or `The Bear (2022)`). Titles are matched case-insensitively but must otherwise be exact.

---

### Integration fails to load after HA update

**Cause:** Breaking changes in HA's config entry or options flow API between major versions.

**Fix:** Open an issue on GitHub with your HA version and the full traceback from the HA log.

---

### HTTP 401 on direct server calls

**Cause:** The account token has expired, or the server-specific `accessToken` is missing.

**Fix:** Delete the integration entry and re-add it. The PIN flow will obtain a fresh token.

---

### `Failed to load services.yaml`

**Cause:** HA schema validation issue on first load before restart. Usually self-resolving.

**Fix:** Restart HA once after deploying. If it persists, check that `services.yaml` is present in the `plex_watch/` folder.

---

## Known limitations

| Limitation | Reason |
|---|---|
| `plex_now_playing` = 0 on shared servers | Plex admin-only endpoint |
| `plex_on_deck` may show `unknown` on shared servers | Server-specific token may not be available |
| Managed / home users not supported | Requires additional Plex API calls not yet implemented |
| No real-time push updates | Plex does not provide webhooks for non-Plex-Pass users; integration polls every 30 s |
| `play_series` requires knowing `ratingKey` | No UI selector yet; key is visible in the Plex web URL |

---

## License

```
Copyright 2026 Manuel PA

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
