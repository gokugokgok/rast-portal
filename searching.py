"""Bounded page pooling: changing display size never discards source results."""
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
import yaml
from cache import MemoryCache

COUNTS = (10, 20, 40, 60, 100)
MODES = {'fast': '高速', 'balanced': 'バランス', 'wide': '広く検索'}
ENGINE_SETS = {
    'all': ('google', 'bing', 'duckduckgo', 'brave'),
    'images': ('google images', 'bing images', 'duckduckgo images', 'brave.images'),
    'videos': ('google videos', 'bing videos', 'duckduckgo videos', 'brave.videos'),
    'news': ('google news', 'bing news', 'duckduckgo news', 'brave.news'),
}


def configured_engines(settings):
    keep = settings.get('use_default_settings', {}).get('engines', {}).get('keep_only')
    disabled = {item['name'] for item in settings.get('engines', [])
                if item.get('disabled') or item.get('inactive')}
    return {category: tuple(name for name in names if name not in disabled and (keep is None or name in keep))
            for category, names in ENGINE_SETS.items()}


ACTIVE_ENGINES = configured_engines(yaml.safe_load(Path(__file__).with_name('searxng-settings.yml').read_text()))
TIMEOUTS = {'fast': 1.5, 'balanced': 2.2, 'wide': 3.5}
search_cache = MemoryCache(ttl=60, max_entries=24, max_bytes=4 * 1024 * 1024)
_locks = [threading.Lock() for _ in range(64)]
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix='search')
FIELDS = ('url', 'title', 'content', 'thumbnail_src', 'thumbnail', 'img_src',
          'template', 'resolution', 'length', 'duration', 'publishedDate', 'pubdate', 'engines')


class SearchError(Exception):
    def __init__(self, status=502):
        self.status = status


def result_key(item, category):
    """Remove tracking-only parameters, keep functional URL parameters."""
    url = item.get('url')
    if not isinstance(url, str) or any(ord(c) < 32 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return None
        # Reading port also validates malformed authorities.
        port = parsed.port
        netloc = parsed.hostname.lower()
        if ':' in netloc:
            netloc = '[' + netloc + ']'
        if port and not (parsed.scheme == 'http' and port == 80 or parsed.scheme == 'https' and port == 443):
            netloc += ':' + str(port)
        params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                  if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid', 'msclkid')]
        canonical = urlunsplit((parsed.scheme, netloc, parsed.path or '/', urlencode(params), ''))
        # Several distinct images can belong to the same article.
        image = str(item.get('img_src') or item.get('thumbnail_src') or item.get('thumbnail') or '') if category == 'images' else ''
        return canonical, image
    except ValueError:
        return None


def fetch_page(query, category, safe, region, mode, match, number, budget):
    engines = ACTIVE_ENGINES[category][:{'fast': 2, 'balanced': 3, 'wide': 4}[mode]]
    if not engines:
        raise SearchError(503)
    if not budget():
        raise SearchError(429)
    upstream_query = '"' + query.replace('"', ' ') + '"' if match == 'phrase' else query
    with requests.Session() as client:
        client.trust_env = False
        response = client.post(
            'http://127.0.0.1:8888/search',
            headers={'Accept': 'application/json'},
            # SearXNG adds category engines to explicit engines when BOTH are
            # sent. Only send engines, or fast mode would silently use all four.
            data={'q': upstream_query, 'format': 'json', 'language': region,
                  'engines': ','.join(engines), 'pageno': number,
                  'safesearch': safe, 'timeout_limit': TIMEOUTS[mode]},
            timeout=(1, 5), allow_redirects=False,
        )
    if response.status_code != 200:
        raise SearchError(429 if response.status_code == 429 else 502)
    raw = response.json()
    if not isinstance(raw, dict) or not isinstance(raw.get('results', []), list):
        raise SearchError()
    results = []
    for item in raw.get('results', [])[:100]:
        if isinstance(item, dict) and result_key(item, category):
            results.append({key: item[key] for key in FIELDS if key in item})
    return {'results': results, 'failed': bool(raw.get('unresponsive_engines'))}


def search_results(query, category, safe, region, mode, match, page, count, budget, max_fetch=6):
    key = (query, category, safe, region, mode, match)
    lock = _locks[int.from_bytes(search_cache.key(*key)[:2], 'big') % len(_locks)]
    if not lock.acquire(timeout=1):
        raise SearchError(503)
    try:
        pool = search_cache.get(*key) or {'results': [], 'next': 1, 'exhausted': False, 'failed': False, 'yield': 20}
        seen = {result_key(item, category) for item in pool['results']}
        target = (page + 1) * count
        fetched = 0
        fetch_error = None
        # Default searches normally need one source page. Large result sizes
        # can request up to three source pages together, six in one HTTP POST.
        # This cap also bounds deliberately forged far-away page requests.
        while len(pool['results']) < target and not pool['exhausted'] and fetched < max_fetch:
            # Learn the actual page size first; image providers sometimes
            # return 100 items at once, so speculative extra pages waste time.
            estimate = max(10, pool['yield'])
            batch = min(3, max_fetch - fetched, max(1, math.ceil((target - len(pool['results'])) / estimate)))
            if pool['next'] == 1:
                batch = 1
            batch = min(batch, 51 - pool['next'])
            if batch <= 0 or len(pool['results']) >= 1000:
                pool['exhausted'] = True
                break
            numbers = range(pool['next'], pool['next'] + batch)
            futures = [_executor.submit(fetch_page, query, category, safe, region, mode, match, n, budget) for n in numbers]
            # Consume in page order, regardless of completion order. Never
            # reorder older displayed pages when new source pages arrive.
            stop = False
            for future in futures:
                try:
                    payload = future.result()
                except (SearchError, requests.RequestException, ValueError, TypeError, AttributeError) as error:
                    fetch_error = error if isinstance(error, SearchError) else SearchError()
                    stop = True
                    continue
                if stop:
                    continue  # A failed page must be retried before later pages.
                pool['failed'] = pool['failed'] or payload['failed']
                added = 0
                for item in payload['results']:
                    identity = result_key(item, category)
                    if identity not in seen and len(pool['results']) < 1000:
                        seen.add(identity)
                        pool['results'].append(item)
                        added += 1
                if not added:
                    if payload['failed']:
                        fetch_error = SearchError(503)
                    else:
                        pool['exhausted'] = True
                    stop = True
                else:
                    pool['yield'] = added
                    pool['next'] += 1
            fetched += batch
            if stop:
                break
        if not pool['results'] and fetch_error and not pool['failed']:
            raise fetch_error
        if pool['results']:
            search_cache.put(pool, *key)
        # The cache may expire while someone reads a deep page. Recover to an
        # available page, rather than discard fetched results or loop forever.
        resolved = min(page, max(0, (len(pool['results']) - 1) // count))
        start = resolved * count
        results = pool['results'][start:start + count]
        more = resolved < 9 and (start + count < len(pool['results']) or bool(results) and not pool['exhausted'])
        available = math.ceil(len(pool['results']) / count)
        # Like the previous UI, these are navigation targets, not a claimed
        # total page count. An unseen page can require additional source pages.
        end = 10 if more and not pool['exhausted'] else min(10, available)
        first = max(0, min(resolved - 2, end - 5))
        return {'results': results, 'page': resolved, 'more': more,
                'page_numbers': list(range(first, min(end, first + 5))),
                'unresponsive_engines': pool['failed'] or bool(fetch_error),
                'limited': not pool['exhausted'] and len(results) < count,
                'recovered': page != resolved}
    finally:
        lock.release()
