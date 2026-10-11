"""Short-lived opaque image URLs and a bounded, public-IP-only image proxy."""
from collections import OrderedDict
import ipaddress
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit, urljoin, quote

import urllib3

_images = OrderedDict()
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(4)
TTL = 600
MAX_IMAGES = 600
MAX_BYTES = 2 * 1024 * 1024


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


def register_image(url):
    if not valid_url(url):
        return ''
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        stale = [key for key, (_, expiry) in _images.items() if expiry <= now]
        for key in stale:
            _images.pop(key, None)
        while len(_images) >= MAX_IMAGES:
            _images.popitem(last=False)
        _images[token] = (url, now + TTL)
    return '/media/' + token


def forget_images():
    with _lock:
        _images.clear()


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


def fetch_public_image(url):
    """Pin the validated address, including on redirects, to prevent rebinding."""
    deadline = time.monotonic() + 10
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
        address = addresses[0]
        path = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
        if parts.query:
            path += '?' + quote(parts.query, safe="%=&/?+:,;@!$'()*-._~")
        authority = '[' + host + ']' if ':' in host else host
        default_port = 443 if parts.scheme == 'https' else 80
        if port != default_port:
            authority += ':' + str(port)
        headers = {'Host': authority, 'User-Agent': 'PortalImageProxy/1.0', 'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/gif', 'Accept-Encoding': 'identity'}
        if parts.scheme == 'https':
            pool = urllib3.HTTPSConnectionPool(address, port=port, server_hostname=host, assert_hostname=host, cert_reqs='CERT_REQUIRED')
        else:
            pool = urllib3.HTTPConnectionPool(address, port=port)
        response = None
        try:
            response = pool.urlopen('GET', path, headers=headers, redirect=False, retries=False,
                                    preload_content=False, timeout=urllib3.Timeout(connect=3, read=3))
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get('Location', ''))
                continue
            if response.status != 200:
                raise ValueError('image unavailable')
            length = response.headers.get('Content-Length')
            if length and int(length) > MAX_BYTES:
                raise ValueError('image too large')
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError('image deadline')
                chunk = response.read(65536, decode_content=False)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError('image too large')
            data = bytes(data)
            mime = image_mime(data)
            if mime is None:
                raise ValueError('unsupported image')
            return data, mime
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise ValueError('too many redirects')


def load_image(token):
    with _lock:
        entry = _images.get(token)
        if not entry or entry[1] <= time.monotonic():
            _images.pop(token, None)
            return None
        url = entry[0]
    if not _slots.acquire(blocking=False):
        return None
    try:
        return fetch_public_image(url)
    except (OSError, ValueError, urllib3.exceptions.HTTPError, UnicodeError):
        return None
    finally:
        _slots.release()
