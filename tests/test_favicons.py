import unittest
from unittest.mock import patch, MagicMock
import app
import favicons
import media


class FaviconTests(unittest.TestCase):
    def setUp(self):
        favicons.clear_icons()

    @patch('favicons.fetch_public_image')
    def test_real_icon_is_cached_per_origin_without_query(self, fetcher):
        png = (b'\x89PNG\r\n\x1a\nicon', 'image/png')
        fetcher.return_value = png
        url = favicons.register_icon('https://www.example.com/article?q=private-search')
        token = url.split('/')[-1]
        self.assertEqual(len(token), 32)
        self.assertNotIn('private-search', url)
        self.assertEqual(favicons.register_icon('https://www.example.com/other'), url)
        self.assertEqual(favicons.load_icon(token), png)
        self.assertEqual(favicons.load_icon(token), png)
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(fetcher.call_args.args[0], 'https://icons.duckduckgo.com/ip2/www.example.com.ico')
        self.assertNotIn('private-search', str(fetcher.call_args))

    @patch('favicons.fetch_public_image')
    def test_missing_service_icon_uses_site_favicon(self, fetcher):
        ico = (b'\x00\x00\x01\x00' + b'x' * 40, 'image/x-icon')
        fetcher.side_effect = [ValueError('missing'), ico]
        token = favicons.register_icon('https://example.com/page').split('/')[-1]
        self.assertEqual(favicons.load_icon(token), ico)
        self.assertEqual(fetcher.call_args.args[0], 'https://example.com/favicon.ico')

    def test_private_and_credential_origins_rejected(self):
        for url in ('http://127.0.0.1/', 'http://169.254.169.254/', 'http://localhost/', 'https://u:p@example.com/'):
            self.assertEqual(favicons.register_icon(url), '')
        self.assertIsNone(favicons.load_icon('unknown'))

    @patch('app.load_icon')
    def test_icon_endpoint_headers_and_busy_response(self, loader):
        loader.return_value = (b'\x89PNG\r\n\x1a\n', 'image/png')
        response = app.app.test_client().get('/icon/' + 'a' * 32)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response.headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotIn('Set-Cookie', response.headers)
        loader.side_effect = media.ImageBusy
        self.assertEqual(app.app.test_client().get('/icon/' + 'a' * 32).status_code, 503)

    @patch('media.socket.getaddrinfo')
    @patch('media.urllib3.HTTPSConnectionPool')
    def test_ico_is_accepted_only_for_icons(self, pool, resolver):
        resolver.return_value = [(2, 1, 6, '', ('93.184.216.34', 443))]
        ico = b'\x00\x00\x01\x00' + b'x' * 40
        response = MagicMock(status=200, headers={})
        pool.return_value.urlopen.return_value = response
        response.read.side_effect = [ico, b'']
        self.assertEqual(media.fetch_public_image('https://example.com/favicon.ico', allow_icon=True), (ico, 'image/x-icon'))
        response.read.side_effect = [ico, b'']
        with self.assertRaises(ValueError):
            media.fetch_public_image('https://example.com/favicon.ico')
