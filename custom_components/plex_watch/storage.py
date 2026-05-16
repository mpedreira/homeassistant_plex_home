from homeassistant.helpers.storage import Store
from .const import STORAGE_KEY, STORAGE_VERSION

class PlexWatchStorage:
    def __init__(self, hass, entry_id):
        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}")
        self._data = None

    async def async_load(self):
        self._data = await self._store.async_load() or {}
        return self._data

    async def async_save(self, data):
        self._data = data
        await self._store.async_save(data)

    @property
    def data(self):
        return self._data or {}
