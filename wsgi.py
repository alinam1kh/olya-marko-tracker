"""Вебзастосунок трекера: Fava з двома журналами (Марко, Оля), вхід за паролем і лише HTTPS.

Логін і пароль беремо зі змінних середовища TRACKER_USER і TRACKER_PASSWORD.
На сервері їх кладемо у файл .env поруч із цим файлом (.env у git не потрапляє).
Без логіна й пароля застосунок не стартує: краще не працювати, ніж працювати без входу.

Локальна перевірка без HTTPS: TRACKER_ALLOW_HTTP=1 (лише для localhost).
"""
import base64
import hmac
import os
from pathlib import Path

from fava.application import create_app

ROOT = Path(__file__).parent


def _load_env_file(path):
    """Простий .env: рядки КЛЮЧ=значення; змінні, вже задані в середовищі, не перезаписуємо."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file(ROOT / ".env")
USER = os.environ.get("TRACKER_USER", "")
PASSWORD = os.environ.get("TRACKER_PASSWORD", "")
if not USER or not PASSWORD:
    raise RuntimeError("Задайте TRACKER_USER і TRACKER_PASSWORD (у .env або змінних середовища)")
ALLOW_HTTP = os.environ.get("TRACKER_ALLOW_HTTP") == "1"

fava_app = create_app([ROOT / "marko.beancount", ROOT / "main.beancount"], load=True)


def _authorized(environ):
    header = environ.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        return False
    try:
        user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except ValueError:
        return False
    # Порівняння за сталий час, щоб не підказувати пароль за швидкістю відповіді.
    return hmac.compare_digest(user, USER) & hmac.compare_digest(password, PASSWORD)


def application(environ, start_response):
    scheme = environ.get("HTTP_X_FORWARDED_PROTO", environ.get("wsgi.url_scheme", "http"))
    if scheme != "https" and not ALLOW_HTTP:
        host = environ.get("HTTP_HOST", "")
        location = f"https://{host}{environ.get('PATH_INFO', '/')}"
        start_response("301 Moved Permanently", [("Location", location), ("Content-Length", "0")])
        return [b""]
    if not _authorized(environ):
        body = "Потрібен вхід".encode("utf-8")
        start_response("401 Unauthorized", [
            ("WWW-Authenticate", 'Basic realm="Tracker", charset="UTF-8"'),
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return [body]
    return fava_app(environ, start_response)


app = application  # PythonAnywhere шукає змінну application; лишаємо й app
