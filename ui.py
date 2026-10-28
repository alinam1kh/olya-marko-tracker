"""Новий звіт трекера (мобільний, світлий): головна сторінка «/».

Цифри рахує report_data.py з журналів при кожному відкритті, тож нова витрата одразу видна і тут.
Готівку Марко додає формою «Додати витрату» (POST /api/cash): запис дописуємо в marko-cash.beancount,
перевіряємо журнал і відкочуємо, якщо з'явилась помилка. Шрифт Outfit (OFL) лежить у static/.
"""
import json
import re
import threading
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from beancount import loader
from flask import Flask, jsonify, request, send_from_directory

import report_data

ROOT = Path(__file__).parent
ui_app = Flask(__name__, static_folder=None)


@ui_app.route("/static/<path:name>")
def static_file(name):
    return send_from_directory(ROOT / "static", name, max_age=86400)


# застосунок на телефоні: «Додати на початковий екран» відкриває трекер окремим вікном з іконкою
@ui_app.route("/manifest.webmanifest")
def manifest():
    data = {
        "name": "Фінанси Олі й Марка", "short_name": "Фінанси", "lang": "uk",
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#F5F6F8", "theme_color": "#F5F6F8",
        "icons": [
            {"src": "static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    resp = jsonify(data)
    resp.mimetype = "application/manifest+json"
    return resp


# service worker без кешу: дані завжди свіжі з сервера, він лише робить сайт «встановлюваним»
@ui_app.route("/sw.js")
def service_worker():
    js = ('self.addEventListener("install", function () { self.skipWaiting(); });\n'
          'self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });\n'
          'self.addEventListener("fetch", function (e) { e.respondWith(fetch(e.request)); });\n')
    return ui_app.response_class(js, mimetype="text/javascript", headers={"Cache-Control": "no-cache"})


_write_lock = threading.Lock()


def _fail(message):
    return jsonify({"ok": False, "error": message}), 400


@ui_app.post("/api/cash")
def add_cash():
    # Захист від запитів з чужих сайтів: лише JSON і лише з цієї ж адреси.
    origin = request.headers.get("Origin", "")
    if not request.is_json or urlparse(origin).netloc != request.host:
        return jsonify({"ok": False, "error": "Запит не з трекера"}), 403
    body = request.get_json(silent=True) or {}
    files = report_data.CASH_FILES.get(body.get("person"))
    if not files:
        return _fail("Готівку поки можна додавати лише Марку")
    ledger, cash = (report_data.ROOT / f for f in files)

    try:
        amount = Decimal(str(body.get("amount", "")).replace(",", ".").replace(" ", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return _fail("Сума має бути числом")
    if not Decimal("0") < amount <= Decimal("100000"):
        return _fail("Сума має бути більша за 0")
    try:
        day = date.fromisoformat(str(body.get("date", "")))
    except ValueError:
        return _fail("Невірна дата")
    if not date(2026, 7, 31) < day <= date.today() + timedelta(days=1):
        return _fail("Дата поза періодом обліку")

    entries, before, _ = loader.load_file(str(ledger))
    accounts = {e.account for e in entries if type(e).__name__ == "Open"}
    account = "Expenses:" + str(body.get("category", ""))
    if account not in accounts:
        return _fail("Оберіть категорію")
    # Опис: один рядок без лапок і зворотних слешів, щоб не зламати синтаксис Beancount.
    title = re.sub(r'[\s"\\]+', " ", str(body.get("title", ""))).strip()[:80] or account.split(":", 1)[1].replace("-", " ")

    entry = f'\n{day.isoformat()} * "{title}"\n  {account}  {amount} PLN\n  Assets:Готівка\n'
    with _write_lock:
        original = cash.read_bytes()
        with cash.open("a", encoding="utf-8", newline="\n") as f:
            f.write(entry)
        _, after, _ = loader.load_file(str(ledger))
        if len(after) > len(before):
            cash.write_bytes(original)
            return _fail("Журнал не прийняв запис, нічого не збережено")
        # номер рядка нового запису — щоб тост міг одразу «Скасувати»
        line = original.decode("utf-8").count("\n") + 2
    # свіжі цифри одразу у відповіді — звіт оновлюється на місці, без перезавантаження сторінки
    return jsonify({"ok": True, "month": day.strftime("%Y-%m"), "line": line, "date": day.isoformat(), "title": title,
                    "data": _data()})


@ui_app.post("/api/cash/delete")
def delete_cash():
    """Видаляє один готівковий запис із файлу готівки (лише його; виписку не чіпаємо).

    Запис шукаємо за номером рядка й перевіряємо, що там справді та сама дата й опис —
    щоб не видалити інший запис, якщо файл тим часом змінився.
    """
    origin = request.headers.get("Origin", "")
    if not request.is_json or urlparse(origin).netloc != request.host:
        return jsonify({"ok": False, "error": "Запит не з трекера"}), 403
    body = request.get_json(silent=True) or {}
    files = report_data.CASH_FILES.get(body.get("person"))
    if not files:
        return _fail("Готівку веде лише Марко")
    ledger, cash = (report_data.ROOT / f for f in files)
    try:
        line = int(body.get("line"))
    except (TypeError, ValueError):
        return _fail("Невідомий запис")
    expected = f'{body.get("date", "")} * "{body.get("title", "")}"'
    _, before, _ = loader.load_file(str(ledger))
    with _write_lock:
        original = cash.read_bytes()
        lines = original.decode("utf-8").split("\n")
        i = line - 1
        if not 0 <= i < len(lines) or lines[i].strip() != expected:
            return _fail("Запис уже змінився, оновіть сторінку")
        j = i + 1
        while j < len(lines) and lines[j].startswith(" "):  # рядки проводок запису
            j += 1
        start = i - 1 if i > 0 and lines[i - 1].strip() == "" else i  # разом з порожнім рядком перед записом
        cash.write_text("\n".join(lines[:start] + lines[j:]), encoding="utf-8", newline="\n")
        _, errors, _ = loader.load_file(str(ledger))
        if len(errors) > len(before):
            cash.write_bytes(original)
            return _fail("Журнал не прийняв зміну, нічого не видалено")
    # свіжі цифри одразу у відповіді — сторінка оновлюється на місці, без перезавантаження
    return jsonify({"ok": True, "month": body.get("date", "")[:7], "data": _data()})


# «Відкладати щомісяця»: скільки людина хоче відкладати — її налаштування, а не облік.
# Лежить на сервері в plans.json (у git не йде): однаково на всіх телефонах.
# Для кожної людини — історія планів [{monthly, from, total?, until?}]; план діє з місяця from,
# тож минулі місяці зберігають свій ліміт.
PLANS = ROOT / "plans.json"
_YM = re.compile(r"\d{4}-\d{2}$")


def _plans():
    try:
        return json.loads(PLANS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _data():
    data = report_data.report()
    data["plans"] = _plans()
    return data


def _plan_entry(raw, today):
    """Перевіряє один план; повертає словник або None."""
    try:
        monthly = int(Decimal(str(raw.get("monthly", ""))).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not 0 < monthly <= 1_000_000:
        return None
    start = str(raw.get("from") or today.strftime("%Y-%m"))
    if not _YM.match(start) or start > today.strftime("%Y-%m"):
        return None
    plan = {"monthly": monthly, "from": start}
    if raw.get("total") or raw.get("until"):  # режим «зібрати суму до дати»
        try:
            total = int(Decimal(str(raw.get("total", ""))).quantize(Decimal("1")))
            until = date.fromisoformat(str(raw.get("until", "")))
        except (InvalidOperation, ValueError, TypeError):
            return None
        if not 0 < total <= 10_000_000 or not today < until <= today + timedelta(days=3660):
            return None
        plan.update(total=total, until=until.isoformat())
    return plan


@ui_app.post("/api/plan")
def save_plan():
    """Зберігає план людини. {person, monthly[, total, until]} — новий план з поточного місяця
    (у тому ж місяці замінює попередній); {person, history: [...]} — відновити як було («Скасувати»)."""
    origin = request.headers.get("Origin", "")
    if not request.is_json or urlparse(origin).netloc != request.host:
        return jsonify({"ok": False, "error": "Запит не з трекера"}), 403
    body = request.get_json(silent=True) or {}
    person = str(body.get("person", ""))
    if person not in {key for key, _, _ in report_data.LEDGERS}:
        return _fail("Невідома людина")
    today = date.today()
    with _write_lock:
        plans = _plans()
        if "history" in body:
            items = [_plan_entry(x, today) for x in body.get("history") or [] if isinstance(x, dict)]
            if None in items:
                return _fail("Невірний план")
            history = sorted(items, key=lambda x: x["from"])
        else:
            plan = _plan_entry(body, today)
            if not plan:
                return _fail("Вкажіть суму від 1 зл")
            history = [x for x in plans.get(person, []) if x.get("from") != plan["from"]] + [plan]
        if history:
            plans[person] = history
        else:
            plans.pop(person, None)
        tmp = PLANS.with_suffix(".tmp")
        tmp.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PLANS)
    return jsonify({"ok": True, "plans": plans})


@ui_app.route("/")
def index():
    payload = json.dumps(_data(), ensure_ascii=False).replace("<", "\\u003c")
    return PAGE.replace("__DATA__", payload)


PAGE = r"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#F5F6F8">
<link rel="manifest" href="manifest.webmanifest" crossorigin="use-credentials">
<link rel="icon" type="image/png" sizes="192x192" href="static/icon-192.png">
<link rel="apple-touch-icon" href="static/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Фінанси">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<title>Фінанси Олі й Марка</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Onest:wght@400;500&display=swap" rel="stylesheet">
<style>
@font-face { font-family: "Outfit"; src: url("static/Outfit.ttf") format("truetype"); font-weight: 100 900; font-display: swap; }
/* стиль v2 з Claude Design: світлий фон, білі картки з тонкою рамкою, синій акцент, кольорові іконки категорій.
   Від людини залежить лише градієнт головної картки (наш, із бліком) і аватар. Без інтернету — запасний шрифт Outfit. */
:root {
  --bg: #F5F6F8; --card: #FFFFFF; --border: #ECEEF2; --soft: #F1F2F5; --track: #EEF0F3; --seg: #E9EBEF;
  --ink: #111318; --ink2: #6B7180; --ink3: #4A5060; --ink4: #2A2E38; --mute: #C4C8D0;
  --blue: #1F63E6; --blue-g: linear-gradient(180deg, #3B82F6, #1D5FE0); --blue-soft: #EAF1FF; --blue-txt: #1F55C9; --blue-l: #A9C6F8;
  --good: #15803D; --good-bg: #E3F7EA; --bad: #C0261B; --bad-bg: #FDE8E6; --cash: #7A4E00; --cash-bg: #FFF2DB;
  --hero-on: #DCE3F4; --glow: rgba(120, 160, 245, .24); --glow2: rgba(130, 175, 255, .62); --glow3: rgba(190, 215, 255, .45); --glow4: rgba(90, 120, 255, .55); --glow5: rgba(150, 130, 255, .5); --h1: rgba(86, 146, 255, .72); --h2: rgba(56, 189, 248, .5); --h3: rgba(139, 92, 246, .58); --h4: rgba(4, 8, 32, .9); --h5: rgba(59, 91, 255, .62); --h6: rgba(110, 231, 255, .78); --g1: #3A62C2; --g2: #142456; --g3: #010205; --l1: #8AF2FF; --l2: #2F6BFF; --l3: #2CCFD8; --l4: #7C5CFF;
  --ease: cubic-bezier(.33, 1, .68, 1); --spring: cubic-bezier(.34, 1.2, .64, 1);
  --font: "Onest", "Outfit", system-ui, -apple-system, "Segoe UI", sans-serif;
}
body.p-olya { --hero-on: #F1DDEA; --glow: rgba(214, 120, 182, .34); --glow2: rgba(232, 130, 200, .6); --glow3: rgba(255, 190, 230, .42); --glow4: rgba(255, 110, 170, .5); --glow5: rgba(190, 100, 230, .48); --h1: rgba(244, 114, 182, .62); --h2: rgba(251, 146, 120, .42); --h3: rgba(192, 86, 240, .55); --h4: rgba(28, 2, 20, .9); --h5: rgba(255, 72, 150, .55); --h6: rgba(255, 176, 120, .68); --g1: #9A4783; --g2: #3E1338; --g3: #040105; --l1: #FFBDE6; --l2: #E8449A; --l3: #A855F7; --l4: #D58CFF; }
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.4 var(--font); -webkit-font-smoothing: antialiased; font-variant-numeric: tabular-nums; }
.app { max-width: 440px; margin: 0 auto; padding: 8px 14px 130px; }
button { font: inherit; color: inherit; }
a { color: inherit; text-decoration: none; }
[hidden] { display: none !important; }
/* у звичайному тексті цифри природної ширини («15 днів» без «дірки» біля 1); рівні цифри лишаються для сум у стовпчиках */
.ins > span:first-child, .note, .sub, .lbl, .hero .lt, h2, .op .t1, .op .t2, .cal-note, .tag, .day-h, .info, .duo span, .duo small, .fv2 span { font-variant-numeric: proportional-nums; }
:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
.ico16 { flex: none; width: 16px; height: 16px; }
.ico16.chev, .more-r svg { width: 18px; height: 18px; }  /* шеврони ⌄ у кнопках-дропдаунах — трохи більші, щоб добре видно */

/* шапка */
.top { position: relative; z-index: 25; display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 4px 2px 0; }
.who, .mpick { position: relative; }
.who > button { display: flex; align-items: center; gap: 10px; height: 48px; padding: 0 14px 0 6px; border: 1px solid var(--border); border-radius: 24px; background: var(--card); font-size: 15px; font-weight: 500; cursor: pointer; }
.av { position: relative; flex: none; width: 36px; height: 36px; border-radius: 50%; overflow: hidden; background: #DCE8FD; }
.av::before, .av::after { content: ""; position: absolute; border-radius: 50%; background: #3B82F6; }
.av::before { left: 30%; top: 17%; width: 40%; height: 40%; }
.av::after { left: 10%; top: 66%; width: 80%; height: 60%; }
.av.olya { background: #F8E1EF; }
.av.olya::before, .av.olya::after { background: #C2569E; }
.mpick > button, .mstatic { display: flex; align-items: center; gap: 8px; height: 48px; padding: 0 14px 0 12px; border: 1px solid var(--border); border-radius: 24px; background: var(--card); font-size: 15px; white-space: nowrap; cursor: pointer; }
.mstatic { cursor: default; }
.who > button[aria-expanded="true"] .chev, .mpick > button[aria-expanded="true"] .chev { transform: rotate(180deg); }
.chev { transition: transform .45s var(--ease); }
.sw { overflow: hidden; }
/* «світч»: стрілки виїжджають кожна у свій бік і заходять з протилежного — ніби міняються місцями.
   Грає, коли меню відкривається і коли закривається (після вибору людини) */
.who > button[aria-expanded="true"] .sw-a { animation: swOutR .8s linear; }
.who > button[aria-expanded="true"] .sw-b { animation: swOutL .8s linear; }
.who > button[aria-expanded="false"] .sw-a { animation: swOutR2 .8s linear; }
.who > button[aria-expanded="false"] .sw-b { animation: swOutL2 .8s linear; }
/* виїзд з розгоном і згасанням, заїзд з протилежного боку з проявом і мʼяким гальмуванням */
@keyframes swOutR { 0% { transform: none; opacity: 1; animation-timing-function: cubic-bezier(.55, 0, .75, .4); } 44% { transform: translateX(15px); opacity: 0; } 45% { transform: translateX(-15px); opacity: 0; animation-timing-function: cubic-bezier(.2, .7, .3, 1); } 100% { transform: none; opacity: 1; } }
@keyframes swOutL { 0% { transform: none; opacity: 1; animation-timing-function: cubic-bezier(.55, 0, .75, .4); } 44% { transform: translateX(-15px); opacity: 0; } 45% { transform: translateX(15px); opacity: 0; animation-timing-function: cubic-bezier(.2, .7, .3, 1); } 100% { transform: none; opacity: 1; } }
@keyframes swOutR2 { 0% { transform: none; opacity: 1; animation-timing-function: cubic-bezier(.55, 0, .75, .4); } 44% { transform: translateX(15px); opacity: 0; } 45% { transform: translateX(-15px); opacity: 0; animation-timing-function: cubic-bezier(.2, .7, .3, 1); } 100% { transform: none; opacity: 1; } }
@keyframes swOutL2 { 0% { transform: none; opacity: 1; animation-timing-function: cubic-bezier(.55, 0, .75, .4); } 44% { transform: translateX(-15px); opacity: 0; } 45% { transform: translateX(15px); opacity: 0; animation-timing-function: cubic-bezier(.2, .7, .3, 1); } 100% { transform: none; opacity: 1; } }
.pop { position: absolute; top: 56px; padding: 6px; border: 1px solid var(--border); border-radius: 24px; background: var(--card); box-shadow: 0 20px 40px rgba(17,19,24,.16); }
.who .pop { left: 0; width: 272px; }
.pop > p { margin: 10px 12px 6px; font-size: 13px; color: var(--ink2); }
.pop .opt { display: flex; align-items: center; gap: 12px; width: 100%; min-height: 60px; padding: 6px 12px 6px 8px; border: 0; border-radius: 18px; background: transparent; text-align: left; cursor: pointer; }
.pop .opt b { display: block; font-size: 15px; font-weight: 500; }
.pop .opt small { display: block; font-size: 12px; color: var(--ink2); }
.pop .opt .radio { flex: none; width: 20px; height: 20px; margin-left: auto; border: 1.5px solid var(--mute); border-radius: 50%; }
.pop .opt[aria-checked="true"], .pop .opt[aria-selected="true"] { background: var(--soft); }
.pop .opt[aria-checked="true"] .radio { border: 6px solid var(--blue); }
.pop .logout { margin: 6px 0 0; padding-top: 6px; border-top: 1px solid var(--soft); }
.pop .logout button { display: flex; align-items: center; gap: 12px; width: 100%; min-height: 48px; padding: 0 12px 0 16px; border: 0; border-radius: 18px; background: transparent; color: var(--ink2); font-size: 15px; text-align: left; cursor: pointer; }
.pop .logout button:hover { background: var(--soft); color: var(--ink); }
.pop .logout svg { width: 20px; height: 20px; }
.pop .foot { margin: 6px 6px 4px; padding: 10px 6px 6px; border-top: 1px solid var(--soft); font-size: 12px; color: var(--ink2); line-height: 1.45; }
.mmenu { right: 0; width: min(332px, calc(100vw - 28px)); padding: 16px; }
.cal-head { display: grid; grid-template-columns: 44px 1fr 44px; align-items: center; margin-bottom: 8px; }
.cal-head b { text-align: center; font-size: 17px; font-weight: 500; }
.cal-head button { width: 44px; height: 44px; display: grid; place-items: center; border: 0; border-radius: 22px; background: var(--soft); cursor: pointer; }
.cal-head button svg { width: 18px; height: 18px; }
.cal-head button:disabled { opacity: .35; cursor: default; }
.cal-wd, .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); }
.cal-wd span { padding: 4px 0 6px; text-align: center; font-size: 12px; color: var(--ink2); }
.cal-grid { row-gap: 2px; }
.cal-grid button { position: relative; justify-self: center; width: 42px; height: 42px; padding: 0; border: 0; border-radius: 50%; background: transparent; font-size: 14px; cursor: pointer; }
.cal-grid button:disabled { color: var(--mute); cursor: default; }
.cal-grid button.has::after { content: ""; position: absolute; left: 50%; bottom: 6px; width: 4px; height: 4px; margin-left: -2px; border-radius: 50%; background: var(--blue); opacity: .55; }
.cal-grid button:not(:disabled):hover { background: var(--soft); }
.cal-grid button.today { box-shadow: inset 0 0 0 1.5px var(--blue); }
.cal-grid button[aria-pressed="true"] { background: var(--blue-g); color: #fff; }
.cal-grid button[aria-pressed="true"].has::after { background: #fff; opacity: .8; }
.cal-note { margin: 10px 4px 0; font-size: 12px; color: var(--ink2); }
.cal-all { display: block; width: 100%; min-height: 44px; margin-top: 10px; border: 0; border-top: 1px solid var(--soft); background: transparent; color: var(--blue); font: 500 14px var(--font); cursor: pointer; }
.acal { margin-top: 10px; padding: 12px; border: 1px solid var(--border); border-radius: 20px; background: var(--card); animation: popin .45s var(--ease) both; transform-origin: top right; }

/* головна картка: градієнт у кольорі людини, а над ним «рідкі» плями світла */
.hero-wrap { position: relative; isolation: isolate; margin-top: 12px; border-radius: 24px; overflow: hidden; clip-path: inset(0 round 24px); -webkit-mask-image: -webkit-radial-gradient(white, black);
  background: linear-gradient(160deg, color-mix(in srgb, var(--g2) 74%, var(--g3)) 0%, color-mix(in srgb, var(--g2) 58%, var(--g3)) 50%, color-mix(in srgb, var(--g2) 42%, var(--g3)) 100%); }
.hero { position: relative; z-index: 1; padding: 22px 20px 20px; border-radius: 24px; color: #fff; }
.hero-wrap::after { content: ""; position: absolute; inset: 0; z-index: 0; pointer-events: none; border-radius: inherit; opacity: .26; mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .9 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E"); }
/* SVG-шум браузер раструє зі своєю роздільністю й розмиває — тому JS малює зерно попіксельно під екран (одна крапка = один фізичний піксель); SVG лишається запасним */
.hero-wrap.grain::after { opacity: .14; mix-blend-mode: soft-light; background-image: var(--grain); background-size: 160px 160px; image-rendering: pixelated; }
/* Під час відкриття — один живий прохід бліку: світло збирається в лівому верхньому куті, смугою на всю картку
   хвилясто (кут смуги «гуляє») проходить до правого нижнього кута й зникає. Кант і розмита рамка по краях
   горять разом зі смугою. Далі лишаються тільки мʼякі абстрактні форми, що повільно перетікають усередині */
@property --p { syntax: "<percentage>"; inherits: true; initial-value: -40%; }
@property --ex { syntax: "<percentage>"; inherits: true; initial-value: 0%; }
@property --ey { syntax: "<percentage>"; inherits: true; initial-value: 20%; }
@property --a { syntax: "<angle>"; inherits: true; initial-value: 115deg; }
.liquid { position: absolute; inset: 0; z-index: 0; pointer-events: none; border-radius: inherit; --sheen: .45; }
.liquid.sheen { animation: sheen 7s linear both, edge 12s linear infinite; }
body.p-olya .liquid { --sheen: .4; }
.liquid > span { position: absolute; inset: 0; border-radius: inherit; }
.liquid .glow, .liquid .halo, .liquid .rim { opacity: var(--sheen); }
.liquid .glow { filter: blur(24px);
  background: linear-gradient(var(--a), transparent calc(var(--p) - 27%), color-mix(in srgb, var(--l2) 40%, transparent) calc(var(--p) - 13%),
    var(--l2) calc(var(--p) - 4.5%), var(--l1) var(--p), var(--l2) calc(var(--p) + 5%),
    color-mix(in srgb, var(--l2) 40%, transparent) calc(var(--p) + 15%), transparent calc(var(--p) + 28%)); }
.liquid .halo { filter: blur(8px); }
.liquid .halo i, .liquid .rim { position: absolute; inset: 0; border-radius: inherit;
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; }
.liquid .halo i { padding: 12px; background: linear-gradient(var(--a), transparent calc(var(--p) - 20%), var(--l2) calc(var(--p) - 6%), var(--l1) var(--p), var(--l2) calc(var(--p) + 6%), transparent calc(var(--p) + 20%)); }
.liquid .rim { padding: 1.5px; background: linear-gradient(var(--a), transparent calc(var(--p) - 15%), var(--l2) calc(var(--p) - 5%), #fff var(--p), var(--l2) calc(var(--p) + 5%), transparent calc(var(--p) + 15%)); }
@keyframes sheen {
  0.0% { --p: -40.0%; --a: 120.0deg; }
  1.8% { --p: -34.4%; --a: 120.7deg; }
  3.6% { --p: -28.9%; --a: 121.4deg; }
  5.4% { --p: -23.4%; --a: 122.1deg; }
  7.1% { --p: -17.9%; --a: 122.8deg; }
  8.9% { --p: -12.5%; --a: 123.5deg; }
  10.7% { --p: -7.1%; --a: 124.1deg; }
  12.5% { --p: -1.8%; --a: 124.7deg; }
  14.3% { --p: 3.4%; --a: 125.3deg; }
  16.1% { --p: 8.6%; --a: 125.8deg; }
  17.9% { --p: 13.8%; --a: 126.3deg; }
  19.6% { --p: 18.9%; --a: 126.7deg; }
  21.4% { --p: 23.9%; --a: 127.0deg; }
  23.2% { --p: 28.9%; --a: 127.4deg; }
  25.0% { --p: 33.8%; --a: 127.6deg; }
  26.8% { --p: 38.6%; --a: 127.8deg; }
  28.6% { --p: 43.4%; --a: 127.9deg; }
  30.4% { --p: 48.1%; --a: 128.0deg; }
  32.1% { --p: 52.7%; --a: 128.0deg; }
  33.9% { --p: 57.2%; --a: 127.9deg; }
  35.7% { --p: 61.7%; --a: 127.8deg; }
  37.5% { --p: 66.0%; --a: 127.6deg; }
  39.3% { --p: 70.3%; --a: 127.4deg; }
  41.1% { --p: 74.5%; --a: 127.0deg; }
  42.9% { --p: 78.7%; --a: 126.7deg; }
  44.6% { --p: 82.7%; --a: 126.3deg; }
  46.4% { --p: 86.6%; --a: 125.8deg; }
  48.2% { --p: 90.4%; --a: 125.3deg; }
  50.0% { --p: 94.2%; --a: 124.7deg; }
  51.8% { --p: 97.8%; --a: 124.1deg; }
  53.6% { --p: 101.4%; --a: 123.5deg; }
  55.4% { --p: 104.8%; --a: 122.8deg; }
  57.1% { --p: 108.1%; --a: 122.1deg; }
  58.9% { --p: 111.3%; --a: 121.4deg; }
  60.7% { --p: 114.4%; --a: 120.7deg; }
  62.5% { --p: 117.4%; --a: 120.0deg; }
  64.3% { --p: 120.3%; --a: 119.3deg; }
  66.1% { --p: 123.1%; --a: 118.6deg; }
  67.9% { --p: 125.7%; --a: 117.9deg; }
  69.6% { --p: 128.2%; --a: 117.2deg; }
  71.4% { --p: 130.6%; --a: 116.5deg; }
  73.2% { --p: 132.9%; --a: 115.9deg; }
  75.0% { --p: 135.0%; --a: 115.3deg; }
  76.8% { --p: 137.0%; --a: 114.7deg; }
  78.6% { --p: 138.9%; --a: 114.2deg; }
  80.4% { --p: 140.6%; --a: 113.7deg; }
  82.1% { --p: 142.2%; --a: 113.3deg; }
  83.9% { --p: 143.7%; --a: 113.0deg; }
  85.7% { --p: 145.0%; --a: 112.6deg; }
  87.5% { --p: 146.1%; --a: 112.4deg; }
  89.3% { --p: 147.1%; --a: 112.2deg; }
  91.1% { --p: 148.0%; --a: 112.1deg; }
  92.9% { --p: 148.7%; --a: 112.0deg; }
  94.6% { --p: 149.3%; --a: 112.0deg; }
  96.4% { --p: 149.7%; --a: 112.1deg; }
  98.2% { --p: 149.9%; --a: 112.2deg; }
  100.0% { --p: 150.0%; --a: 112.4deg; }
}
/* після бліку світло лишається на краях: розмита рамка й тонкий кант біля лівого верхнього й правого нижнього кутів,
   повільно ковзають уздовж країв і перетікають (дві точки дзеркальні одна одній) */
.liquid .after { -webkit-mask: linear-gradient(var(--a), #000 calc(var(--p) - 14%), transparent calc(var(--p) + 6%)), linear-gradient(rgba(0, 0, 0, .7) 0 0);
  mask: linear-gradient(var(--a), #000 calc(var(--p) - 14%), transparent calc(var(--p) + 6%)), linear-gradient(rgba(0, 0, 0, .7) 0 0); }
.liquid.lit .after { -webkit-mask: none; mask: none; }  /* після першого проходу: те саме, що кінець бліку (--p 150%) */
.liquid .after > .ehalo, .liquid .after > .erim { position: absolute; inset: 0; border-radius: inherit; }
.liquid .after > .amb { position: absolute; }
body.p-olya .liquid .after { opacity: .68; }  /* в Олі світлі тони яскравіші — трохи тихіше, щоб лишалась глибина */
.liquid .ehalo { opacity: .46; } .liquid .erim { opacity: .5; }
.liquid .ehalo { filter: blur(8px); }
.liquid .ehalo i, .liquid .erim { position: absolute; inset: 0; border-radius: inherit;
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; }
.liquid .ehalo i { padding: 12px; background:
  radial-gradient(70% 90% at var(--ex) var(--ey), var(--l1) 0%, var(--l2) 30%, transparent 70%),
  radial-gradient(60% 80% at calc(100% - var(--ex)) calc(100% - var(--ey)), var(--l3) 0%, color-mix(in srgb, var(--l3) 50%, transparent) 35%, transparent 70%); }
.liquid .erim { padding: 1.5px; background:
  radial-gradient(55% 75% at var(--ex) var(--ey), #fff 0%, var(--l1) 25%, var(--l2) 50%, transparent 78%),
  radial-gradient(45% 65% at calc(100% - var(--ex)) calc(100% - var(--ey)), var(--l1) 0%, var(--l2) 40%, transparent 75%); }
@keyframes edge {
  0.0% { --ex: 0.0%; --ey: 0.0%; }
  2.5% { --ex: 7.2%; --ey: 0.0%; }
  5.0% { --ex: 14.2%; --ey: 0.0%; }
  7.5% { --ex: 20.9%; --ey: 0.0%; }
  10.0% { --ex: 27.0%; --ey: 0.0%; }
  12.5% { --ex: 32.5%; --ey: 0.0%; }
  15.0% { --ex: 37.2%; --ey: 0.0%; }
  17.5% { --ex: 41.0%; --ey: 0.0%; }
  20.0% { --ex: 43.7%; --ey: 0.0%; }
  22.5% { --ex: 45.4%; --ey: 0.0%; }
  25.0% { --ex: 46.0%; --ey: 0.0%; }
  27.5% { --ex: 45.4%; --ey: 0.0%; }
  30.0% { --ex: 43.7%; --ey: 0.0%; }
  32.5% { --ex: 41.0%; --ey: 0.0%; }
  35.0% { --ex: 37.2%; --ey: 0.0%; }
  37.5% { --ex: 32.5%; --ey: 0.0%; }
  40.0% { --ex: 27.0%; --ey: 0.0%; }
  42.5% { --ex: 20.9%; --ey: 0.0%; }
  45.0% { --ex: 14.2%; --ey: 0.0%; }
  47.5% { --ex: 7.2%; --ey: 0.0%; }
  50.0% { --ex: 0.0%; --ey: 0.0%; }
  52.5% { --ex: 0.0%; --ey: 12.8%; }
  55.0% { --ex: 0.0%; --ey: 25.3%; }
  57.5% { --ex: 0.0%; --ey: 37.2%; }
  60.0% { --ex: 0.0%; --ey: 48.2%; }
  62.5% { --ex: 0.0%; --ey: 58.0%; }
  65.0% { --ex: 0.0%; --ey: 66.3%; }
  67.5% { --ex: 0.0%; --ey: 73.1%; }
  70.0% { --ex: 0.0%; --ey: 78.0%; }
  72.5% { --ex: 0.0%; --ey: 81.0%; }
  75.0% { --ex: 0.0%; --ey: 82.0%; }
  77.5% { --ex: 0.0%; --ey: 81.0%; }
  80.0% { --ex: 0.0%; --ey: 78.0%; }
  82.5% { --ex: 0.0%; --ey: 73.1%; }
  85.0% { --ex: 0.0%; --ey: 66.3%; }
  87.5% { --ex: 0.0%; --ey: 58.0%; }
  90.0% { --ex: 0.0%; --ey: 48.2%; }
  92.5% { --ex: 0.0%; --ey: 37.2%; }
  95.0% { --ex: 0.0%; --ey: 25.3%; }
  97.5% { --ex: 0.0%; --ey: 12.8%; }
  100.0% { --ex: 0.0%; --ey: 0.0%; }
}
/* плавні безупинні траєкторії (згенеровано синусами, лінійно між точками — без «зависань»):
   форми дрейфують біля своїх кутів, по черзі заходять усередину, змінюють обрис (коло ↔ абстрактна пляма)
   і яскравість — то розгоряються, то згасають, перетікаючи одна в одну */
@keyframes flow1 {
  0.0% { transform: translate(0.0%, 0.0%) rotate(0.0deg) scale(1.163, 0.735); border-radius: 50% 74% 49% 26% / 50% 71% 29% 50%; opacity: 0.66; }
  2.1% { transform: translate(0.9%, 2.1%) rotate(2.9deg) scale(1.201, 0.749); border-radius: 53% 73% 45% 27% / 56% 69% 27% 54%; opacity: 0.69; }
  4.2% { transform: translate(1.1%, 4.5%) rotate(5.7deg) scale(1.235, 0.767); border-radius: 56% 70% 42% 30% / 62% 67% 26% 57%; opacity: 0.72; }
  6.2% { transform: translate(0.6%, 7.1%) rotate(8.4deg) scale(1.265, 0.790); border-radius: 59% 66% 40% 35% / 67% 65% 27% 60%; opacity: 0.74; }
  8.3% { transform: translate(-0.5%, 9.7%) rotate(11.0deg) scale(1.290, 0.816); border-radius: 62% 61% 37% 40% / 71% 62% 29% 62%; opacity: 0.76; }
  10.4% { transform: translate(-2.1%, 12.1%) rotate(13.4deg) scale(1.311, 0.845); border-radius: 65% 56% 34% 46% / 73% 59% 33% 65%; opacity: 0.78; }
  12.5% { transform: translate(-3.9%, 14.2%) rotate(15.6deg) scale(1.326, 0.877); border-radius: 67% 49% 32% 52% / 74% 56% 38% 67%; opacity: 0.78; }
  14.6% { transform: translate(-5.9%, 15.8%) rotate(17.5deg) scale(1.336, 0.911); border-radius: 69% 43% 30% 58% / 73% 53% 44% 69%; opacity: 0.78; }
  16.7% { transform: translate(-7.8%, 16.7%) rotate(19.1deg) scale(1.340, 0.946); border-radius: 71% 37% 29% 64% / 71% 50% 50% 71%; opacity: 0.76; }
  18.8% { transform: translate(-9.5%, 17.0%) rotate(20.3deg) scale(1.338, 0.982); border-radius: 72% 33% 27% 68% / 67% 47% 56% 72%; opacity: 0.74; }
  20.8% { transform: translate(-10.9%, 16.7%) rotate(21.3deg) scale(1.330, 1.019); border-radius: 73% 29% 26% 72% / 62% 44% 62% 73%; opacity: 0.72; }
  22.9% { transform: translate(-11.7%, 15.6%) rotate(21.8deg) scale(1.317, 1.055); border-radius: 74% 27% 26% 74% / 56% 41% 67% 74%; opacity: 0.69; }
  25.0% { transform: translate(-12.0%, 14.0%) rotate(22.0deg) scale(1.298, 1.091); border-radius: 74% 26% 26% 74% / 50% 38% 71% 74%; opacity: 0.66; }
  27.1% { transform: translate(-11.7%, 11.9%) rotate(21.8deg) scale(1.275, 1.124); border-radius: 74% 27% 26% 73% / 44% 35% 73% 74%; opacity: 0.63; }
  29.2% { transform: translate(-10.9%, 9.5%) rotate(21.3deg) scale(1.246, 1.156); border-radius: 73% 30% 27% 70% / 38% 33% 74% 73%; opacity: 0.60; }
  31.2% { transform: translate(-9.5%, 6.9%) rotate(20.3deg) scale(1.213, 1.185); border-radius: 72% 34% 28% 65% / 33% 31% 73% 72%; opacity: 0.58; }
  33.3% { transform: translate(-7.8%, 4.3%) rotate(19.1deg) scale(1.177, 1.211); border-radius: 71% 39% 30% 60% / 29% 29% 71% 71%; opacity: 0.56; }
  35.4% { transform: translate(-5.9%, 1.9%) rotate(17.5deg) scale(1.137, 1.233); border-radius: 69% 44% 32% 54% / 27% 28% 67% 69%; opacity: 0.54; }
  37.5% { transform: translate(-3.9%, -0.2%) rotate(15.6deg) scale(1.096, 1.251); border-radius: 67% 51% 34% 48% / 26% 27% 62% 67%; opacity: 0.54; }
  39.6% { transform: translate(-2.1%, -1.8%) rotate(13.4deg) scale(1.052, 1.265); border-radius: 65% 57% 37% 42% / 27% 26% 56% 64%; opacity: 0.54; }
  41.7% { transform: translate(-0.5%, -2.7%) rotate(11.0deg) scale(1.008, 1.275); border-radius: 62% 63% 39% 36% / 29% 26% 50% 62%; opacity: 0.56; }
  43.8% { transform: translate(0.6%, -3.0%) rotate(8.4deg) scale(0.964, 1.279); border-radius: 59% 67% 42% 32% / 33% 26% 44% 59%; opacity: 0.58; }
  45.8% { transform: translate(1.1%, -2.7%) rotate(5.7deg) scale(0.920, 1.279); border-radius: 56% 71% 45% 28% / 38% 27% 38% 56%; opacity: 0.60; }
  47.9% { transform: translate(0.9%, -1.6%) rotate(2.9deg) scale(0.877, 1.275); border-radius: 53% 73% 48% 26% / 44% 28% 33% 53%; opacity: 0.63; }
  50.0% { transform: translate(0.0%, -0.0%) rotate(0.0deg) scale(0.837, 1.265); border-radius: 50% 74% 51% 26% / 50% 29% 29% 50%; opacity: 0.66; }
  52.1% { transform: translate(-1.7%, 2.1%) rotate(-2.9deg) scale(0.799, 1.251); border-radius: 47% 73% 55% 27% / 56% 31% 27% 46%; opacity: 0.69; }
  54.2% { transform: translate(-4.1%, 4.5%) rotate(-5.7deg) scale(0.765, 1.233); border-radius: 44% 70% 58% 30% / 62% 33% 26% 43%; opacity: 0.72; }
  56.2% { transform: translate(-7.0%, 7.1%) rotate(-8.4deg) scale(0.735, 1.210); border-radius: 41% 66% 60% 35% / 67% 35% 27% 40%; opacity: 0.74; }
  58.3% { transform: translate(-10.5%, 9.7%) rotate(-11.0deg) scale(0.710, 1.184); border-radius: 38% 61% 63% 40% / 71% 38% 29% 38%; opacity: 0.76; }
  60.4% { transform: translate(-14.2%, 12.1%) rotate(-13.4deg) scale(0.689, 1.155); border-radius: 35% 56% 66% 46% / 73% 41% 33% 35%; opacity: 0.78; }
  62.5% { transform: translate(-18.1%, 14.2%) rotate(-15.6deg) scale(0.674, 1.123); border-radius: 33% 49% 68% 52% / 74% 44% 38% 33%; opacity: 0.78; }
  64.6% { transform: translate(-21.8%, 15.8%) rotate(-17.5deg) scale(0.664, 1.089); border-radius: 31% 43% 70% 58% / 73% 47% 44% 31%; opacity: 0.78; }
  66.7% { transform: translate(-25.2%, 16.7%) rotate(-19.1deg) scale(0.660, 1.054); border-radius: 29% 37% 71% 64% / 71% 50% 50% 29%; opacity: 0.76; }
  68.8% { transform: translate(-28.0%, 17.0%) rotate(-20.3deg) scale(0.662, 1.018); border-radius: 28% 33% 73% 68% / 67% 53% 56% 28%; opacity: 0.74; }
  70.8% { transform: translate(-30.2%, 16.7%) rotate(-21.3deg) scale(0.670, 0.981); border-radius: 27% 29% 74% 72% / 62% 56% 62% 27%; opacity: 0.72; }
  72.9% { transform: translate(-31.5%, 15.6%) rotate(-21.8deg) scale(0.683, 0.945); border-radius: 26% 27% 74% 74% / 56% 59% 67% 26%; opacity: 0.69; }
  75.0% { transform: translate(-32.0%, 14.0%) rotate(-22.0deg) scale(0.702, 0.909); border-radius: 26% 26% 74% 74% / 50% 62% 71% 26%; opacity: 0.66; }
  77.1% { transform: translate(-31.5%, 11.9%) rotate(-21.8deg) scale(0.725, 0.876); border-radius: 26% 27% 74% 73% / 44% 65% 73% 26%; opacity: 0.63; }
  79.2% { transform: translate(-30.2%, 9.5%) rotate(-21.3deg) scale(0.754, 0.844); border-radius: 27% 30% 73% 70% / 38% 67% 74% 27%; opacity: 0.60; }
  81.2% { transform: translate(-28.0%, 6.9%) rotate(-20.3deg) scale(0.787, 0.815); border-radius: 28% 34% 72% 65% / 33% 69% 73% 28%; opacity: 0.58; }
  83.3% { transform: translate(-25.2%, 4.3%) rotate(-19.1deg) scale(0.823, 0.789); border-radius: 29% 39% 70% 60% / 29% 71% 71% 29%; opacity: 0.56; }
  85.4% { transform: translate(-21.8%, 1.9%) rotate(-17.5deg) scale(0.863, 0.767); border-radius: 31% 44% 68% 54% / 27% 72% 67% 31%; opacity: 0.54; }
  87.5% { transform: translate(-18.1%, -0.2%) rotate(-15.6deg) scale(0.904, 0.749); border-radius: 33% 51% 66% 48% / 26% 73% 62% 33%; opacity: 0.54; }
  89.6% { transform: translate(-14.2%, -1.8%) rotate(-13.4deg) scale(0.948, 0.735); border-radius: 35% 57% 63% 42% / 27% 74% 56% 36%; opacity: 0.54; }
  91.7% { transform: translate(-10.5%, -2.7%) rotate(-11.0deg) scale(0.992, 0.725); border-radius: 38% 63% 61% 36% / 29% 74% 50% 38%; opacity: 0.56; }
  93.8% { transform: translate(-7.0%, -3.0%) rotate(-8.4deg) scale(1.036, 0.721); border-radius: 41% 67% 58% 32% / 33% 74% 44% 41%; opacity: 0.58; }
  95.8% { transform: translate(-4.1%, -2.7%) rotate(-5.7deg) scale(1.080, 0.721); border-radius: 44% 71% 55% 28% / 38% 73% 38% 44%; opacity: 0.60; }
  97.9% { transform: translate(-1.7%, -1.6%) rotate(-2.9deg) scale(1.123, 0.725); border-radius: 47% 73% 52% 26% / 44% 72% 33% 47%; opacity: 0.63; }
  100.0% { transform: translate(-0.0%, -0.0%) rotate(-0.0deg) scale(1.163, 0.735); border-radius: 50% 74% 49% 26% / 50% 71% 29% 50%; opacity: 0.66; }
}
@keyframes flow2 {
  0.0% { transform: translate(26.0%, -8.1%) rotate(21.9deg) scale(1.175, 1.212); border-radius: 71% 37% 30% 64% / 71% 29% 50% 71%; opacity: 0.61; }
  2.1% { transform: translate(27.5%, -11.1%) rotate(22.0deg) scale(1.136, 1.234); border-radius: 69% 32% 32% 68% / 67% 28% 57% 69%; opacity: 0.58; }
  4.2% { transform: translate(28.3%, -13.8%) rotate(21.7deg) scale(1.094, 1.252); border-radius: 67% 29% 34% 72% / 62% 27% 62% 67%; opacity: 0.56; }
  6.2% { transform: translate(28.3%, -16.0%) rotate(21.1deg) scale(1.050, 1.266); border-radius: 65% 27% 37% 74% / 56% 26% 67% 64%; opacity: 0.55; }
  8.3% { transform: translate(27.4%, -17.6%) rotate(20.1deg) scale(1.006, 1.275); border-radius: 62% 26% 39% 74% / 50% 26% 71% 62%; opacity: 0.54; }
  10.4% { transform: translate(25.8%, -18.4%) rotate(18.7deg) scale(0.962, 1.280); border-radius: 59% 27% 42% 73% / 44% 26% 73% 59%; opacity: 0.54; }
  12.5% { transform: translate(23.3%, -18.5%) rotate(17.0deg) scale(0.918, 1.279); border-radius: 56% 30% 45% 70% / 38% 27% 74% 56%; opacity: 0.55; }
  14.6% { transform: translate(20.1%, -17.9%) rotate(15.1deg) scale(0.876, 1.274); border-radius: 53% 34% 48% 65% / 33% 28% 73% 53%; opacity: 0.57; }
  16.7% { transform: translate(16.4%, -16.4%) rotate(12.9deg) scale(0.835, 1.264); border-radius: 50% 39% 52% 60% / 29% 29% 71% 49%; opacity: 0.59; }
  18.8% { transform: translate(12.3%, -14.4%) rotate(10.4deg) scale(0.798, 1.250); border-radius: 47% 45% 55% 54% / 27% 31% 67% 46%; opacity: 0.62; }
  20.8% { transform: translate(8.0%, -11.8%) rotate(7.8deg) scale(0.764, 1.232); border-radius: 44% 51% 58% 48% / 26% 33% 62% 43%; opacity: 0.65; }
  22.9% { transform: translate(3.8%, -8.9%) rotate(5.1deg) scale(0.734, 1.209); border-radius: 41% 57% 61% 42% / 27% 36% 56% 40%; opacity: 0.68; }
  25.0% { transform: translate(-0.1%, -5.9%) rotate(2.2deg) scale(0.709, 1.183); border-radius: 38% 63% 63% 36% / 29% 38% 50% 38%; opacity: 0.71; }
  27.1% { transform: translate(-3.6%, -2.9%) rotate(-0.7deg) scale(0.688, 1.154); border-radius: 35% 68% 66% 32% / 33% 41% 43% 35%; opacity: 0.74; }
  29.2% { transform: translate(-6.4%, -0.2%) rotate(-3.5deg) scale(0.673, 1.122); border-radius: 33% 71% 68% 28% / 38% 44% 38% 33%; opacity: 0.76; }
  31.2% { transform: translate(-8.4%, 2.0%) rotate(-6.3deg) scale(0.664, 1.088); border-radius: 31% 73% 70% 26% / 44% 47% 33% 31%; opacity: 0.77; }
  33.3% { transform: translate(-9.6%, 3.6%) rotate(-9.0deg) scale(0.660, 1.053); border-radius: 29% 74% 72% 26% / 50% 50% 29% 29%; opacity: 0.78; }
  35.4% { transform: translate(-9.8%, 4.4%) rotate(-11.6deg) scale(0.662, 1.016); border-radius: 28% 73% 73% 27% / 56% 53% 27% 28%; opacity: 0.78; }
  37.5% { transform: translate(-9.1%, 4.5%) rotate(-13.9deg) scale(0.670, 0.980); border-radius: 27% 70% 74% 30% / 62% 56% 26% 27%; opacity: 0.77; }
  39.6% { transform: translate(-7.6%, 3.9%) rotate(-16.0deg) scale(0.684, 0.943); border-radius: 26% 66% 74% 35% / 67% 59% 27% 26%; opacity: 0.75; }
  41.7% { transform: translate(-5.4%, 2.4%) rotate(-17.8deg) scale(0.703, 0.908); border-radius: 26% 61% 74% 40% / 71% 62% 29% 26%; opacity: 0.73; }
  43.8% { transform: translate(-2.7%, 0.4%) rotate(-19.4deg) scale(0.727, 0.874); border-radius: 26% 55% 74% 46% / 73% 65% 33% 26%; opacity: 0.70; }
  45.8% { transform: translate(0.5%, -2.2%) rotate(-20.6deg) scale(0.755, 0.843); border-radius: 27% 49% 73% 52% / 74% 67% 38% 27%; opacity: 0.67; }
  47.9% { transform: translate(3.8%, -5.1%) rotate(-21.4deg) scale(0.788, 0.814); border-radius: 28% 43% 72% 58% / 73% 69% 44% 28%; opacity: 0.64; }
  50.0% { transform: translate(7.1%, -8.1%) rotate(-21.9deg) scale(0.825, 0.788); border-radius: 29% 37% 70% 64% / 71% 71% 50% 29%; opacity: 0.61; }
  52.1% { transform: translate(10.1%, -11.1%) rotate(-22.0deg) scale(0.864, 0.766); border-radius: 31% 32% 68% 68% / 67% 72% 57% 31%; opacity: 0.58; }
  54.2% { transform: translate(12.8%, -13.8%) rotate(-21.7deg) scale(0.906, 0.748); border-radius: 33% 29% 66% 72% / 62% 73% 62% 33%; opacity: 0.56; }
  56.2% { transform: translate(15.0%, -16.0%) rotate(-21.1deg) scale(0.950, 0.734); border-radius: 35% 27% 63% 74% / 56% 74% 67% 36%; opacity: 0.55; }
  58.3% { transform: translate(16.6%, -17.6%) rotate(-20.1deg) scale(0.994, 0.725); border-radius: 38% 26% 61% 74% / 50% 74% 71% 38%; opacity: 0.54; }
  60.4% { transform: translate(17.5%, -18.4%) rotate(-18.7deg) scale(1.038, 0.720); border-radius: 41% 27% 58% 73% / 44% 74% 73% 41%; opacity: 0.54; }
  62.5% { transform: translate(17.7%, -18.5%) rotate(-17.0deg) scale(1.082, 0.721); border-radius: 44% 30% 55% 70% / 38% 73% 74% 44%; opacity: 0.55; }
  64.6% { transform: translate(17.4%, -17.9%) rotate(-15.1deg) scale(1.124, 0.726); border-radius: 47% 34% 52% 65% / 33% 72% 73% 47%; opacity: 0.57; }
  66.7% { transform: translate(16.5%, -16.4%) rotate(-12.9deg) scale(1.165, 0.736); border-radius: 50% 39% 48% 60% / 29% 71% 71% 51%; opacity: 0.59; }
  68.8% { transform: translate(15.3%, -14.4%) rotate(-10.4deg) scale(1.202, 0.750); border-radius: 53% 45% 45% 54% / 27% 69% 67% 54%; opacity: 0.62; }
  70.8% { transform: translate(13.8%, -11.8%) rotate(-7.8deg) scale(1.236, 0.768); border-radius: 56% 51% 42% 48% / 26% 67% 62% 57%; opacity: 0.65; }
  72.9% { transform: translate(12.4%, -8.9%) rotate(-5.1deg) scale(1.266, 0.791); border-radius: 59% 57% 39% 42% / 27% 64% 56% 60%; opacity: 0.68; }
  75.0% { transform: translate(11.0%, -5.9%) rotate(-2.2deg) scale(1.291, 0.817); border-radius: 62% 63% 37% 36% / 29% 62% 50% 62%; opacity: 0.71; }
  77.1% { transform: translate(9.9%, -2.9%) rotate(0.7deg) scale(1.312, 0.846); border-radius: 65% 68% 34% 32% / 33% 59% 43% 65%; opacity: 0.74; }
  79.2% { transform: translate(9.3%, -0.2%) rotate(3.5deg) scale(1.327, 0.878); border-radius: 67% 71% 32% 28% / 38% 56% 38% 67%; opacity: 0.76; }
  81.2% { transform: translate(9.1%, 2.0%) rotate(6.3deg) scale(1.336, 0.912); border-radius: 69% 73% 30% 26% / 44% 53% 33% 69%; opacity: 0.77; }
  83.3% { transform: translate(9.6%, 3.6%) rotate(9.0deg) scale(1.340, 0.947); border-radius: 71% 74% 28% 26% / 50% 50% 29% 71%; opacity: 0.78; }
  85.4% { transform: translate(10.6%, 4.4%) rotate(11.6deg) scale(1.338, 0.984); border-radius: 72% 73% 27% 27% / 56% 47% 27% 72%; opacity: 0.78; }
  87.5% { transform: translate(12.1%, 4.5%) rotate(13.9deg) scale(1.330, 1.020); border-radius: 73% 70% 26% 30% / 62% 44% 26% 73%; opacity: 0.77; }
  89.6% { transform: translate(14.2%, 3.9%) rotate(16.0deg) scale(1.316, 1.057); border-radius: 74% 66% 26% 35% / 67% 41% 27% 74%; opacity: 0.75; }
  91.7% { transform: translate(16.6%, 2.4%) rotate(17.8deg) scale(1.297, 1.092); border-radius: 74% 61% 26% 40% / 71% 38% 29% 74%; opacity: 0.73; }
  93.8% { transform: translate(19.1%, 0.4%) rotate(19.4deg) scale(1.273, 1.126); border-radius: 74% 55% 26% 46% / 73% 35% 33% 74%; opacity: 0.70; }
  95.8% { transform: translate(21.7%, -2.2%) rotate(20.6deg) scale(1.245, 1.157); border-radius: 73% 49% 27% 52% / 74% 33% 38% 73%; opacity: 0.67; }
  97.9% { transform: translate(24.0%, -5.1%) rotate(21.4deg) scale(1.212, 1.186); border-radius: 72% 43% 28% 58% / 73% 31% 44% 72%; opacity: 0.64; }
  100.0% { transform: translate(26.0%, -8.1%) rotate(21.9deg) scale(1.175, 1.212); border-radius: 71% 37% 30% 64% / 71% 29% 50% 71%; opacity: 0.61; }
}
@keyframes flow3 {
  0.0% { transform: translate(-21.9%, -4.7%) rotate(4.4deg) scale(0.660, 1.051); border-radius: 29% 39% 72% 60% / 29% 50% 71% 29%; opacity: 0.69; }
  2.1% { transform: translate(-19.9%, -3.2%) rotate(1.6deg) scale(0.662, 1.015); border-radius: 28% 45% 73% 54% / 27% 54% 67% 28%; opacity: 0.71; }
  4.2% { transform: translate(-17.6%, -1.5%) rotate(-1.3deg) scale(0.671, 0.978); border-radius: 27% 51% 74% 48% / 26% 57% 62% 27%; opacity: 0.72; }
  6.2% { transform: translate(-15.2%, 0.4%) rotate(-4.2deg) scale(0.684, 0.942); border-radius: 26% 57% 74% 42% / 27% 60% 56% 26%; opacity: 0.72; }
  8.3% { transform: translate(-12.9%, 2.4%) rotate(-7.0deg) scale(0.703, 0.907); border-radius: 26% 63% 74% 36% / 29% 62% 49% 26%; opacity: 0.71; }
  10.4% { transform: translate(-10.8%, 4.2%) rotate(-9.6deg) scale(0.728, 0.873); border-radius: 26% 68% 74% 31% / 33% 65% 43% 26%; opacity: 0.70; }
  12.5% { transform: translate(-9.0%, 5.8%) rotate(-12.1deg) scale(0.757, 0.841); border-radius: 27% 71% 73% 28% / 38% 67% 38% 27%; opacity: 0.68; }
  14.6% { transform: translate(-7.7%, 7.1%) rotate(-14.4deg) scale(0.790, 0.813); border-radius: 28% 73% 71% 26% / 44% 69% 33% 28%; opacity: 0.65; }
  16.7% { transform: translate(-6.9%, 8.0%) rotate(-16.5deg) scale(0.826, 0.787); border-radius: 29% 74% 70% 26% / 50% 71% 29% 30%; opacity: 0.62; }
  18.8% { transform: translate(-6.7%, 8.4%) rotate(-18.2deg) scale(0.866, 0.765); border-radius: 31% 73% 68% 28% / 56% 72% 27% 31%; opacity: 0.59; }
  20.8% { transform: translate(-7.0%, 8.3%) rotate(-19.7deg) scale(0.908, 0.747); border-radius: 33% 70% 66% 31% / 62% 73% 26% 34%; opacity: 0.56; }
  22.9% { transform: translate(-7.8%, 7.7%) rotate(-20.8deg) scale(0.951, 0.734); border-radius: 36% 66% 63% 35% / 67% 74% 27% 36%; opacity: 0.53; }
  25.0% { transform: translate(-9.0%, 6.7%) rotate(-21.6deg) scale(0.996, 0.725); border-radius: 38% 61% 61% 40% / 71% 74% 29% 39%; opacity: 0.51; }
  27.1% { transform: translate(-10.5%, 5.2%) rotate(-21.9deg) scale(1.040, 0.720); border-radius: 41% 55% 58% 46% / 73% 74% 33% 41%; opacity: 0.49; }
  29.2% { transform: translate(-12.1%, 3.5%) rotate(-22.0deg) scale(1.084, 0.721); border-radius: 44% 49% 55% 52% / 74% 73% 38% 44%; opacity: 0.48; }
  31.2% { transform: translate(-13.7%, 1.6%) rotate(-21.6deg) scale(1.126, 0.726); border-radius: 47% 43% 51% 58% / 73% 72% 44% 48%; opacity: 0.48; }
  33.3% { transform: translate(-15.0%, -0.4%) rotate(-20.9deg) scale(1.166, 0.736); border-radius: 50% 37% 48% 64% / 71% 71% 51% 51%; opacity: 0.49; }
  35.4% { transform: translate(-16.0%, -2.2%) rotate(-19.8deg) scale(1.204, 0.751); border-radius: 53% 32% 45% 69% / 67% 69% 57% 54%; opacity: 0.50; }
  37.5% { transform: translate(-16.6%, -3.8%) rotate(-18.4deg) scale(1.237, 0.769); border-radius: 56% 29% 42% 72% / 62% 67% 62% 57%; opacity: 0.52; }
  39.6% { transform: translate(-16.5%, -5.1%) rotate(-16.6deg) scale(1.267, 0.792); border-radius: 59% 27% 39% 74% / 56% 64% 67% 60%; opacity: 0.55; }
  41.7% { transform: translate(-15.9%, -6.0%) rotate(-14.6deg) scale(1.292, 0.818); border-radius: 62% 26% 37% 74% / 50% 62% 71% 63%; opacity: 0.58; }
  43.8% { transform: translate(-14.7%, -6.4%) rotate(-12.3deg) scale(1.312, 0.848); border-radius: 65% 27% 34% 72% / 44% 59% 73% 65%; opacity: 0.61; }
  45.8% { transform: translate(-12.9%, -6.3%) rotate(-9.8deg) scale(1.327, 0.879); border-radius: 67% 30% 32% 69% / 38% 56% 74% 67%; opacity: 0.64; }
  47.9% { transform: translate(-10.6%, -5.7%) rotate(-7.2deg) scale(1.337, 0.913); border-radius: 69% 34% 30% 65% / 33% 53% 73% 69%; opacity: 0.67; }
  50.0% { transform: translate(-7.9%, -4.7%) rotate(-4.4deg) scale(1.340, 0.949); border-radius: 71% 39% 28% 60% / 29% 50% 71% 71%; opacity: 0.69; }
  52.1% { transform: translate(-5.1%, -3.2%) rotate(-1.6deg) scale(1.338, 0.985); border-radius: 72% 45% 27% 54% / 27% 46% 67% 72%; opacity: 0.71; }
  54.2% { transform: translate(-2.1%, -1.5%) rotate(1.3deg) scale(1.329, 1.022); border-radius: 73% 51% 26% 48% / 26% 43% 62% 73%; opacity: 0.72; }
  56.2% { transform: translate(0.6%, 0.4%) rotate(4.2deg) scale(1.316, 1.058); border-radius: 74% 57% 26% 42% / 27% 40% 56% 74%; opacity: 0.72; }
  58.3% { transform: translate(3.1%, 2.4%) rotate(7.0deg) scale(1.297, 1.093); border-radius: 74% 63% 26% 36% / 29% 38% 49% 74%; opacity: 0.71; }
  60.4% { transform: translate(5.1%, 4.2%) rotate(9.6deg) scale(1.272, 1.127); border-radius: 74% 68% 26% 31% / 33% 35% 43% 74%; opacity: 0.70; }
  62.5% { transform: translate(6.4%, 5.8%) rotate(12.1deg) scale(1.243, 1.159); border-radius: 73% 71% 27% 28% / 38% 33% 38% 73%; opacity: 0.68; }
  64.6% { transform: translate(7.0%, 7.1%) rotate(14.4deg) scale(1.210, 1.187); border-radius: 72% 73% 29% 26% / 44% 31% 33% 72%; opacity: 0.65; }
  66.7% { transform: translate(6.9%, 8.0%) rotate(16.5deg) scale(1.174, 1.213); border-radius: 71% 74% 30% 26% / 50% 29% 29% 70%; opacity: 0.62; }
  68.8% { transform: translate(5.9%, 8.4%) rotate(18.2deg) scale(1.134, 1.235); border-radius: 69% 73% 32% 28% / 56% 28% 27% 69%; opacity: 0.59; }
  70.8% { transform: translate(4.2%, 8.3%) rotate(19.7deg) scale(1.092, 1.253); border-radius: 67% 70% 34% 31% / 62% 27% 26% 66%; opacity: 0.56; }
  72.9% { transform: translate(1.8%, 7.7%) rotate(20.8deg) scale(1.049, 1.266); border-radius: 64% 66% 37% 35% / 67% 26% 27% 64%; opacity: 0.53; }
  75.0% { transform: translate(-1.2%, 6.7%) rotate(21.6deg) scale(1.004, 1.275); border-radius: 62% 61% 39% 40% / 71% 26% 29% 61%; opacity: 0.51; }
  77.1% { transform: translate(-4.5%, 5.2%) rotate(21.9deg) scale(0.960, 1.280); border-radius: 59% 55% 42% 46% / 73% 26% 33% 59%; opacity: 0.49; }
  79.2% { transform: translate(-8.1%, 3.5%) rotate(22.0deg) scale(0.916, 1.279); border-radius: 56% 49% 45% 52% / 74% 27% 38% 56%; opacity: 0.48; }
  81.2% { transform: translate(-11.7%, 1.6%) rotate(21.6deg) scale(0.874, 1.274); border-radius: 53% 43% 49% 58% / 73% 28% 44% 52%; opacity: 0.48; }
  83.3% { transform: translate(-15.2%, -0.4%) rotate(20.9deg) scale(0.834, 1.264); border-radius: 50% 37% 52% 64% / 71% 29% 51% 49%; opacity: 0.49; }
  85.4% { transform: translate(-18.3%, -2.2%) rotate(19.8deg) scale(0.796, 1.249); border-radius: 47% 32% 55% 69% / 67% 31% 57% 46%; opacity: 0.50; }
  87.5% { transform: translate(-20.9%, -3.8%) rotate(18.4deg) scale(0.763, 1.231); border-radius: 44% 29% 58% 72% / 62% 33% 62% 43%; opacity: 0.52; }
  89.6% { transform: translate(-22.8%, -5.1%) rotate(16.6deg) scale(0.733, 1.208); border-radius: 41% 27% 61% 74% / 56% 36% 67% 40%; opacity: 0.55; }
  91.7% { transform: translate(-24.1%, -6.0%) rotate(14.6deg) scale(0.708, 1.182); border-radius: 38% 26% 63% 74% / 50% 38% 71% 37%; opacity: 0.58; }
  93.8% { transform: translate(-24.6%, -6.4%) rotate(12.3deg) scale(0.688, 1.152); border-radius: 35% 27% 66% 72% / 44% 41% 73% 35%; opacity: 0.61; }
  95.8% { transform: translate(-24.3%, -6.3%) rotate(9.8deg) scale(0.673, 1.121); border-radius: 33% 30% 68% 69% / 38% 44% 74% 33%; opacity: 0.64; }
  97.9% { transform: translate(-23.4%, -5.7%) rotate(7.2deg) scale(0.663, 1.087); border-radius: 31% 34% 70% 65% / 33% 47% 73% 31%; opacity: 0.67; }
  100.0% { transform: translate(-21.9%, -4.7%) rotate(4.4deg) scale(0.660, 1.051); border-radius: 29% 39% 72% 60% / 29% 50% 71% 29%; opacity: 0.69; }
}
/* мʼякі абстрактні форми після бліку: три великі розмиті плями біля країв (рух і яскравість — у кадрах flow1–3) */
.liquid .amb { inset: auto; width: 96%; aspect-ratio: 1.7; border-radius: 50%; filter: blur(34px); mix-blend-mode: screen;
  will-change: transform; opacity: .7; }
.liquid .a3 { right: -44%; top: 6%; width: 68%; aspect-ratio: 1.2; background: radial-gradient(42% 38% at 30% 26%, var(--l4) 0%, color-mix(in srgb, var(--l4) 50%, transparent) 50%, transparent 100%), radial-gradient(32% 28% at 66% 76%, var(--l3) 0%, color-mix(in srgb, var(--l3) 45%, transparent) 50%, transparent 100%);
  animation: flow3 9.5s linear infinite; }
.liquid .a1 { animation: flow1 11s linear infinite; }
.liquid .a2 { animation: flow2 13s linear infinite; }
.liquid .ehalo i { animation: glint 5s ease-in-out infinite; }
.liquid .erim { animation: glintR 5s ease-in-out infinite; }
@keyframes glintR { 0%, 100% { opacity: .3; } 50% { opacity: .62; } }
@keyframes glint { 0%, 100% { opacity: .55; } 50% { opacity: 1; } }
.liquid .a1 { right: -38%; top: -48%; background: radial-gradient(44% 52% at 35% 40%, var(--l2) 0%, color-mix(in srgb, var(--l2) 55%, transparent) 55%, transparent 100%), radial-gradient(65% 60% at 68% 62%, var(--l4) 0%, color-mix(in srgb, var(--l4) 50%, transparent) 50%, transparent 100%); }
.liquid .a2 { left: -36%; bottom: -50%; width: 74%; background: radial-gradient(46% 54% at 35% 40%, var(--l3) 0%, color-mix(in srgb, var(--l3) 55%, transparent) 55%, transparent 100%), radial-gradient(48% 44% at 68% 62%, var(--l2) 0%, color-mix(in srgb, var(--l2) 50%, transparent) 50%, transparent 100%); }
/* легка тінь під білим текстом — підпис лишається читабельним, коли блік пропливає під ним */
.hero .lbl, .hero .num, .hero .sub, .hero .lt, .hero .lg2 b { text-shadow: 0 1px 10px rgba(5, 10, 30, .28); }
.hero .sub .inc { color: inherit; }
.hero .sub .bd { display: inline-flex; margin-left: 6px; vertical-align: 1px; }
/* смуга доходу: біла — залишок, напівпрозора — витрати; витрати ростуть зліва й стискають білу */
.hero .split2 { display: flex; gap: 4px; height: 8px; margin-top: 28px; }
.hero .split2 b { display: block; height: 100%; width: 0; border-radius: 4px; transition: width 2.1s var(--ease); }
.hero .split2 .e { background: rgba(255, 255, 255, .34); }
.hero .split2 .s { flex: 1; min-width: 0; background: #fff; }
.hero .lg2 { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-top: 8px; }
.hero .lt { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
.hero .lt i { flex: 0 0 8px; width: 8px; height: 8px; aspect-ratio: 1; border-radius: 50%;  /* 8px — на екранах 1,25×/1,5× 7px округлювався в сплющений овал */ background: rgba(255, 255, 255, .45); }
.hero .ls .lt i { background: #fff; }
.hero .lv { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; margin-top: 6px; }  /* бейдж «зазвичай» — окремим рядком під сумою */
.hero .lg2 b { font-size: 18px; font-weight: 500; }
.hero .ls { flex: none; max-width: 52%; display: flex; flex-direction: column; align-items: flex-end; text-align: right; }
.hero .ls .lt { justify-content: flex-end; }
.hero .le { min-width: 0; } .hero .lg2 b { white-space: nowrap; }
.hero .ls b { margin-top: 6px; }
.hero .lbl { font-size: 14px; }
.hero .num.cu { animation: numin .7s var(--ease) both .1s; }
@keyframes numin { from { opacity: 0; transform: translateY(8px); filter: blur(4px); } to { opacity: 1; transform: none; filter: none; } }
.hero .num { margin-top: 12px; font-size: 40px; font-weight: 500; letter-spacing: -.02em; line-height: 1.05; }
.hero .sub { margin-top: 6px; font-size: 14px; }
/* велика цифра й «/ з доходу …» одразу за нею, по базовій лінії */
.hero .numrow { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; margin-top: 12px; }  /* на вузькому екрані «з доходу» переходить під цифру */
.hero .numrow .num { margin-top: 0; min-width: 0; white-space: nowrap; }
.hero .side { flex: none; display: inline-flex; align-items: baseline; gap: 4px; font-size: 13px; color: rgba(255,255,255,.72); white-space: nowrap; text-shadow: 0 1px 10px rgba(5, 10, 30, .28); }
.hero .side b { font-weight: 500; color: #fff; }
.hero .side .bd { align-self: center; margin-left: 4px; }
.hero .lbl .tag { margin-left: auto; }  /* «дані до …» — до правого краю */.hero .lbl { display: flex; align-items: center; gap: 8px; }
.hero .tag { flex: none; height: 22px; padding: 0 8px; border-radius: 11px; background: rgba(255,255,255,.16); -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px); color: #fff; font-size: 12px; font-weight: 500; display: flex; align-items: center; }
.hero .meter { height: 8px; margin: 14px 0 8px; border-radius: 4px; background: rgba(255,255,255,.25); overflow: hidden; }
.hero .meter b { display: block; height: 100%; width: 0; border-radius: 4px; background: #fff; transition: width 2.1s var(--ease); }
.hero .hint { display: flex; gap: 6px; align-items: flex-start; margin-top: 16px; padding-top: 14px; border-top: 1px solid rgba(255,255,255,.35); font-size: 14px; line-height: 1.4; }
.hero .hint svg { flex: none; width: 18px; height: 18px; margin-top: 1px; }
/* бейджі: маленькі заокруглені плашки, стрілка діагональна */
.bd { flex: none; display: inline-flex; align-items: center; gap: 3px; height: 24px; padding: 0 8px; border-radius: 12px; background: var(--soft); color: var(--ink3); font-size: 12px; font-weight: 500; white-space: nowrap; }
.bd.good { background: var(--good-bg); color: var(--good); }
.bd.bad { background: var(--bad-bg); color: var(--bad); }
.bd.blue { padding: 0 6px; background: var(--blue-soft); color: var(--blue-txt); letter-spacing: -.02em; word-spacing: -.12em; }
.bd.new { background: var(--cash-bg); color: var(--cash); }
.hero .bd:not(.good):not(.bad) { background: rgba(255,255,255,.92); color: #3A4050; }

.cashbtn { display: flex; align-items: center; justify-content: center; gap: 10px; height: 56px; margin-top: 12px; border-radius: 28px; background: var(--blue-g);
  box-shadow: 0 8px 18px rgba(29,95,224,.32), inset 0 1px 0 rgba(255,255,255,.3); color: #fff; font-size: 16px; font-weight: 500; }
.cashbtn svg { width: 22px; height: 22px; }

/* картки */
.card { margin-top: 12px; padding: 16px; border: 1px solid var(--border); border-radius: 20px; background: var(--card); }
.card.list { padding: 6px 16px 8px; }
.ch { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 6px 10px; min-height: 44px; }
.ch h2 { margin: 0; font-size: 18px; font-weight: 500; white-space: nowrap; letter-spacing: -.01em; }
.ch a { display: inline-flex; align-items: center; gap: 2px; min-height: 44px; font-size: 14px; font-weight: 500; color: var(--blue); }
.ch .sub { font-size: 14px; color: var(--ink2); }
.note { font-size: 13px; line-height: 1.45; color: var(--ink2); }
#t-note { display: flex; gap: 5px; align-items: flex-start; }
#t-note svg { flex: none; width: 16px; height: 16px; margin-top: 1px; }
.fx { display: inline-block; padding: 0 8px; border-radius: 10px; line-height: 20px; background: var(--soft); color: var(--ink3); font-size: 12px; }
.fx.cash { background: var(--cash-bg); color: var(--cash); }
.fx.now { background: var(--blue); color: #fff; }
/* «Що змінилось у жовтні» */
.ins { display: flex; justify-content: space-between; align-items: center; gap: 12px; min-height: 56px; padding: 14px 0; border-top: 1px solid var(--soft); font-size: 14px; line-height: 1.45; color: var(--ink4); }
.ins .it { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.ins .it small { font-size: 13px; color: var(--ink2); }
.ins .ir { flex: none; display: inline-flex; align-items: center; gap: 4px; color: var(--ink2); }
a.ins { color: var(--ink4); }
.fadein { animation: rise .8s var(--ease) both; }
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
/* категорії: кольорова іконка, назва, сума, частка, шкала */
.crow { display: grid; grid-template-columns: 36px 1fr auto auto; gap: 8px 12px; align-items: center; padding: 12px 0; }
.crow + .crow { border-top: 1px solid var(--soft); }
.crow .nm { font-size: 15px; display: flex; align-items: center; gap: 6px; min-width: 0; }
.crow .am { font-size: 14px; font-weight: 500; }
.crow .bar6 { grid-column: 2 / -1; height: 6px; border-radius: 999px; background: var(--track); overflow: hidden; }
.crow .bar6 b { display: block; height: 100%; width: 0; border-radius: 999px; background: linear-gradient(90deg, #3B82F6, #1D5FE0); transition: width 2.1s var(--ease); }
.cic { flex: none; width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; background: var(--soft); }
.cic svg { width: 18px; height: 18px; }
.cic.inc { background: var(--good-bg); }
.cic .glyph, .ji .glyph { font-weight: 400; line-height: 1; }
.more { display: flex; justify-content: space-between; align-items: center; width: 100%; min-height: 52px; margin-top: 4px; padding: 0; border: 0; border-top: 1px solid var(--soft);
  background: none; font: inherit; font-size: 15px; color: var(--ink2); cursor: pointer; text-align: left; }
.more-r { display: inline-flex; align-items: center; gap: 6px; }
.more svg { transition: transform .45s var(--ease); }
.more[aria-expanded="true"] svg { transform: rotate(180deg); }
.more[aria-expanded="true"] .more-sum { opacity: 0; }
.more-sum { transition: opacity .3s var(--ease); }
/* розгортання як у решті застосунку: висота мʼяко відкривається, рядки по черзі піднімаються (як .stag, крок 45 мс);
   при згортанні рядки спершу тануть, потім висота закривається */
.more-rows { display: grid; grid-template-rows: 0fr; transition: grid-template-rows .55s cubic-bezier(.4, 0, .2, 1) .12s; }
.more-rows > div { overflow: hidden; min-height: 0; }
.more-rows .crow { opacity: 0; transform: translateY(6px); transition: opacity .25s var(--ease), transform .25s var(--ease); }
.more-rows.open { grid-template-rows: 1fr; transition-delay: 0s; transition-duration: .7s; }
.more-rows.open .crow { opacity: 1; transform: none; transition-duration: .8s; transition-delay: calc(var(--i, 0) * 45ms + 120ms); }
/* операції */
.op { display: flex; align-items: center; gap: 12px; min-height: 64px; padding: 12px 0; border-top: 1px solid var(--soft); }
.op .cic { width: 40px; height: 40px; border-radius: 20px; }
.op .cic svg { width: 20px; height: 20px; }
.op .t { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.op .t1 { font-size: 15px; }
.op .t1.one { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.op .t1row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.op .t1row .t1 { min-width: 0; }
.op .t1row .fx { flex: none; }
.op .t1:not(.one) { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; }
.op .t2 { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 13px; color: var(--ink2); }
.op .r { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.op .am { font-size: 15px; font-weight: 500; white-space: nowrap; }
.op .am.inc { color: var(--good); }
/* тонка вертикальна риска між категорією і датою */
.sep { display: inline-block; width: 1px; height: .85em; margin: 0 7px; vertical-align: -.08em; background: currentColor; opacity: .4; }
/* видалення готівкового запису: кнопка-кошик у рядку журналу, а потім підтвердження в тому ж місці */
.op .del { flex: none; width: 44px; height: 44px; margin-right: -10px; border: 0; border-radius: 22px; background: transparent; color: var(--ink2); display: grid; place-items: center; cursor: pointer; }
.op .del:hover { background: var(--bad-bg); color: var(--bad); }
.op .del svg { width: 18px; height: 18px; }
.op .ask { flex: none; display: flex; gap: 6px; }
.swipe { position: relative; z-index: 1; clip-path: inset(-6px -16px -8px 0); }
.swipe .op { position: relative; z-index: 1; background: var(--card); touch-action: pan-y; transition: transform .45s cubic-bezier(.32, .72, 0, 1); }
.swdel { position: absolute; right: -8px; top: 0; bottom: 0; width: 88px; z-index: 0; border: 0; border-radius: 0 14px 14px 0; background: var(--bad-bg); color: var(--bad);
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; font: 500 12px var(--font); cursor: pointer; opacity: 0; transition: opacity .3s var(--ease); }
.swdel svg { width: 18px; height: 18px; }
/* під кнопкою ховаємо лінію наступного рядка на всю ширину кнопки (і під її заокругленими кутами) */
.swipe::after { content: ""; position: absolute; right: -16px; bottom: -1px; width: 96px; height: 1px; background: var(--card); opacity: 0; transition: opacity .2s ease; }
.swipe.on::after { opacity: 1; }
.swipe::before { content: ""; position: absolute; left: 0; right: 80px; top: 0; z-index: 2; border-top: 1px solid var(--soft); opacity: 0; transition: opacity .2s ease; pointer-events: none; }
.swipe.on::before { opacity: 1; }
.day .card .swipe:first-child::before { display: none; }
.day .card .swipe:first-child .swdel { top: 2px; }   /* 8 px від верху картки (відступ картки 6 px) */
.card .swipe:last-child .swdel { bottom: 0; }            /* 8 px від низу картки (відступ картки 8 px) */
.card .swipe:last-child::after { display: none; }
.swipe.on .swdel { opacity: 1; }
.swipe.gone { pointer-events: none; overflow: hidden; transition: height .35s cubic-bezier(.4, 0, .2, 1) .28s, opacity .3s ease .3s; }
.swipe.gone .op { transform: translateX(-110%) !important; transition: transform .35s cubic-bezier(.4, 0, .2, 1) !important; }
.swipe.gone .swdel { opacity: 0; transition: opacity .25s ease; }
.op .ask button { min-height: 44px; padding: 0 12px; border: 0; border-radius: 22px; background: var(--soft); font-size: 13px; font-weight: 500; cursor: pointer; }
.op .ask button.yes { background: var(--bad); color: #fff; }
.toast button { min-height: 36px; margin-left: 7px; padding: 0 12px; border: 0; border-radius: 12px; background: var(--bad-bg); color: var(--bad); font: 500 13px var(--font); cursor: pointer; }  /* «Скасувати» в плашках — червона */

/* подушка безпеки (Оля) */
/* картка ліміту на Головній */
.limc { display: block; color: inherit; text-decoration: none; }
.limc .ch { min-height: 0; flex-wrap: nowrap; }
.limc .ch svg { flex: none; color: var(--ink2); }
.lbig { display: flex; align-items: baseline; gap: 8px; margin-top: 12px; }
.lbig b { font-size: 32px; font-weight: 500; line-height: 1; }
.lbig span { font-size: 14px; color: var(--ink2); }
.lbar { height: 8px; margin-top: 14px; border-radius: 999px; background: var(--track); overflow: hidden; }
.lbar b { display: block; height: 100%; width: 0; border-radius: 999px; background: linear-gradient(90deg, #3B82F6, #1D5FE0); transition: width 2.1s var(--ease); }
.lbar b.over { background: var(--bad); }
.lnote { margin: 10px 0 0; font-size: 13px; color: var(--ink2); }
.limc .lmain { display: block; color: inherit; }
/* тихий рядок унизу картки: до шторки «Відкладати щомісяця» */
.lfoot { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 40px; margin-top: 12px; padding: 8px 0 0; border: 0; border-top: 1px solid var(--soft); background: transparent; font: 400 14px var(--font); color: var(--ink3); text-align: left; cursor: pointer; }
.lfoot .lft { flex: 1; min-width: 0; white-space: nowrap; }
.lfoot .lft b { font-weight: 500; color: var(--ink); }
.lfoot .llink { display: inline-flex; align-items: center; gap: 6px; color: var(--blue); font-weight: 500; }
.lfoot .llink svg { flex: none; stroke-width: 2.2; }  /* плюс більший і чіткіший за текстовий «+» */
.lfoot > svg { flex: none; color: var(--ink2); }
.lpig { flex: none; display: grid; margin-right: -6px; color: var(--ink2); }  /* прапорець цілі без кола, «Відкладено» — впритул */
.lsegs { flex: none; display: flex; gap: 3px; }
.lsegs i { display: block; width: 18px; height: 6px; border-radius: 3px; background: var(--track); overflow: hidden; }
.lsegs b { display: block; height: 100%; background: var(--blue); }
/* шторка «Відкладати щомісяця» */
.plintro { margin: 6px 0 0; font-size: 14px; line-height: 1.45; color: var(--ink3); }
.amount.pla { justify-content: flex-start; padding-top: 14px; }  /* у шторці плану все — від лівого краю */
.plchips { justify-content: flex-start; margin-top: 14px; }
.pllink { display: block; min-height: 44px; margin: 8px 0 0; padding: 0; border: 0; background: none; color: var(--blue); font: 500 14px var(--font); cursor: pointer; }
.plres { margin-top: 14px; padding: 12px 14px; border-radius: 14px; background: var(--blue-soft); font-size: 13px; line-height: 1.4; color: var(--ink3); }
.plres b { display: block; margin-bottom: 2px; font-size: 14px; font-weight: 500; color: var(--blue-txt); }
.plres.ok { background: var(--good-bg); } .plres.ok b { color: var(--good); }
.plres.warn { background: var(--cash-bg); } .plres.warn b { color: var(--cash); }
/* «До коли» — рядок-дропдаун: згорнутий показує термін, розгорнутий — пігулки і скільки збереться */
.pldd { margin-top: 14px; border: 1px solid var(--border); border-radius: 16px; background: var(--card); transition: border-color .2s; }
.pldd.open { border-color: var(--blue-l); }
.pldd-h { display: flex; align-items: center; gap: 8px; width: 100%; min-height: 52px; padding: 0 14px; border: 0; background: transparent; font: 400 14px var(--font); color: var(--ink2); text-align: left; cursor: pointer; }
.pldd-h span { flex: 1; }
.pldd-h b { font-weight: 500; color: var(--ink); }
.pldd-h .chev { color: var(--ink2); transition: transform .2s; }
.pldd.open .pldd-h .chev { transform: rotate(180deg); }
.pldd-w { display: grid; grid-template-rows: 0fr; opacity: 0; transition: grid-template-rows .38s cubic-bezier(.32, .72, 0, 1), opacity .25s ease; }
.pldd-w > div { min-height: 0; overflow: hidden; }
.pldd.open .pldd-w { grid-template-rows: 1fr; opacity: 1; }
.pldd-b { padding: 0 14px 14px; }
.pldd-b .pldate { margin-top: 10px; }
.pldd-s { margin: 12px 0 0; font-size: 13px; color: var(--ink3); }
.pldd-s b { font-weight: 500; color: var(--ink); }
.pllab { margin: 16px 2px 0; font-size: 13px; color: var(--ink2); }
.pldate { display: block; width: 100%; min-height: 44px; margin-top: 10px; padding: 0 12px; border: 1px solid var(--border); border-radius: 14px; background: var(--card); font: 400 15px var(--font); color: var(--ink); }
.plgoal { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin-top: 6px; font-size: 12px; color: var(--ink2); }
.plgoal .gdue { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 12px; background: var(--blue-soft); color: var(--blue); font-weight: 500; white-space: nowrap; }
.plrow { position: relative; }
.plwas { position: absolute; right: 2px; bottom: 12px; font-size: 12px; color: var(--ink2); }
.plcalc { margin-top: 14px; padding: 0 14px; border-radius: 14px; background: #F8F9FB; }
.plcalc .oprow:first-child { border-top: 0; }
.plcalc .oprow.tot { border-top-color: var(--mute); }
.plcalc .oprow.tot span, .plcalc .oprow.tot b { color: var(--ink); font-weight: 500; }
.plnote { margin: 12px 2px 0; font-size: 13px; color: var(--blue-txt); }
.plwarn { margin: 6px 2px 0; font-size: 13px; color: var(--cash); }
.goal .gbig { display: flex; align-items: baseline; gap: 8px; margin: 14px 0 0; }
.goal .gbig b { font-size: 32px; font-weight: 500; line-height: 1; color: var(--blue); }
.goal .gbig span { font-size: 16px; font-weight: 500; color: var(--ink); }
.goal .gsub { display: flex; align-items: center; gap: 4px; margin: 6px 0 14px; font-size: 13px; color: var(--ink2); }
.goal .gsub svg { flex: none; }
.goal .ch { flex-wrap: nowrap; gap: 8px; }
.goal .gdue { flex: none; display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 12px; background: var(--blue-soft); color: var(--blue); font-size: 12px; font-weight: 500; white-space: nowrap; }
.goal .gpace { margin-top: 16px; align-items: flex-start; }
.goal .gpace b { font-weight: 500; color: var(--ink); }  /* бейдж — у верхньому правому куті */
.goal .gpace > span:first-child { text-wrap: pretty; }  /* без одинокого слова в останньому рядку */
.goal .gpace .bd { flex: none; white-space: nowrap; }
.segs { display: grid; grid-template-columns: repeat(6, 1fr); gap: 4px; }
.segs i { position: relative; height: 8px; border-radius: 4px; background: var(--track); overflow: hidden; }
.segs i b { position: absolute; inset: 0; width: 0; border-radius: 4px; background: var(--blue-g); transition: width 2.1s var(--ease); }
.seglab { display: grid; grid-template-columns: repeat(6, 1fr); gap: 4px; margin-top: 8px; font-size: 12px; color: var(--ink2); }
.gcols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 20px 0 18px; padding-top: 18px; border-top: 1px solid var(--soft); }
.gcols span { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink2); }
.gcols span svg { flex: none; width: 16px; height: 16px; }
.gcols b { display: block; margin: 7px 0 4px; font-size: 18px; font-weight: 500; }
.gcols small { display: block; font-size: 12px; color: var(--ink2); }
.gcols b .of { font-style: normal; font-size: 13px; font-weight: 400; color: var(--ink2); }
.gcols .bd { display: inline-flex; padding: 0 7px; font-size: 12px; letter-spacing: -.01em; word-spacing: normal; }
.gpace { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 12px 14px; border-radius: 14px; background: #F8F9FB; font-size: 13px; line-height: 1.4; color: var(--ink4); }
/* помилка обліку */
/* «цифри можуть бути неточні» — спокійна жовта плашка над карткою */
.alert { display: flex; align-items: flex-start; gap: 8px; margin-top: 12px; padding: 12px 8px 12px 16px; border-radius: 18px; background: #FFF6DB; color: #6B4E00; font-size: 14px; line-height: 1.45; }
.alert > span { flex: 1; padding-top: 2px; }
.alert a { color: inherit; font-weight: 500; text-decoration: underline; text-underline-offset: 2px; }
.alert button { flex: none; width: 32px; height: 32px; border: 0; border-radius: 16px; background: transparent; color: inherit; display: grid; place-items: center; cursor: pointer; }

/* сегменти */
.seg { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 4px; margin-top: 12px; padding: 4px; border-radius: 24px; background: var(--seg); }
.seg button { min-height: 44px; border: 0; border-radius: 20px; background: transparent; color: var(--ink3); font-size: 14px; cursor: pointer; }
.seg button[aria-pressed="true"] { background: var(--card); color: var(--ink); font-weight: 500; box-shadow: 0 1px 3px rgba(17,19,24,.08); }
.seg.sm { margin: 0; padding: 3px; gap: 2px; border-radius: 14px; background: var(--soft); }
.seg.sm button { min-height: 36px; padding: 0 10px; border-radius: 11px; font-size: 12px; white-space: nowrap; }

/* капсульні графіки: сіра доріжка, синя заливка, неповний місяць — штрих */
/* Аналітика: шапка графіка (місяць + «Місяці / Тижні»), підсумок обраного місяця над стовпчиками */
.ach { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 10px 8px; min-height: 36px; }  /* на вузькому екрані перемикач переходить під назву */
.anav { display: flex; align-items: center; gap: 6px; min-width: 0; }
.anav h2 { margin: 0; white-space: nowrap; }
.aarr { flex: none; width: 32px; height: 32px; border: 0; border-radius: 16px; background: var(--soft); color: var(--ink); font: 500 18px/1 var(--font); cursor: pointer; }
.aarr:disabled { opacity: .35; cursor: default; }
/* Аналітика: кнопка місяця з календарем — як колишня в шапці, лише без рамки */
#an-chart { position: relative; z-index: 5; }
#an-chart .ach { position: relative; z-index: 10; }
#a-title { margin: 0; font-size: 16px; font-weight: 500; white-space: nowrap; }
.anav .aarr { width: 28px; height: 28px; background: transparent; color: var(--ink2); font-size: 18px; }
.alab .sep { margin: 0 8px; }
.abig { display: flex; align-items: center; gap: 10px; }
.abig .bd { font-size: 12px; line-height: 1; letter-spacing: 0; }  /* без успадкованої висоти рядка — текст рівно посередині плашки */
#a-sum .aline { margin-top: 4px; font-size: 14px; color: var(--ink2); }
#a-sum .anote { margin-top: 2px; font-size: 12px; color: var(--ink2); }  /* звідки ліміт */
/* графік без осі й доріжок: лише стовпчики, назви місяців і пунктир середнього з підписом */
.caps.plain .trk { background: none; overflow: visible; }
.avgl .avgt { position: absolute; right: 0; bottom: 5px; padding: 1px 7px; border-radius: 999px; background: var(--card); box-shadow: inset 0 0 0 1px var(--mute); font-size: 11px; line-height: 1.4; color: var(--ink3); white-space: nowrap; }  /* «сер. …» — біла плашка з сірою обводкою, щоб не зливалась із доріжками */
.bnote { display: flex; align-items: center; gap: 8px; margin: 14px 0 0; font-size: 12px; color: var(--ink2); }
.bnote .mk { flex: none; width: 14px; height: 0; border-top: 2px solid var(--ink); border-radius: 1px; }
.mmenu { position: fixed; right: auto; z-index: 45; }
.mmenu:not([hidden]) { transform-origin: top left; }
#a-sum { margin-top: 16px; }
.alab { display: flex; align-items: center; gap: 8px; min-height: 24px; font-size: 13px; color: var(--ink2); }
/* перемикач «Ліміт» — квадратик-чекбокс праворуч у рядку підпису */
.limt { margin: -6px -4px -6px auto; display: inline-flex; align-items: center; gap: 6px; min-height: 36px; padding: 0 4px; border: 0; background: transparent; font: 400 13px var(--font); color: var(--ink3); cursor: pointer; }
/* «Ліміт» — тугл, як у налаштуваннях телефона: підпис + повзунок; тап по всьому рядку */
.ach .limt { margin: 0 -4px 0 0; min-height: 44px; gap: 10px; font-size: 14px; color: var(--ink3); transition: color .25s; }
.ach .limt[aria-checked="true"] { color: var(--ink); }
/* доріжка: сіра → синій градієнт; кулька пружно їде й трохи витягується, поки палець на перемикачі */
.limt .sw { position: relative; flex: none; width: 42px; height: 26px; border-radius: 13px; background: #DDE1E7; box-shadow: inset 0 0 0 1px rgba(17,19,24,.04); transition: background .3s ease; }
.limt .sw i { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 10px; background: #fff;
  box-shadow: 0 1px 2px rgba(17,19,24,.12), 0 3px 8px rgba(17,19,24,.12); transition: transform .38s cubic-bezier(.34, 1.4, .64, 1), width .2s ease; }
.limt .sw { transition: background .3s ease, box-shadow .3s ease; }
/* увімкнений — як наша primary-кнопка «Додати готівку»: синій градієнт, синє світіння знизу і світла кромка зверху */
.limt[aria-checked="true"] .sw { background: var(--blue-g); box-shadow: 0 4px 10px rgba(29,95,224,.32), inset 0 1px 0 rgba(255,255,255,.3); }
.limt[aria-checked="true"] .sw i { transform: translateX(16px); }
.limt:active .sw i { width: 24px; }
.limt[aria-checked="true"]:active .sw i { transform: translateX(12px); }
.limt:focus-visible { outline: none; } .limt:focus-visible .sw { box-shadow: 0 0 0 3px var(--blue-soft); }
#a-scale button { min-width: 88px; }  /* «Місяці / Тижні» ширше — місце в шапці звільнилось */
.wsort { display: inline-flex; align-items: center; gap: 4px; min-height: 28px; margin-top: -8px; padding: 0; border: 0; background: transparent; font: 500 13px var(--font); color: var(--blue); cursor: pointer; }  /* висота під тег «дані до …» — картка не стрибає між місяцями */
.hero .tagx { gap: 4px; height: 28px; padding: 0 8px 0 10px; border: 0; font-family: var(--font); cursor: pointer; }  /* «на 7 жовтня ✕» — стан на обраний день, ✕ повертає до сьогодні */
.alab .tag { flex: none; padding: 3px 10px; border-radius: 12px; background: var(--soft); font-size: 12px; font-weight: 500; color: var(--ink2); }
.abig { margin-top: 2px; font-size: 28px; font-weight: 500; letter-spacing: -.01em; white-space: nowrap; }
.aline { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; margin-top: 6px; font-size: 14px; color: var(--ink2); }
.legs .avg-leg + .avg-leg { margin-top: 12px; }
.legs .apace { flex-direction: column; align-items: stretch; gap: 4px; margin-top: 14px; }
.legs .apace .aline { justify-content: space-between; margin: 0; font-size: 13px; color: var(--ink4); }
.legs .apace .aline.sub { color: var(--ink2); }
/* «Куди пішли гроші»: під назвою категорії — дрібно «зазвичай …», як у «Що змінилось» */
.wrow { color: inherit; }
.wrow .nm { flex-direction: column; align-items: flex-start; gap: 2px; }
.wrow .nm small { display: flex; align-items: center; flex-wrap: wrap; gap: 2px 6px; font-size: 13px; color: var(--ink2); }
.wrow .nm small .bd { padding: 0 6px; min-height: 20px; font-size: 11px; }
.wrow .ir { display: inline-flex; align-items: center; gap: 4px; color: var(--ink2); }
#an-where .dh { align-items: baseline; margin: 18px 0 2px; padding: 0 0 10px; border-bottom: 1px solid var(--soft); font-size: 15px; }  /* заголовок розділу: наш шрифт списків, 500, роздільник під ним */
#an-where .dh b { font-weight: 500; color: var(--ink); }
#an-where .dh > span { font-size: 14px; color: var(--ink2); }
/* підсумок місяця під категоріями: «Лишилось з доходу» — як заголовок розділу, але з роздільником зверху */
#an-where .lrow { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; margin: 14px 0 6px; padding-top: 14px; border-top: 1px solid var(--soft); }
#an-where .lrow b { display: block; font-size: 15px; font-weight: 500; }
#an-where .lrow small { display: block; margin-top: 2px; font-size: 12px; color: var(--ink2); }
#an-where .lrow > span { font-size: 15px; font-weight: 500; white-space: nowrap; }  /* сума — сіра, як підсумок дня в Журналі */
.chart { display: flex; gap: 8px; margin-top: 24px; }
#an-chart .chart { touch-action: pan-y; user-select: none; }
#an-chart .chart.in-r { animation: chin-r .3s cubic-bezier(.32, .72, 0, 1); }
#an-chart .chart.in-l { animation: chin-l .3s cubic-bezier(.32, .72, 0, 1); }
#an-chart .chart.bump { animation: chbump .3s ease; }
@keyframes chin-r { from { opacity: 0; transform: translateX(24px); } }
@keyframes chin-l { from { opacity: 0; transform: translateX(-24px); } }
@keyframes chbump { 30% { transform: translateX(-6px); } 60% { transform: translateX(6px); } }
.ticks { flex: none; display: flex; flex-direction: column; justify-content: space-between; height: 150px; width: 66px; font-size: 12px; color: var(--ink2); text-align: right; }
.ticks span { line-height: 1; white-space: nowrap; }
/* «Динаміка»: числа осі притиснуті праворуч, з відступом до пунктиру середнього */
#mticks, #wticks { width: 46px; margin-right: 8px; text-align: right; }  /* обидва графіки — однакова вісь */
.caps { position: relative; flex: 1; display: flex; justify-content: space-around; }
/* місяці сторінками по 5: вікно обрізає стрічку, стрічка їде за пальцем */
.caps.paged { display: block; min-width: 0; }
.cview { overflow: hidden; }
.ctrk { display: flex; will-change: transform; }
.ctrk .cap { flex: 0 0 calc(100% / var(--per)); min-width: 0; }
.ctrk .cap .r1, .ctrk .cap .r2 { white-space: nowrap; }
.ticks:has(+ .caps.paged) { width: 38px; }  /* вужча шкала — 5 стовпчиків вміщаються з відступами */
.ctrk .cap .r2 { font-size: 11px; }
/* середнє: більші заокруглені штрихи (SVG-візерунок), окремим шаром поверх стовпчиків */
.avgl { position: absolute; left: 0; right: 0; height: 3px; margin-top: -1.5px; pointer-events: none; z-index: 3; transform: translateZ(0);
  background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='3'%3E%3Cline x1='1.5' y1='1.5' x2='7.5' y2='1.5' stroke='%23C3C9D3' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E") repeat-x left center / 12px 3px;
  clip-path: inset(-24px 100% -4px 0); opacity: 0; transition: clip-path 2.2s cubic-bezier(.45, 0, .2, 1) .3s, opacity 1.4s ease .3s; }
/* поява разом зі стовпчиками: лінія «прокреслюється» зліва направо */
.avgl.in { clip-path: inset(-24px -4px -4px -4px); opacity: 1; }  /* запас угору — для підпису «сер. …» */
.avgl.liml { height: 2px; margin-top: -1px; background: var(--blue); border-radius: 1px; }  /* ліміт — суцільна синя */
.liml .avgt { left: 0; right: auto; color: var(--blue-txt); box-shadow: inset 0 0 0 1px var(--blue-l); }
.avg-leg { display: flex; align-items: center; gap: 10px; margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--soft); font-size: 14px; color: var(--ink2); }
.avg-leg i { flex: none; width: 20px; height: 3px; background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='3'%3E%3Cline x1='1.5' y1='1.5' x2='7.5' y2='1.5' stroke='%23C3C9D3' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E") repeat-x left center / 12px 3px; }
.avg-leg b { margin-left: auto; font-size: 15px; font-weight: 500; color: var(--ink); }
.avg-leg:empty { display: none; }
.cap { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.cap.pick { cursor: pointer; }
.cap .trk { width: 40px; height: 150px; border-radius: 10px; background: var(--track); display: flex; flex-direction: column; justify-content: flex-end; overflow: hidden; }
.cap .trk.emp { background: none; justify-content: flex-end; align-items: center; }
.cap .trk.emp svg { width: 40px; height: 28px; display: block; }
.cap .fill { display: flex; flex-direction: column; height: 0; border-radius: 10px; overflow: hidden; transition: height 2.1s var(--ease); }
.cap .fill .rent { background: repeating-linear-gradient(135deg, #A9C6F8 0 5px, #D7E4FC 5px 10px); }
.cap .fill .main { flex: 1; background: var(--blue-g); }
.cap.dim .fill .main { background: #D5DAE2; }
.cap.part .fill .main { background: repeating-linear-gradient(135deg, #7FA9F1 0 5px, #C9DAFA 5px 10px); }
.cap.part.dim .fill .main { background: repeating-linear-gradient(135deg, #CBD1DA 0 5px, #E6E9EE 5px 10px); }
.cap .r1 { font-size: 12px; color: var(--ink2); }
.cap .r2 { font-size: 12px; color: var(--ink2); }
.cap.on .r1 { color: var(--ink); font-weight: 500; }
.cap.on .r2 { color: var(--ink3); font-weight: 500; }
/* обовʼязкові (фіксовані) й щоденні (змінні) */
.split { display: flex; gap: 3px; height: 10px; margin: 4px 0 14px; }
.split i { display: block; height: 100%; border-radius: 5px; }
.fv2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.fv2 > div { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.fv2 span { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink2); }
.fv2 span i { width: 8px; height: 8px; border-radius: 4px; }
.fv2 b { font-size: 18px; font-weight: 500; }
/* «Динаміка» */
.duo { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
.duo .card { margin: 0; padding: 14px; }
.duo span { display: block; font-size: 13px; color: var(--ink2); }
.duo b { display: block; margin: 4px 0 6px; font-size: 20px; font-weight: 500; }
.duo small { display: block; margin-top: 6px; font-size: 13px; color: var(--ink2); }
.duo .duo-cmp { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 6px; }
.duo .duo-cmp small { margin-top: 0; }
.duo .duo-cmp .bd { display: inline-flex; font-size: 12px; }
#t-duo { display: block; margin-top: 16px; }  /* підсумок місяця — заголовок над стовпчиками всередині картки графіка */
.vs > span { display: block; font-size: 13px; color: var(--ink2); }
.vs .vs-big { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 10px; margin: 6px 0 2px; }
.vs .vs-big b { margin: 0; font-size: 28px; font-weight: 500; letter-spacing: -.01em; }
.vs .vs-big .bd { display: inline-flex; font-size: 12px; color: inherit; }
.vs .vs-big .bd.bad { color: var(--bad); } .vs .vs-big .bd.good { color: var(--good); }
.vs b.vs-one { margin: 6px 0 2px; font-size: 17px; }
.vs-avg { display: flex; justify-content: space-between; align-items: baseline; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--soft); }
.vs-avg span { display: inline; font-size: 14px; color: var(--ink2); }
.vs-avg b { display: inline; margin: 0; font-size: 15px; font-weight: 500; }
.vs .vs-sub { display: block; margin-top: 2px; font-size: 13px; color: var(--ink2); }
.vs2 { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; }
.vs2 .vs-l { min-width: 0; }
.vs2 .vs-big { margin-bottom: 0; }
.vs2 .vs-r { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 2px; padding-bottom: 4px; text-align: right; }
.vs2 .vs-r .bd { display: inline-flex; font-size: 13px; white-space: nowrap; color: var(--ink2); }
.vs2 .vs-r .bd.bad { color: var(--bad); } .vs2 .vs-r .bd.good { color: var(--good); }
.vs2 .vs-r small { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 13px; color: var(--ink2); line-height: 1.3; }
.vs2 .vs-r .dash { display: inline-block; width: 12px; height: 0; border-top: 2px dashed var(--blue); }
.vs-head { display: flex; justify-content: flex-start; align-items: center; gap: 8px; min-height: 24px; }
.vs-head span { font-size: 13px; color: var(--ink2); }
.vs-tag { flex: none; padding: 3px 10px; border-radius: 12px; background: var(--soft); font-style: normal; font-size: 12px; font-weight: 500; color: var(--ink2); white-space: nowrap; }
.vs-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--soft); }
.vs-row > span:first-child { display: inline; font-size: 14px; color: var(--ink2); line-height: 1.35; }
.vs-row .nw { display: inline; margin: 0; font-size: inherit; font-weight: 400; white-space: nowrap; }
.vs-row .bd { flex: none; display: inline-flex; font-size: 12px; white-space: nowrap; }
.vs-row .bd.bad { color: var(--bad); } .vs-row .bd.good { color: var(--good); }
.vs-row .dash { display: inline-block; width: 14px; height: 0; margin: 0 8px 3px 0; border-top: 2px dashed var(--blue); vertical-align: middle; }
.vs .vs-avg { margin-top: 0; border-top: 0; padding-top: 10px; }
.cap .trk { position: relative; }
.cap .mark { position: absolute; left: 6px; right: 6px; height: 0; border-top: 2px dashed var(--blue); }
.cap .r3 { display: block; margin-top: 2px; font-size: 11px; color: var(--ink2); }
.vs-parts { margin-top: 12px; }
.vs-parts:empty { display: none; }
/* пояснення під назвою — тим самим шрифтом, що й .note */
.ch .wsub { display: flex; align-items: center; gap: 4px; margin-top: 2px; font-size: 13px; line-height: 1.45; }
.wsub svg { flex: none; width: 15px; height: 15px; }
.wleg { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 12px; margin-top: 24px; }
.wleg .vs-part { padding: 0; background: none; margin: 0; gap: 8px; }
.wleg .vs-part + .vs-part { margin: 0; text-align: right; }
.wleg .vs-part i { width: 14px; height: 14px; border-radius: 4px; }
.wleg .vs-part span { font-size: 12px; color: var(--ink2); white-space: nowrap; }
.vs-part { display: flex; gap: 10px; align-items: center; padding: 10px 12px; border-radius: 14px; background: var(--soft); }
.vs-part + .vs-part { margin-top: 8px; }
.vs-part i { flex: none; width: 22px; height: 16px; border-radius: 4px; background: repeating-linear-gradient(135deg, #CBD1DA 0 4px, #E6E9EE 4px 8px); }
.vs-part.on i { background: repeating-linear-gradient(135deg, #7FA9F1 0 4px, #C9DAFA 4px 8px); }
.vs-part span { display: inline; font-size: 13px; line-height: 1.4; color: var(--ink); }
.share { display: flex; gap: 3px; height: 10px; margin: 6px 0 12px; }
.share i { display: block; height: 100%; border-radius: 5px; min-width: 6px; }
.leg { display: grid; grid-template-columns: 12px 1fr auto auto; gap: 10px; align-items: center; min-height: 36px; font-size: 14px; }
.leg i { width: 8px; height: 8px; border-radius: 4px; }
.cmp { display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 12px 0; border-top: 1px solid var(--soft); font-size: 15px; }
.cmp .n { flex: 1; min-width: 0; }
.cmp .v { color: var(--ink2); }
.cmp .bd { min-width: 46px; justify-content: center; padding: 0 6px; letter-spacing: -.02em; word-spacing: -.12em; }
.fava { display: inline-flex; align-items: center; gap: 2px; min-height: 44px; margin: 10px 4px 0; font-size: 14px; font-weight: 500; color: var(--blue); }

/* журнал */
.filters { display: flex; gap: 8px; margin: 9px -4px 0; padding: 3px 4px; overflow-x: auto; scrollbar-width: none; }  /* запас 3–4px — рамка фокуса не обрізається */
.filters::-webkit-scrollbar { display: none; }
.filters button { flex: none; min-height: 44px; padding: 0 16px; border: 1px solid var(--border); border-radius: 22px; background: var(--card); font-size: 14px; cursor: pointer; }
.filters button[aria-pressed="true"] { border-color: transparent; background: var(--blue-g); color: #fff; font-weight: 500; }
/* «на 7 жовтня ✕» — обраний у календарі день (Аналітика, Журнал); ✕ повертає до місяця */
.daychip { display: inline-flex; align-items: center; gap: 4px; height: 24px; padding: 0 8px 0 10px; border: 0; border-radius: 12px; background: var(--blue-soft); color: var(--blue); font: 500 12px var(--font); white-space: nowrap; cursor: pointer; }
.filters button.daychip { height: auto; min-height: 44px; padding: 0 12px 0 16px; gap: 6px; border-color: transparent; border-radius: 22px; font-size: 14px; }
.filters button.catf { display: inline-flex; align-items: center; gap: 6px; padding-right: 12px; }
/* та сама кнопка місяця, що в Аналітиці: іконка календаря, місяць і рік, шеврон; 14px, вага 400 */
.filters .mchip { display: inline-flex; align-items: center; gap: 6px; padding: 0 12px 0 16px; color: var(--ink); font-weight: 400; }
.filters .mchip .chev { color: var(--ink2); }
.filters .mchip[aria-expanded="true"] .chev { transform: rotate(180deg); }
.filters .mchip:disabled { opacity: .5; cursor: default; }


/* Журнал: пошук по всіх місяцях + «Готівка», під фільтрами — підсумок одним рядком */
.jtop { display: flex; gap: 8px; margin-top: 12px; }
.jsearch { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; height: 48px; padding: 0 14px; border: 1px solid var(--border); border-radius: 24px; background: var(--card); color: var(--ink2); }
.jsearch:focus-within { border-color: var(--blue); box-shadow: 0 0 0 4px var(--blue-soft); }
.jsearch svg { flex: none; width: 18px; height: 18px; }
.jsearch input { flex: 1; min-width: 0; height: 100%; border: 0; outline: 0; background: none; font: 16px var(--font); color: var(--ink); }
.jsearch input::placeholder { color: var(--ink2); }
.jsearch input::-webkit-search-cancel-button { -webkit-appearance: none; appearance: none; }  /* системний хрестик ховаємо — є наш */
.jclear { flex: none; width: 32px; height: 32px; margin-right: -8px; border: 0; border-radius: 16px; background: transparent; color: var(--ink2); display: grid; place-items: center; cursor: pointer; }
.jclear:hover { background: var(--soft); }
.jsug { position: fixed; z-index: 45; }
.jsug .opt { min-height: 52px; gap: 10px; }
.jsug .opt .cic { width: 32px; height: 32px; }
.jcat { width: 300px; max-height: min(60vh, 440px); overflow-y: auto; overscroll-behavior: contain; }
/* повзунок прокрутки в меню: тонка сіра «пігулка» з відступом від країв, без стрілок */
.jcat::-webkit-scrollbar { width: 12px; }
.jcat::-webkit-scrollbar-track { margin: 18px 0; background: transparent; }
.jcat::-webkit-scrollbar-thumb { border: 4px solid transparent; border-radius: 6px; background: #D9DDE3 padding-box; }
.jcat::-webkit-scrollbar-thumb:hover { background-color: #C4C8D0; }
.jcat::-webkit-scrollbar-button { display: none; height: 0; }
@supports not selector(::-webkit-scrollbar) { .jcat { scrollbar-width: thin; scrollbar-color: #D9DDE3 transparent; } }
.jsug .opt .qi { flex: none; width: 32px; height: 32px; display: grid; place-items: center; border-radius: 50%; background: var(--soft); color: var(--ink2); }
.filters .catchip[aria-pressed="true"] { background: var(--blue); color: #fff; }
.filters .catchip[aria-pressed="true"] .chev { color: #fff; }
.jcash { flex: none; display: inline-flex; align-items: center; gap: 6px; height: 48px; padding: 0 16px; border-radius: 24px; background: var(--blue-g); color: #fff; font-size: 15px; font-weight: 500; box-shadow: 0 6px 14px rgba(29,95,224,.24); }
.jcash svg { width: 16px; height: 16px; }
.jsum { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
.jsum .card { margin: 0; padding: 14px 16px; display: flex; align-items: center; gap: 12px; }
.jsum .ji { flex: none; width: 36px; height: 36px; border-radius: 18px; display: grid; place-items: center; }
.jsum .ji svg { width: 18px; height: 18px; }
.jsum .ji .glyph { display: inline; font-size: 20px; color: inherit; }
.jsum span { display: block; font-size: 12px; color: var(--ink2); }
.jsum b { display: block; margin-top: 2px; font-size: 17px; font-weight: 500; white-space: nowrap; }
.jsum .card > div { min-width: 0; }
.dh { display: flex; justify-content: space-between; gap: 10px; margin: 16px 4px 6px; font-size: 14px; }
.dh b { font-weight: 500; }
.dh b span { font-weight: 400; color: var(--ink2); }
.dh > span { color: var(--ink2); white-space: nowrap; }
.day { scroll-margin-top: 12px; }
.day .card { margin-top: 0; }
.day .card .op:first-child, .day .card .swipe:first-child > .op { border-top: 0; }
.empty { padding: 26px 10px; text-align: center; color: var(--ink2); }

/* шторка «Витрата готівкою» */
.sheet-bg { position: fixed; inset: 0; z-index: 40; background: rgba(17,19,24,.38); opacity: 0; transition: opacity .6s cubic-bezier(.32, .72, 0, 1); }
.sheet { position: fixed; left: 0; right: 0; bottom: 0; z-index: 41; max-width: 440px; max-height: 92vh; margin: 0 auto; display: flex; flex-direction: column;
  background: var(--card); border-radius: 28px 28px 0 0; transform: translateY(100%); transition: transform .7s cubic-bezier(.32, .72, 0, 1); will-change: transform; }
.sheet.open { transform: none; }
.sheet-bg.open { opacity: 1; }
.sheet-head { display: grid; grid-template-columns: 1fr 44px; align-items: center; padding: 8px 16px 0; touch-action: none; }
.sheet-head::before { content: ""; grid-column: 1 / -1; justify-self: center; width: 36px; height: 4px; margin-bottom: 10px; border-radius: 2px; background: #D9DDE3; }
.sheet-head h2 { margin: 0; font-size: 18px; font-weight: 500; }
.sheet-head button { width: 44px; height: 44px; border: 0; border-radius: 22px; background: var(--soft); display: grid; place-items: center; cursor: pointer; }
.sheet-head button svg { width: 16px; height: 16px; }
.sheet-scroll { overflow-y: auto; padding: 0 16px 12px; }
.amount { display: flex; justify-content: center; align-items: baseline; gap: 6px; padding: 22px 0 0; cursor: text; }
.amount input { width: 30px; min-width: 0; max-width: 100%; padding: 0; margin: 0; border: 0; border-radius: 0; outline: 0; background: transparent; text-align: right; -webkit-appearance: none; appearance: none;
  font: 500 48px/1.1 var(--font); letter-spacing: -0.02em; color: var(--ink); caret-color: var(--blue); }
.amount input::placeholder { color: var(--mute); opacity: 1; }
.amount input::-webkit-input-placeholder { color: var(--mute); opacity: 1; }
.amount span { font-size: 22px; color: var(--ink2); }
.amount-mirror { position: absolute; left: -9999px; top: 0; visibility: hidden; white-space: pre; font: 500 48px/1.1 var(--font); letter-spacing: -0.02em; }
.amount-sub { margin: 4px 0 14px; text-align: center; font-size: 12px; color: var(--ink2); }
.lab { margin: 14px 2px 8px; font-size: 13px; font-weight: 500; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chips button { min-height: 44px; padding: 0 14px; border: 1px solid var(--border); border-radius: 22px; background: var(--card); font-size: 14px; cursor: pointer; }
.chips button[aria-pressed="true"] { border-color: transparent; background: var(--blue-g); color: #fff; font-weight: 500; }
.field { width: 100%; height: 52px; margin-top: 16px; padding: 0 16px; border: 1px solid #DDE1E8; border-radius: 16px; background: var(--card); font: 16px var(--font); color: var(--ink);
  -webkit-appearance: none; appearance: none; transition: border-color .3s var(--ease), box-shadow .3s var(--ease); }
.field::placeholder { color: #8A90A0; opacity: 1; }
.field:focus { outline: 0; border-color: var(--blue); box-shadow: 0 0 0 4px var(--blue-soft); }
.dates { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.dates button { min-height: 52px; border: 0; border-radius: 14px; background: var(--soft); font-size: 14px; font-weight: 500; line-height: 1.2; cursor: pointer; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.dates button small { font-size: 12px; font-weight: 400; color: var(--ink2); }
.dates button.other { flex-direction: row; gap: 6px; }
.dates button.other svg { color: var(--ink2); }
.dates button.other[aria-pressed="true"] svg { color: #fff; }
.dates button[aria-pressed="true"] { background: var(--blue-g); color: #fff; }
.dates button[aria-pressed="true"] small { color: rgba(255,255,255,.8); }
.vh { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.info { margin-top: 14px; padding: 12px 14px; border-radius: 14px; background: #FFF7E8; color: var(--cash); font-size: 12px; line-height: 1.5; }
.sheet-foot { padding: 10px 16px calc(14px + env(safe-area-inset-bottom, 0px)); }
/* картка операції */
#v-op .sheet-head h2 { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.op[data-op] { cursor: pointer; }
.opbig { margin: 10px 0 14px; font-size: 28px; font-weight: 500; letter-spacing: -.01em; }
.opbig.inc { color: var(--good); }
.oplist .oprow { display: flex; justify-content: space-between; align-items: center; gap: 12px; min-height: 52px; border-top: 1px solid var(--soft); font-size: 14px; }
.oplist .oprow span { color: var(--ink2); }
.oplist .oprow b { font-weight: 400; text-align: right; }  /* значення — звичайною вагою, як у наших списках */
.opcat { display: inline-flex; align-items: center; gap: 8px; color: var(--ink) !important; }
.opcat .cic { width: 28px; height: 28px; }
.opbtns { display: flex; gap: 10px; }
/* дві кнопки без рамки, як свайп «Видалити»: «Змінити» — голуба, «Видалити» — червона */
.obtn { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 52px; padding: 0 16px; border: 0; border-radius: 26px; background: var(--blue-soft); color: var(--blue); font: 500 15px var(--font); cursor: pointer; }
.obtn svg { width: 18px; height: 18px; flex: none; }
.obtn.del { background: var(--bad-bg); color: var(--bad); }
#op-foot:empty { display: none; }
#op-foot { padding-bottom: calc(28px + env(safe-area-inset-bottom, 0px)); }
#v-op:has(#op-foot:empty) #op-body { padding-bottom: calc(28px + env(safe-area-inset-bottom, 0px)); }  /* операція з виписки без кнопок — той самий нижній відступ */
.msg { margin: 0 4px 8px; font-size: 14px; text-align: center; color: var(--bad); }
.msg:empty { display: none; }
.save { width: 100%; height: 52px; border: 0; border-radius: 26px; background: var(--blue-g); box-shadow: 0 8px 18px rgba(29,95,224,.3); color: #fff; font: 500 16px var(--font); cursor: pointer; }
.save:disabled { background: #D9DDE3; box-shadow: none; cursor: default; }
.toast { position: fixed; left: 50%; bottom: calc(104px + env(safe-area-inset-bottom, 0px)); z-index: 30; transform: translateX(-50%); display: flex; align-items: center; gap: 9px;
  padding: 8px 8px 8px 14px; min-height: 52px; border-radius: 18px; background: rgba(255, 255, 255, .72); -webkit-backdrop-filter: blur(18px) saturate(1.6); backdrop-filter: blur(18px) saturate(1.6);
  border: 1px solid #fff; box-shadow: 0 10px 30px rgba(17,19,24,.10); color: var(--ink); font-size: 14px; font-weight: 500; white-space: nowrap; }
.toast:not(:has(button)) { padding-right: 18px; }
.toast:has(.tbar) { overflow: hidden; }
.toast .tbar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; pointer-events: none; }
.toast .tbar b { display: block; height: 100%; background: var(--blue); opacity: .45; transform-origin: left; animation: tbar 6s linear forwards; }
.toast.hold .tbar b { animation-play-state: paused; }
@keyframes tbar { from { transform: scaleX(1); } to { transform: scaleX(0); } }
.toast i { flex: none; width: 19px; height: 19px; border-radius: 50%; display: grid; place-items: center; background: var(--good); font-style: normal; }
.toast i svg { display: block; }
.toast .tx { display: flex; flex-direction: column; line-height: 1.3; }
.toast .tx b { font-weight: 500; }
.toast .tx small { font-size: 12px; font-weight: 400; color: var(--ink2); }

/* нижня навігація v2: біла плаваюча панель, активний пункт — синя капсула; fade під нею */
.fade { z-index: 19; position: fixed; left: 0; right: 0; bottom: 0; height: 130px; pointer-events: none; background: linear-gradient(rgba(245,246,248,0), var(--bg) 70%); }
.nav { z-index: 20; position: fixed; left: 50%; bottom: calc(12px + env(safe-area-inset-bottom, 0px)); transform: translateX(-50%); width: min(342px, calc(100vw - 48px)); height: 68px;
  display: flex; align-items: center; padding: 0 8px; border: 1px solid var(--border); border-radius: 32px; background: var(--card); box-shadow: 0 10px 30px rgba(17,19,24,.10); }
.nav a { position: relative; flex: 1; height: 54px; border-radius: 27px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; font-size: 13px; color: var(--ink3); transition: color .35s var(--ease); }
.nav a.on { color: var(--blue); font-weight: 500; }
.nav a svg { width: 22px; height: 22px; transition: transform .45s var(--spring); }
.nav a.on svg { transform: scale(1.06); }
/* плашка активної вкладки — одна на всіх, переїжджає з пружинкою */
.nav .ind { position: absolute; left: 8px; top: 6px; width: calc((100% - 16px) / 3); height: 54px; border-radius: 27px; background: var(--blue-soft);
  transform: translateX(calc(var(--n, 0) * 100%)); transition: transform .7s var(--spring); }
.nav { padding: 0 8px; }
/* ---------- рух ---------- */
/* екран завантаження: знак із градієнтом, назва, три крапки; зникає, щойно готові шрифт і дані */
.splash { position: fixed; inset: 0; z-index: 100; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; background: var(--bg);
  transition: opacity 1.2s cubic-bezier(.4, 0, .2, 1), visibility 0s linear 1.2s; }
.splash.out { opacity: 0; visibility: hidden; }
.app.pre > * { opacity: 0; animation-play-state: paused !important; }
.app.pre *, .app.pre *::before { transition: none !important; }
.splash.out .sp-mark, .splash.out .sp-mark g, .splash.out .sp-mark::before { animation-play-state: paused; }
.sp-mark { position: relative; width: 84px; height: 84px; border-radius: 20px; display: grid; place-items: center; overflow: hidden; isolation: isolate;
  background: var(--blue-g); box-shadow: 0 10px 24px rgba(29,95,224,.34), inset 0 1px 0 rgba(255,255,255,.3);
  animation: breathe 2.4s ease-in-out infinite; transition: transform 1.2s cubic-bezier(.4, 0, .2, 1); }
/* відблиск, що проходить по кнопці */
.sp-mark::before { content: ""; position: absolute; inset: 0; z-index: -1; background: linear-gradient(110deg, transparent 35%, rgba(255,255,255,.28) 50%, transparent 65%);
  transform: translateX(-100%); animation: shine 1.8s ease-in-out infinite; }
.sp-mark svg { width: 40px; height: 40px; overflow: visible; }
.sp-mark g { transform-box: view-box; }
.sp-back { transform-origin: 13px 9px; animation: spBack 2.4s var(--ease) infinite; }
.sp-note { transform-origin: 10px 13px; animation: spNote 2.4s var(--ease) infinite; }
.sp-coin { transform-origin: 18.6px 17.6px; animation: spCoin 2.4s var(--ease) infinite; }
@keyframes spBack { 0%, 30%, 100% { transform: none; } 45% { transform: translate(.4px, -.8px) rotate(2deg); } 60% { transform: none; } }
@keyframes spNote { 0%, 100% { transform: none; } 14% { transform: translateY(-1.2px) scale(1.03); } 28% { transform: none; } 52% { transform: translateY(-.6px) rotate(-1.5deg); } 66% { transform: none; } }
@keyframes spCoin { 0%, 18%, 100% { transform: none; } 30% { transform: translateY(-2.6px); } 40% { transform: none; } 48% { transform: translateY(-1px); } 56% { transform: none; } }
.sp-name { font-size: 16px; font-weight: 500; color: var(--ink); opacity: 0; animation: rise .5s .15s var(--ease) both; }
/* повзунок: швидко доходить до ~85 %, а коли все готово — до кінця */
.sp-bar { width: 148px; height: 6px; margin-top: 4px; border-radius: 3px; background: var(--track); overflow: hidden; }
.sp-bar b { display: block; height: 100%; width: 0; border-radius: 3px; background: var(--blue-g); transition: width 2.4s cubic-bezier(.35, .15, .65, .85); }
.splash.done .sp-bar b { transition: width .5s cubic-bezier(.55, 0, 1, .45); }
@keyframes shine { 0% { transform: translateX(-100%); } 60%, 100% { transform: translateX(100%); } }
@keyframes breathe { 50% { transform: scale(1.05); } }
/* поява екрана: картки виїжджають знизу одна за одною */
.stag > *:not([hidden]), .stag .stagk:not([hidden]) { animation: lift 1s var(--ease) both; animation-delay: calc(var(--i, 0) * 45ms); }
@keyframes lift { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
/* натискання: кнопка трохи «вдавлюється» */
button, .cashbtn, .ch a, .hero .le, .more, .op { transition: transform .45s var(--spring), background-color .4s var(--ease), box-shadow .4s var(--ease), color .4s var(--ease); }
button:active:not(:disabled), .cashbtn:active, .ch a:active, .more:active { transition-duration: .12s; }
button:active:not(:disabled), .cashbtn:active, .nav a:active, .ch a:active, .more:active { transform: scale(.96); }
.cashbtn:hover { box-shadow: 0 10px 22px rgba(29,95,224,.4), inset 0 1px 0 rgba(255,255,255,.3); }
/* спливаючі меню розкриваються від кнопки */
.pop:not([hidden]) { animation: popin .45s var(--ease) both; transform-origin: top left; }

@keyframes popin { from { opacity: 0; transform: translateY(-6px) scale(.96); } to { opacity: 1; transform: none; } }
/* тост виринає знизу */
.toast { animation: toastin .7s var(--spring) both; }
.toast.out { animation: toastout .45s ease forwards; }
@keyframes toastout { from { opacity: 1; transform: translateX(-50%); } to { opacity: 0; transform: translate(-50%, 8px); } }
@keyframes toastin { from { opacity: 0; transform: translate(-50%, 16px) scale(.96); } to { opacity: 1; transform: translateX(-50%); } }
@media (prefers-reduced-motion: reduce) {
  .splash, .sp-mark, .sp-mark::before, .sp-mark g, .sp-bar b, .sp-name, .stag > *, .more-rows, .more-rows .crow, .hero .num.cu, .pop:not([hidden]), .toast { animation: none !important; transition: none !important; }
  .nav .ind, .sw path { transition: none; animation: none !important; }
}
@media (prefers-reduced-motion: reduce) { .cap .fill, .crow .bar6 b, .segs i b, .hero .meter b, .hero .split2 b { transition: none !important; } .fadein, .liquid, .liquid span { animation: none !important; }
  .avgl { clip-path: none !important; transition: none !important; }
  .liquid .after { -webkit-mask: none; mask: none; } }
</style>
</head>
<body class="p-marko">
<div class="splash" id="splash" role="progressbar" aria-label="Завантаження">
  <div class="sp-mark"><svg viewBox="0 0 24 24" aria-hidden="true"><g class="sp-back"><rect x="4.2" y="3.4" width="17.5" height="11" rx="1.8" fill="#fff" opacity=".45" transform="rotate(-7 13 9)"/></g><g class="sp-note"><rect x="1.5" y="7.5" width="17.5" height="11" rx="1.8" fill="#fff"/><g fill="none" stroke="#2A6BE8" stroke-linecap="round"><rect x="3.2" y="9.2" width="14.1" height="7.6" rx=".9" stroke-width=".55" opacity=".55"/><circle cx="10.25" cy="13" r="2.3" stroke-width="1.1"/><path d="M10.25 11.9v2.2" stroke-width=".9"/></g><circle cx="5.5" cy="13" r=".8" fill="#2A6BE8"/><circle cx="15" cy="13" r=".8" fill="#2A6BE8"/></g><g class="sp-coin"><circle cx="18.6" cy="17.6" r="4.4" fill="#2A6BE8"/><circle cx="18.6" cy="17.6" r="3.6" fill="#fff"/><circle cx="18.6" cy="17.6" r="2.5" fill="none" stroke="#2A6BE8" stroke-width=".6" opacity=".6"/></g></svg></div>
  <div class="sp-name">Фінанси Олі й Марка</div>
  <div class="sp-bar" aria-hidden="true"><b id="sp-fill"></b></div>
</div>
</div>
<div class="app pre" id="app">
  <div class="top">
    <div class="who">
      <button type="button" id="who-btn" aria-haspopup="menu" aria-expanded="false" aria-controls="who-menu"><span class="av" id="who-av"></span><span id="who-name"></span><svg class="ico16 sw" style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="#6B7180" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path class="sw-a" d="M3 6.5h18M16.5 2 21 6.5 16.5 11"/><path class="sw-b" d="M21 17.5H3M7.5 13 3 17.5 7.5 22"/></svg></button>
      <div class="pop" id="who-menu" role="menu" aria-label="Чиї дані показати" hidden><p>Профіль</p><div id="who-opts"></div>
        <form class="logout" method="post" action="logout"><button type="submit"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10"/></svg>Вийти</button></form>
</div>
    </div>
    <div class="mpick" id="mpick">
      <button type="button" id="mcal" aria-haspopup="dialog" aria-expanded="false" aria-controls="mmenu"><svg class="ico16" viewBox="0 0 24 24" fill="none" stroke="#6B7180" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg><span id="mcal-name"></span><svg class="ico16 chev" viewBox="0 0 24 24" fill="none" stroke="#6B7180" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>
      <div class="pop mmenu" id="mmenu" role="dialog" aria-label="Вибір дня" hidden>
        <div class="cal-head"><button type="button" id="cal-prev" aria-label="Попередній місяць"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m14 6-6 6 6 6"/></svg></button>
          <b id="cal-year"></b><button type="button" id="cal-next" aria-label="Наступний місяць"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m10 6 6 6-6 6"/></svg></button></div>
        <div class="cal-wd" aria-hidden="true"><span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span>Сб</span><span>Нд</span></div>
        <div class="cal-grid" id="cal-grid"></div>
        <p class="cal-note" id="cal-note"></p>
        <button type="button" class="cal-all" id="cal-all" hidden>Шукати в усіх місяцях</button>
      </div>
    </div>
    <span class="mstatic" id="mrange" hidden></span>
  </div>

  <div id="v-report">
    <div id="h-alert"></div>
    <div class="hero-wrap"><div class="liquid" id="liquid" aria-hidden="true"><span class="after"><span class="amb a1"></span><span class="amb a2"></span><span class="amb a3"></span><span class="ehalo"><i></i></span><span class="erim"></span></span><span class="glow"></span><span class="halo"><i></i></span><span class="rim"></span></div><section class="hero" id="hero" aria-live="polite"></section></div>
    <a class="cashbtn" id="cash" href="#add"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/></svg>Додати готівку</a>
    <section class="card limc" id="limc" hidden></section>
    <section class="card list fadein" id="insights" hidden></section>
    <section class="card list"><div class="ch"><h2>Останні операції</h2><a href="#journal" id="ops-all">Журнал<span id="arr-j"></span></a></div><div id="recent"></div></section>
  </div>

  <div id="v-analytics" hidden>
    <!-- один графік: місяці або тижні обраного місяця; лише щоденні витрати, повна сума — рядком під цифрою -->
    <section class="card" id="an-chart">
      <div class="ach"><div class="anav" hidden><button type="button" class="aarr" id="a-prev" aria-label="Попередній місяць">‹</button><h2 id="a-title"></h2><button type="button" class="aarr" id="a-next" aria-label="Наступний місяць">›</button></div>        <div class="seg sm" id="a-scale" role="group" aria-label="Масштаб"></div></div>
      <div class="asum" id="a-sum"></div>
      <div class="chart"><div class="ticks" id="mticks" aria-hidden="true"></div><div class="caps" id="mbars"></div></div><div class="legs" id="t-avg"></div>
    </section>
    <section class="card list" id="an-where"></section>
  </div>

  <div id="v-journal" hidden>
    <div class="jtop">
      <label class="jsearch"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>
        <input id="j-q" type="search" placeholder="Назва, сума або категорія" autocomplete="off" enterkeyhint="search" aria-label="Пошук операцій у всіх місяцях">
        <button type="button" class="jclear" id="j-clear" aria-label="Очистити пошук" hidden><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17"/></svg></button></label>
      <a class="jcash" id="j-cash" href="#add"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>Готівка</a>
    </div>
    <div class="pop jsug" id="j-sug" role="listbox" aria-label="Підказки пошуку" hidden></div>
    <div class="pop jsug jcat" id="cat-menu" role="listbox" aria-label="Категорії" hidden></div>
    <div class="filters" id="j-filters" role="group" aria-label="Фільтр"></div>
    <div class="jsum" id="j-sum" hidden></div>
    <div id="j-list"></div>
    <a class="fava" id="fava">Повний журнал у Fava<span id="arr-f"></span></a>
  </div>

  <div class="sheet-bg" id="sheet-bg" hidden></div>
  <form class="sheet" id="v-add" hidden novalidate role="dialog" aria-modal="true" aria-labelledby="sheet-title">
    <div class="sheet-head" id="sheet-head"><h2 id="sheet-title">Витрата готівкою</h2>
      <button type="button" id="sheet-close" aria-label="Закрити"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
    <div class="sheet-scroll">
      <label class="amount"><input id="a-amount" inputmode="decimal" autocomplete="off" placeholder="0" aria-label="Сума у злотих"><span>зл</span></label>
      <div class="amount-sub">Сума</div>
      <div class="lab" id="cat-lab">Категорія</div>
      <div class="chips" id="a-cats" role="group" aria-labelledby="cat-lab"></div>
      <input class="field" id="a-title" maxlength="80" autocomplete="off" placeholder="Опис, за бажанням: ринок, овочі" aria-label="Опис, за бажанням">
      <div class="lab" id="date-lab">Дата</div>
      <div class="dates" id="a-dates" role="group" aria-labelledby="date-lab"></div>
      <div class="acal" id="a-cal" role="group" aria-label="Інша дата" hidden>
        <div class="cal-head"><button type="button" id="a-cal-prev" aria-label="Попередній місяць"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m14 6-6 6 6 6"/></svg></button>
          <b id="a-cal-title"></b><button type="button" id="a-cal-next" aria-label="Наступний місяць"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m10 6 6 6-6 6"/></svg></button></div>
        <div class="cal-wd" aria-hidden="true"><span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span>Сб</span><span>Нд</span></div>
        <div class="cal-grid" id="a-cal-grid"></div>
      </div>
      <input class="vh" id="a-date" type="date" tabindex="-1" aria-hidden="true">
      <div class="info">Лише готівка. Оплати карткою вносити не треба: вони прийдуть з банківською випискою.</div>
    </div>
    <div class="sheet-foot"><p class="msg" id="a-msg" role="status"></p><button class="save" id="a-save" type="submit" disabled>Зберегти</button></div>
  </form>
  <!-- картка операції: тап по рядку в Журналі чи «Останніх операціях» -->
  <!-- «Відкладати щомісяця»: з картки ліміту на Головній -->
  <div class="sheet-bg" id="pl-bg" hidden></div>
  <div class="sheet" id="v-plan" hidden role="dialog" aria-modal="true" aria-labelledby="pl-title">
    <div class="sheet-head" id="pl-head"><h2 id="pl-title">Відкладати щомісяця</h2>
      <button type="button" id="pl-close" aria-label="Закрити"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
    <div class="sheet-scroll" id="pl-body"></div>
    <div class="sheet-foot"><p class="msg" id="pl-msg" role="status"></p><button class="save" id="pl-save" type="button">Зберегти</button></div>
  </div>
  <div class="sheet-bg" id="op-bg" hidden></div>
  <div class="sheet" id="v-op" hidden role="dialog" aria-modal="true" aria-labelledby="op-title">
    <div class="sheet-head" id="op-head"><h2 id="op-title"></h2>
      <button type="button" id="op-close" aria-label="Закрити"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
    <div class="sheet-scroll" id="op-body"></div>
    <div class="sheet-foot" id="op-foot"></div>
  </div>
</div>
<div class="fade" aria-hidden="true"></div>
<nav class="nav" aria-label="Навігація">
  <i class="ind" id="nav-ind" aria-hidden="true"></i>
  <a id="navr" href="#report"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M11.3 2.8a1.1 1.1 0 0 1 1.4 0l8 6.6a1 1 0 0 1-.65 1.8H19V20a1 1 0 0 1-1 1h-4v-5h-4v5H6a1 1 0 0 1-1-1v-8.8H3.95a1 1 0 0 1-.65-1.8z"/></svg>Головна</a>
  <a id="navt" href="#analytics"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="3.5" y="12" width="4.2" height="8.5" rx="1.4"/><rect x="9.9" y="4" width="4.2" height="16.5" rx="1.4"/><rect x="16.3" y="8.5" width="4.2" height="12" rx="1.4"/></svg>Аналітика</a>
  <a id="navj" href="#journal"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M6.6 2.5H18a1.5 1.5 0 0 1 1.5 1.5v13.8a.9.9 0 0 1-.9.9H6.9a1.25 1.25 0 0 0 0 2.5h11.8a.8.8 0 0 1 0 1.6H6.9A2.85 2.85 0 0 1 4.05 19.95V5.05A2.55 2.55 0 0 1 6.6 2.5zM10 2.5v6.6l2.1-1.5 2.1 1.5V2.5z"/></svg>Журнал</a>
</nav>

<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
"use strict";
var D = JSON.parse(document.getElementById("data").textContent);
var FAVA = { marko: "fava/%D1%84%D1%96%D0%BD%D0%B0%D0%BD%D1%81%D0%B8-%D0%BC%D0%B0%D1%80%D0%BA%D0%B0/",
             olya: "fava/%D1%84%D1%96%D0%BD%D0%B0%D0%BD%D1%81%D0%B8-%D0%BE%D0%BB%D1%96/" };
var INS = ["січнем", "лютим", "березнем", "квітнем", "травнем", "червнем", "липнем", "серпнем", "вереснем", "жовтнем", "листопадом", "груднем"];  // орудний: «порівняно з вереснем»
var GEN = ["січня","лютого","березня","квітня","травня","червня","липня","серпня","вересня","жовтня","листопада","грудня"];
var LOC = ["січні","лютому","березні","квітні","травні","червні","липні","серпні","вересні","жовтні","листопаді","грудні"];
var MONTHS_FULL = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень", "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"];
var nf2 = new Intl.NumberFormat("uk-UA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
var nf1 = new Intl.NumberFormat("uk-UA", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
var nf0 = new Intl.NumberFormat("uk-UA", { maximumFractionDigits: 0 });
function zl(v) { return nf2.format(v) + " зл"; }
function $(id) { return document.getElementById(id); }
function h(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; }
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
function mi(m) { return parseInt(m.key.slice(5), 10) - 1; }
function iso(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
// шкали й стовпчики заповнюються, коли зʼявляються на екрані: одразу видимі — після першого кадру,
// нижчі — тоді, коли до них догортають; ті, що виринули разом, ростуть по черзі
var growIO = "IntersectionObserver" in window ? new IntersectionObserver(function (list) {
  var k = 0;
  list.forEach(function (en) {
    var e = en.target, above = !en.isIntersecting && en.boundingClientRect.bottom < 0;
    if (!en.isIntersecting && !above) return;  // ще нижче екрана — чекаємо
    growIO.unobserve(e);
    // якщо шкалу різко прогорнули повз екран — просто заповнюємо, без анімації
    e.style.transitionDelay = above ? "0ms" : (e.dataset.delay ? +e.dataset.delay + 150 : Math.min(k++, 8) * 80 + 150) + "ms"; e.style[e.dataset.attr] = e.dataset.to;
  });
}, { rootMargin: "0px 0px -60px 0px" }) : null;
function grow(sel, attr) {
  requestAnimationFrame(function () { requestAnimationFrame(function () {
    document.querySelectorAll(sel).forEach(function (e, i) {
      if (growIO) { e.dataset.attr = attr; growIO.observe(e); }
      else { e.style.transitionDelay = Math.min(i, 8) * 80 + 150 + "ms"; e.style[attr] = e.dataset.to; }
    });
  }); });
}
function plural(n, one, few, many) { var a = n % 10, b = n % 100; return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many; }
function svgI(color, body, size) {
  return '<svg viewBox="0 0 24 24" width="' + (size || 18) + '" height="' + (size || 18) + '" fill="none" stroke="' + color + '" stroke-width="' + (color === "#fff" || color === "currentColor" || color === "#6B7180" ? 1.8 : 2.1) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + "</svg>";
}
var CHR = svgI("currentColor", '<path d="m9 6 6 6-6 6"/>', 20);  // шеврон «далі» — 20 px, щоб добре було видно
var CAL = svgI("#6B7180", '<rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/>', 16);
// кольорові іконки категорій (з макета v2): фон, колір лінії, малюнок
var CATS = {
  "Продукти": ["#E3F7EA", "#22C55E", '<path d="M2 3.25A.75.75 0 0 1 2.75 2.5h1.6c.6 0 1.1.42 1.22 1L5.8 5H20a1 1 0 0 1 .97 1.24l-1.6 6.6A2 2 0 0 1 17.4 14.4H8.1l.3 1.6H18a.9.9 0 0 1 0 1.8H7.7a1 1 0 0 1-.98-.8L4 4H2.75A.75.75 0 0 1 2 3.25z"/><circle cx="9" cy="20.2" r="1.6"/><circle cx="17.5" cy="20.2" r="1.6"/>'],
  "Кафе": ["#FDE8EC", "#F43F5E", '<path fill-rule="evenodd" d="M4 7.5a.5.5 0 0 1 .5-.5h12a.5.5 0 0 1 .5.5V9h1.3a3.2 3.2 0 0 1 0 6.4h-1.7A6 6 0 0 1 11.2 19H9.8A5.8 5.8 0 0 1 4 13.2zM17 10.8v2.8h1.3a1.4 1.4 0 0 0 0-2.8z"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M8 2.5V4.5M12 2.5V4.5"/>'],
  "Доставка Їжі": ["#E6EFFE", "#0EA5E9", '<path fill-rule="evenodd" d="M8 7V6a4 4 0 0 1 8 0v1h2.1a1 1 0 0 1 1 .92l.9 11A2 2 0 0 1 18 21H6a2 2 0 0 1-2-2.08l.9-11A1 1 0 0 1 5.9 7zm1.8 0h4.4V6a2.2 2.2 0 0 0-4.4 0z"/>'],
  "Звязок": ["#F1E8FD", "#A855F7", '<path fill-rule="evenodd" d="M8.5 2h7A2.5 2.5 0 0 1 18 4.5v15a2.5 2.5 0 0 1-2.5 2.5h-7A2.5 2.5 0 0 1 6 19.5v-15A2.5 2.5 0 0 1 8.5 2zm2.5 16a1 1 0 0 0 0 2h2a1 1 0 0 0 0-2z"/>'],
  "Дохід": ["#E3F7EA", "#22C55E", '<path fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="M17 7 7.5 16.5M7.5 9.5v7h7"/>'],
  "Комунальні": ["#DFF5F2", "#14B8A6", '<path d="M13.6 2.2a.6.6 0 0 1 1 .5L13.5 10H18a.6.6 0 0 1 .5 1l-8.1 10.8a.6.6 0 0 1-1-.5L10.5 14H6a.6.6 0 0 1-.5-1z"/>'],
  "Спорт": ["#FFEDDF", "#FB923C", '<rect x="5" y="5.5" width="3.2" height="13" rx="1.2"/><rect x="15.8" y="5.5" width="3.2" height="13" rx="1.2"/><rect x="2" y="8.5" width="2.6" height="7" rx="1"/><rect x="19.4" y="8.5" width="2.6" height="7" rx="1"/><rect x="8" y="11" width="8" height="2" rx=".5"/>'],
  "Житло": ["#E9EAFD", "#6366F1", '<path d="M11.3 2.8a1.1 1.1 0 0 1 1.4 0l8 6.6a1 1 0 0 1-.65 1.8H19V20a1 1 0 0 1-1 1h-4v-5h-4v5H6a1 1 0 0 1-1-1v-8.8H3.95a1 1 0 0 1-.65-1.8z"/>'],
  "Транспорт": ["#E0F2FE", "#3B82F6", '<path fill-rule="evenodd" d="M8 3h8a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3zm-1 3.5v4h10v-4z"/><circle cx="8.5" cy="14.5" r="1.1" fill="#fff"/><circle cx="15.5" cy="14.5" r="1.1" fill="#fff"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M8 18.5V21M16 18.5V21"/>'],
  "Підписки": ["#FEF6D8", "#F59E0B", '<circle cx="12" cy="12" r="9.5"/><path fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M8 12a4 4 0 0 1 6.9-2.8M16 12a4 4 0 0 1-6.9 2.8M15.4 6.8v2.6h-2.6M8.6 17.2v-2.6h2.6"/>'],
  "Краса і Побут": ["#FDE8F4", "#EC4899", '<path stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" d="M11 3.5l1.8 5.2L18 10.5l-5.2 1.8L11 17.5l-1.8-5.2L4 10.5l5.2-1.8z"/><path d="M18.5 14.5l.75 1.75L21 17l-1.75.75-.75 1.75-.75-1.75L16 17l1.75-.75z"/>'],
  "Розваги": ["#EDE9FE", "#D946EF", '<path fill-rule="evenodd" d="M7 7h10a5 5 0 0 1 4.9 6l-.5 2.6a3 3 0 0 1-5.1 1.5L14.6 15H9.4l-1.7 2.1a3 3 0 0 1-5.1-1.5L2.1 13A5 5 0 0 1 7 7zm.2 3a.9.9 0 0 0-.9.9v.9h-.9a.9.9 0 0 0 0 1.8h.9v.9a.9.9 0 0 0 1.8 0v-.9h.9a.9.9 0 0 0 0-1.8h-.9v-.9a.9.9 0 0 0-.9-.9zm8.8 1a1 1 0 1 0 0 2 1 1 0 0 0 0-2z"/>'],
  "Покупки": ["#FCE7F3", "#8B5CF6", '<path fill-rule="evenodd" d="M8.2 7a3.8 3.8 0 0 1 7.6 0h2.3a1 1 0 0 1 1 .9l1 12A1 1 0 0 1 19 21H5a1 1 0 0 1-1-1.1l1-12A1 1 0 0 1 6 7zm1.8 0h4a2 2 0 0 0-4 0z"/>'],
  "Інші": ["#F1F2F5", "#94A3B8", '<circle cx="5.5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="18.5" cy="12" r="2"/>']
};
// зсув малюнка до центру кола: виміряно за видимими пікселями (разом із товщиною ліній)
// плюс невелика оптична поправка на «вагу» — щоб сумки, чашка й кошик не здавались зміщеними
var SHIFT = {"Продукти": [0.18, 0.25], "Кафе": [-0.2, 1.08], "Доставка Їжі": [0, -0.19], "Звязок": [0, 0.09], "Дохід": [0.14, -0.15], "Комунальні": [0, 0], "Спорт": [0, 0], "Житло": [0, 0.01], "Транспорт": [0, -0.2], "Підписки": [0, 0], "Краса і Побут": [0.06, 0.89], "Розваги": [-0.15, -0.3], "Покупки": [-0.05, -0.67], "Інші": [0, 0]};
// іконка категорії: світло-сірий круг, залитий малюнок у кольорі категорії, по центру
// яскраві кольори категорій — для шкал, часток, крапок і малюнків іконок
var BRIGHT = {"Продукти": "#22C55E", "Кафе": "#F43F5E", "Доставка Їжі": "#0EA5E9", "Звязок": "#A855F7", "Дохід": "#22C55E", "Комунальні": "#14B8A6", "Спорт": "#FB923C", "Житло": "#6366F1", "Транспорт": "#3B82F6", "Підписки": "#F59E0B", "Краса і Побут": "#EC4899", "Розваги": "#D946EF", "Покупки": "#8B5CF6", "Інші": "#94A3B8"};
function bright(n) { return BRIGHT[n] || BRIGHT["Інші"]; }
function cic(name, big) {
  var k = CATS[name] ? name : "Інші", c = CATS[k], sh = SHIFT[k] || [0, 0];
  var s = big ? 20 : 18;
  if (k === "Дохід") return '<span class="cic inc"><span class="glyph" style="color:' + c[1] + ';font-size:' + (s + 2) + 'px" aria-hidden="true">↙</span></span>';
  return '<span class="cic' + (k === "Дохід" ? " inc" : "") + '"><svg viewBox="0 0 24 24" width="' + s + '" height="' + s + '" fill="currentColor" style="color:' + c[1] + '" aria-hidden="true">' +
    '<g transform="translate(' + sh[0] + " " + sh[1] + ')">' + c[2] + "</g></svg></span>";
}
// стрілка зміни: діагональна вгору/вниз
function arr(d) { return d > 0 ? "↗" : "↘"; }

function lastFull(P) { for (var i = P.months.length - 1; i >= 0; i--) if (P.months[i].income > 0) return i; return P.months.length - 1; }
function curMonth(P) { return P.months.length - 1; }  // Головна завжди відкривається на поточному місяці, навіть до зарплати
function toCur(P) { selDay = null; }  // переходи з Головної в Журнал — той самий місяць, що в шапці, без обраного дня
var person = 0, month = curMonth(D.people[0]), trendMonth = null, filter = "all", amode = "month", catF = null, jq = "", jqMonth = false;  // jqMonth — пошук лише в обраному місяці  // catF — фільтр Журналу за категорією (з «Що змінилось»)
var GOAL = { olya: { months: 6, deadline: [2027, 6], title: "Подушка безпеки" } };  // фіча 5 з роадмапу ДЗ
var VIEWS = { report: 1, analytics: 1, journal: 1, add: 1 };
var saved = null;  // після збереження готівки: сума й дата, щоб підсвітити новий запис
var justAdded = [];  // усі записи, додані за цей сеанс, — кожен має «щойно»
function markFresh(s) { if (s) justAdded.push({ amount: +s.amount, day: s.day, title: s.title }); }
(function () {
  var q = new URLSearchParams(location.search), pi = -1;
  D.people.forEach(function (p, i) { if (p.key === q.get("p")) pi = i; });
  if (pi < 0) { history.replaceState(null, "", location.pathname + location.hash); return; }
  person = pi;
  D.people[pi].months.forEach(function (m, i) { if (m.key === q.get("m")) month = i; });
  if (q.get("saved")) {
    saved = { amount: +q.get("a") || 0, day: q.get("d") || "", line: +q.get("l") || 0, title: q.get("t") || "" }; markFresh(saved);
    setTimeout(function () {
      toast(okMsg("Готівку додано"), saved.line ? ["Скасувати", function () {
        delCash(D.people[person], saved.line, saved.day, saved.title, "#report", null, true);
      }] : null);
    }, 100);
  }
  if (q.get("deleted")) setTimeout(function () { toast(okMsg("Запис видалено")); }, 100);
  history.replaceState(null, "", location.pathname + location.hash);
})();
function view() {
  var v = location.hash.slice(1);
  if (v === "trends") { amode = "trends"; return "analytics"; }
  return VIEWS[v] ? v : "report";
}

// ---------- шапка: людина ----------
function renderPeople() {
  var P = D.people[person];
  document.body.className = "p-" + P.key;
  $("who-av").className = "av " + P.key;
  $("who-name").textContent = P.name;
  $("who-btn").setAttribute("aria-label", "Дані: " + P.name + ". Змінити");
  var box = $("who-opts"); box.innerHTML = "";
  D.people.forEach(function (p, i) {
    var b = h("button", "opt", '<span class="av ' + p.key + '"></span><span><b>' + esc(p.name) + '</b></span><span class="radio" aria-hidden="true"></span>');
    b.type = "button"; b.setAttribute("role", "menuitemradio"); b.setAttribute("aria-checked", i === person);
    b.onclick = function () { popMenu("w", false); if (i === person) return; person = i; month = curMonth(D.people[i]); trendMonth = null; selDay = null; render(); if (view() === "report") setTimeout(replaySheen, 250); };
    box.appendChild(b);
  });
}
// ---------- шапка: календар днів (лише місяці з даними; дні без даних неактивні) ----------
var calKey = null, selDay = null;
var WD0 = function (d) { return (d.getDay() + 6) % 7; };
function renderCal(P) {
  var keys = P.months.map(function (m) { return m.key; }), ki = keys.indexOf(calKey);
  if (ki < 0) { ki = month; calKey = keys[ki]; }
  var M = P.months[ki], y = +calKey.slice(0, 4), mo = +calKey.slice(5) - 1;
  $("cal-year").textContent = MONTHS_FULL[mo] + " " + y;
  $("cal-prev").disabled = ki <= 0; $("cal-next").disabled = ki >= keys.length - 1;
  var dim = new Date(y, mo + 1, 0).getDate(), lastDay = M.incomplete ? +M.last_op.slice(0, 2) : dim;
  var withOps = {}; M.ops.forEach(function (o) { withOps[o.day] = true; });
  var today = iso(new Date()), first = new Date(y, mo, 1), start = new Date(y, mo, 1 - WD0(first));
  var grid = $("cal-grid"); grid.innerHTML = "";
  var rows = Math.ceil((WD0(first) + dim) / 7);
  for (var c = 0; c < rows * 7; c++) {
    var d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + c), key = iso(d), inMonth = d.getMonth() === mo;
    var b = h("button", (withOps[key] && inMonth ? "has" : "") + (key === today ? " today" : ""), String(d.getDate()));
    b.type = "button"; b.disabled = !inMonth || d.getDate() > lastDay;
    b.setAttribute("aria-pressed", key === selDay);
    b.setAttribute("aria-label", d.getDate() + " " + GEN[d.getMonth()] + " " + d.getFullYear() + (b.disabled ? ", даних немає" : withOps[key] ? ", є операції" : ""));
    if (!b.disabled) (function (key) { b.onclick = function () { selDay = key; month = ki; if (view() === "journal" && jq.trim()) jqMonth = true; calOpen(false); render(); scrollToDay(key); }; })(key);
    grid.appendChild(b);
  }
  var lastM = P.months[P.months.length - 1];
  $("cal-note").textContent = M.incomplete ? "Сірі дні — даних ще немає. Облік до " + M.last_op + "." : "";
  $("cal-note").hidden = !M.incomplete;
}
function scrollToDay(key) {
  if (view() !== "journal") return;
  var el = document.querySelector('[data-day="' + key + '"]');
  if (el) requestAnimationFrame(function () { window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 12); });
}
$("cal-prev").onclick = function () { var P = D.people[person], k = P.months.map(function (m) { return m.key; }).indexOf(calKey); if (k > 0) { month = k - 1; calKey = P.months[month].key; selDay = null; trendMonth = null; if (view() === "journal" && jq.trim()) jqMonth = true; render(); calOpen(true); } };
$("cal-next").onclick = function () { var P = D.people[person], k = P.months.map(function (m) { return m.key; }).indexOf(calKey); if (k < P.months.length - 1) { month = k + 1; calKey = P.months[month].key; selDay = null; trendMonth = null; if (view() === "journal" && jq.trim()) jqMonth = true; render(); calOpen(true); } };
var MENUS = { w: ["who-menu", "who-btn"], m: ["mmenu", "mcal"] };
function popMenu(k, open) {
  if (!open) { $(MENUS[k][0]).hidden = true; $(MENUS[k][1]).setAttribute("aria-expanded", false); return; }  // закриваємо лише своє меню
  Object.keys(MENUS).forEach(function (x) { var on = x === k && open; $(MENUS[x][0]).hidden = !on; $(MENUS[x][1]).setAttribute("aria-expanded", on); });
  if (open) { var cur = $(MENUS[k][0]).querySelector('[aria-checked="true"], [aria-pressed="true"]'); if (cur) cur.focus(); }
}
$("who-btn").onclick = function () { popMenu("w", $("who-menu").hidden); };
document.body.appendChild($("mmenu"));  // поза картками й рядком фільтрів — нічого його не обрізає
function calAnchor() { return $("mcal"); }
function calPlace() {
  var m = $("mmenu"), b = calAnchor(); if (m.hidden || !b) return;
  var r = b.getBoundingClientRect();
  m.style.top = r.bottom + 8 + "px"; m.style.left = Math.max(12, Math.min(r.left, innerWidth - m.offsetWidth - 12)) + "px";
}
function calOpen(open) {
  $("cal-all").hidden = !(view() === "journal" && jq.trim());  // під час пошуку — повернення до всіх місяців
  popMenu("m", open);  calPlace();
}
$("mcal").onclick = function () { calOpen($("mmenu").hidden); };
$("a-prev").onclick = function () { if (month > 0) { month--; render(); } };
$("a-next").onclick = function () { if (month < D.people[person].months.length - 1) { month++; render(); } };
$("cal-all").onclick = function () { jqMonth = false; selDay = null; calOpen(false); render(); };
// свайп по графіку Аналітики. «Місяці»: стрічка їде за пальцем; відпустили далі за 50 px — сусідня сторінка
// (праворуч від найновішої — порожнеча, тягнеться пружно й повертається). «Тижні»: свайп — сусідній місяць
(function () {
  var ch = document.querySelector("#an-chart .chart"), x0 = null, y0 = 0, swiped = false, drag = false;
  var pgMax = function (g) { return Math.ceil(g.n / g.per) - 1; };
  var shift = function (g, dx) { return (dx > 0 && mpage.p >= pgMax(g)) || (dx < 0 && mpage.p <= 0) ? dx * .35 : dx; };  // за краєм — пружина
  ch.addEventListener("pointerdown", function (e) { x0 = e.clientX; y0 = e.clientY; swiped = false; drag = false; });
  ch.addEventListener("pointermove", function (e) {
    var box = $("mbars"), g = box._pg; if (x0 === null || !g) return;
    var dx = e.clientX - x0, dy = e.clientY - y0;
    if (!drag) { if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy)) return; drag = true; try { ch.setPointerCapture(e.pointerId); } catch (er) {} }
    pageTo(box, mpage.p, false, shift(g, dx));
  });
  ch.addEventListener("pointerup", function (e) {
    if (x0 === null) return;
    var dx = e.clientX - x0, dy = e.clientY - y0, box = $("mbars"), g = box._pg; x0 = null;
    if (g) {
      if (!drag) return;
      swiped = true; drag = false;
      if (dx > 50 && mpage.p < pgMax(g)) mpage.p++;
      else if (dx < -50 && mpage.p > 0) mpage.p--;
      pageTo(box, mpage.p, true);
      return;
    }
    if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    var n = D.people[person].months.length, to = month + (dx < 0 ? 1 : -1);
    swiped = true;
    if (to < 0 || to >= n) { ch.classList.remove("bump"); void ch.offsetWidth; ch.classList.add("bump"); return; }
    month = to; render();
    ch.classList.remove("in-l", "in-r"); void ch.offsetWidth; ch.classList.add(dx < 0 ? "in-r" : "in-l");
  });
  ch.addEventListener("pointercancel", function () { x0 = null; if (drag) { drag = false; pageTo($("mbars"), mpage.p, true); } });
  // після свайпу мишкою не спрацьовує тап по стовпчику
  ch.addEventListener("click", function (e) { if (swiped) { e.stopPropagation(); e.preventDefault(); swiped = false; } }, true);
})();
document.addEventListener("click", function (e) { if (!e.target.closest("#mmenu, #mcal")) calOpen(false); });
window.addEventListener("scroll", calPlace, { passive: true });  // їде разом із кнопкою
window.addEventListener("hashchange", function () { calOpen(false); });
document.addEventListener("click", function (e) { if (!e.target.closest(".who, .mpick")) popMenu("w", false); });
document.addEventListener("keydown", function (e) {
  if (e.key !== "Escape") return;
  Object.keys(MENUS).forEach(function (x) { if (!$(MENUS[x][0]).hidden) { popMenu(x, false); $(MENUS[x][1]).focus(); } });
});

// ---------- допоміжне ----------
// бейдж зміни: зелений — краще, червоний — гірше, сірий «без змін»; стрілка діагональна
function badge(now, was, isExp, prevGen, whole) {
  if (!was) return "";
  var ch = (now - was) / was * 100, fmt = whole ? nf0 : nf1;
  if (Math.abs(ch) < 0.5) return '<span class="bd">без змін</span>';
  var good = (ch > 0) !== isExp;
  return '<span class="bd ' + (good ? "good" : "bad") + '" aria-label="' + (ch > 0 ? "більше" : "менше") + " на " + fmt.format(Math.abs(ch)) + " %, " + (good ? "краще" : "гірше") + '">' +
    arr(ch) + " " + fmt.format(Math.abs(ch)) + " %" + (prevGen ? " до " + prevGen : "") + "</span>";
}
// плашка на головній картці: коротка, праворуч від підпису; «без змін» не показуємо
function heroBadge(now, was, isExp, gen) {
  if (!was) return "";
  var ch = (now - was) / was * 100;
  if (Math.abs(ch) < 0.5) return "";
  var good = (ch > 0) !== isExp;
  return '<span class="bd ' + (good ? "good" : "bad") + '" title="' + (ch > 0 ? "більше" : "менше") + ", ніж у " + gen + '" aria-label="' +
    (ch > 0 ? "більше" : "менше") + " на " + nf1.format(Math.abs(ch)) + " %, ніж у " + gen + ", " + (good ? "краще" : "гірше") + '">' + arr(ch) + " " + nf1.format(Math.abs(ch)) + " %</span>";
}
// «зазвичай»: середні витрати за повні місяці до k (є зарплата, місяць закрито), не більше 6 останніх
function usualExp(P, k) {
  var ms = P.months.slice(0, k).filter(function (m) { return m.income > 0 && !m.incomplete; }).slice(-6);
  return ms.length ? { v: ms.reduce(function (s, m) { return s + m.expenses; }, 0) / ms.length, n: ms.length } : null;
}
function usualBadge(now, u) {
  if (!u) return "";
  var d = now - u.v, t = "середнє за " + u.n + " міс.: " + nf0.format(Math.round(u.v)) + " зл";
  if (Math.abs(d) < u.v * .03) return '<span class="bd" title="' + t + '">як зазвичай</span>';
  return '<span class="bd ' + (d > 0 ? "bad" : "good") + '" title="' + t + '" aria-label="' + (d > 0 ? "більше" : "менше") + ', ніж зазвичай">' + (d > 0 ? "+" : "−") + nf0.format(Math.abs(Math.round(d))) + " зл</span>";
}
function prevWithIncome(P, k) { for (var j = k - 1; j >= 0; j--) if (P.months[j].income > 0) return P.months[j]; return null; }
function fixedCats(M) {
  var fx = {};
  M.ops.forEach(function (o) { if (o.income) return; fx[o.cat] = (fx[o.cat] === undefined ? true : fx[o.cat]) && o.fixed; });
  return fx;
}
function nice(s) { return s === s.toUpperCase() ? s.toLowerCase().replace(/(^|[\s\/*.-])(\S)/g, function (_, a, b) { return a + b.toUpperCase(); }) : s; }
var TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>';
var OPS = [];  // операції з рядків на екрані — для картки операції (data-op = індекс)
function opRow(o, recent, fresh) {
  return '<div class="op' + (fresh ? " fresh" : "") + '" data-op="' + (OPS.push(o) - 1) + '" role="button" tabindex="0">' + cic(o.income ? "Дохід" : o.cat, true) + '<div class="t">' + (fresh ? '<div class="t1row"><div class="t1 one">' + esc(nice(o.title)) + '</div><span class="fx now">щойно</span></div>'
      : '<div class="t1' + (recent ? " one" : "") + '">' + esc(nice(o.title)) + "</div>") +
    '<div class="t2"><span>' + esc(o.income ? "Дохід" : o.cat) + (recent ? '<i class="sep" aria-hidden="true"></i>' + o.date : "") + "</span>" + (o.fixed ? '<span class="fx">обовʼязкова</span>' : "") + (o.cash ? '<span class="fx cash">готівка</span>' : "") +
    '</div></div><div class="r"><span class="am' + (o.income ? " inc" : "") + '">' + (o.income ? "+" : "−") + zl(o.amount) + "</span>" +
    "</div>" +
    "</div>";
}
// частки категорій: 3 найбільші відтінками синього + «Інші»
var SH = ["", "", "", "#DDE1E7"];  // «Інші» — сірим
function slices(M) {
  var top = M.categories.slice(0, 3).map(function (c) { return { name: c.name, amount: c.amount, color: bright(c.name) }; });
  var rest = M.categories.slice(3).reduce(function (s, c) { return s + c.amount; }, 0);
  if (rest > 0) top.push({ name: "Інші " + (M.categories.length - 3), amount: rest, color: SH[3] });
  return top;
}
function payDays(P) {
  var d = [];
  P.months.forEach(function (m) { m.ops.forEach(function (o) { if (o.income) d.push(+o.date.slice(0, 2)); }); });
  if (!d.length) return "";
  var lo = Math.min.apply(null, d), hi = Math.max.apply(null, d);
  return lo === hi ? lo + "-го" : lo + "–" + hi;
}

// ---------- головна ----------
// картка ліміту: лише для поточного (неповного) місяця і коли ліміт уже є; тап — Аналітика з увімкненим лімітом.
// Внизу — тихий рядок до шторки «Відкладати щомісяця»: у Марка «+ Відкладати щомісяця» (або його план), в Олі — подушка
function renderLimit(P) {
  var box = $("limc"), j = P.months.length - 1, M = P.months[j], LM = month === j && M.incomplete ? limitsFor(P, j) : null;
  box.hidden = !LM; if (!LM) return;
  // обраний у календарі день — витрачене до нього включно і скільки днів лишається після нього
  var cut = selDay && selDay.slice(0, 7) === M.key ? selDay : null;
  var spent = cut ? M.ops.reduce(function (s, o) { return s + (!o.income && !o.fixed && o.day <= cut ? o.amount : 0); }, 0) : M.variable;
  var left = LM.total - spent, dn = cut ? +cut.slice(8) : M.last_op ? +M.last_op.slice(0, 2) : 0, dl = new Date(+M.key.slice(0, 4), +M.key.slice(5), 0).getDate() - dn, g = goalPlan(P);
  var p = LM.total ? Math.min(100, spent / LM.total * 100) : 100;
  // Марко — скільки на день до кінця місяця; Оля — те саме, але видно, навіщо: щоб відкласти план
  var note = left < 0 ? "Щоденні вже понад ліміт, до кінця місяця ще " + dl + " " + plural(dl, "день", "дні", "днів")
    : "Це ≈ " + zr(left / Math.max(1, dl)) + " на день" + (LM.src !== "best" ? ", щоб відкласти " + zr(LM.save) : " до кінця місяця");
  var pl = LM.plan, foot;
  if (g) {
    var segs = "";
    for (var i = 0; i < g.G.months; i++) segs += '<i><b style="width:' + (Math.max(0, Math.min(1, g.life - i)) * 100).toFixed(2) + '%"></b></i>';
    foot = '<button type="button" class="lfoot" id="limc-plan"><span class="lpig">' + svgI("currentColor", '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>', 16) + "</span><span class=\"lft\">Відкладено <b>" + nf1.format(g.life) + " з " + g.G.months + " міс.</b></span>" +
      '<span class="lsegs" aria-hidden="true">' + segs + "</span>" + CHR + "</button>";
  } else if (pl) {
    foot = '<button type="button" class="lfoot" id="limc-plan"><span class="lft">' + "Відкладаєте <b>" + zr(pl.monthly) + "</b> на місяць" + (pl.until ? " до " + dmy(pl.until) : "") +
      '</span><span class="llink">Змінити</span></button>';
  } else foot = '<button type="button" class="lfoot ladd" id="limc-plan"><span class="llink">' + svgI("currentColor", '<path d="M12 5v14M5 12h14"/>', 18) + "Відкладати щомісяця</span></button>";
  box.innerHTML = '<a class="lmain" href="#analytics"><div class="ch"><h2>Ліміт на щоденні витрати</h2>' + CHR + "</div>" +
    '<div class="lbig"><b>' + zr(Math.abs(left)) + "</b><span>" + (left < 0 ? "понад ліміт " : "лишилось з ") + nf0.format(Math.round(LM.total)) + "</span></div>" +
    '<div class="lbar" role="img" aria-label="Витрачено ' + nf0.format(Math.round(p)) + ' % ліміту"><b' + (left < 0 ? ' class="over"' : "") + ' data-to="' + p.toFixed(2) + '%"></b></div>' +
    '<p class="lnote">' + note + "</p></a>" + foot;
  box.querySelector(".lmain").onclick = function () { lim = true; ascale = "m"; try { localStorage.setItem("lim", "1"); } catch (e) {} };
  $("limc-plan").onclick = function () { planOpen(P); };
  grow("#limc .lbar b", "width");
}
function renderReport(P) {
  var M = P.months[month], k = mi(M), prevM = P.months[month - 1] || null, prevI = prevWithIncome(P, month);
  var noIncome = !(M.income > 0), hero = $("hero"), approx = P.ok ? "" : "≈ ";
  // облік з помилками: спокійна жовта плашка, нічого не блокує; ✕ ховає її, доки дані не оновляться
  var akey = "alert-off:" + P.key + ":" + (M.last_op || ""), off = false;
  try { off = localStorage.getItem(akey) === "1"; } catch (e) {}
  $("h-alert").innerHTML = P.ok || off ? "" : '<div class="alert" role="status"><span>Цифри можуть бути неточні: облік прочитався з помилками. <a href="' +
    FAVA[P.key] + 'errors/">Деталі</a></span><button type="button" aria-label="Сховати">' + svgI("currentColor", '<path d="M6 6l12 12M18 6 6 18"/>', 16) + "</button></div>";
  var ab = $("h-alert").querySelector("button");
  if (ab) ab.onclick = function () { try { localStorage.setItem(akey, "1"); } catch (e) {} $("h-alert").innerHTML = ""; };
  var tag = M.incomplete ? '<span class="tag">дані до ' + M.last_op + "</span>" : "";
  var dn = M.last_op ? +M.last_op.slice(0, 2) : 0, spent = "Витрати" + (M.incomplete ? " за " + dn + " " + plural(dn, "день", "дні", "днів") : "");
  // поточний місяць рахуємо від зарплати до зарплати: скільки лишилось з останньої зарплати — це факт, як Марко й думає про гроші
  // день з календаря: Головна показує стан на кінець цього дня — від останньої зарплати до нього, за операціями виписки й готівки
  var cut = selDay && selDay.slice(0, 7) === M.key ? selDay : null;
  if (cut) tag = '<button type="button" class="tag tagx" id="h-day" aria-label="Стан на ' + (+cut.slice(8)) + " " + GEN[k] + ', прибрати">на ' + (+cut.slice(8)) + " " + GEN[k] + svgI("currentColor", '<path d="M6 6l12 12M18 6 6 18"/>', 12) + "</button>";
  var ops = [].concat.apply([], P.months.map(function (m) { return m.ops; })).filter(function (o) { return !cut || o.day <= cut; });
  var sd = ops.reduce(function (d, o) { return o.income && o.day > d ? o.day : d; }, "");
  var dm = function (iso) { return iso.slice(8) + "." + iso.slice(5, 7); }, z0 = function (v) { return (v < 0 ? "−" : "") + nf0.format(Math.abs(Math.round(v))) + " зл"; };
  if ((month === P.months.length - 1 && M.incomplete || cut) && sd) {
    var A = 0, out = 0;
    ops.forEach(function (o) { if (o.day >= sd) { if (o.income) A += o.day === sd ? o.amount : 0; else out += o.amount; } });
    var ep = Math.min(100, Math.max(0, out / A * 100));
    hero.innerHTML = '<div class="lbl">Лишилось коштів' + tag + "</div>" +
      '<div class="numrow"><div class="num">' + approx + z0(A - out) + "</div>" +
      '<a href="#journal" data-filter="inc" class="side inc">/ із зарплати ' + dm(sd) + " — <b>" + z0(A) + "</b></a></div>" +
      (P.ok ? "" : '<div class="sub">Потребує перевірки</div>') +
      '<div class="split2" role="img" aria-label="Витрачено ' + nf0.format(ep) + " % зарплати, лишилось " + nf0.format(100 - ep) + ' %">' +
        '<b class="e" data-to="calc(' + ep.toFixed(2) + '% - 2px)"></b><b class="s"></b></div>' +
      '<div class="lg2"><a href="#journal" data-filter="exp" class="le"><span class="lt"><i></i>Витрати, ' + nf0.format(ep) + " %</span>" +
        '<span class="lv"><b>' + z0(out) + "</b></span></a>" +
      '<div class="ls"><span class="lt"><i></i>Лишилось, ' + nf0.format(100 - ep) + " %</span><b>" + z0(A - out) + "</b></div></div>";
  } else if (noIncome) {
    // дохід ще жодного разу не надходив (або вибрано місяць без доходу): лише скільки витрачено
    hero.innerHTML = '<div class="lbl">Витрачено у ' + LOC[k] + tag + "</div>" +
      '<div class="numrow"><div class="num">' + approx + z0(M.expenses) + '</div><div class="side">/ дохід <b>не надходив</b></div></div>';
  } else {
    var inc = M.incomplete ? "" : (prevI ? heroBadge(M.income, prevI.income, false, LOC[mi(prevI)]) : "");
    var exp = M.incomplete ? "" : usualBadge(M.expenses, usualExp(P, month));
    // увесь дохід — це смуга; витрати й залишок — її частини
    var ep = M.income > 0 ? Math.min(100, M.expenses / M.income * 100) : 100, sp = 100 - ep;
    hero.innerHTML = '<div class="lbl">Лишилось у ' + LOC[k] + tag + "</div>" +
      '<div class="numrow"><div class="num">' + approx + z0(M.saved) + "</div>" +
      '<a href="#journal" data-filter="inc" class="side inc">/ з доходу <b>' + nf0.format(M.income) + " зл</b>" + inc + "</a></div>" +
      (P.ok ? "" : '<div class="sub">Потребує перевірки</div>') +
      '<div class="split2" role="img" aria-label="Витрати ' + nf0.format(ep) + " %, лишилось " + nf0.format(sp) + ' % доходу">' +
        '<b class="e" data-to="calc(' + ep.toFixed(2) + '% - 2px)"></b><b class="s"></b></div>' +
      '<div class="lg2"><a href="#journal" data-filter="exp" class="le"><span class="lt"><i></i>' + spent + ", " + nf0.format(ep) + " %</span>" +
        '<span class="lv"><b>' + z0(M.expenses) + "</b>" + exp + "</span></a>" +
      '<div class="ls"><span class="lt"><i></i>Лишилось, ' + nf0.format(sp) + " %</span><b>" + z0(M.saved) + "</b></div></div>";
  }
  hero.querySelectorAll("[data-filter]").forEach(function (a) { a.onclick = function () { filter = a.dataset.filter; toCur(P); }; });
  if ($("h-day")) $("h-day").onclick = function () { selDay = null; reportKey = null; render(); };
  $("cash").hidden = !P.cash;
  renderLimit(P);
  renderInsights(P, M);
  var last = P.months[P.months.length - 1], ops = last ? last.ops.slice(0, 5) : [], left = justAdded.slice();
  $("recent").innerHTML = ops.length ? ops.map(function (o) {
    var k = !o.cash ? -1 : left.findIndex(function (s) { return Math.abs(o.amount - s.amount) < 0.005 && (!s.day || o.day === s.day) && (!s.title || o.title === s.title); });
    var fresh = k >= 0; if (fresh) left.splice(k, 1);
    return swipeWrap(o, opRow(o, true, fresh));
  }).join("") : '<div class="empty">Операцій немає</div>';
  $("recent").querySelectorAll(".swipe").forEach(function (sw) { swipeRow(sw, P, "#report"); });
  grow("#hero .meter b, #hero .split2 .e", "width");
}
// «Що змінилось у жовтні»: лише відхилення від звичного, 0–2 пункти; якщо все звично — блоку немає.
// Категорія: витрачено помітно більше/менше, ніж зазвичай за місяць (у неповному місяці — лише «вже більше») → Журнал з цією категорією.
// Минулий місяць: щойно закритий місяць і його залишок проти звичного — просто факт.
function renderInsights(P, M) {
  var month = P.months.indexOf(M);
  var box = $("insights"), k = mi(M), items = [];
  var fullBefore = function (j) { return P.months.slice(0, j).filter(function (m) { return m.income > 0 && !m.incomplete; }).slice(-6); };
  var z0 = function (v) { return nf0.format(Math.round(Math.abs(v))) + " зл"; };
  var row = function (title, sub, bd, cat) {
    return (cat ? '<a class="ins go" href="#journal" data-cat="' + esc(cat) + '">' : '<div class="ins">') + '<span class="it"><span>' + title + "</span>" + (sub ? "<small>" + sub + "</small>" : "") + "</span>" +
      '<span class="ir">' + bd + (cat ? CHR : "") + "</span>" + (cat ? "</a>" : "</div>");
  };
  var base = fullBefore(month);
  if (base.length) {
    var usual = {};
    base.forEach(function (m) { m.categories.forEach(function (c) { usual[c.name] = (usual[c.name] || 0) + c.amount / base.length; }); });
    var cats = M.categories.map(function (c) { return { name: c.name, now: c.amount, was: usual[c.name] || 0, d: c.amount - (usual[c.name] || 0) }; })
      .filter(function (c) { return M.incomplete ? c.d >= Math.max(30, c.was * .15) : Math.abs(c.d) >= Math.max(50, c.was * .2); })
      .sort(function (a, b) { return Math.abs(b.d) - Math.abs(a.d); });
  }
  var pm = M.incomplete ? P.months[month - 1] : null, mItem = "";
  if (pm && pm.income > 0 && !pm.incomplete) {
    var pb = fullBefore(month - 1), us = pb.length ? pb.reduce(function (s, m) { return s + m.saved; }, 0) / pb.length : null, ds = us === null ? 0 : pm.saved - us;
    mItem = row("У " + LOC[mi(pm)] + " лишилось " + z0(pm.saved),
      us === null ? "" : Math.abs(ds) < Math.max(50, us * .05) ? "як зазвичай" : (ds > 0 ? "більше" : "менше") + ", ніж зазвичай",
      us === null || Math.abs(ds) < Math.max(50, us * .05) ? "" : '<span class="bd ' + (ds > 0 ? "good" : "bad") + '">' + (ds > 0 ? "+" : "−") + z0(ds) + "</span>");
  }
  (cats || []).slice(0, mItem ? 1 : 2).forEach(function (c) {
    items.push(row(esc(c.name) + " — " + z0(c.now), c.was ? "зазвичай за місяць ≈ " + z0(c.was) : "минулими місяцями не було",
      '<span class="bd ' + (c.d > 0 ? "bad" : "good") + '">' + (c.d > 0 ? "+" : "−") + z0(c.d) + "</span>", c.name));
  });
  if (mItem) items.push(mItem);
  box.hidden = !items.length;
  box.innerHTML = '<div class="ch"><h2>Що змінилось у ' + LOC[k] + "</h2></div>" + items.join("");
  box.querySelectorAll("[data-cat]").forEach(function (a) { a.onclick = function () { filter = "all"; catF = a.dataset.cat; toCur(P); }; });
  box.classList.remove("fadein"); void box.offsetWidth; box.classList.add("fadein");
}
// ---------- аналітика ----------
// шкала осі: «гарний» максимум і 4 позначки
function niceMax(v) { var s = [400, 800, 1200, 1500, 2000, 3000, 4500, 6000, 8000, 10000, 15000]; for (var i = 0; i < s.length; i++) if (v <= s[i]) return s[i]; return Math.ceil(v / 5000) * 5000; }
// вісь з круглим кроком: не більше 4 поділок (наприклад 0–8 000 по 2 000)
function niceAxis(v) {
  var steps = [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000];
  for (var i = 0; i < steps.length; i++) { var n = Math.max(2, Math.ceil(v / steps[i])); if (n <= 4) return { max: n * steps[i], n: n }; }
  return { max: Math.ceil(v / 10000) * 10000, n: 4 };
}
function capsules(boxId, tickId, items, max, sel, onPick, n, avg, opt) {
  var H = 150, box = $(boxId); box.innerHTML = ""; opt = opt || {};
  box.classList.toggle("plain", !!opt.plain); $(tickId).hidden = !!opt.plain;
  // сторінки по opt.paged.per стовпчиків: стрічка всіх місяців у вікні, зсув — у частках ширини вікна
  var pg = opt.paged, dest = box;
  box.classList.toggle("paged", !!pg); box._pg = null;
  if (pg) {
    var vw = box.appendChild(h("div", "cview", "")); dest = vw.appendChild(h("div", "ctrk", ""));
    dest.style.setProperty("--per", pg.per);
    box._pg = { trk: dest, n: items.length, per: pg.per, page: pg.page, onPage: pg.onPage };
    pageTo(box, pg.page, false);
  }
  if (n) $(tickId).innerHTML = Array.apply(null, Array(n + 1)).map(function (_, k) { return "<span>" + nf0.format(Math.round(max * (n - k) / n)) + "</span>"; }).join("");
  else $(tickId).innerHTML = [1, 2 / 3, 1 / 3, 0].map(function (f) { return "<span>" + nf0.format(Math.round(max * f)) + " зл</span>"; }).join("");
  items.forEach(function (x, i) {
    var tap = onPick && !x.nopick;
    var c = h("div", "cap" + (tap ? " pick" : "") + (i === sel ? " on" : "") + (x.part ? " part" : "") + (tap && sel >= 0 && i !== sel ? " dim" : ""));
    var hh = Math.max(8, Math.round(x.v / max * H)), rent = x.rent ? Math.round(x.rent / max * H) : 0;
    // немає даних: порожня капсула — наш пунктир (круглі кінці, #C3C9D3) і риска всередині
    if (x.empty) c.innerHTML = '<div class="trk emp"><svg viewBox="0 0 40 28" aria-hidden="true"><rect x=".75" y=".75" width="38.5" height="26.5" rx="9.25" fill="none" stroke="#C3C9D3" stroke-width="1.5" stroke-dasharray="4 4" stroke-linecap="round"/><path d="M15 14h10" stroke="#C3C9D3" stroke-width="1.5" stroke-linecap="round"/></svg></div>';
    else c.innerHTML = '<div class="trk"><div class="fill" data-to="' + hh + 'px">' + (rent ? '<div class="rent" style="height:' + rent + 'px"></div>' : "") + '<div class="main"></div></div>' +
      (x.mark ? '<i class="mark" style="bottom:' + Math.round(x.mark / max * H) + 'px" aria-hidden="true"></i>' : "") + "</div>";
    c.innerHTML += '<span class="r1">' + esc(x.label) + '</span><span class="r2">' + esc(x.sub) + "</span>" + (x.sub2 ? '<span class="r3">' + esc(x.sub2) + "</span>" : "");
    c.setAttribute("aria-label", (x.aria || x.label) + ": " + (x.empty ? "немає даних" : zl(x.v)));
    if (tap) { c.setAttribute("role", "button"); c.tabIndex = 0;
      c.onclick = c.onkeydown = function (e) { if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return; onPick(i); }; }
    dest.appendChild(c);
  });
  // середнє за повні місяці — одна сіра пунктирна лінія через увесь графік (число одне на весь період)
  if (avg) {
    var al = box.appendChild(h("div", "avgl", opt.avgLabel ? '<span class="avgt">' + opt.avgLabel + "</span>" : "")); al.style.top = Math.round(H - avg / max * H) + "px";
    requestAnimationFrame(function () { requestAnimationFrame(function () { al.classList.add("in"); }); });
  }
  // ліміт — друга, синя суцільна лінія; підпис ліворуч, щоб не налазити на «сер.» праворуч
  if (opt.lim) {
    var ll = box.appendChild(h("div", "avgl liml", '<span class="avgt">ліміт ' + nf0.format(Math.round(opt.lim)) + "</span>")); ll.style.top = Math.round(H - opt.lim / max * H) + "px";
    requestAnimationFrame(function () { requestAnimationFrame(function () { ll.classList.add("in"); }); });
  }
  grow("#" + boxId + " .fill", "height");
}
// сторінка p (0 — найновіша, праворуч): перші стовпчики сторінки — від кінця стрічки; dx — зсув пальцем, px
function pageStart(g, p) { return Math.max(0, g.n - g.per * (p + 1)); }
function pageTo(box, p, anim, dx) {
  var g = box._pg; if (!g) return;
  g.trk.style.transition = anim ? "transform .38s cubic-bezier(.32, .72, 0, 1)" : "none";
  g.trk.style.transform = "translateX(calc(" + (-pageStart(g, p) * 100 / g.per) + "% + " + (dx || 0) + "px))";
}
// «зазвичай» для Аналітики: повні місяці перед k (є зарплата, місяць закрито), не більше 6 останніх
function usualMonths(P, k) { return P.months.slice(0, k).filter(function (m) { return m.income > 0 && !m.incomplete; }).slice(-6); }
function avgOf(ms, f) { return ms.length ? ms.reduce(function (s, m) { return s + f(m); }, 0) / ms.length : null; }
function zr(v) { return (v < 0 ? "−" : "") + nf0.format(Math.abs(Math.round(v))) + " зл"; }
function dayOf(o) { return +o.day.slice(8); }
// порівняння зі «зазвичай»: бейдж біля великої цифри + висновок рядком під нею
function vsUsual(now, u, goodIfMore, when) {
  if (u === null || u === undefined) return { badge: "", text: "" };
  var d = now - u;
  if (Math.abs(d) < Math.max(30, u * .05)) return { badge: "", text: "Як зазвичай" + when };
  return { badge: '<span class="bd ' + ((d > 0) === goodIfMore ? "good" : "bad") + '">' + (d > 0 ? "+" : "−") + zr(Math.abs(d)) + "</span>", text: (d > 0 ? "Більше" : "Менше") + ", ніж зазвичай" + when };
}
// підсумок над графіком: підпис (уточнення — через нашу вертикальну риску), сума з бейджем, висновок
function sumHTML(label, extra, big, c) {
  return '<div class="alab"><span>' + label + (extra ? '<i class="sep" aria-hidden="true"></i>' + extra : "") + "</span></div>" +
    '<div class="abig"><span>' + big + "</span>" + (c.badge || "") + "</div>" + (c.text ? '<div class="aline">' + c.text + "</div>" : "") + (c.note ? '<div class="anote">' + c.note + "</div>" : "");
}
var ascale = "m";  // масштаб графіка витрат: m — місяці, w — тижні обраного місяця
var mpage = { who: -1, p: 0 };  // сторінка графіка місяців (0 — найновіша) для людини who
var wsel = { key: "", i: -1 };  // обраний тиждень (у межах місяця key); -1 — увесь місяць
var lim = true, wsort = "left";  // ліміти увімкнено (за замовчуванням так); сортування категорій з лімітом: left — за залишком, sum — за сумою
try { lim = localStorage.getItem("lim") !== "0"; } catch (e) {}
// «бажання» — те, що ріжемо першим, коли ціль відстає; потреби (Продукти, Транспорт…) не чіпаємо
var WANTS = { "Кафе": 1, "Доставка Їжі": 1, "Розваги": 1, "Покупки": 1 };
// місяць «на кінець дня cut»: лише операції до цього дня включно, суми перераховані, місяць — неповний
// (так Аналітика й Журнал показують стан на обраний у календарі день тими самими правилами, що й для поточного місяця)
function monthAt(M, cut) {
  var ops = M.ops.filter(function (o) { return o.day <= cut; }), c = {}, inc = 0, fixed = 0, variable = 0;
  ops.forEach(function (o) { if (o.income) inc += o.amount; else { if (o.fixed) fixed += o.amount; else variable += o.amount; c[o.cat] = (c[o.cat] || 0) + o.amount; } });
  return Object.assign({}, M, { ops: ops, income: inc, fixed: fixed, variable: variable, expenses: fixed + variable, saved: inc - fixed - variable, incomplete: true,
    last_op: cut.slice(8) + "." + cut.slice(5, 7), categories: Object.keys(c).map(function (n) { return { name: n, amount: c[n] }; }).sort(function (a, b) { return b.amount - a.amount; }) });
}
function dayCut(M) { return selDay && M && selDay.slice(0, 7) === M.key ? selDay : null; }
// «на 7 жовтня ✕» — чип обраного дня; ✕ повертає до місяця
function dayChip(cut, k, id) {
  return '<button type="button" class="daychip" id="' + id + '" aria-label="Стан на ' + (+cut.slice(8)) + " " + GEN[k] + ', прибрати">на ' + (+cut.slice(8)) + " " + GEN[k] + svgI("currentColor", '<path d="M6 6l12 12M18 6 6 18"/>', 12) + "</button>";
}
// план «Відкладати щомісяця», що діє в місяці key (останній з from ≤ key); минулі місяці — зі своїм планом або без
function planFor(P, key) {
  var h = ((D.plans || {})[P.key] || []).filter(function (x) { return x.from <= key; });
  return h.length ? h[h.length - 1] : null;
}
// темп цілі: скільки відкладають за повний місяць і скільки треба, щоб устигнути до терміну (як у картці цілі)
function goalPlan(P) {
  var G = GOAL[P.key], full = P.months.filter(function (m) { return !m.incomplete && m.income > 0; });
  if (!G || !full.length) return null;
  var avgExp = full.reduce(function (s, m) { return s + m.expenses; }, 0) / full.length, have = full.reduce(function (s, m) { return s + m.saved; }, 0);
  var last = full[full.length - 1], li = +last.key.slice(0, 4) * 12 + +last.key.slice(5) - 1, di = G.deadline[0] * 12 + G.deadline[1] - 1;
  var ml = di - li - 1, left = Math.max(0, G.months * avgExp - have);  // ml — місяців після останнього повного до терміну
  return { pace: have / full.length, need: ml > 0 ? left / ml : left, left: left, li: li, di: di, life: have / avgExp, G: G };
}
// ліміти щоденних витрат на місяць j (з повних місяців перед ним):
// • категорії: потреби — звичне (середнє), «бажання» — як у найекономніший місяць; потрібно ≥ 2 місяці з категорією
// • загальний: є ціль — дохід мінус обовʼязкові мінус план відкладень («скільки можна, щоб устигнути»);
//   цілі немає — сума лімітів категорій (категорії з малою кількістю даних — за звичним)
// якщо ліміт від цілі менший за суму категорій — різницю знімаємо лише з «бажань»
function limitsFor(P, j) {
  var base = usualMonths(P, j), M = P.months[j], u = {}, mn = {}, cnt = {}, g = goalPlan(P);
  if (base.length < (g ? 1 : 2)) return null;
  base.forEach(function (m) { var f = fixedCats(m); m.categories.forEach(function (c) { if (f[c.name]) return;
    u[c.name] = (u[c.name] || 0) + c.amount / base.length; mn[c.name] = Math.min(mn[c.name] === undefined ? Infinity : mn[c.name], c.amount); cnt[c.name] = (cnt[c.name] || 0) + 1; }); });
  var L = {}, wants = 0;
  Object.keys(u).forEach(function (n) { if (cnt[n] >= 2) { L[n] = WANTS[n] ? mn[n] : u[n]; if (WANTS[n]) wants += L[n]; } });
  var catSum = Object.keys(u).reduce(function (s, n) { return s + (n in L ? L[n] : u[n]); }, 0), total = catSum, src = "best";
  var pm = M.income > 0 ? M : prevWithIncome(P, j), pl = planFor(P, M.key);
  // скільки відкладати: план людини («Відкладати щомісяця»), а з ціллю без плану — скільки треба, щоб устигнути
  var save = pl ? pl.monthly : g ? g.need : null;
  if (save !== null && pm) {
    var cap = Math.max(0, pm.income - avgOf(base, function (m) { return m.expenses - m.variable; }) - save);
    if (g) { total = cap; src = "goal"; }
    else if (cap < catSum) { total = cap; src = "plan"; }  // без цілі план лише знижує ліміт, якщо він не вміщається
    var cut = catSum - total;
    if (cut > 0 && wants > 0) { var f = Math.max(0, 1 - cut / wants); Object.keys(L).forEach(function (n) { if (WANTS[n]) L[n] *= f; }); }
  }
  return { cat: L, total: total, src: src, save: save, auto: catSum, wants: wants, plan: pl };
}
function renderAnalytics(P) {
  if (month >= P.months.length) month = curMonth(P);
  var seg = function (id, list, cur, set) {
    var box = $(id); box.innerHTML = "";
    list.forEach(function (x) { var b = h("button", "", x[1]); b.type = "button"; b.setAttribute("aria-pressed", x[0] === cur); b.onclick = function () { set(x[0]); render(); }; box.appendChild(b); });
  };
  seg("a-scale", [["m", "Місяці"], ["w", "Тижні"]], ascale, function (v) { ascale = v; });
  // обраний у календарі день: обраний місяць — «на кінець цього дня» (неповний), решта — як є;
  // ліміти, середнє й ціль рахуємо з повних даних (P0), щоб день не змінював самих правил
  var P0 = P, cutA = dayCut(P.months[month]);
  if (cutA) { P = Object.assign({}, P, { months: P.months.slice() }); P.months[month] = monthAt(P0.months[month], cutA); }
  var M = P.months[month], k = mi(M), weeks = ascale === "w", base = usualMonths(P, month);
  var allFull = usualMonths(P0, P0.months.length), dn = M.last_op ? +M.last_op.slice(0, 2) : 0;
  if (cutA && weeks && wsel.day !== cutA) wsel = { key: M.key, i: Math.min(4, Math.floor((+cutA.slice(8) - 1) / 7)), day: cutA };  // у «Тижнях» — одразу тиждень з цим днем
  // місяць обирають тапом по стовпчику; у тижнях — стрілками ‹ ›
  $("a-title").textContent = M.name + " " + M.key.slice(0, 4);
  $("a-prev").hidden = $("a-next").hidden = !weeks;
  $("a-prev").disabled = month <= 0; $("a-next").disabled = month >= P.months.length - 1;
  var pick = function (i) { month = i; render(); };
  var avgTag = function (v) { return v ? "сер. " + nf0.format(Math.round(v)) : ""; };
  // місяці: без осі, доріжок і сум — лише назви; пунктир середнього з підписом «сер. …»
  // місяці — з січня року першого місяця з даними; без даних — порожні пунктирні капсули; сторінки по 5, найновіша праворуч
  var monthBars = function (val, avg, approx, limV) {
    var vs = P.months.map(val), ax = niceAxis(Math.max.apply(null, vs.map(function (v) { return v || 0; }).concat([avg || 0, limV || 0, 1])));
    var byKey = {}, keys = [], lastK = P.months[P.months.length - 1].key;
    P.months.forEach(function (m, j) { byKey[m.key] = j; });
    for (var t = +P.months[0].key.slice(0, 4) * 12; ; t++) { var kk = Math.floor(t / 12) + "-" + ("0" + (t % 12 + 1)).slice(-2); keys.push(kk); if (kk === lastK || keys.length > 240) break; }
    var sel = keys.indexOf(M.key), per = 5, g = { n: keys.length, per: per }, maxP = Math.ceil(keys.length / per) - 1;
    if (mpage.who !== person) mpage = { who: person, p: 0 };
    mpage.p = Math.min(mpage.p, maxP);
    var shows = function (p) { var s = pageStart(g, p); return sel >= s && sel < s + per; };
    if (!shows(mpage.p)) for (var p = 0; p <= maxP; p++) if (shows(p)) { mpage.p = p; break; }  // обраний місяць — завжди на видимій сторінці
    capsules("mbars", "mticks", keys.map(function (kk) {
      var j = byKey[kk];
      var short = MONTHS_FULL[+kk.slice(5) - 1].slice(0, 3);  // 5 стовпчиків на сторінці — короткі назви: Сер, Вер, Жов
      if (j === undefined) return { v: 0, label: short, aria: MONTHS_FULL[+kk.slice(5) - 1], empty: true, sub: "—", nopick: true };
      var m = P.months[j], v = vs[j], none = v === null || !m.ops.length;
      return { v: none ? 0 : Math.max(0, v), label: short, aria: m.name, part: m.incomplete, empty: none, sub: none ? "—" : (approx && m.incomplete ? "≈ " : "") + nf0.format(v) + " зл" };
    }), ax.max, sel, function (i) { month = byKey[keys[i]]; render(); }, ax.n, avg || 0, { avgLabel: avgTag(avg), lim: limV || 0, paged: { per: per, page: mpage.p } });
  };
  var note = "";
  {
    var c, extra = cutA ? dayChip(cutA, k, "a-day") : M.incomplete ? "дані до " + M.last_op : "", LM = lim ? limitsFor(P0, month) : null;
    var dimM = new Date(+M.key.slice(0, 4), +M.key.slice(5), 0).getDate(), G = GOAL[P.key];
    // з лімітом: бейдж «лишилось …» / «+… понад ліміт»; рядком — ліміт (і скільки на день до кінця місяця), нижче — звідки він
    var vsLim = function (spent, l, open, per, perDay) {
      if (!LM) return { badge: "", text: "Ліміт зʼявиться, коли буде два повні місяці" };
      var d = l - spent, dl = dimM - dn;
      return { badge: '<span class="bd ' + (d >= 0 ? "good" : "bad") + '">' + (d < 0 ? "+" + zr(-d) + " понад ліміт" : open ? "лишилось " + zr(d) : "вклались") + "</span>",
        text: (open ? "Ліміт " + per + " — " + zr(l) : "Ліміт був " + zr(l)) + (perDay && open && d > 0 && dl > 0 ? ", до кінця місяця ≈ " + zr(d / dl) + " на день" : "") };
    };
    if (!weeks) {
      var avgV = avgOf(allFull, function (m) { return m.variable; });
      // неповний місяць порівнюємо зі звичним на те саме число — і на графіку це риска, а не середнє за місяць
      var uDay = M.incomplete ? avgOf(base, function (m) { return m.ops.reduce(function (s, o) { return s + (!o.income && !o.fixed && dayOf(o) <= dn ? o.amount : 0); }, 0); }) : null;
      monthBars(function (m) { return m.variable; }, avgV, false, LM ? LM.total : 0);
      c = lim ? vsLim(M.variable, LM && LM.total, M.incomplete, "на місяць", true)
        : M.incomplete ? vsUsual(M.variable, uDay, false, " на " + dn + "-те") : vsUsual(M.variable, avgOf(base, function (m) { return m.variable; }), false, " за місяць");
    } else {
      var y = +M.key.slice(0, 4), mo = +M.key.slice(5), dim = new Date(y, mo, 0).getDate(), last = M.incomplete ? dn : dim;
      var bounds = [[1, 7], [8, 14], [15, 21], [22, 28], [29, dim]].filter(function (b) { return b[0] <= dim; });
      var w = bounds.map(function (b) { return M.ops.reduce(function (s, o) { var d = dayOf(o); return s + (!o.income && !o.fixed && d >= b[0] && d <= b[1] ? o.amount : 0); }, 0); });
      var uw = avgOf(base.length ? base : allFull, function (m) { var yy = +m.key.slice(0, 4), mm = +m.key.slice(5); return m.variable * 7 / new Date(yy, mm, 0).getDate(); });
      var lw = LM ? LM.total * 7 / dim : 0;  // ліміт тижня — частка місячного
      var top = -1, axW; w.forEach(function (v, i) { if (bounds[i][0] <= last && (top < 0 || v > w[top])) top = i; });
      if (wsel.key !== M.key || wsel.i >= bounds.length || (wsel.i >= 0 && bounds[wsel.i][0] > last)) wsel = { key: M.key, i: -1 };
      var wi = wsel.i;
      // тап по тижню — його підсумок і категорії, як у місяцях; повторний тап — назад до місяця
      var pickW = function (i) { if (bounds[i][0] > last) return; wsel = { key: M.key, i: wsel.i === i ? -1 : i, day: wsel.day }; render(); };
      capsules("mbars", "mticks", bounds.map(function (b, i) { var future = b[0] > last; return { v: future ? 0 : w[i], label: b[0] + "–" + b[1], empty: future, sub: future ? "—" : nf0.format(w[i]) + " зл" }; }),
        (axW = niceAxis(Math.max.apply(null, w.concat([uw || 0, lw, 1])))).max, wi, pickW, axW.n, uw || 0, { avgLabel: avgTag(uw), lim: lw });
      if (wi >= 0) {
        var B = bounds[wi], upTo = Math.min(B[1], last), days = upTo - B[0] + 1, wpart = upTo < B[1], wt = B[0] + "–" + B[1] + " " + GEN[k];
        c = lim ? vsLim(w[wi], LM && LM.total * (B[1] - B[0] + 1) / dim, wpart, "на тиждень")
          : vsUsual(w[wi], uw === null ? null : uw * days / 7, false, wpart ? " на " + upTo + "-те" : " за тиждень");
        var wc = {};
        M.ops.forEach(function (o) { var d = dayOf(o); if (!o.income && d >= B[0] && d <= B[1]) wc[o.cat] = (wc[o.cat] || 0) + o.amount; });
        var wcats = Object.keys(wc).map(function (n) { return { name: n, amount: wc[n] }; }).sort(function (a, b) { return b.amount - a.amount; });
        $("a-sum").innerHTML = sumHTML("Щоденні витрати", wt + (cutA ? " " + dayChip(cutA, k, "a-day") : ""), zr(w[wi]), c);
        renderWhere(P, M, { cats: wcats, k: (B[1] - B[0] + 1) / dim, title: wt, partial: wpart }, LM);
      } else if (lim) {
        c = vsLim(M.variable, LM && LM.total, M.incomplete, "на місяць", true);
      } else {
        c = top >= 0 ? vsUsual(w[top], uw, false, "") : { badge: "", text: "" };
        if (top >= 0) c.text = "Найдорожчий тиждень — " + bounds[top][0] + "–" + bounds[top][1];
      }
    }
    if (!weeks || wi < 0) { $("a-sum").innerHTML = sumHTML("Щоденні витрати", extra, zr(M.variable), c); renderWhere(P, M, null, LM); }
    // перемикач ліміту — у шапці картки праворуч (ліворуч «Місяці / Тижні»); вмикає ліміти і на графіку, і в «Витрати за …»
    var tg = h("button", "limt", 'Ліміт<span class="sw" aria-hidden="true"><i></i></span>');  // тугл-перемикач: сірий → синій
    tg.type = "button"; tg.setAttribute("role", "switch"); tg.setAttribute("aria-checked", String(lim)); tg.setAttribute("aria-pressed", String(lim));
    // спершу їде кулька, а графік і списки перемальовуємо, коли вона доїхала
    tg.onclick = function () { lim = !lim; try { localStorage.setItem("lim", lim ? "1" : "0"); } catch (e) {} tg.setAttribute("aria-checked", String(lim)); tg.setAttribute("aria-pressed", String(lim)); setTimeout(render, 280); };
    if ($("a-day")) $("a-day").onclick = function () { selDay = null; render(); };
    var ach = $("a-scale").parentNode, old = ach.querySelector(".limt"); if (old) old.remove();
    ach.appendChild(tg);  // праворуч від «Місяці / Тижні»
  }
  // висновок «Більше, ніж зазвичай…» — рядком одразу під цифрою (у sumHTML), поруч із бейджем
  $("t-avg").innerHTML = note;
}
// «Куди пішли гроші»: усі витрати місяця — спершу щоденні (вони змінюються) зі «зазвичай», внизу обовʼязкові, згорнуті
// W — обраний тиждень: { cats, k — частка звичного місяця, title, partial }; без W — увесь місяць
// LM — ліміти (limitsFor): тоді в щоденних «лишилось X з Y», смужка — від ліміту, сортування — за залишком
function renderWhere(P, M, W, LM) {
  var box = $("an-where"), j = P.months.indexOf(M), base = usualMonths(P, j), u = {}, fx = fixedCats(M);
  var all = W ? W.cats : M.categories, f = W ? W.k : 1, part = W ? W.partial : M.incomplete, LL = LM ? LM.cat : null;
  var limOf = function (c) { return LL && !fx[c.name] && c.name in LL ? LL[c.name] * f : null; };
  box.hidden = false;
  base.forEach(function (m) { m.categories.forEach(function (c) { u[c.name] = (u[c.name] || 0) + c.amount / base.length * f; }); });
  var daily = all.filter(function (c) { return !fx[c.name]; }), oblig = all.filter(function (c) { return fx[c.name]; });
  var sum = function (a) { return a.reduce(function (s, c) { return s + c.amount; }, 0); }, tot = W ? sum(all) : M.expenses;
  var row = function (c, i, st) {
    var was = u[c.name], d = was === undefined ? null : c.amount - was;
    var show = d !== null && (part ? d >= Math.max(30, was * .15) : Math.abs(d) >= Math.max(30, was * .15));
    var sub = !base.length ? "" : was === undefined ? "минулими місяцями не було" : show || part ? "зазвичай ≈ " + zr(was) : "як зазвичай";
    var p = Math.max(tot ? c.amount / tot * 100 : 0, 1), col = bright(c.name), bdg = null;
    if (LL && !fx[c.name]) {
      var l = limOf(c);
      if (l === null) { sub = "мало даних для ліміту"; bdg = ""; p = 1; }
      else {
        var left = l - c.amount;
        sub = left >= 0 && part ? "лишилось " + nf0.format(Math.round(left)) + " з " + zr(l) : "ліміт " + zr(l);
        bdg = left < 0 ? '<span class="bd bad">+' + zr(-left) + "</span>" : part ? "" : '<span class="bd good">вклались</span>';
        p = Math.max(l ? Math.min(100, c.amount / l * 100) : 100, 1);  // смужка — від ліміту категорії
        col = left < 0 ? "var(--bad)" : "var(--blue)";  // з лімітом колір смужки — стан (у межах / понад), а не категорія
      }
    }
    return '<a class="crow wrow" href="#journal" data-cat="' + esc(c.name) + '"' + (i >= 0 ? ' style="--i:' + i + '"' : "") + ">" + cic(c.name) +
      // бейдж різниці — у підрядку одразу після «зазвичай …»: суми праворуч стоять рівним стовпчиком
      '<span class="nm"><span>' + esc(c.name) + "</span>" + (sub ? "<small>" + sub + (bdg !== null ? bdg : show ?'<span class="bd ' + (d > 0 ? "bad" : "good") + '">' + (d > 0 ? "+" : "−") + nf0.format(Math.abs(Math.round(d))) + " зл</span>" : "") + "</small>" : "") +
      '</span><span class="am">' + zl(c.amount) + '</span><span class="ir">' + CHR + "</span>" +
      '<div class="bar6"><b style="background:' + col + (st ? ";width:" + p + '%"' : '" data-to="' + p + '%"') + "></b></div></a>";
  };
  var chev = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  var more = function (label, right) {
    return '<button type="button" class="more" aria-expanded="false" data-label="' + label + '"><span class="more-l">' + label + '</span><span class="more-r">' + (right ? '<span class="more-sum">' + right + "</span>" : "") + chev + "</span></button>";
  };
  var cats = function (q) { return q + " " + plural(q, "категорія", "категорії", "категорій"); };
  // з лімітами — угорі ті, де лишилось найменше (перевищені першими); без ліміту — у кінці; кнопка — назад до «за сумою»
  if (LL && wsort === "left") daily.sort(function (a, b) { var la = limOf(a), lb = limOf(b); return (la === null ? Infinity : la - a.amount) - (lb === null ? Infinity : lb - b.amount); });
  var html = '<div class="ch"><h2>Витрати ' + (W ? "за " + W.title : dayCut(M) ? "на " + (+selDay.slice(8)) + " " + GEN[mi(M)] : "за " + M.name.toLowerCase()) + "</h2>" +
    (LL ? '<button type="button" class="wsort" id="w-sort">' + svgI("currentColor", '<path d="M7 4v16M3.5 16.5 7 20l3.5-3.5M17 20V4M13.5 7.5 17 4l3.5 3.5"/>', 14) + (wsort === "left" ? "За залишком" : "За сумою") + "</button>" : "") + "</div>";
  // обидва розділи однаково: кілька найбільших, решта під «Ще N категорій»; 3 і менше — усі одразу, без кнопки
  var section = function (title, list, show) {
    if (!list.length) return;
    html += '<div class="dh"><b>' + title + "</b><span>" + zl(sum(list)) + "</span></div>";
    var top = list.length <= 3 || list.length === show + 1 ? list.length : show, rest = list.slice(top);  // одну категорію під «Ще» не ховаємо
    list.slice(0, top).forEach(function (c) { html += row(c, -1, false); });
    if (rest.length) html += '<div class="more-rows"><div>' + rest.map(function (c, i) { return row(c, i, true); }).join("") + "</div></div>" + more("Ще " + cats(rest.length), zl(sum(rest)));
  };
  section("Щоденні", daily, 4);
  section("Обовʼязкові", oblig, 2);
  // замість окремої вкладки «Лишилось» — підсумковий рядок місяця: скільки лишилось з доходу і як це до звичного
  if (!W && M.income > 0) {
    var us = avgOf(base, function (m) { return m.saved; }), ds = us === null ? 0 : M.saved - us;
    html += '<div class="lrow"><div><b>Лишилось з доходу</b><small>' + nf0.format(Math.round(M.saved / M.income * 100)) + " % з " + zr(M.income) +
      (M.incomplete ? ", поки що" : us !== null && Math.abs(ds) >= Math.max(30, us * .05) ? ", на " + zr(Math.abs(ds)) + (ds > 0 ? " більше" : " менше") + ", ніж зазвичай" : us !== null ? ", як зазвичай" : "") +
      "</small></div><span>" + zl(M.saved) + "</span></div>";
  }
  box.innerHTML = html;
  if (LL) $("w-sort").onclick = function () { wsort = wsort === "left" ? "sum" : "left"; render(); };
  box.querySelectorAll(".more").forEach(function (b) {
    b.onclick = function () {
      var open = b.getAttribute("aria-expanded") !== "true"; b.setAttribute("aria-expanded", open); b.previousElementSibling.classList.toggle("open", open);
      b.querySelector(".more-l").textContent = open ? "Згорнути" : b.dataset.label;
    };
  });
  box.querySelectorAll("[data-cat]").forEach(function (a) { a.onclick = function () { filter = "all"; catF = a.dataset.cat; selDay = null; }; });
  grow("#an-where .bar6 b[data-to]", "width");
}

// ---------- журнал ----------
var dayFmt = new Intl.DateTimeFormat("uk-UA", { day: "numeric", month: "long", weekday: "long" });
var FILTERS = [["all", "Усі"], ["exp", "Витрати"], ["inc", "Дохід"], ["fixed", "Обовʼязкові"], ["cash", "Готівка"]];
function renderJournal(P) {
  var M = P.months[month], fb = $("j-filters"); fb.innerHTML = "";
  // місяць обирають у шапці (той самий календар, тап по дню — прокрутка до нього); категорія — першим чипом-дропдауном: «Категорія ⌄» або обрана (синя); «Усі категорії» в меню знімає фільтр
  var cb = h("button", "mchip catchip", esc(catF || "Категорія") + '<svg class="ico16 chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>');
  cb.type = "button"; cb.id = "jc-btn"; cb.setAttribute("aria-haspopup", "listbox"); cb.setAttribute("aria-expanded", String(!$("cat-menu").hidden));
  if (catF) cb.setAttribute("aria-pressed", "true");
  cb.onclick = function () { catOpen($("cat-menu").hidden); };
  // обраний у календарі день (без пошуку): список і плашки — на кінець цього дня; першим чипом «на 7 жовтня ✕»
  var cutJ = jq.trim() ? null : dayCut(M);
  if (cutJ) { var dc = h("span", "", dayChip(cutJ, mi(M), "j-day")).firstChild; dc.onclick = function () { selDay = null; render(); }; fb.appendChild(dc); }
  fb.appendChild(cb);
  FILTERS.forEach(function (f) {
    var b = h("button", "", f[1]); b.type = "button"; b.setAttribute("aria-pressed", f[0] === filter);
    b.onclick = function () { filter = f[0]; render(); };
    fb.appendChild(b);
  });
  // пошук шукає в усіх місяцях: за назвою, категорією або сумою («72,29»); без запиту — лише обраний місяць
  var q = jq.trim().toLowerCase(), qn = q.replace(",", ".");
  var src = q && !jqMonth ? [].concat.apply([], P.months.slice().reverse().map(function (m) { return m.ops; })) : M.ops;
  var ops = src.filter(function (o) {
    if (cutJ && o.day > cutJ) return false;
    if (catF && o.cat !== catF) return false;
    if (q && o.title.toLowerCase().indexOf(q) < 0 && o.cat.toLowerCase().indexOf(q) < 0 && !(/\d/.test(qn) && o.amount.toFixed(2).indexOf(qn) >= 0)) return false;
    return filter === "all" || (filter === "exp" && !o.income) || (filter === "inc" && o.income) || (filter === "fixed" && o.fixed) || (filter === "cash" && o.cash);
  });
  var out = ops.reduce(function (s, o) { return s + (o.income ? 0 : o.amount); }, 0);
  // підсумок плашками — лише коли щось відібрано (пошук, категорія, фільтр); для «Усі» за місяць він дублює Головну й Аналітику
  var inc = ops.reduce(function (s, o) { return s + (o.income ? o.amount : 0); }, 0), isInc = filter === "inc";
  $("j-sum").hidden = !(q || catF || cutJ || filter !== "all");
  $("j-sum").innerHTML = '<div class="card"><span class="ji" style="background:var(--blue-soft);color:var(--blue)" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 2.5h12a1 1 0 0 1 1 1v17.2a.6.6 0 0 1-.95.5L15.5 19.4l-2.9 1.9a1 1 0 0 1-1.1 0l-2.9-1.9-2.55 1.8A.6.6 0 0 1 5 20.7V3.5a1 1 0 0 1 1-1z"/><path fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" d="M9 8h6M9 12h6M9 15.5h3"/></svg></span>' +
    "<div><span>Операцій" + "</span><b>" + ops.length + "</b></div></div>" +
    '<div class="card"><span class="ji" style="background:var(' + (isInc ? "--good-bg" : "--bad-bg") + ");color:var(" + (isInc ? "--good" : "--bad") + ')" aria-hidden="true"><span class="glyph">' + (isInc ? "↙" : "↗") + "</span></span>" +
    "<div><span>" + (isInc ? "Дохід" : "Витрати") + "</span><b>" + zl(isInc ? inc : out) + "</b></div></div>";
  $("j-cash").hidden = !P.cash;
  var html = "", day = null, buf = "";
  var flush = function () { if (day) html += buf + "</div></section>"; };
  ops.forEach(function (o) {
    if (o.day !== day) {
      flush(); day = o.day;
      // сума дня — лише якщо операцій у дні хоча б дві (для однієї вона повторює суму рядка)
      var dayOps = ops.filter(function (x) { return x.day === day; });
      var e = dayOps.length > 1 ? dayOps.filter(function (x) { return !x.income; }).reduce(function (s, x) { return s + x.amount; }, 0) : 0;
      var d = dayFmt.format(new Date(day + "T12:00:00")).split(", ");
      buf = '<section class="day" data-day="' + day + '"><div class="dh"><b>' + esc(d[1] || d[0]) + (d[1] ? ", <span>" + esc(d[0]) + "</span>" : "") + "</b><span>" + (e ? "−" + nf2.format(e) + " зл" : "") +
        '</span></div><div class="card list">';
    }
    buf += swipeWrap(o, opRow(o, false, false));
  });
  flush();
  $("j-list").innerHTML = html || '<section class="card empty">Операцій не знайдено</section>';
  $("j-list").querySelectorAll(".swipe").forEach(function (sw) { swipeRow(sw, P, "#journal"); });
  $("fava").href = FAVA[P.key] + "journal/";
}

// свайп рядка вліво відкриває кнопку «Видалити» справа; відпустили не дотягнувши — рядок повертається
var SW_OPEN = 96;  // ширина .swdel (88) − її вихід за край (8) + повітря до суми (16)
function swipeWrap(o, row) {
  return o.cash && o.line ? '<div class="swipe" data-line="' + o.line + '" data-day="' + o.day + '" data-title="' + esc(o.title) + '" data-amount="' + o.amount + '" data-cat="' + esc(o.cat.replace(/ /g, "-")) + '">' +
    '<button type="button" class="swdel" tabindex="-1" aria-label="Видалити запис «' + esc(o.title) + '»">' + TRASH + "<span>Видалити</span></button>" + row + "</div>" : row;
}
function swipeRow(sw, P, back) {
  var op = sw.querySelector(".op"), x0 = null, y0 = 0, dx = 0, base = 0, horiz = null;
  // x — наскільки рядок зсунутий уліво (додатне число)
  var set = function (x, anim) { op.style.transition = anim ? "" : "none"; op.style.transform = x ? "translateX(" + -x + "px)" : ""; sw.classList.toggle("on", x > 0); };
  op.addEventListener("pointerdown", function (e) { x0 = e.clientX; y0 = e.clientY; base = sw.classList.contains("open") ? SW_OPEN : 0; dx = 0; horiz = null; });
  op.addEventListener("pointermove", function (e) {
    if (x0 === null) return;
    var mx = e.clientX - x0, my = e.clientY - y0;
    if (horiz === null && (Math.abs(mx) > 8 || Math.abs(my) > 8)) horiz = Math.abs(mx) > Math.abs(my);
    if (!horiz) return;
    dx = -mx; var x = Math.max(0, base + dx); set(x > SW_OPEN ? SW_OPEN + (x - SW_OPEN) * .25 : x, false);
  });
  var end = function () {
    if (x0 === null) return; x0 = null;
    if (!horiz) { if (sw.classList.contains("open") ) { sw.classList.remove("open"); set(0, true); } return; }
    var open = base + dx > SW_OPEN * .55;
    document.querySelectorAll(".swipe.open").forEach(function (o) { if (o !== sw) { o.classList.remove("open"); o.querySelector(".op").style.transform = ""; o.classList.remove("on"); } });
    sw.classList.toggle("open", open); set(open ? SW_OPEN : 0, true);
  };
  op.addEventListener("pointerup", end); op.addEventListener("pointercancel", end);
  sw.querySelector(".swdel").onclick = function () {
    sw.style.height = sw.offsetHeight + "px"; void sw.offsetHeight;
    sw.classList.add("gone"); sw.style.height = "0px"; sw.style.opacity = "0";
    var undo = { amount: sw.dataset.amount, category: sw.dataset.cat, title: sw.dataset.title, date: sw.dataset.day };
    setTimeout(function () { delCash(P, +sw.dataset.line, sw.dataset.day, sw.dataset.title, back, undo); }, 620);
  };
}
// видалення одного готівкового запису; після — перезавантаження зі свіжими даними і тостом
function delCash(P, line, day, title, back, undo, cancel, doneMsg) {
  fetch("api/cash/delete", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
    body: JSON.stringify({ person: P.key, line: line, date: day, title: title }) })
    .then(function (r) { return r.json().catch(function () { return { ok: false, error: "Сервер не відповів" }; }); })
    .then(function (res) {
      if (!res.ok) throw new Error(res.error || "Не вдалося видалити");
      // оновлюємо дані на місці: той самий екран, місяць і прокрутка, лише без видаленого запису
      var mk = D.people[person].months[month] && D.people[person].months[month].key;
      D = res.data;
      var ms = D.people[person].months.map(function (m) { return m.key; });
      month = ms.indexOf(mk) >= 0 ? ms.indexOf(mk) : curMonth(D.people[person]);
      saved = null; reportKey = null;
      justAdded = justAdded.filter(function (s) { return !(s.day === day && s.title === title); });
      if (back && location.hash !== back) location.hash = back; else render();
      // «Скасувати» записує той самий запис назад (та сама сума, категорія, опис і дата)
      toast(okMsg(doneMsg || (cancel ? "Скасовано" : "Запис видалено")), undo ? ["Скасувати", function () { restoreCash(P, undo); }] : null);
    })
    .catch(function (err) { toast(err.message || "Немає звʼязку, спробуйте ще раз"); });
}
// тост успіху у два рядки: що сталося + сума й категорія
function okMsg(title, P, amount, cat) {
  var a = P && P.accounts.filter(function (x) { return x.key === cat; })[0];
  return OKI + '<span class="tx"><b>' + title + "</b>" + (a ? "<small>" + zl(+amount) + " в «" + esc(a.name) + "»</small>" : "") + "</span>";
}
var INFO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><circle cx="12" cy="7.8" r=".6" fill="currentColor"/></svg>';
var OKI = '<i aria-hidden="true"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="#fff" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="m5.5 12.5 4 4 9-9"/></svg></i>';
function restoreCash(P, u) {
  fetch("api/cash", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
    body: JSON.stringify({ person: P.key, amount: u.amount, category: u.category, title: u.title, date: u.date }) })
    .then(function (r) { return r.json().catch(function () { return { ok: false, error: "Сервер не відповів" }; }); })
    .then(function (res) {
      if (!res.ok) throw new Error(res.error || "Не вдалося відновити");
      D = res.data; saved = { amount: +u.amount, day: res.date, line: res.line, title: res.title }; markFresh(saved); reportKey = null; render();
      toast(okMsg("Скасовано"));
    })
    .catch(function (err) { toast(err.message || "Немає звʼязку, спробуйте ще раз"); });
}
// тост зі «Скасувати» живе 6 с: смужка внизу показує, скільки лишилось; поки палець чи курсор на тості — час на паузі
function toast(html, action) {
  document.querySelectorAll(".toast").forEach(function (x) { x.remove(); });
  var t = h("div", "toast", html), left = action ? 6000 : 3200, t0 = 0, timer = null; t.setAttribute("role", "status");
  var close = function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 500); };
  var run = function () { t0 = Date.now(); t.classList.remove("hold"); timer = setTimeout(close, left); };
  var hold = function () { if (t.classList.contains("hold") || t.classList.contains("out")) return; clearTimeout(timer); left -= Date.now() - t0; t.classList.add("hold"); };
  if (action) {
    var b = h("button", "", action[0]); b.type = "button"; b.onclick = action[1]; t.appendChild(b);
    t.appendChild(h("span", "tbar", "<b></b>"));
    t.addEventListener("pointerenter", hold); t.addEventListener("pointerdown", hold);
    ["pointerleave", "pointercancel"].forEach(function (e) { t.addEventListener(e, function () { if (t.classList.contains("hold")) run(); }); });
  }
  document.body.appendChild(t);
  run();
}

// ---------- шторка «Витрата готівкою» ----------
var pick = null, dpick = 1;  // 1 — «Сьогодні» (порядок: Вчора, Сьогодні, Інша дата)
function amountValue() { var v = parseFloat($("a-amount").value.replace(/\s/g, "").replace(",", ".")); return isFinite(v) ? v : 0; }
function check() { var a = amountValue(); $("a-save").disabled = !(a > 0 && pick); $("a-save").textContent = "Зберегти" + (a > 0 ? " " + zl(a) : ""); }
var wd = new Intl.DateTimeFormat("uk-UA", { weekday: "short" }), dm = new Intl.DateTimeFormat("uk-UA", { day: "2-digit", month: "2-digit" });
function renderDates() {
  var t = new Date(), y = new Date(t.getTime() - 864e5), box = $("a-dates"); box.innerHTML = "";
  var v = $("a-date").value, other = v && v !== iso(t) && v !== iso(y);
  [["Вчора", y], ["Сьогодні", t], ["Інша дата", null]].forEach(function (x, i) {
    var b;
    if (x[1]) b = h("button", "", x[0] + "<small>" + wd.format(x[1]) + ", " + dm.format(x[1]) + "</small>");
    else b = h("button", other ? "" : "other", other ? "Інша дата<small>" + v.slice(8) + "." + v.slice(5, 7) + "</small>" : CAL.replace('stroke="#6B7180"', 'stroke="currentColor"') + "Інша дата");
    var calOpen = !$("a-cal").hidden;
    b.type = "button"; b.setAttribute("aria-pressed", calOpen || other ? i === 2 : i === dpick);
    b.onclick = function () {
      if (x[1]) { dpick = i; $("a-date").value = iso(x[1]); $("a-cal").hidden = true; renderDates(); }
      else { var c = $("a-cal"); c.hidden = !c.hidden; if (!c.hidden) { aCal = ($("a-date").value || iso(new Date())).slice(0, 7); renderACal(); } renderDates(); }
    };
    box.appendChild(b);
  });
}
$("a-date").addEventListener("change", function () { dpick = -1; renderDates(); });
// календар «Інша дата»: як у хедері, але для вибору дня витрати (до сьогодні включно, рік назад)
var aCal = null;
function renderACal() {
  var y = +aCal.slice(0, 4), mo = +aCal.slice(5) - 1, now = new Date(), today = iso(now), sel = $("a-date").value;
  var minKey = iso(new Date(now.getFullYear() - 1, now.getMonth(), 1)).slice(0, 7), maxKey = today.slice(0, 7);
  $("a-cal-title").textContent = MONTHS_FULL[mo] + " " + y;
  $("a-cal-prev").disabled = aCal <= minKey; $("a-cal-next").disabled = aCal >= maxKey;
  var first = new Date(y, mo, 1), dim = new Date(y, mo + 1, 0).getDate(), start = new Date(y, mo, 1 - WD0(first));
  var grid = $("a-cal-grid"); grid.innerHTML = "";
  for (var c = 0; c < Math.ceil((WD0(first) + dim) / 7) * 7; c++) {
    var d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + c), key = iso(d), inMonth = d.getMonth() === mo;
    var b = h("button", key === today ? "today" : "", inMonth ? String(d.getDate()) : "");
    b.type = "button"; b.disabled = !inMonth || key > today;
    b.setAttribute("aria-pressed", key === sel);
    if (inMonth) b.setAttribute("aria-label", d.getDate() + " " + GEN[mo] + " " + y + (key > today ? ", ще не настав" : ""));
    if (!b.disabled) (function (key) { b.onclick = function () { $("a-date").value = key; dpick = -1; $("a-cal").hidden = true; renderDates(); }; })(key);
    grid.appendChild(b);
  }
}
function shiftACal(n) { var y = +aCal.slice(0, 4), mo = +aCal.slice(5) - 1 + n; aCal = iso(new Date(y, mo, 1)).slice(0, 7); renderACal(); }
$("a-cal-prev").onclick = function () { shiftACal(-1); };
$("a-cal-next").onclick = function () { shiftACal(1); };
function renderAdd(P) {
  if (!$("a-date").value) { $("a-date").value = iso(new Date()); dpick = 1; }
  $("a-date").max = iso(new Date());
  var box = $("a-cats"); box.innerHTML = "";
  P.accounts.forEach(function (a) {
    var b = h("button", "", esc(a.name)); b.type = "button"; b.setAttribute("aria-pressed", a.key === pick);
    b.onclick = function () { pick = a.key; renderAdd(P); check(); };
    box.appendChild(b);
  });
  renderDates(); check();
}
$("a-amount").addEventListener("input", function () {
  this.value = this.value.replace(/[^0-9.,]/g, "");
  fitAmount(); check();
});
// поле росте рівно на ширину набраного (або плейсхолдера «0») плюс місце для курсора
function fitAmount() {
  var a = $("a-amount"), m = fitAmount.m || (fitAmount.m = document.body.appendChild(h("span", "amount-mirror")));
  m.textContent = a.value || a.placeholder;
  a.style.width = Math.ceil(m.getBoundingClientRect().width) + 4 + "px";
}
if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAmount);
document.querySelector(".amount").addEventListener("click", function () { $("a-amount").focus(); });
$("v-add").addEventListener("submit", function (e) {
  e.preventDefault();
  var P = D.people[person], btn = $("a-save");
  if (btn.disabled) return;
  btn.disabled = true; btn.textContent = "Зберігаю…"; $("a-msg").textContent = "";
  fetch("api/cash", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
    body: JSON.stringify({ person: P.key, amount: $("a-amount").value, category: pick, title: $("a-title").value, date: $("a-date").value }) })
    .then(function (r) { return r.json().catch(function () { return { ok: false, error: "Сервер не відповів" }; }); })
    .then(function (res) {
      if (!res.ok) throw new Error(res.error || "Не вдалося зберегти");
      // оновлюємо звіт на місці: закриваємо шторку, показуємо місяць запису й підсвічуємо його
      D = res.data;
      var ms = D.people[person].months.map(function (m) { return m.key; });
      if (ms.indexOf(res.month) >= 0) month = ms.indexOf(res.month);
      saved = { amount: amountValue(), day: res.date, line: res.line, title: res.title }; markFresh(saved);
      reportKey = null; btn.disabled = false;
      // «Змінити»: новий запис уже додано — тепер прибираємо старий (він вище у файлі, його рядок не зсунувся)
      if (editing) {
        var ed = editing; editing = null;
        $("a-title").value = ""; $("a-date").value = iso(new Date()); dpick = 1; $("a-cal").hidden = true; pick = null;
        $("a-amount").value = ""; $("a-amount").dispatchEvent(new Event("input", { bubbles: true }));
        location.replace("#" + sheetBase);
        delCash(D.people[person], ed.line, ed.day, ed.title, null, null, false, "Запис змінено");
        return;
      }
      // форма для наступного запису — з чистого аркуша: без суми, категорії й опису, дата «Сьогодні»
      var cat = pick; pick = null; $("a-title").value = ""; $("a-date").value = iso(new Date()); dpick = 1; $("a-cal").hidden = true;
      $("a-amount").value = ""; $("a-amount").dispatchEvent(new Event("input", { bubbles: true })); renderAdd(P);
      location.replace("#" + sheetBase);
      toast(okMsg("Готівку додано", D.people[person], saved.amount, cat), ["Скасувати", function () {
        delCash(D.people[person], saved.line, saved.day, saved.title, "#report", null, true);
      }]);
    })
    .catch(function (err) { $("a-msg").textContent = err.message || "Немає звʼязку, спробуйте ще раз"; check(); });
});
function sheet(open, P) {
  var s = $("v-add"), bg = $("sheet-bg"), was = !s.hidden;
  if (open) {
    renderAdd(P);
    if (!was) {
      s.hidden = bg.hidden = false; document.body.style.overflow = "hidden";
      $("a-amount").value = ""; $("a-cal").hidden = true;
      // «Змінити» з картки операції: поля вже заповнені цим записом
      if (editing) {
        var t0 = iso(new Date()), y0 = iso(new Date(Date.now() - 864e5));
        $("a-amount").value = String(editing.amount).replace(".", ","); $("a-title").value = editing.title; $("a-date").value = editing.day;
        dpick = editing.day === t0 ? 1 : editing.day === y0 ? 0 : -1; pick = editing.cat; renderAdd(P);
      }
      $("sheet-title").textContent = editing ? "Змінити запис" : "Витрата готівкою";
      fitAmount(); check();
      requestAnimationFrame(function () { requestAnimationFrame(function () { s.classList.add("open"); bg.classList.add("open"); }); });
      $("a-amount").focus({ preventScroll: true });
    }
  } else if (was) {
    s.classList.remove("open"); bg.classList.remove("open"); document.body.style.overflow = "";
    setTimeout(function () { if (!s.classList.contains("open")) s.hidden = bg.hidden = true; }, 300);
  }
}
var sheetFromApp = false, sheetBase = "report";  // sheetBase — екран під шторкою: Головна або Журнал (кнопка «Готівка» там)
function closeSheet() { if (history.length > 1 && sheetFromApp) history.back(); else location.replace("#" + sheetBase); }
$("sheet-close").onclick = closeSheet;
$("sheet-bg").onclick = closeSheet;
document.addEventListener("keydown", function (e) { if (e.key === "Escape" && view() === "add") closeSheet(); });
(function () {
  var y0 = null, s = $("v-add");
  $("sheet-head").addEventListener("pointerdown", function (e) { y0 = e.clientY; s.style.transition = "none"; });
  window.addEventListener("pointermove", function (e) { if (y0 !== null) s.style.transform = "translateY(" + Math.max(0, e.clientY - y0) + "px)"; });
  window.addEventListener("pointerup", function (e) {
    if (y0 === null) return;
    var dy = e.clientY - y0; y0 = null; s.style.transition = ""; s.style.transform = "";
    if (dy > 80) closeSheet();
  });
})();
// ---------- картка операції: деталі; для готівки — «Змінити» і «Видалити» ----------
var editing = null;  // готівковий запис, який змінюємо через шторку «Витрата готівкою»
var opFmt = new Intl.DateTimeFormat("uk-UA", { day: "numeric", month: "long", weekday: "long" });
function opCard(o) {
  var P = D.people[person], acc = P.accounts.filter(function (a) { return a.key === o.cat.replace(/ /g, "-") || a.name === o.cat; })[0];
  var d = opFmt.format(new Date(o.day + "T12:00:00")).split(", ");
  var row = function (k, v) { return '<div class="oprow"><span>' + k + "</span><b>" + v + "</b></div>"; };
  $("op-title").textContent = nice(o.title) || (o.income ? "Дохід" : o.cat);
  $("op-body").innerHTML = '<div class="opbig' + (o.income ? " inc" : "") + '">' + (o.income ? "+" : "−") + zl(o.amount) + "</div>" +
    '<div class="oplist">' + row("Категорія", '<span class="opcat">' + cic(o.income ? "Дохід" : o.cat, true) + esc(o.income ? "Дохід" : o.cat) + "</span>") +
    row("Дата", esc((d[1] || d[0]) + (d[1] ? ", " + d[0] : ""))) +
    row("Звідки", o.cash ? "готівка, додано вручну" : "банківська виписка") + (o.fixed ? row("Тип", "обовʼязкова витрата") : "") + "</div>" +
    (o.cash && o.line ? "" : '<p class="note" style="margin:14px 2px 0">Операції з виписки змінити не можна: вони приходять з банку.</p>');
  var foot = $("op-foot"); foot.innerHTML = "";
  if (o.cash && o.line && acc) {
    foot.innerHTML = '<div class="opbtns"><button type="button" class="obtn" id="op-edit">' + svgI("currentColor", '<path d="M14.5 5.5l4 4M4 20l1-4.5L15.5 5a1.4 1.4 0 0 1 2 0l1.5 1.5a1.4 1.4 0 0 1 0 2L8.5 19 4 20z"/>', 18) + 'Змінити</button><button type="button" class="obtn del" id="op-del">' + TRASH + 'Видалити</button></div>';
    $("op-edit").onclick = function () {
      editing = { line: o.line, day: o.day, title: o.title, amount: o.amount, cat: acc.key };
      closeOp(); sheetFromApp = true; sheetBase = view() === "journal" ? "journal" : "report"; location.hash = "#add";
    };
    $("op-del").onclick = function () {
      closeOp();
      delCash(P, o.line, o.day, o.title, null, { amount: o.amount, category: acc.key, title: o.title, date: o.day });
    };
  }
  var s = $("v-op"), bg = $("op-bg");
  s.hidden = bg.hidden = false; document.body.style.overflow = "hidden";
  requestAnimationFrame(function () { requestAnimationFrame(function () { s.classList.add("open"); bg.classList.add("open"); }); });
}
function closeOp() {
  var s = $("v-op"), bg = $("op-bg"); if (s.hidden) return;
  s.classList.remove("open"); bg.classList.remove("open"); document.body.style.overflow = "";
  setTimeout(function () { if (!s.classList.contains("open")) s.hidden = bg.hidden = true; }, 300);
}
$("op-close").onclick = closeOp; $("op-bg").onclick = closeOp;

// ---------- шторка «Відкладати щомісяця» ----------
// Марко: сума на місяць (або «зібрати суму до дати»); ліміт без цілі — менший з автоматичного й «від плану».
// Оля: план замість «скільки треба для цілі»; видно розрахунок ліміту рядками і коли буде подушка.
var PIG = svgI("currentColor", '<path d="M4.5 11.5a6.5 5.5 0 0 1 6.5-5.5h3a6.5 5.5 0 0 1 6 3.4l1.5.4v3.4l-1.6.5a6.5 5.5 0 0 1-2.4 2.7V20h-2.6v-1.8h-3.8V20H8.5v-2.6a5.5 5.5 0 0 1-4-5.9z"/><path d="M11 6V4.2M10 9.5h3.5"/><circle cx="16.5" cy="10.5" r=".6" fill="currentColor"/>', 16);
function dmy(iso) { return +iso.slice(8) + " " + GEN[+iso.slice(5, 7) - 1] + " " + iso.slice(0, 4); }
var plS = null;  // стан шторки: { P, monthly, span (null — без терміну, 3/6/12 міс., 0 — інша дата), until, dd — розгорнуто «До коли» }
function plNum(id) { var el = $(id); if (!el) return 0; var v = parseFloat(el.value.replace(/\s/g, "").replace(",", ".")); return isFinite(v) && v > 0 ? Math.round(v) : 0; }
function plUntil() {  // 3/6/12 міс. — до кінця того місяця (30 квітня 2027), «Інша дата» — як обрали
  if (plS.span) { var d = new Date(); return iso(new Date(d.getFullYear(), d.getMonth() + plS.span + 1, 0)); }
  return plS.span === 0 ? plS.until || "" : "";
}
function plMonths(until) {  // скільки місяців до дати, від поточного (неповного) включно
  if (!until) return 0;
  var t = new Date(), u = new Date(until + "T12:00:00");
  return Math.max(1, (u.getFullYear() - t.getFullYear()) * 12 + u.getMonth() - t.getMonth());
}
// що станеться з лімітом при відкладанні m на місяць
function planCalc(P, m) {
  var j = P.months.length - 1, M = P.months[j], base = usualMonths(P, j), LM = limitsFor(P, j), pm = M.income > 0 ? M : prevWithIncome(P, j);
  if (!LM || !pm) return null;
  var fixU = avgOf(base, function (x) { return x.expenses - x.variable; }), free = pm.income - fixU, g = goalPlan(P);
  var cap = Math.max(0, free - m);
  return { inc: pm.income, fixU: fixU, free: free, auto: LM.auto, floor: LM.auto - LM.wants, now: LM.total, cap: cap,
    lim: g ? cap : Math.min(LM.auto, cap), g: g, usual: avgOf(base, function (x) { return x.variable; }) };
}
function plResult(c, m, dateMode) {
  if (!m) return "";
  if (c.cap < c.floor) {  // не вміщаються навіть потреби — жовтий блок, без заборони
    var most = Math.max(0, Math.floor((c.free - c.floor) / 10) * 10), txt = "Ліміт тоді не вмістить навіть потреби (продукти, транспорт). Найбільше — ≈ " + zr(most) + " на місяць.";
    if (dateMode && most > 0) { var n = Math.ceil(plNum("pl-total") / most), d = new Date(); d.setMonth(d.getMonth() + n); txt = "Так не вийде: найбільше ≈ " + zr(most) + " на місяць. Найближча можлива дата — " + dmy(iso(d)) + "."; }
    return '<div class="plres warn"><b>Забагато для цього доходу</b><span>' + txt + "</span></div>";
  }
  if (!c.g && c.cap >= c.auto) return '<div class="plres ok"><b>Ліміт не зміниться — ' + zr(c.auto) + "</b><span>" +
    (dateMode ? "З ним і так лишається ≈ " + zr(c.free - c.auto) + " на місяць." : "Ви й так лишаєте більше, ніж " + zr(m) + ". Ліміт стане нижчим, лише якщо відкладати понад " + zr(c.free - c.auto) + ".") + "</span></div>";
  var d0 = c.lim - c.now;
  return '<div class="plres"><b>Ліміт на щоденні — ' + zr(c.lim) + "</b><span>" + (Math.abs(d0) < 1 ? "Як зараз." : (d0 < 0 ? "На " + zr(-d0) + " менше, ніж зараз; першими зменшаться бажання — Кафе, Доставка, Розваги, Покупки." : "На " + zr(d0) + " більше, ніж зараз.")) + "</span></div>";
}
function plRender(keepFocus) {
  var P = plS.P, g = goalPlan(P), body = $("pl-body"), m, html = "";
  var amt = function (id, v, ph) { return '<label class="amount pla"><input id="' + id + '" type="text" inputmode="numeric" autocomplete="off" placeholder="' + ph + '" value="' + (v || "") + '"><span>зл</span></label>'; };
  if (g) {
    var was = (LMnow(P) || {}).save;
    m = plS.monthly;
    var c = planCalc(P, m || 0);
    html += '<div class="plgoal"><span class="gdue">' + svgI("currentColor", '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>', 14) + "ціль до 1 " + GEN[g.G.deadline[1] - 1] + " " + g.G.deadline[0] + "</span><span>" + nf1.format(g.life) + " з " + g.G.months + " місяців витрат</span></div>" +
      '<div class="plrow">' + amt("pl-amount", m, nf0.format(Math.round(g.need)).replace(/\s/g, "")) + (was && Math.round(was) !== m ? '<span class="plwas">було ' + nf0.format(Math.round(was)) + "</span>" : "") + "</div>";
    if (c && m) {
      html += '<div class="oplist plcalc"><div class="oprow"><span>Дохід</span><b>' + zr(c.inc) + '</b></div><div class="oprow"><span>Обовʼязкові</span><b>−' + zr(c.fixU) + "</b></div>" +
        '<div class="oprow"><span>Відкладати</span><b>−' + zr(m) + '</b></div><div class="oprow tot"><span>Ліміт на щоденні</span><b>' + zr(c.lim) + "</b></div></div>";
      var n = Math.ceil(g.left / m - .01), ei = g.li + n, ahead = g.di - ei;  // місяць, коли збереться подушка при такому плані
      html += '<p class="plnote">' + (g.left <= 0 ? "Подушку вже зібрано." : "Подушка — у " + LOC[ei % 12] + " " + Math.floor(ei / 12) + (ahead > 0 ? ", на " + (ahead === 1 ? "місяць" : ahead + " " + plural(ahead, "місяць", "місяці", "місяців")) + " раніше" : ahead < 0 ? ", на " + (-ahead === 1 ? "місяць" : -ahead + " " + plural(-ahead, "місяць", "місяці", "місяців")) + " пізніше терміну" : ", вчасно")) + "</p>";
      if (c.cap < c.floor) html += plResult(c, m, false);
      else if (c.usual && c.lim < c.usual) html += '<p class="plwarn">Ліміт на ' + zr(c.usual - c.lim) + " нижчий, ніж ви зазвичай витрачаєте на щоденні</p>";
    }
  } else {
    // Марко: сума на місяць; «До коли» — необовʼязковий рядок-дропдаун у тій самій шторці (за замовчуванням «без терміну»)
    m = plS.monthly;
    var c2 = planCalc(P, m || 0), until = plUntil(), n2 = plMonths(until);
    var spans = [[null, "Без терміну"], [3, "3 міс."], [6, "6 міс."], [12, "Рік"], [0, "Інша дата"]];
    if (!LMnow(P).plan) html += '<p class="plintro">Зараз ліміт рахується сам: бажання — як у найекономніший місяць, решта — як зазвичай. З ним лишається ≈ ' + zr(c2.free - c2.auto) + " на місяць.</p>";
    html += amt("pl-amount", m, "1000") + '<div class="chips plchips">' + [500, 1000, 2000, 5000].map(function (v) { return '<button type="button" data-v="' + v + '" aria-pressed="' + (m === v) + '">' + nf0.format(v) + "</button>"; }).join("") + "</div>" +
      '<div class="pldd' + (plS.dd ? " open" : "") + '"><button type="button" class="pldd-h" id="pl-dd" aria-expanded="' + !!plS.dd + '"><span>До коли</span><b>' + (until ? dmy(until) : "без терміну") + "</b>" +
        '<svg class="ico16 chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>' +
      ('<div class="pldd-w"><div><div class="pldd-b"><div class="chips">' + spans.map(function (x) { return '<button type="button" data-span="' + x[0] + '" aria-pressed="' + (plS.span === x[0]) + '">' + x[1] + "</button>"; }).join("") + "</div>" +
        (plS.span === 0 ? '<input class="pldate" id="pl-until" type="date" min="' + iso(new Date(Date.now() + 864e5)) + '" value="' + (plS.until || "") + '">' : "") +
        (until && m ? '<p class="pldd-s">До ' + dmy(until) + " збереться <b>" + zr(m * n2) + "</b></p>" : "") + "</div></div></div>") + "</div>" +
      plResult(c2, m, false);
  }
  plS.m = m;
  body.innerHTML = html;
  $("pl-save").disabled = !m; $("pl-msg").textContent = "";
  var inp = $("pl-amount");
  if (inp) {
    var fit = function () { var mm = fitAmount.m || (fitAmount.m = document.body.appendChild(h("span", "amount-mirror"))); mm.textContent = inp.value || inp.placeholder; inp.style.width = Math.ceil(mm.getBoundingClientRect().width) + 4 + "px"; };
    fit();
    inp.oninput = function () { plS.monthly = plNum("pl-amount"); plRender(true); };
    if (keepFocus) { inp.focus(); var L = inp.value.length; try { inp.setSelectionRange(L, L); } catch (e) {} }
  }
  body.querySelectorAll("[data-v]").forEach(function (b) { b.onclick = function () { plS.monthly = +b.dataset.v; plRender(); }; });
  body.querySelectorAll("[data-span]").forEach(function (b) { b.onclick = function () { plS.span = b.dataset.span === "null" ? null : +b.dataset.span; plRender(); }; });
  if ($("pl-until")) $("pl-until").onchange = function () { plS.until = this.value; plRender(); };
  // розгортання — без перемальовування: міняємо клас, висота плавно їде (grid 0fr → 1fr)
  if ($("pl-dd")) $("pl-dd").onclick = function () { plS.dd = !plS.dd; this.parentNode.classList.toggle("open", plS.dd); this.setAttribute("aria-expanded", String(plS.dd)); };
}
function LMnow(P) { return limitsFor(P, P.months.length - 1) || {}; }
function planOpen(P) {
  var pl = planFor(P, P.months[P.months.length - 1].key);
  var g = goalPlan(P);  // в Олі одразу стоїть поточний план (або скільки треба для цілі) — його лише підкручують
  plS = { P: P, monthly: pl ? pl.monthly : g ? Math.round(g.need) : 0, span: pl && pl.until ? 0 : null, until: pl && pl.until || "", dd: false };
  // збережений термін — на своїй пігулці (3/6 міс., Рік), інакше — «Інша дата»
  if (plS.until) [3, 6, 12].some(function (s) { var d = new Date(); return iso(new Date(d.getFullYear(), d.getMonth() + s + 1, 0)) === pl.until && (plS.span = s); });
  plRender();
  var s = $("v-plan"), bg = $("pl-bg");
  s.hidden = bg.hidden = false; document.body.style.overflow = "hidden";
  requestAnimationFrame(function () { requestAnimationFrame(function () { s.classList.add("open"); bg.classList.add("open"); }); });
}
function planClose() {
  var s = $("v-plan"), bg = $("pl-bg"); if (s.hidden) return;
  s.classList.remove("open"); bg.classList.remove("open"); document.body.style.overflow = "";
  setTimeout(function () { if (!s.classList.contains("open")) s.hidden = bg.hidden = true; }, 300);
}
function planPost(body) {
  return fetch("api/plan", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify(body) })
    .then(function (r) { return r.json().catch(function () { return { ok: false, error: "Сервер не відповів" }; }); })
    .then(function (res) { if (!res.ok) throw new Error(res.error || "Не вдалося зберегти"); D.plans = res.plans; reportKey = null; render(); return res; });
}
$("pl-save").onclick = function () {
  var P = plS.P, m = plS.m, prev = ((D.plans || {})[P.key] || []).slice(), btn = this;
  if (!m) return;
  var body = { person: P.key, monthly: m };
  var until = goalPlan(P) ? "" : plUntil();
  if (until) { body.until = until; body.total = m * plMonths(until); }  // термін — разом із сумою, яка збереться
  btn.disabled = true;
  planPost(body).then(function () {
    btn.disabled = false; planClose();
    toast(okMsg("Відкладати " + zr(m) + " на місяць"), ["Скасувати", function () {
      planPost({ person: P.key, history: prev }).then(function () { toast(okMsg("Скасовано")); }).catch(function (err) { toast(err.message); });
    }]);
  }).catch(function (err) { btn.disabled = false; $("pl-msg").textContent = err.message || "Немає звʼязку, спробуйте ще раз"; });
};
$("pl-close").onclick = planClose; $("pl-bg").onclick = planClose;
document.addEventListener("keydown", function (e) { if (e.key === "Escape") planClose(); });
window.addEventListener("hashchange", planClose);
document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeOp(); });
window.addEventListener("hashchange", closeOp);
// тап по рядку (не після свайпу «Видалити»)
document.addEventListener("click", function (e) {
  var r = e.target.closest(".op[data-op]");
  if (!r || e.target.closest(".swdel")) return;
  var sw = r.closest(".swipe");
  if (sw && (sw.classList.contains("open") || sw.classList.contains("on"))) return;
  opCard(OPS[+r.dataset.op]);
});
document.addEventListener("keydown", function (e) { var r = e.target.closest && e.target.closest(".op[data-op]"); if (r && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); opCard(OPS[+r.dataset.op]); } });
$("cash").addEventListener("click", function () { sheetFromApp = true; sheetBase = "report"; });
$("j-cash").addEventListener("click", function () { sheetFromApp = true; sheetBase = "journal"; });
$("ops-all").addEventListener("click", function () { filter = "all"; toCur(D.people[person]); });
// пошук у Журналі: перемальовуємо лише список, поле лишається у фокусі
$("j-q").addEventListener("input", function () { jq = this.value; if (!jq.trim()) jqMonth = false; $("j-clear").hidden = !jq; renderJournal(D.people[person]); mcalName("journal", D.people[person].months[month]); sugRender(); });
// кількість операцій-витрат за категоріями й за назвами — у всіх місяцях людини
function opCounts(P) {
  var c = {}, t = {};
  P.months.forEach(function (m) { m.ops.forEach(function (o) {
    if (!o.income) c[o.cat] = (c[o.cat] || 0) + 1;
    var nm = nice(o.title); if (nm) { var k = nm.toLowerCase(); (t[k] = t[k] || { name: nm, n: 0, cat: o.income ? "Дохід" : o.cat }).n++; }
  }); });
  return { cats: c, titles: t };
}
var nOps = function (k) { return k + " " + plural(k, "операція", "операції", "операцій"); };
var catOpt = function (c, k) { return '<button type="button" class="opt" role="option" data-c="' + esc(c) + '"' + (c === catF ? ' aria-selected="true"' : "") + ">" + cic(c, true) + "<span><b>" + esc(c) + "</b><small>" + nOps(k) + "</small></span></button>"; };
// підказки пошуку: з перших літер — категорії (тап вмикає фільтр) і назви операцій (тап шукає цю назву)
function sugRender() {
  var box = $("j-sug"), q = jq.trim().toLowerCase(), P = D.people[person];
  if (!q || document.activeElement !== $("j-q")) { box.hidden = true; return; }
  var C = opCounts(P), starts = function (s) { return s.toLowerCase().split(/[\s\/*.,-]+/).some(function (w) { return w.indexOf(q) === 0; }); };
  var cats = Object.keys(C.cats).filter(function (c) { return c !== catF && starts(c); }).sort(function (a, b) { return C.cats[b] - C.cats[a]; }).slice(0, 3);
  var titles = Object.keys(C.titles).map(function (k) { return C.titles[k]; })
    .filter(function (t) { return t.name.toLowerCase() !== q && !(t.name in C.cats) && starts(t.name); })  // назва = категорія (готівка «Кафе») — лише як категорія.sort(function (a, b) { return b.n - a.n; }).slice(0, 5);
  box.hidden = !cats.length && !titles.length;
  if (box.hidden) return;
  box.innerHTML = (cats.length ? "<p>Категорії</p>" + cats.map(function (c) { return catOpt(c, C.cats[c]); }).join("") : "") +
    (titles.length ? "<p>Операції</p>" + titles.map(function (t) {
      return '<button type="button" class="opt" role="option" data-t="' + esc(t.name) + '"><span class="qi">' + svgI("currentColor", '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>', 16) + "</span><span><b>" + esc(t.name) + "</b><small>" + esc(t.cat) + ", " + nOps(t.n) + "</small></span></button>";
    }).join("") : "");
  var r = $("j-q").closest(".jsearch").getBoundingClientRect();
  box.style.top = r.bottom + 8 + "px"; box.style.left = r.left + "px"; box.style.width = r.width + "px";
  box.querySelectorAll(".opt").forEach(function (b) { b.onpointerdown = function (e) { e.preventDefault(); }; });  // поле не втрачає фокус до вибору
  box.querySelectorAll("[data-c]").forEach(function (b) {
    b.onclick = function () { catF = b.dataset.c; filter = "all"; var q = $("j-q"); q.value = ""; jq = ""; $("j-clear").hidden = true; box.hidden = true; q.blur(); render(); };
  });
  box.querySelectorAll("[data-t]").forEach(function (b) {
    b.onclick = function () { var q = $("j-q"); q.value = b.dataset.t; jq = q.value; $("j-clear").hidden = false; box.hidden = true; q.blur(); render(); };
  });
}
// дропдаун категорій у фільтрах Журналу: усі категорії витрат людини, найчастіші зверху; «Усі категорії» знімає фільтр
function catOpen(open) {
  var m = $("cat-menu"), b = $("jc-btn");
  m.hidden = !open; if (b) b.setAttribute("aria-expanded", String(open));
  if (!open) return;
  calOpen(false); popMenu("w", false);
  var C = opCounts(D.people[person]).cats;
  m.innerHTML = '<button type="button" class="opt" role="option" data-c=""' + (catF ? "" : ' aria-selected="true"') + '><span class="qi">' + svgI("currentColor", '<path d="M4 6h16M4 12h16M4 18h16"/>', 16) + "</span><span><b>Усі категорії</b></span></button>" +
    Object.keys(C).sort(function (a, c) { return C[c] - C[a]; }).map(function (c) { return catOpt(c, C[c]); }).join("");
  m.querySelectorAll("[data-c]").forEach(function (x) { x.onclick = function () { catF = x.dataset.c || null; filter = "all"; catOpen(false); render(); }; });
  catPlace();
  var cur = m.querySelector('[aria-selected="true"]'); if (cur) cur.focus();
}
function catPlace() {
  var m = $("cat-menu"), b = $("jc-btn"); if (m.hidden || !b) return;
  var r = b.getBoundingClientRect();
  m.style.top = r.bottom + 8 + "px"; m.style.left = Math.max(12, Math.min(r.left, innerWidth - m.offsetWidth - 12)) + "px";
}
document.body.appendChild($("cat-menu"));
document.addEventListener("click", function (e) { if (!e.target.closest("#cat-menu, #jc-btn")) catOpen(false); });
document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !$("cat-menu").hidden) { catOpen(false); var b = $("jc-btn"); if (b) b.focus(); } });
window.addEventListener("scroll", catPlace, { passive: true });
window.addEventListener("hashchange", function () { catOpen(false); });
document.body.appendChild($("j-sug"));  // поза екраном Журналу — поверх чипів і списку
$("j-q").addEventListener("focus", sugRender);
$("j-q").addEventListener("blur", function () { setTimeout(function () { $("j-sug").hidden = true; }, 120); });
window.addEventListener("scroll", function () { $("j-sug").hidden = true; }, { passive: true });
// наш хрестик замість системного: очищає пошук і лишає курсор у полі
$("j-clear").onclick = function () { var q = $("j-q"); q.value = ""; q.dispatchEvent(new Event("input")); q.focus(); };
$("arr-j").outerHTML = CHR; $("arr-f").outerHTML = CHR;

// ---------- рендер ----------
// назва на кнопці місяця в шапці: у Журналі під час пошуку — «Усі місяці», з обраним днем — «15 жовтня 2026»
function mcalName(base, M) {
  $("mcal-name").textContent = base === "journal" && jq.trim() && !jqMonth ? "Усі місяці"
    : selDay ? (+selDay.slice(8)) + " " + GEN[mi(M)] + " " + M.key.slice(0, 4) : M.name + " " + M.key.slice(0, 4);
}
function render() {
  var v = view(), P = D.people[person];
  renderPeople();
  if (v === "add" && !P.cash) { location.replace("#report"); return; }
  var base = v === "add" ? sheetBase : v, M = P.months[month];
  // місяць — у шапці на всіх екранах; один для Головної, Аналітики й Журналу
  $("mrange").hidden = true; $("mpick").hidden = false;
  mcalName(base, M);
  $("mcal").setAttribute("aria-label", "Місяць: " + M.name + " " + M.key.slice(0, 4) + ". Змінити");
  calKey = M.key; if (selDay && selDay.slice(0, 7) !== M.key) selDay = null; renderCal(P);
  ["report", "analytics", "journal"].forEach(function (k) { $("v-" + k).hidden = k !== base; });
  [["navr", "report"], ["navt", "analytics"], ["navj", "journal"]].forEach(function (n) {
    $(n[0]).classList.toggle("on", n[1] === base);
    if (n[1] === base) $("nav-ind").style.setProperty("--n", ["report", "analytics", "journal"].indexOf(base));
    if (n[1] === base) $(n[0]).setAttribute("aria-current", "page"); else $(n[0]).removeAttribute("aria-current");
  });
  var rkey = person + "|" + month, sheetOnly = base === "report" && (v === "add" || prevView === "add") && rkey === reportKey;
  var newReport = base === "report" && !sheetOnly && rkey !== reportKey;
  if (base === "analytics") renderAnalytics(P); else if (base === "journal") renderJournal(P); else if (!sheetOnly) { renderReport(P); reportKey = rkey; }
  if (base !== lastBase) { lastBase = base; enter(base); }
  // інша людина чи місяць на головній — сума знову «набігає»
  else if (newReport) countUp(document.querySelector("#hero .num"));
  sheet(v === "add", P);
}
// поява: при першому показі екрана картки виїжджають по черзі, а головна сума «набігає»
var lastBase = null;
function enter(base) {
  var root = $("v-" + base), inner = [].slice.call(root.querySelectorAll("#j-list > *, #v-analytics > section"), 0, 8);
  root.classList.remove("stag"); void root.offsetWidth;
  [].slice.call(root.children).concat(inner).forEach(function (k, i) { k.style.setProperty("--i", Math.min(i, 8)); });
  inner.forEach(function (k) { k.classList.add("stagk"); });
  root.classList.add("stag");
  if (base === "report") countUp(document.querySelector("#hero .num"));
}
function countUp(el) {
  if (!el || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  el.classList.add("cu");
  var txt = el.textContent, m = txt.match(/\d[\d\s\u00a0\u202f]*,\d{2}/);
  if (!m) return;
  var to = Math.floor(parseFloat(m[0].replace(/[\s\u00a0\u202f]/g, "").replace(",", "."))), from = Math.round(to * .85), cents = m[0].slice(-3), t0 = null, dur = 1400;
  function step(ts) {
    if (t0 === null) t0 = ts;
    var k = Math.min(1, (ts - t0) / dur), e = k === 1 ? 1 : 1 - Math.pow(2, -10 * k);
    el.textContent = txt.replace(m[0], nf0.format(Math.round(from + (to - from) * e)) + cents);
    if (k < 1) requestAnimationFrame(step); else el.textContent = txt;
  }
  requestAnimationFrame(step);
}
// абстрактні форми щоразу стартують з іншого положення
// старт форм зміщений випадково, але спільно — щоб протифаза яскравості зберігалась
var ambD = -(Math.random() * 60).toFixed(1) + "s"; $("liquid").querySelectorAll(".amb").forEach(function (s) { s.style.animationDelay = ambD; });
// зерно головної картки: випадкова сіра крапка на кожен фізичний піксель плитки 160×160 (м'яке світло — не бруднить темні тони)
(function () {
  var dpr = Math.min(3, window.devicePixelRatio || 1), s = Math.round(160 * dpr), c = document.createElement("canvas");
  c.width = c.height = s;
  var x = c.getContext("2d"); if (!x || !c.toBlob) return;
  var im = x.createImageData(s, s), p = im.data;
  for (var i = 0; i < p.length; i += 4) { var v = 128 + (Math.random() - .5) * 150 | 0; /* навколо нейтрального сірого — без глибоких чорних крапок */ p[i] = p[i + 1] = p[i + 2] = v; p[i + 3] = 255; }
  x.putImageData(im, 0, 0);
  c.toBlob(function (b) {
    if (!b) return;
    var w = document.querySelector(".hero-wrap");
    w.style.setProperty("--grain", "url(" + URL.createObjectURL(b) + ")"); w.classList.add("grain");
  });
})();
var lastView = view(), prevView = null, reportKey = null;
// діагональний блік на головній картці: програти ще раз (крайове світло не перезапускається)
function replaySheen() {
  var l = $("liquid");
  if (!l || !l.classList.contains("sheen") || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  l.getAnimations().forEach(function (a) {
    if (a.animationName !== "sheen" || a.playState === "running") return;
    l.classList.add("lit");  // форми вже відкриті — повтор бліку їх не ховає, без обриву
    a.currentTime = 0; a.play();
  });
}
setInterval(function () { if (!document.hidden && view() === "report") replaySheen(); }, 30000);
window.addEventListener("hashchange", function () {
  var v = view();
  if (v === "report" && lastView !== "report" && lastView !== "add") setTimeout(replaySheen, 250);
  if (v !== "add" && lastView !== "add") window.scrollTo(0, 0);
  if (lastView === "add" && v !== "add") {
    sheetFromApp = false; sheetBase = "report";
    // закрили «Змінити запис» без збереження — форма знову чиста
    if (editing) { editing = null; pick = null; $("a-title").value = ""; $("a-date").value = iso(new Date()); dpick = 1; $("a-amount").value = ""; }
  }
  if (lastView === "report" && v !== "report" && v !== "add") reportKey = null;
  if (lastView === "journal" && v !== "journal" && v !== "add") { filter = "all"; catF = null; jq = ""; jqMonth = false; $("j-q").value = ""; $("j-clear").hidden = true; }
  prevView = lastView; lastView = v; render();
});
render();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(function () {});
// сплеш зникає, коли готовий шрифт (але не довше 3 с і не швидше 2,4 с — повний цикл анімації знака — щоб гроші й повзунок встигли спокійно зіграти)
(function () {
  var s = $("splash"), t = Date.now(), done = false, fill = $("sp-fill");
  requestAnimationFrame(function () { fill.style.width = "90%"; });
  function hide() { if (done) return; done = true; setTimeout(function () { s.classList.add("done"); fill.style.width = "100%"; setTimeout(function () { lastBase = null; render(); requestAnimationFrame(function () { requestAnimationFrame(function () { $("app").classList.remove("pre"); s.classList.add("out"); $("liquid").classList.add("sheen"); }); }); setTimeout(function () { s.remove(); }, 1500); }, 550); }, Math.max(0, 2400 - (Date.now() - t))); }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(hide);
  setTimeout(hide, 3000);
})();
})();
</script>
</body>
</html>
"""
