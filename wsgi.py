"""Вебзастосунок трекера: Fava з двома журналами (Марко, Оля), вхід за паролем і лише HTTPS.

Логін і пароль беремо зі змінних середовища TRACKER_USER і TRACKER_PASSWORD.
На сервері їх кладемо у файл .env поруч із цим файлом (.env у git не потрапляє).
Без логіна й пароля застосунок не стартує: краще не працювати, ніж працювати без входу.

Вхід — власна сторінка /login (а не системне вікно пароля): так iPhone може встановити
трекер як застосунок. Після входу телефон тримає підписане cookie 30 днів; підпис робимо
з пароля, тож зміна пароля в .env одразу виводить усіх. Кілька хибних спроб поспіль — пауза.

Локальна перевірка без HTTPS: TRACKER_ALLOW_HTTP=1 (лише для localhost).
"""
import base64
import hashlib
import hmac
import html
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

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

# «/» — новий звіт (ui.py), «/fava» — Fava (журнал, запити, кнопка «+»). Вхід і HTTPS нижче діють для обох.
from werkzeug.middleware.dispatcher import DispatcherMiddleware  # noqa: E402

from ui import ui_app  # noqa: E402

site = DispatcherMiddleware(ui_app, {"/fava": fava_app})


def _credentials_ok(user, password):
    # Порівняння за сталий час, щоб не підказувати пароль за швидкістю відповіді.
    return hmac.compare_digest(user.encode(), USER.encode()) & hmac.compare_digest(password.encode(), PASSWORD.encode())


def _authorized(environ):
    """Скрипти й curl можуть і далі входити заголовком Basic; браузер — через cookie."""
    header = environ.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        return False
    try:
        user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except ValueError:
        return False
    return _credentials_ok(user, password)


# ---------- сесія в cookie ----------
COOKIE = "tracker_session"
SESSION_DAYS = 30
_KEY = hashlib.sha256(("tracker-session:" + USER + ":" + PASSWORD).encode()).digest()


def _sign(expires):
    return hmac.new(_KEY, str(expires).encode(), hashlib.sha256).hexdigest()


def _new_session():
    expires = int(time.time()) + SESSION_DAYS * 86400
    return f"{expires}.{_sign(expires)}"


def _session_ok(environ):
    for part in environ.get("HTTP_COOKIE", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            expires, _, sig = value.partition(".")
            if expires.isdigit() and int(expires) > time.time() and hmac.compare_digest(sig, _sign(int(expires))):
                return True
    return False


def _cookie_header(value, max_age):
    flags = "; Path=/; HttpOnly; SameSite=Lax" + ("" if ALLOW_HTTP else "; Secure")
    return ("Set-Cookie", f"{COOKIE}={value}; Max-Age={max_age}{flags}")


# ---------- захист від підбору: не більше 5 хибних спроб за 10 хвилин з однієї адреси ----------
_fails = {}
_fails_lock = threading.Lock()


def _client(environ):
    return environ.get("HTTP_X_REAL_IP") or environ.get("REMOTE_ADDR", "")


def _too_many(ip):
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if now - t < 600]
        _fails[ip] = recent
        return len(recent) >= 5


def _note_fail(ip):
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


def _safe_next(value):
    """Після входу повертаємо лише на свою сторінку: «/щось», але не «//чужий-сайт»."""
    path = urlparse(value or "/")
    if path.scheme or path.netloc or not (value or "/").startswith("/") or (value or "").startswith("//"):
        return "/"
    return value or "/"


def _same_origin(environ):
    origin = environ.get("HTTP_ORIGIN")
    return not origin or urlparse(origin).netloc == environ.get("HTTP_HOST", "")


LOGIN_PAGE = (ROOT / "login.html").read_text(encoding="utf-8")


def _login_page(start_response, next_url="/", error="", status="200 OK", user=""):
    body = (LOGIN_PAGE.replace("__NEXT__", html.escape(next_url, quote=True))
            .replace("__USER__", html.escape(user, quote=True))
            .replace("__ERROR__", f'<p class="err" role="alert">{html.escape(error)}</p>' if error else "")).encode("utf-8")
    start_response(status, [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
                            ("Cache-Control", "no-store"), ("X-Frame-Options", "DENY")])
    return [body]


def _redirect(start_response, location, extra=()):
    start_response("303 See Other", [("Location", location), ("Content-Length", "0"), *extra])
    return [b""]


def _handle_login(environ, start_response):
    if environ["REQUEST_METHOD"] == "GET":
        query = parse_qs(environ.get("QUERY_STRING", ""))
        return _login_page(start_response, _safe_next(query.get("next", ["/"])[0]))
    if environ["REQUEST_METHOD"] != "POST" or not _same_origin(environ):
        start_response("403 Forbidden", [("Content-Type", "text/plain; charset=utf-8")])
        return ["Заборонено".encode("utf-8")]
    try:
        size = min(int(environ.get("CONTENT_LENGTH") or 0), 4096)
    except ValueError:
        size = 0
    form = parse_qs(environ["wsgi.input"].read(size).decode("utf-8", "replace"))
    user, password = form.get("user", [""])[0].strip(), form.get("password", [""])[0]
    next_url = _safe_next(form.get("next", ["/"])[0])
    ip = _client(environ)
    if _too_many(ip):
        return _login_page(start_response, next_url, "Забагато спроб. Зачекайте кілька хвилин і спробуйте ще раз.",
                           "429 Too Many Requests", user)
    if not _credentials_ok(user, password):
        _note_fail(ip)
        time.sleep(1)  # повільніше підбирати
        return _login_page(start_response, next_url, "Неправильний логін або пароль", "401 Unauthorized", user)
    with _fails_lock:
        _fails.pop(ip, None)
    return _redirect(start_response, next_url, [_cookie_header(_new_session(), SESSION_DAYS * 86400)])


# Файли, без яких телефон не встановить застосунок: маніфест, service worker та іконки.
# Телефон забирає їх без пароля, тож віддаємо без входу; даних у них немає.
PUBLIC_PATHS = {
    "/manifest.webmanifest", "/sw.js", "/static/apple-touch-icon.png",
    "/static/icon-192.png", "/static/icon-512.png", "/static/icon-maskable-512.png",
}


def application(environ, start_response):
    scheme = environ.get("HTTP_X_FORWARDED_PROTO", environ.get("wsgi.url_scheme", "http"))
    if scheme != "https" and not ALLOW_HTTP:
        host = environ.get("HTTP_HOST", "")
        location = f"https://{host}{environ.get('PATH_INFO', '/')}"
        start_response("301 Moved Permanently", [("Location", location), ("Content-Length", "0")])
        return [b""]
    path = environ.get("PATH_INFO", "")
    if path in PUBLIC_PATHS:
        return site(environ, start_response)
    if path == "/login":
        return _handle_login(environ, start_response)
    if path == "/logout":
        if environ["REQUEST_METHOD"] != "POST" or not _same_origin(environ):
            start_response("403 Forbidden", [("Content-Type", "text/plain; charset=utf-8")])
            return ["Заборонено".encode("utf-8")]
        return _redirect(start_response, "/login", [_cookie_header("", 0)])
    if not (_session_ok(environ) or _authorized(environ)):
        # сторінку браузера ведемо на вхід; запитам даних (API, Fava у фоні) — просто 401
        wants_page = environ["REQUEST_METHOD"] == "GET" and "text/html" in environ.get("HTTP_ACCEPT", "")
        if wants_page:
            query = environ.get("QUERY_STRING", "")
            return _redirect(start_response, "/login?next=" + quote(path + ("?" + query if query else ""), safe=""))
        body = "Потрібен вхід".encode("utf-8")
        start_response("401 Unauthorized", [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))])
        return [body]
    return site(environ, start_response)


app = application  # PythonAnywhere шукає змінну application; лишаємо й app
