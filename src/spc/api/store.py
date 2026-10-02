"""In-memory dataset store.

A stand-in until a database is added. It holds a limited number of datasets and drops the least
recently used one when full. Datasets are immutable, so an update replaces the entry.
Data is lost when the server stops. Archive results by exporting them.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

from spc.data import Dataset


class DatasetNotFound(KeyError):
    """No dataset with this id. The id is wrong, or the server restarted."""


class DatasetStore:
    def __init__(self, max_items: int = 50):
        self._items: OrderedDict[str, Dataset] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_items

    def add(self, dataset: Dataset) -> str:
        key = uuid.uuid4().hex
        with self._lock:
            self._items[key] = dataset
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return key

    def get(self, key: str) -> Dataset:
        with self._lock:
            if key not in self._items:
                raise DatasetNotFound(key)
            self._items.move_to_end(key)
            return self._items[key]

    def replace(self, key: str, dataset: Dataset) -> None:
        with self._lock:
            if key not in self._items:
                raise DatasetNotFound(key)
            self._items[key] = dataset
            self._items.move_to_end(key)

    def __len__(self) -> int:
        return len(self._items)
