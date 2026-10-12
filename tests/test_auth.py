import unittest
from unittest.mock import patch

from itsdangerous import TimestampSigner
from werkzeug.security import generate_password_hash
import app
import auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(app.app.config, {
            'AUTH_SECRET': 'test-only-signing-secret-with-more-than-32-characters',
            'AUTH_PASSWORD_HASH': generate_password_hash('test-access', method='pbkdf2:sha256:1000'),
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        with auth._attempt_lock:
            auth._attempts.clear()
        self.client = app.app.test_client()

    def enter(self):
        return self.client.post('/login', data={'password': 'test-access'})

    def test_first_visit_has_only_password_page(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'type="password"', response.data)
        self.assertNotIn(b'id="q"', response.data)
        self.assertNotIn(b'app.js', response.data)
        self.assertNotIn('Set-Cookie', response.headers)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        with self.client.get('/static/style.css') as stylesheet:
            self.assertEqual(stylesheet.status_code, 200)

    def test_login_remembers_browser_with_private_signed_cookie(self):
        response = self.enter()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['Location'], '/')
        cookie = response.headers['Set-Cookie']
        for flag in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/', 'Max-Age=31536000'):
            self.assertIn(flag, cookie)
        self.assertNotIn('test-access', cookie)
        self.assertNotIn('Domain=', cookie)
        self.assertIn(b'id="q"', self.client.get('/').data)
        self.assertNotIn('Set-Cookie', self.client.get('/').headers)
        other = app.app.test_client()
        self.assertIn(b'type="password"', other.get('/').data)

    def test_wrong_password_stays_out_and_is_not_echoed(self):
        response = self.client.post('/login', data={'password': 'wrong-private-password'})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(b'wrong-private-password', response.data)
        self.assertNotIn('Set-Cookie', response.headers)
        self.assertIn(b'type="password"', self.client.get('/').data)

    @patch('app.search_results')
    @patch('app.load_image')
    @patch('app.load_icon')
    @patch('app.requests.Session')
    def test_direct_requests_do_not_bypass_gate(self, upstream, icons, images, search):
        for path in ('/overview', '/'):
            response = self.client.post(path, data={'q': 'private-search'})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers['X-Portal-Auth'], 'required')
            self.assertNotIn(b'private-search', response.data)
        for path in ('/media/' + 'a' * 32, '/icon/' + 'a' * 32, '/static/app.js'):
            self.assertEqual(self.client.get(path).status_code, 401)
        upstream.assert_not_called()
        search.assert_not_called()
        icons.assert_not_called()
        images.assert_not_called()

    def test_tampered_cookie_is_rejected(self):
        token = auth.issue_token(app.app)
        self.client.set_cookie(auth.COOKIE_NAME, token + 'x', secure=True)
        self.assertIn(b'type="password"', self.client.get('/').data)

    def test_expired_cookie_is_rejected(self):
        with patch.object(TimestampSigner, 'get_timestamp', return_value=100000):
            token = auth.issue_token(app.app)
        self.client.set_cookie(auth.COOKIE_NAME, token, secure=True)
        with patch.object(TimestampSigner, 'get_timestamp', return_value=100000 + auth.REMEMBER_SECONDS + 1):
            self.assertIn(b'type="password"', self.client.get('/').data)

    def test_restart_with_same_secret_preserves_cookie_and_rotation_revokes(self):
        self.enter()
        self.assertIn(b'id="q"', self.client.get('/').data)
        # A new serializer (as on restart) uses the configured stable key.
        with patch.dict(app.app.config, {'AUTH_SECRET': app.app.config['AUTH_SECRET']}):
            self.assertIn(b'id="q"', self.client.get('/').data)
        with patch.dict(app.app.config, {'AUTH_SECRET': 'different-secret-key-with-more-than-32-characters'}):
            self.assertIn(b'type="password"', self.client.get('/').data)

    def test_password_change_invalidates_saved_authentication(self):
        self.enter()
        with patch.dict(app.app.config, {'AUTH_PASSWORD_HASH': generate_password_hash('new-password', method='pbkdf2:sha256:1000')}):
            self.assertIn(b'type="password"', self.client.get('/').data)

    def test_logout_forgets_this_browser(self):
        self.enter()
        response = self.client.post('/logout')
        self.assertEqual(response.status_code, 303)
        self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
        self.assertIn(b'type="password"', self.client.get('/').data)

    def test_cross_site_login_is_rejected(self):
        response = self.client.post('/login', data={'password': 'test-access'},
                                    headers={'Origin': 'https://other.example', 'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Set-Cookie', response.headers)

    def test_query_password_is_not_accepted(self):
        response = self.client.get('/login?password=test-access')
        self.assertEqual(response.status_code, 303)
        self.assertNotIn('test-access', response.headers['Location'])
        self.assertNotIn('Set-Cookie', response.headers)

    def test_login_budget_and_oversize_input(self):
        self.assertEqual(self.client.post('/login', data={'password': 'x' * 9000}).status_code, 413)
        for _ in range(19):
            self.assertEqual(self.client.post('/login', data={'password': ''}).status_code, 401)
        response = self.enter()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '60')

    @patch('app.requests.Session')
    def test_health_check_is_public(self, factory):
        factory.return_value.__enter__.return_value.get.return_value.status_code = 200
        self.assertEqual(self.client.get('/health').status_code, 200)


if __name__ == '__main__':
    unittest.main()
