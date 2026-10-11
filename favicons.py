"""Asynchronous favicon proxy; send domain only, never search terms."""
from collections import OrderedDict
import ipaddress
import secrets
import threading
import time
from urllib.parse import quote
import urllib3
from media import valid_url, fetch_public_image, ImageBusy

_tokens = OrderedDict()
_cache = OrderedDict()
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(2)


def register_icon(site_url):
    parts = valid_url(site_url)
    if parts is None:
        return ''
    try:
        host = parts.hostname.encode('idna').decode('ascii')
    except UnicodeError:
        return ''
    if host == 'localhost' or '.' not in host:
        return ''
    try:
        if not ipaddress.ip_address(host).is_global:
            return ''
    except ValueError:
        pass
    origin = parts.scheme + '://' + host
    now = time.monotonic()
    with _lock:
        for token, (saved_origin, _, expires) in list(_tokens.items()):
            if expires <= now:
                del _tokens[token]
            elif saved_origin == origin:
                _tokens[token] = (origin, host, now + 600)
                _tokens.move_to_end(token)
                return '/icon/' + token
        while len(_tokens) >= 600:
            _tokens.popitem(last=False)
        token = secrets.token_urlsafe(24)
        _tokens[token] = (origin, host, now + 600)
    return '/icon/' + token


def load_icon(token):
    with _lock:
        entry = _tokens.get(token)
        now = time.monotonic()
        if not entry or entry[2] <= now:
            _tokens.pop(token, None)
            return None
        origin, host, _ = entry
        for key, (_, expiry) in list(_cache.items()):
            if expiry <= now:
                del _cache[key]
        if origin in _cache:
            _cache.move_to_end(origin)
            return _cache[origin][0]
    if not _slots.acquire(blocking=False):
        raise ImageBusy()
    try:
        result = None
        deadline = time.monotonic() + 6
        # Same cached-icon source supported by SearXNG's DuckDuckGo resolver.
        for url in ('https://icons.duckduckgo.com/ip2/' + quote(host, safe='.') + '.ico', origin + '/favicon.ico'):
            if time.monotonic() >= deadline:
                break
            try:
                result = fetch_public_image(url, deadline=min(deadline, time.monotonic() + 3), max_bytes=65536, allow_icon=True)
                break
            except (OSError, ValueError, UnicodeError, urllib3.exceptions.HTTPError):
                continue
        with _lock:
            while len(_cache) >= 128:
                _cache.popitem(last=False)
            _cache[origin] = (result, time.monotonic() + (3600 if result else 60))
        return result
    finally:
        _slots.release()


def clear_icons():
    with _lock:
        _tokens.clear()
        _cache.clear()
