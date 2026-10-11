import gzip
import unittest
from unittest.mock import MagicMock, patch

import app


class LightweightTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        app.search_cache.clear()
        app._used = 0

    def test_filters_are_visible_before_the_search_field(self):
        html = self.client.get('/').get_data(as_text=True)
        for field in ('safesearch', 'region', 'per_page'):
            self.assertLess(html.index(f'name="{field}"'), html.index('id="q"'))
        self.assertIn('class="quick-controls"', html)
        self.assertNotIn('class="home-info"', html)

    def test_gzip_html_preserves_content_and_search_privacy(self):
        plain = self.client.get('/')
        compressed = self.client.get('/', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(gzip.decompress(compressed.data), plain.data)
        self.assertLess(len(compressed.data), len(plain.data) // 2)
        self.assertEqual(compressed.headers['Content-Encoding'], 'gzip')
        self.assertIn('Accept-Encoding', compressed.headers['Vary'])
        self.assertIn('no-store', compressed.headers['Cache-Control'])
        self.assertNotIn('Set-Cookie', compressed.headers)
        refused = self.client.get('/', headers={'Accept-Encoding': 'gzip;q=0'})
        self.assertNotIn('Content-Encoding', refused.headers)

    def test_code_assets_compress_and_revalidate_without_caching_results(self):
        for path in ('/static/app.js', '/static/style.css'):
            plain = self.client.get(path)
            encoded = self.client.get(path, headers={'Accept-Encoding': 'gzip'})
            self.assertEqual(gzip.decompress(encoded.data), plain.data)
            self.assertIn('must-revalidate', encoded.headers['Cache-Control'])
            self.assertNotIn('no-store', encoded.headers['Cache-Control'])
            fresh = self.client.get(path, headers={'Accept-Encoding': 'gzip', 'If-None-Match': encoded.headers['ETag']})
            self.assertEqual(fresh.status_code, 304)
            self.assertEqual(fresh.data, b'')
            plain.close()
            encoded.close()
            fresh.close()
        self.assertIn('no-store', self.client.get('/').headers['Cache-Control'])

    @patch('app.load_image', return_value=(b'\x89PNG\r\n\x1a\nfixture', 'image/png'))
    def test_images_remain_uncompressed_and_private(self, loader):
        response = self.client.get('/media/' + 'a' * 32, headers={'Accept-Encoding': 'gzip'})
        self.assertNotIn('Content-Encoding', response.headers)
        self.assertIn('no-store', response.headers['Cache-Control'])

    @patch('searching.requests.Session')
    def test_first_results_do_not_wait_for_extra_source_pages(self, factory):
        session = factory.return_value.__enter__.return_value
        def upstream(url, **kwargs):
            number = kwargs['data']['pageno']
            return MagicMock(status_code=200, json=lambda: {'results': [
                {'url': f'https://example.com/{i}', 'title': f'Result {i}'}
                for i in range((number - 1) * 5, number * 5)]})
        session.post.side_effect = upstream
        data = {'q': 'first paint', 'per_page': '20', 'progressive': 'initial'}
        headers = {'X-Portal-Request': '1'}
        first = self.client.post('/', data=data, headers=headers).get_data(as_text=True)
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(first.count('<article class="result-card'), 5)
        self.assertIn('data-completing="1"', first)
        completed = self.client.post('/', data={**data, 'progressive': 'fill'}, headers=headers).get_data(as_text=True)
        self.assertEqual(completed.count('<article class="result-card'), 20)
        self.assertIn('data-completing="0"', completed)
        self.assertIn('>Result 19</a>', completed)
        sent_pages = sorted(call.kwargs['data']['pageno'] for call in session.post.call_args_list)
        self.assertEqual(sent_pages, [1, 2, 3, 4])
        next_page = self.client.post('/', data={**data, 'progressive': '', 'page': '1'}, headers=headers).get_data(as_text=True)
        self.assertIn('>Result 20</a>', next_page)
        self.assertNotIn('>Result 19</a>', next_page)

    def test_invalid_progressive_mode_is_rejected(self):
        self.assertEqual(self.client.post('/', data={'q': 'x', 'progressive': 'unknown'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
