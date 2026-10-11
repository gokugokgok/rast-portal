"""Bounded, expiring response cache; no plaintext query keys or disk writes."""
from collections import OrderedDict
import hashlib
import json
import threading
import time


class MemoryCache:
    def __init__(self, ttl, max_entries, max_bytes):
        self.ttl, self.max_entries, self.max_bytes = ttl, max_entries, max_bytes
        self._items = OrderedDict()
        self._lock = threading.Lock()
        self._bytes = 0

    @staticmethod
    def key(*parts):
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).digest()

    def _expire(self):
        now = time.monotonic()
        for key in list(self._items):
            data, expires = self._items[key]
            if expires <= now:
                self._bytes -= len(data)
                del self._items[key]

    def get(self, *parts):
        key = self.key(*parts)
        with self._lock:
            self._expire()
            entry = self._items.get(key)
            if entry is None:
                return None
            self._items.move_to_end(key)
            return json.loads(entry[0])

    def put(self, value, *parts):
        data = json.dumps(value, ensure_ascii=False).encode()
        if len(data) > self.max_bytes:
            return
        key = self.key(*parts)
        with self._lock:
            self._expire()
            old = self._items.pop(key, None)
            if old:
                self._bytes -= len(old[0])
            while self._items and (len(self._items) >= self.max_entries or self._bytes + len(data) > self.max_bytes):
                _, (removed, _) = self._items.popitem(last=False)
                self._bytes -= len(removed)
            self._items[key] = (data, time.monotonic() + self.ttl)
            self._bytes += len(data)

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0
