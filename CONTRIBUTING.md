# Contributing to Plex Watch

Thank you for your interest in contributing! This document explains how to set up a development environment, submit changes, and follow the project conventions.

---

## Table of contents

- [Code of conduct](#code-of-conduct)
- [Getting started](#getting-started)
- [Development environment](#development-environment)
- [Project structure](#project-structure)
- [Making changes](#making-changes)
- [Commit messages](#commit-messages)
- [Pull request process](#pull-request-process)
- [Reporting bugs](#reporting-bugs)
- [Requesting features](#requesting-features)

---

## Code of conduct

Be respectful. Constructive criticism is welcome; personal attacks are not. All contributors are expected to follow the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).

---

## Getting started

1. **Fork** the repository on GitHub.
2. **Clone** your fork:
   ```bash
   git clone https://github.com/<your-user>/plex_watch.git
   cd plex_watch
   ```
3. Create a feature branch:
   ```bash
   git checkout -b feat/my-feature
   ```

---

## Development environment

### Requirements

- Python 3.12+
- A running Home Assistant instance (Docker recommended)
- A Plex account with at least one accessible server

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install homeassistant aiohttp
```

### Deploying to a local HA instance (Docker)

```bash
# Copy the integration into HA config
cp -r custom_components/plex_watch /path/to/ha/config/custom_components/

# Restart HA
docker restart homeassistant
```

Or use the included helper script pattern:

```bash
scp -r custom_components/plex_watch user@ha-host:/root/homeassistant/custom_components/
ssh user@ha-host "docker restart homeassistant"
```

### Running the standalone API test

A `test_plex.py` script at the repo root can be used to verify connectivity without HA:

```bash
export PLEX_TOKEN=your_plex_account_token
python3 test_plex.py
```

It will print your servers, connection URIs, and whether `accessToken` is present for each one.

---

## Project structure

```
custom_components/plex_watch/
├── __init__.py          # Entry setup, service registration
├── config_flow.py       # UI config flow (PIN auth + server selection)
├── const.py             # Constants and config key names
├── coordinator.py       # DataUpdateCoordinator — polls Plex every 30 s
├── manifest.json        # HA integration manifest
├── options_flow.py      # "Configure" button — change server / watched series
├── plex_api.py          # All Plex API calls (plex.tv + direct server)
├── sensor.py            # Six SensorEntity subclasses
├── services.yaml        # Service schema declarations
├── storage.py           # HA Storage helper for persistent state
└── translations/
    ├── en.json
    └── es.json
```

### Key concepts

| File | Responsibility |
|---|---|
| `plex_api.py` | Stateless HTTP layer. All methods are `async`. Returns plain dicts/lists. |
| `coordinator.py` | Orchestrates API calls, manages rediscovery, feeds `coordinator.data` |
| `sensor.py` | Pure presentation: reads `coordinator.data`, no API calls |
| `config_flow.py` | PIN auth → server selection → saves entry data |
| `options_flow.py` | Re-reads entry data, saves updates via `async_create_entry` |

---

## Making changes

### Adding a new sensor

1. Implement the class in `sensor.py` inheriting from `CoordinatorEntity` and `SensorEntity`.
2. Add a unique `_attr_unique_id` using `f"{entry_id}_my_sensor"`.
3. Register it in `async_setup_entry` in `sensor.py`.
4. If the sensor needs new data, add the fetch call in `coordinator.py:_async_update_data` and include it in the return dict.

### Adding a new API method

1. Add the method to `PlexAPI` in `plex_api.py`.
2. Use `self._server_headers()` for direct Plex server calls (requires the server-specific token).
3. Use `self._headers()` for plex.tv API calls (uses the account token).
4. Return `None` (or empty list) on error — never raise from an API method.
5. Log errors at `WARNING` level using `_LOGGER`.

### Adding a new config option

1. Add the constant to `const.py` as `CONF_MY_OPTION = "my_option"`.
2. Add it to the `options_flow.py` form schema and save it in `async_create_entry`.
3. Read it in `coordinator.py` via `self.entry.options.get(CONF_MY_OPTION, self.entry.data.get(CONF_MY_OPTION, default))`.
4. Add the label to `translations/en.json` and `translations/es.json`.

---

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>

[optional body]
```

| Type | When to use |
|---|---|
| `feat` | New feature or sensor |
| `fix` | Bug fix |
| `refactor` | Code restructure without behaviour change |
| `docs` | README, CONTRIBUTING, docstrings |
| `chore` | Dependency updates, CI config |

**Examples:**

```
feat(sensor): add plex_new_episodes unwatched count
fix(coordinator): handle None return from get_sessions
docs: update common errors for accessToken issue
```

---

## Pull request process

1. Ensure your branch is up to date with `main`:
   ```bash
   git fetch origin
   git rebase origin/main
   ```
2. Check for syntax errors before pushing:
   ```bash
   python3 -c "import ast; [ast.parse(open(f).read()) for f in $(find custom_components/plex_watch -name '*.py')]"
   ```
3. Open the PR against `main` with:
   - A clear title following the commit convention.
   - A description of **what** changed and **why**.
   - Steps to reproduce if it is a bug fix.
   - Screenshots or sensor state examples if it is a UI/sensor change.
4. A maintainer will review within a few days. Please address review comments with new commits (do not force-push during review).
5. Once approved, the maintainer will squash-merge.

---

## Reporting bugs

Open an issue and include:

- Home Assistant version (`Settings → About`)
- Plex Watch version (from `manifest.json`)
- Whether the server is **owned** or **shared**
- Relevant lines from the HA log (`Settings → System → Logs`, filter by `plex_watch`)
- What you expected vs. what happened

**Do not include your Plex token in the issue.** Redact it as `***`.

---

## Requesting features

Open an issue with the label `enhancement` and describe:

- The use case (what automation or dashboard would this enable?)
- Which Plex API endpoint would provide the data (if known)
- Whether the feature requires admin access on the server
