import time
import unittest
from unittest.mock import patch, MagicMock

import app
import media


class MediaTests(unittest.TestCase):
    def setUp(self):
        media.forget_images()

    def test_opaque_url_and_expiration(self):
        path = media.register_image('https://example.com/image?q=private-search')
        self.assertTrue(path.startswith('/media/'))
        self.assertNotIn('private-search', path)
        token = path.split('/')[-1]
        self.assertEqual(len(token), 32)
        with patch('media.time.monotonic', return_value=time.monotonic() + media.TTL + 1):
            self.assertIsNone(media.load_image(token))

    def test_invalid_urls(self):
        for url in ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:password@example.com/a', 'http://example.com:8888/a', '\nhttps://example.com/a']:
            self.assertEqual(media.register_image(url), '')

    @patch('media.socket.getaddrinfo')
    @patch('media.urllib3.HTTPConnectionPool')
    def test_private_address_is_blocked(self, pool, resolver):
        for address in ['127.0.0.1', '169.254.169.254', '10.0.0.1', '::1', '100.64.0.1']:
            resolver.return_value = [(2, 1, 6, '', (address, 80))]
            with self.assertRaises(ValueError):
                media.fetch_public_image('http://example.com/image')
        pool.assert_not_called()

    @patch('media.socket.getaddrinfo')
    @patch('media.urllib3.HTTPSConnectionPool')
    def test_public_request_pins_dns_and_omits_user_headers(self, factory, resolver):
        resolver.return_value = [(2, 1, 6, '', ('93.184.216.34', 443))]
        response = MagicMock(status=200, headers={})
        response.read.side_effect = [b'\x89PNG\r\n\x1a\ncontent', b'']
        factory.return_value.urlopen.return_value = response
        data, mime = media.fetch_public_image('https://example.com/image')
        self.assertEqual(mime, 'image/png')
        self.assertTrue(data.startswith(b'\x89PNG'))
        self.assertEqual(factory.call_args.args[0], '93.184.216.34')
        self.assertEqual(factory.call_args.kwargs['assert_hostname'], 'example.com')
        headers = factory.return_value.urlopen.call_args.kwargs['headers']
        for name in ['Cookie', 'Referer', 'X-Forwarded-For']:
            self.assertNotIn(name, headers)

    @patch('media.socket.getaddrinfo')
    @patch('media.urllib3.HTTPSConnectionPool')
    def test_redirect_to_local_is_blocked(self, factory, resolver):
        resolver.side_effect = [[(2, 1, 6, '', ('93.184.216.34', 443))], [(2, 1, 6, '', ('127.0.0.1', 80))]]
        response = MagicMock(status=302, headers={'Location': 'http://localhost/image'})
        factory.return_value.urlopen.return_value = response
        with self.assertRaises(ValueError):
            media.fetch_public_image('https://example.com/image')

    @patch('media.socket.getaddrinfo')
    @patch('media.urllib3.HTTPSConnectionPool')
    def test_html_and_oversize_rejected(self, factory, resolver):
        resolver.return_value = [(2, 1, 6, '', ('93.184.216.34', 443))]
        response = MagicMock(status=200, headers={})
        response.read.side_effect = [b'<html>not an image</html>', b'']
        factory.return_value.urlopen.return_value = response
        with self.assertRaises(ValueError):
            media.fetch_public_image('https://example.com/image')
        response.headers = {'Content-Length': str(media.MAX_BYTES + 1)}
        with self.assertRaises(ValueError):
            media.fetch_public_image('https://example.com/image')

    @patch('app.load_image', return_value=(b'\x89PNG\r\n\x1a\n', 'image/png'))
    def test_proxy_headers_and_unknown_token(self, loader):
        client = app.app.test_client()
        response = client.get('/media/' + 'a' * 32)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertEqual(client.get('/media/invalid').status_code, 404)


if __name__ == '__main__':
    unittest.main()
