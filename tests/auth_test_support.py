from auth import COOKIE_NAME, issue_token


def authorized_client(app):
    client = app.test_client()
    client.set_cookie(COOKIE_NAME, issue_token(app), secure=True, httponly=True)
    return client
