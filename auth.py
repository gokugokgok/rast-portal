"""Password entry and a signed browser credential; no search data stored."""
from collections import deque
import hashlib
import hmac
import os
import secrets
import threading
import time

from flask import Response, redirect, render_template, request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.security import check_password_hash

COOKIE_NAME = '__Host-portal-access'
REMEMBER_SECONDS = 365 * 24 * 60 * 60
DEFAULT_PASSWORD_HASH = 'pbkdf2:sha256:600000$VTTOTSipZipvSVQH$57150f2c12d3bc41836ef9a276470e6f1ddd68e03cbacf4a34c1a06ee7e468b7'
_attempts = deque()
_attempt_lock = threading.Lock()
_password_slots = threading.BoundedSemaphore(2)


def signer(app):
    return URLSafeTimedSerializer(app.config['AUTH_SECRET'], salt='portal-browser-access-v1',
                                  signer_kwargs={'digest_method': hashlib.sha256})


def credential_version(app):
    return hashlib.sha256(app.config['AUTH_PASSWORD_HASH'].encode()).hexdigest()


def issue_token(app):
    return signer(app).dumps({'access': True, 'version': credential_version(app)})


def authenticated(app):
    token = request.cookies.get(COOKIE_NAME, '')
    if not token or len(token) > 1024:
        return False
    try:
        payload = signer(app).loads(token, max_age=REMEMBER_SECONDS)
        return (isinstance(payload, dict) and payload.get('access') is True and
                isinstance(payload.get('version'), str) and
                hmac.compare_digest(payload['version'], credential_version(app)))
    except (BadSignature, ValueError, TypeError):
        return False


def login_page(error=None, status=200):
    return render_template('login.html', error=error), status


def login_budget():
    # Service-wide count only. Do not retain visitor IPs or passwords.
    now = time.monotonic()
    with _attempt_lock:
        while _attempts and _attempts[0] <= now - 60:
            _attempts.popleft()
        if len(_attempts) >= 20:
            return False
        _attempts.append(now)
        return True


def install_auth(app):
    secret = os.environ.get('PORTAL_AUTH_SECRET') or secrets.token_urlsafe(48)
    if len(secret) < 32:
        raise RuntimeError('PORTAL_AUTH_SECRET must contain at least 32 characters.')
    app.config.update(AUTH_SECRET=secret,
                      AUTH_PASSWORD_HASH=os.environ.get('PORTAL_PASSWORD_HASH') or DEFAULT_PASSWORD_HASH)

    @app.before_request
    def require_password():
        if request.endpoint in ('login', 'health', 'source'):
            return None
        if request.endpoint == 'static' and request.path == '/static/style.css':
            return None
        if authenticated(app):
            return None
        if request.method == 'GET' and request.path == '/':
            return login_page()
        if request.method == 'GET' and request.accept_mimetypes.best == 'text/html':
            return redirect('/', code=303)
        return Response('', status=401, headers={'X-Portal-Auth': 'required'})

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if authenticated(app) or request.method == 'GET':
            return redirect('/', code=303)
        if not login_budget() or not _password_slots.acquire(blocking=False):
            response = app.make_response(login_page('アクセスが集中しています。1分ほど待って再試行してください。', 429))
            response.headers['Retry-After'] = '60'
            return response
        try:
            password = request.form.get('password', '')
            if not password or len(password) > 256 or not check_password_hash(app.config['AUTH_PASSWORD_HASH'], password):
                return login_page('パスワードが違います。', 401)
        finally:
            _password_slots.release()
        response = redirect('/', code=303)
        response.set_cookie(COOKIE_NAME, issue_token(app), max_age=REMEMBER_SECONDS,
                            secure=True, httponly=True, samesite='Lax', path='/')
        return response

    @app.post('/logout')
    def logout():
        response = redirect('/', code=303)
        response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite='Lax', path='/')
        return response
