import html
import os
import re
import threading
import time
from urllib.parse import urlsplit, quote

import requests
from flask import Flask, Response, redirect, render_template, request
from media import register_image, load_image
from cache import MemoryCache

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=8192)
_lock = threading.Lock()
_window = 0
_used = 0
CATEGORIES = {'all': 'general', 'images': 'images', 'videos': 'videos', 'news': 'news'}
TABS = [('all', 'すべて'), ('images', '画像'), ('videos', '動画'), ('news', 'ニュース')]
search_cache = MemoryCache(ttl=30, max_entries=32, max_bytes=2 * 1024 * 1024)
wiki_cache = MemoryCache(ttl=300, max_entries=64, max_bytes=512 * 1024)
_wiki_slots = threading.BoundedSemaphore(2)


def plain_text(value):
    return html.unescape(re.sub(r'<[^>]*>', '', str(value or '')))


def safe_link(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            return None
        return parts
    except ValueError:
        return None


def budget_available():
    """Global per-worker budget; retain no IPs or queries."""
    global _window, _used
    minute = int(time.monotonic() // 60)
    with _lock:
        if minute != _window:
            _window, _used = minute, 0
        if _used >= 30:
            return False
        _used += 1
        return True


@app.before_request
def reject_query_urls():
    # Do not accept GET searches, even if pasted manually.
    if request.query_string:
        return redirect(request.path, code=303)
    if request.method == 'POST':
        origin = request.headers.get('Origin')
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return 'Forbidden', 403
        # With no-referrer, some browsers send Origin:null for same-site forms.
        if origin == 'null':
            if request.headers.get('Sec-Fetch-Site') != 'same-origin':
                return 'Forbidden', 403
            origin = None
        if origin:
            try:
                parsed = urlsplit(origin)
                if parsed.scheme not in ('http', 'https') or parsed.netloc != request.host:
                    return 'Forbidden', 403
            except ValueError:
                return 'Forbidden', 403


@app.after_request
def privacy_headers(response):
    response.headers.update({
        'Cache-Control': 'no-store, private, max-age=0',
        'Pragma': 'no-cache',
        'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-Robots-Tag': 'noindex, nofollow, noarchive',
        'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
        'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    })
    return response


def show(error=None, status=200, **context):
    context.setdefault('category', 'all')
    context.setdefault('safesearch', '1')
    context['tabs'] = TABS
    template = 'results.html' if request.headers.get('X-Portal-Request') == '1' else 'index.html'
    return render_template(template, error=error, **context), status


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        return show()
    query = request.form.get('q', '').strip()
    if not query or len(query) > 400 or len(query.split()) > 50:
        return show('1〜400文字、50単語以内で入力してください。', 400)
    try:
        page = int(request.form.get('page', '0'))
        if not 0 <= page <= 9:
            raise ValueError()
    except ValueError:
        return show('ページ指定が無効です。', 400)
    category = request.form.get('kind') or request.form.get('category', 'all')
    safesearch = request.form.get('safesearch', '1')
    if category not in CATEGORIES or safesearch not in ('0', '1', '2'):
        return show('検索設定が無効です。', 400)
    try:
        payload = search_cache.get(query, category, safesearch, page)
        if payload is None:
            if not budget_available():
                return show('アクセスが集中しています。1分ほど待って再試行してください。', 429)
            # No user IP, browser headers, cookies or referrer forwarded upstream.
            with requests.Session() as client:
                client.trust_env = False
                upstream = client.post(
                    'http://127.0.0.1:8888/search',
                    headers={'Accept': 'application/json'},
                    data={'q': query, 'format': 'json', 'language': 'ja-JP',
                          'categories': CATEGORIES[category], 'pageno': page + 1, 'safesearch': safesearch},
                    timeout=(2, 8), allow_redirects=False,
                )
            if upstream.status_code == 429:
                return show('検索サービスの利用上限に達しました。時間を置いて再試行してください。', 429)
            if upstream.status_code != 200:
                return show('検索サービスに接続できませんでした。', 502)
            raw = upstream.json()
            # Do not cache the provider's query, timing or request metadata.
            payload = {'results': raw.get('results', [])[:60],
                       'unresponsive_engines': bool(raw.get('unresponsive_engines'))}
            if payload['results'] and not payload['unresponsive_engines']:
                search_cache.put(payload, query, category, safesearch, page)
        results = []
        for item in payload.get('results', []):
            if not isinstance(item, dict):
                continue
            url = item.get('url', '')
            parsed = safe_link(url)
            if parsed is None:
                continue
            kind = 'image' if item.get('template') == 'images.html' or category == 'images' else 'video' if item.get('template') == 'videos.html' or category == 'videos' else 'web'
            thumbnail = item.get('thumbnail_src') or item.get('thumbnail') or item.get('img_src')
            results.append({'url': url, 'host': parsed.hostname,
                            'title': plain_text(item.get('title') or url),
                            'description': plain_text(item.get('content')),
                            'kind': kind, 'thumbnail': register_image(thumbnail),
                            'preview': register_image(item.get('img_src') or thumbnail) if kind == 'image' else '',
                            'resolution': plain_text(item.get('resolution')),
                            'duration': plain_text(item.get('length') or item.get('duration')),
                            'date': plain_text(item.get('publishedDate') or item.get('pubdate'))[:10],
                            'engines': ', '.join(str(x) for x in item.get('engines', []) if isinstance(x, str))})
            if len(results) >= 60:
                break
        # The JSON API has no definitive has-next-page flag.
        more = bool(results) and page < 9
        failed = bool(payload.get('unresponsive_engines', []))
        warning = '一部の検索元が応答していません。取得できた結果を表示しています。' if failed and results else None
        if failed and not results:
            warning = '検索元が応答しませんでした。少し待って再試行してください。'
        end = 10 if more else page + 1
        start = max(0, min(page - 2, end - 5))
        page_numbers = range(start, min(end, start + 5)) if results or page > 0 else []
        return show(results=results, searched=True, q=query, page=page, more=more, warning=warning,
                    category=category, safesearch=safesearch, page_numbers=page_numbers)
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # Never print provider errors: those can contain the query or credentials.
        return show('検索に失敗しました。少し待って再試行してください。', 502)


@app.post('/overview')
def overview():
    query = request.form.get('q', '').strip()
    if not query or len(query) > 400 or len(query.split()) > 50:
        return render_template('overview.html', wiki=None, wiki_error='入力を確認してください。'), 400
    cached = wiki_cache.get(query)
    if cached is not None:
        return render_template('overview.html', wiki=cached or None)
    if not _wiki_slots.acquire(blocking=False):
        return render_template('overview.html', wiki=None, wiki_error='概要を取得できませんでした。'), 503
    try:
        with requests.Session() as client:
            client.trust_env = False
            response = client.post(
                'https://ja.wikipedia.org/w/api.php',
                data={'action': 'query', 'format': 'json', 'formatversion': '2',
                      'generator': 'search', 'gsrsearch': query, 'gsrnamespace': '0', 'gsrlimit': '1',
                      'prop': 'extracts', 'exintro': '1', 'explaintext': '1', 'exchars': '500', 'exlimit': '1'},
                headers={'Accept': 'application/json',
                         'User-Agent': 'PortalSearch/1.1 (Wikipedia overview; https://github.com/gokugokgok/portal-new)'},
                timeout=(2, 4), allow_redirects=False,
            )
        if response.status_code != 200:
            raise ValueError('Wikipedia unavailable')
        payload = response.json()
        if 'error' in payload:
            raise ValueError('Wikipedia API error')
        pages = payload.get('query', {}).get('pages', [])
        wiki = {}
        if pages and isinstance(pages[0], dict):
            title = plain_text(pages[0].get('title'))[:300]
            extract = plain_text(pages[0].get('extract')).strip()[:700]
            if title and extract and 'missing' not in pages[0]:
                wiki = {'title': title, 'extract': extract,
                        'url': 'https://ja.wikipedia.org/wiki/' + quote(title.replace(' ', '_'), safe='')}
        wiki_cache.put(wiki, query)
        return render_template('overview.html', wiki=wiki or None)
    except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError):
        return render_template('overview.html', wiki=None, wiki_error='概要を取得できませんでした。'), 503
    finally:
        _wiki_slots.release()


@app.get('/media/<token>')
def media(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
        return '', 404
    image = load_image(token)
    if image is None:
        return '', 404
    data, mime = image
    return Response(data, mimetype=mime, headers={'Cross-Origin-Resource-Policy': 'same-origin'})


@app.get('/health')
def health():
    try:
        with requests.Session() as client:
            client.trust_env = False
            ready = client.get('http://127.0.0.1:8888/config', timeout=(1, 2))
        return ('ok', 200) if ready.status_code == 200 else ('starting', 503)
    except requests.RequestException:
        return 'starting', 503


@app.get('/source.tar.gz')
def source():
    from flask import send_file
    path = '/opt/portal-source.tar.gz'
    if not os.path.isfile(path):
        return 'Source archive is created by the Docker build.', 404
    return send_file(path, as_attachment=True, download_name='portal-source.tar.gz')


@app.errorhandler(413)
def too_large(_error):
    return show('入力が大きすぎます。', 413)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
