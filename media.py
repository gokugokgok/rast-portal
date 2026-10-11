"""Short-lived opaque image URLs and a bounded, public-IP-only image proxy."""
from collections import OrderedDict
import base64
import hashlib
import ipaddress
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit, urljoin, quote

import urllib3

_images = OrderedDict()
_cache = OrderedDict()
_inflight = {}
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(4)
TTL = 600
MAX_IMAGES = 600
MAX_BYTES = 8 * 1024 * 1024
CACHE_BYTES = 24 * 1024 * 1024
CACHE_TTL = 300
_cache_bytes = 0


class ImageBusy(Exception):
    """Transient congestion, distinct from an unavailable image."""


def normalize_source(url):
    if not isinstance(url, str):
        return None
    if url.startswith('//'):
        url = 'https:' + url
    if url.startswith('data:image/'):
        try:
            if len(url) > 350000:
                return None
            header, encoded = url.split(',', 1)
            if header not in ('data:image/png;base64', 'data:image/jpeg;base64', 'data:image/gif;base64', 'data:image/webp;base64', 'data:image/avif;base64'):
                return None
            if image_mime(base64.b64decode(encoded, validate=True)) != header[5:-7]:
                return None
            return url
        except (ValueError, TypeError):
            return None
    return url if valid_url(url) else None


def valid_url(value):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            return None
        if parts.port not in (None, 80, 443):
            return None
        return parts
    except ValueError:
        return None


def register_image(*urls, thumbnail=False):
    sources = tuple(dict.fromkeys(source for url in urls if (source := normalize_source(url))))[:3]
    if not sources:
        return ''
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        existing = None
        for key, (saved, expiry) in list(_images.items()):
            if expiry <= now:
                _images.pop(key, None)
            elif saved == (sources, thumbnail):
                existing = key
        if existing:
            _images[existing] = ((sources, thumbnail), now + TTL)
            _images.move_to_end(existing)
            return '/media/' + existing
        while len(_images) >= MAX_IMAGES:
            _images.popitem(last=False)
        _images[token] = ((sources, thumbnail), now + TTL)
    return '/media/' + token


def forget_images():
    global _cache_bytes
    with _lock:
        _images.clear()
        _cache.clear()
        _cache_bytes = 0


def image_mime(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if len(data) >= 16 and data[4:8] == b'ftyp' and b'avif' in data[8:32]:
        return 'image/avif'
    return None


def fetch_public_image(url, deadline=None, max_bytes=MAX_BYTES, allow_icon=False):
    """Pin the validated address, including on redirects, to prevent rebinding."""
    deadline = deadline or time.monotonic() + 15
    for _ in range(3):
        parts = valid_url(url)
        if parts is None or time.monotonic() >= deadline:
            raise ValueError('invalid image URL')
        host = parts.hostname.encode('idna').decode('ascii')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError('non-public image address')
        # A host can advertise IPv6 even when the deployment cannot use it.
        addresses.sort(key=lambda value: ipaddress.ip_address(value).version)
        path = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
        if parts.query:
            path += '?' + quote(parts.query, safe="%=&/?+:,;@!$'()*-._~")
        authority = '[' + host + ']' if ':' in host else host
        default_port = 443 if parts.scheme == 'https' else 80
        if port != default_port:
            authority += ':' + str(port)
        # Fixed browser-compatible UA; never copy the visitor's browser headers.
        headers = {'Host': authority, 'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36', 'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/gif', 'Accept-Encoding': 'identity'}
        for number, address in enumerate(addresses[:2]):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('image deadline')
            if parts.scheme == 'https':
                pool = urllib3.HTTPSConnectionPool(address, port=port, server_hostname=host, assert_hostname=host, cert_reqs='CERT_REQUIRED')
            else:
                pool = urllib3.HTTPConnectionPool(address, port=port)
            response = None
            try:
                response = pool.urlopen('GET', path, headers=headers, redirect=False, retries=False,
                                        preload_content=False, timeout=urllib3.Timeout(connect=min(3, remaining), read=min(5, remaining)))
                if response.status in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('Location', ''))
                    break
                if response.status != 200:
                    raise ValueError('image unavailable')
                length = response.headers.get('Content-Length')
                if length and int(length) > max_bytes:
                    raise ValueError('image too large')
                data = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise ValueError('image deadline')
                    chunk = response.read(65536, decode_content=False)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError('image too large')
                data = bytes(data)
                mime = image_mime(data)
                if allow_icon and data.startswith(b'\x00\x00\x01\x00') and len(data) >= 22:
                    mime = 'image/x-icon'
                if mime is None:
                    raise ValueError('unsupported image')
                return data, mime
            except (OSError, urllib3.exceptions.HTTPError):
                if number + 1 >= min(2, len(addresses)):
                    raise
            finally:
                if response is not None:
                    response.close()
                pool.close()
    raise ValueError('too many redirects')


def cached_image(url, deadline, fetch_timeout=15):
    global _cache_bytes
    key = hashlib.sha256(url.encode()).digest()
    while True:
        with _lock:
            now = time.monotonic()
            for stale in list(_cache):
                result, expiry = _cache[stale]
                if expiry <= now:
                    _cache.pop(stale)
                    _cache_bytes -= len(result[0]) if result else 0
            if key in _cache:
                _cache.move_to_end(key)
                return _cache[key][0]
            event = _inflight.get(key)
            if event is None:
                event = _inflight[key] = threading.Event()
                break
        if not event.wait(max(0, deadline - time.monotonic())):
            raise ImageBusy()
    acquired = False
    try:
        acquired = _slots.acquire(timeout=max(0, min(2, deadline - time.monotonic())))
        if not acquired:
            raise ImageBusy()
        try:
            if url.startswith('data:image/'):
                data = base64.b64decode(url.split(',', 1)[1], validate=True)
                result = (data, image_mime(data))
            else:
                result = fetch_public_image(url, deadline=min(deadline, time.monotonic() + fetch_timeout))
        except (OSError, ValueError, urllib3.exceptions.HTTPError, UnicodeError):
            result = None
        with _lock:
            size = len(result[0]) if result else 0
            while _cache and (_cache_bytes + size > CACHE_BYTES or len(_cache) >= MAX_IMAGES):
                _, (removed, _) = _cache.popitem(last=False)
                _cache_bytes -= len(removed[0]) if removed else 0
            _cache[key] = (result, time.monotonic() + (CACHE_TTL if result else 10))
            _cache_bytes += size
        return result
    finally:
        if acquired:
            _slots.release()
        with _lock:
            _inflight.pop(key, None)
            event.set()


def load_image(token):
    with _lock:
        entry = _images.get(token)
        if not entry or entry[1] <= time.monotonic():
            _images.pop(token, None)
            return None
        sources, thumbnail = entry[0]
    deadline = time.monotonic() + (6 if thumbnail else 24)
    for source in sources:
        if time.monotonic() >= deadline:
            return None
        image = cached_image(source, deadline, fetch_timeout=3 if thumbnail else 15)
        if image is not None:
            return image
    return None
