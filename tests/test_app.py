import os
import unittest
from unittest.mock import MagicMock, patch

import app as module


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.client = module.app.test_client()
        module._used = 0
        module.search_cache.clear()
        module.wiki_cache.clear()

    def test_home_privacy(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<title>Portal</title>', response.data)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertNotIn('Set-Cookie', response.headers)

    def test_get_query_is_removed(self):
        response = self.client.get('/?q=private-test')
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['Location'], '/')
        self.assertNotIn(b'private-test', response.data)

    @patch('app.requests.Session')
    def test_search_post_security_and_pagination(self, factory):
        client = MagicMock()
        factory.return_value.__enter__.return_value = client
        client.post.return_value.status_code = 200
        client.post.return_value.json.return_value = {
            'results': [
                {'url': 'https://example.com/', 'title': '<script>alert(1)</script>', 'content': '<b>Text</b>'},
                {'url': 'javascript:alert(1)', 'title': 'bad'},
            ]}
        response = self.client.post('/', data={'q': 'private-test', 'page': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<title>Portal</title>', response.data)
        self.assertNotIn(b'<script>alert', response.data)
        self.assertNotIn(b'javascript:', response.data)
        self.assertIn(b'rel="noopener noreferrer"', response.data)
        self.assertIn(b'name="page" value="2"', response.data)
        self.assertNotIn('Set-Cookie', response.headers)
        args, kwargs = client.post.call_args
        self.assertNotIn('?', args[0])
        self.assertEqual(args[0], 'http://127.0.0.1:8888/search')
        self.assertEqual(kwargs['data']['q'], 'private-test')
        self.assertEqual(kwargs['data']['pageno'], 2)
        self.assertEqual(kwargs['data']['format'], 'json')
        self.assertNotIn('params', kwargs)
        self.assertNotIn('X-Forwarded-For', kwargs['headers'])
        fragment = self.client.post('/', data={'q': 'private-test'}, headers={'X-Portal-Request': '1'})
        self.assertNotIn(b'<title>', fragment.data)

    def test_invalid_and_cross_origin(self):
        self.assertEqual(self.client.post('/', data={'q': ''}).status_code, 400)
        self.assertEqual(self.client.post('/', data={'q': 'x', 'page': '99'}).status_code, 400)
        self.assertEqual(self.client.post('/', data={'q': 'x'}, headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.client.post('/', data={'q': 'x' * 9000}).status_code, 413)

    @patch('app.requests.Session')
    def test_backend_readiness(self, factory):
        session = factory.return_value.__enter__.return_value
        session.get.return_value.status_code = 200
        self.assertEqual(self.client.get('/health').status_code, 200)
        session.get.side_effect = module.requests.ConnectionError()
        self.assertEqual(self.client.get('/health').status_code, 503)

    @patch('app.requests.Session')
    def test_error_does_not_echo_query(self, factory):
        factory.return_value.__enter__.return_value.post.side_effect = module.requests.Timeout('secret-query')
        response = self.client.post('/', data={'q': 'secret-query'})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b'secret-query', response.data)

    @patch('app.requests.Session')
    def test_all_search_engines_unavailable(self, factory):
        session = factory.return_value.__enter__.return_value
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'results': [], 'unresponsive_engines': [['google', 'timeout']]}
        response = self.client.post('/', data={'q': 'private-test'})
        self.assertIn('検索元が応答しませんでした'.encode(), response.data)
        self.assertNotIn(b'class="page-form"', response.data)

    @patch('app.requests.Session')
    def test_categories_safesearch_and_pagination(self, factory):
        session = factory.return_value.__enter__.return_value
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'results': [{'url': 'https://example.com/', 'title': 'Result', 'thumbnail_src': 'https://example.com/a.jpg', 'img_src': 'https://example.com/full.jpg', 'template': 'images.html'}]}
        for category, upstream in [('all', 'general'), ('images', 'images'), ('videos', 'videos'), ('news', 'news')]:
            for safe in ('0', '1', '2'):
                response = self.client.post('/', data={'q': 'sample', 'category': category, 'safesearch': safe})
                self.assertEqual(response.status_code, 200)
                sent = session.post.call_args.kwargs['data']
                self.assertEqual(sent['categories'], upstream)
                self.assertEqual(sent['safesearch'], safe)
                self.assertIn(('name="category" value="' + category + '"').encode(), response.data)
                self.assertIn(('name="safesearch" value="' + safe + '"').encode(), response.data)
                self.assertIn(b'<title>Portal</title>', response.data)
                self.assertNotIn(b'src="https://', response.data)
        self.assertIn(b'class="category-tabs"', response.data)

    def test_invalid_search_settings(self):
        self.assertEqual(self.client.post('/', data={'q': 'x', 'category': 'unknown'}).status_code, 400)
        self.assertEqual(self.client.post('/', data={'q': 'x', 'safesearch': '3'}).status_code, 400)

    @patch('app.requests.Session')
    def test_cache_respects_page_category_safety_and_expiry(self, factory):
        session = factory.return_value.__enter__.return_value
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'results': [{'url': 'https://example.com', 'title': 'Result'}]}
        data = {'q': 'sample', 'category': 'all', 'safesearch': '1', 'page': '0'}
        self.client.post('/', data=data)
        self.client.post('/', data=data)
        self.assertEqual(session.post.call_count, 1)
        for key, value in [('safesearch', '2'), ('category', 'news'), ('page', '1')]:
            self.client.post('/', data={**data, key: value})
        self.assertEqual(session.post.call_count, 4)
        with patch('cache.time.monotonic', return_value=module.time.monotonic() + 31):
            self.client.post('/', data=data)
        self.assertEqual(session.post.call_count, 5)

    @patch('app.requests.Session')
    def test_failed_search_is_not_cached(self, factory):
        session = factory.return_value.__enter__.return_value
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'results': [{'url': 'https://example.com'}], 'unresponsive_engines': [['bing', 'timeout']]}
        for _ in range(2):
            self.client.post('/', data={'q': 'sample'})
        self.assertEqual(session.post.call_count, 2)

    @patch('app.requests.Session')
    def test_numbered_pages_are_post_forms_and_bounded(self, factory):
        session = factory.return_value.__enter__.return_value
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'results': [{'url': 'https://example.com'}]}
        first = self.client.post('/', data={'q': 'sample'}).get_data(as_text=True)
        for number in range(1, 6):
            self.assertIn('aria-label="' + str(number) + 'ページ目"', first)
        self.assertIn('aria-current="page" disabled>1</button>', first)
        last = self.client.post('/', data={'q': 'sample', 'page': '9'}).get_data(as_text=True)
        self.assertNotIn('次へ →', last)
        self.assertIn('aria-current="page" disabled>10</button>', last)
        self.assertNotIn('name="page" value="10"', last)

    @patch('app.requests.Session')
    def test_wikipedia_post_sanitized_and_cached(self, factory):
        session = factory.return_value.__enter__.return_value
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'query': {'pages': [{'title': '東京', 'extract': '東京は日本の都市。<script>bad()</script>', 'fullurl': 'javascript:alert(1)'}]}}
        response = self.client.post('/overview', data={'q': '東京'}, headers={'Cookie': 'visitor=secret', 'X-Forwarded-For': '192.0.2.1'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('東京は日本の都市'.encode(), response.data)
        self.assertNotIn(b'<script>', response.data)
        self.assertNotIn(b'javascript:', response.data)
        self.assertIn(b'CC BY-SA 4.0', response.data)
        self.assertEqual(session.post.call_args.args[0], 'https://ja.wikipedia.org/w/api.php')
        sent = session.post.call_args.kwargs
        self.assertEqual(sent['data']['gsrsearch'], '東京')
        self.assertNotIn('params', sent)
        for name in ('Cookie', 'Referer', 'X-Forwarded-For'):
            self.assertNotIn(name, sent['headers'])
        self.client.post('/overview', data={'q': '東京'})
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertNotIn('Set-Cookie', response.headers)

    @patch('app.requests.Session')
    def test_wikipedia_absence_timeout_and_retry(self, factory):
        session = factory.return_value.__enter__.return_value
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'query': {'pages': []}}
        response = self.client.post('/overview', data={'q': 'no-article'})
        self.assertIn('概要が見つかりませんでした'.encode(), response.data)
        session.post.side_effect = module.requests.Timeout('secret-query')
        for _ in range(2):
            response = self.client.post('/overview', data={'q': 'secret-query'})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b'secret-query', response.data)
        self.assertEqual(session.post.call_count, 3)
        self.assertEqual(self.client.post('/overview', data={'q': ''}).status_code, 400)

    @patch('app.requests.Session')
    def test_private_nojs_form_origin(self, factory):
        session = factory.return_value.__enter__.return_value
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'results': []}
        response = self.client.post('/', data={'q': 'sample', 'kind': 'images'}, headers={'Origin': 'null', 'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.post.call_args.kwargs['data']['categories'], 'images')
        self.assertEqual(self.client.post('/', data={'q': 'sample'}, headers={'Origin': 'null'}).status_code, 403)
        self.assertEqual(self.client.post('/', data={'q': 'sample'}, headers={'Origin': 'null', 'Sec-Fetch-Site': 'cross-site'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
