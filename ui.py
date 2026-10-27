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
    return jsonify({"ok": True, "month": day.strftime("%Y-%m"), "line": line, "date": day.isoformat(), "title": title})


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
    return jsonify({"ok": True, "month": body.get("date", "")[:7]})


@ui_app.route("/")
def index():
    payload = json.dumps(report_data.report(), ensure_ascii=False).replace("<", "\\u003c")
    return PAGE.replace("__DATA__", payload)


PAGE = r"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#F5F6F8">
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
  --hero-on: #DCE3F4; --glow: rgba(120, 160, 245, .24); --glow2: rgba(130, 175, 255, .62); --glow3: rgba(190, 215, 255, .45); --glow4: rgba(90, 120, 255, .55); --glow5: rgba(150, 130, 255, .5); --h1: rgba(120, 165, 255, .6); --h2: rgba(80, 200, 255, .45); --h3: rgba(150, 120, 255, .5); --h4: rgba(200, 225, 255, .42); --h5: rgba(70, 105, 255, .55); --h6: rgba(120, 235, 230, .3); --g1: #3A62C2; --g2: #1A2F6A; --g3: #04070F;
  --ease: cubic-bezier(.33, 1, .68, 1); --spring: cubic-bezier(.34, 1.2, .64, 1);
  --font: "Onest", "Outfit", system-ui, -apple-system, "Segoe UI", sans-serif;
}
body.p-olya { --hero-on: #F1DDEA; --glow: rgba(214, 120, 182, .34); --glow2: rgba(232, 130, 200, .6); --glow3: rgba(255, 190, 230, .42); --glow4: rgba(255, 110, 170, .5); --glow5: rgba(190, 100, 230, .48); --h1: rgba(232, 130, 200, .58); --h2: rgba(255, 150, 140, .4); --h3: rgba(180, 100, 235, .5); --h4: rgba(255, 200, 230, .4); --h5: rgba(255, 100, 165, .5); --h6: rgba(255, 190, 120, .28); --g1: #9A4783; --g2: #4E1B3F; --g3: #0B0309; }
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.4 var(--font); -webkit-font-smoothing: antialiased; font-variant-numeric: tabular-nums; }
.app { max-width: 440px; margin: 0 auto; padding: 8px 14px 130px; }
button { font: inherit; color: inherit; }
a { color: inherit; text-decoration: none; }
[hidden] { display: none !important; }
:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
.ico16 { flex: none; width: 16px; height: 16px; }

/* шапка */
.top { position: relative; z-index: 25; display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 4px 2px 0; }
.who, .mpick { position: relative; }
.who > button { display: flex; align-items: center; gap: 10px; height: 48px; padding: 0 14px 0 6px; border: 1px solid var(--border); border-radius: 24px; background: var(--card); font-size: 15px; font-weight: 500; cursor: pointer; }
.av { position: relative; flex: none; width: 36px; height: 36px; border-radius: 50%; overflow: hidden; background: #DCE8FD; }
.av::before, .av::after { content: ""; position: absolute; border-radius: 50%; background: #3B82F6; }
.av::before { left: 30%; top: 17%; width: 40%; height: 40%; }
.av::after { left: 10%; top: 66%; width: 80%; height: 60%; }
.av.olya { background: #E9E1FB; }
.av.olya::before, .av.olya::after { background: #8B6CE0; }
.mpick > button, .mstatic { display: flex; align-items: center; gap: 8px; height: 48px; padding: 0 14px 0 12px; border: 1px solid var(--border); border-radius: 24px; background: var(--card); font-size: 14px; white-space: nowrap; cursor: pointer; }
.mstatic { cursor: default; }
.who > button[aria-expanded="true"] .chev, .mpick > button[aria-expanded="true"] .chev { transform: rotate(180deg); }
.chev { transition: transform .45s var(--ease); }
.pop { position: absolute; top: 56px; padding: 6px; border: 1px solid var(--border); border-radius: 24px; background: var(--card); box-shadow: 0 20px 40px rgba(17,19,24,.16); }
.who .pop { left: 0; width: 272px; }
.pop > p { margin: 10px 12px 6px; font-size: 12px; color: var(--ink2); }
.pop .opt { display: flex; align-items: center; gap: 12px; width: 100%; min-height: 60px; padding: 6px 12px 6px 8px; border: 0; border-radius: 18px; background: transparent; text-align: left; cursor: pointer; }
.pop .opt b { display: block; font-size: 15px; font-weight: 500; }
.pop .opt small { display: block; font-size: 12px; color: var(--ink2); }
.pop .opt .radio { flex: none; width: 20px; height: 20px; margin-left: auto; border: 1.5px solid var(--mute); border-radius: 50%; }
.pop .opt[aria-checked="true"] { background: var(--soft); }
.pop .opt[aria-checked="true"] .radio { border: 6px solid var(--blue); }
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

/* головна картка: градієнт у кольорі людини, а над ним «рідкі» плями світла */
.hero-wrap { position: relative; isolation: isolate; margin-top: 12px; border-radius: 24px; overflow: hidden;
  background: linear-gradient(162deg, var(--g1) 0%, var(--g2) 48%, var(--g3) 100%); }
.hero { position: relative; z-index: 1; padding: 22px 20px 20px; border-radius: 24px; color: #fff; }
/* шість м'яких плям різних відтінків повільно пливуть кожна своїм шляхом (37–61 с) і змінюють форму;
   спільне розмиття зливає їх, тож кольори переливаються. Старт кожної — випадковий */
.liquid { position: absolute; inset: 0; z-index: 0; pointer-events: none; filter: blur(26px); }
.liquid i { position: absolute; width: 56%; aspect-ratio: 1; border-radius: 42% 58% 63% 37% / 45% 40% 60% 55%; mix-blend-mode: screen; will-change: transform, border-radius;
  background: radial-gradient(circle, var(--c), transparent 68%);
  animation: var(--path) var(--dur) cubic-bezier(.45, 0, .55, 1) infinite, morph calc(var(--dur) * .5) ease-in-out infinite alternate; }
.liquid i:nth-child(1) { --c: var(--h1); --path: flow1; --dur: 43s; left: 48%; top: -30%; }
.liquid i:nth-child(2) { --c: var(--h2); --path: flow2; --dur: 53s; left: -15%; top: 40%; width: 50%; }
.liquid i:nth-child(3) { --c: var(--h3); --path: flow3; --dur: 61s; left: 30%; top: 45%; width: 64%; }
.liquid i:nth-child(4) { --c: var(--h4); --path: flow4; --dur: 37s; left: 68%; top: 30%; width: 40%; }
.liquid i:nth-child(5) { --c: var(--h5); --path: flow2; --dur: 59s; left: 5%; top: -35%; width: 48%; animation-direction: reverse, alternate; }
.liquid i:nth-child(6) { --c: var(--h6); --path: flow1; --dur: 47s; left: 20%; top: 60%; width: 44%; animation-direction: reverse, alternate; }
@keyframes flow1 { 0%, 100% { transform: translate(0, 0) rotate(0); } 25% { transform: translate(-38%, 30%) rotate(40deg) scale(1.15); } 50% { transform: translate(-12%, 72%) rotate(95deg) scale(.9); } 75% { transform: translate(24%, 26%) rotate(150deg) scale(1.1); } }
@keyframes flow2 { 0%, 100% { transform: translate(0, 0) rotate(0); } 30% { transform: translate(85%, -30%) rotate(-60deg) scale(1.25); } 60% { transform: translate(140%, 25%) rotate(-120deg) scale(.85); } 80% { transform: translate(50%, 20%) rotate(-160deg) scale(1.05); } }
@keyframes flow3 { 0%, 100% { transform: translate(0, 0) scale(1); } 33% { transform: translate(-45%, -55%) scale(1.2); } 66% { transform: translate(30%, -35%) scale(.8); } }
@keyframes flow4 { 0%, 100% { transform: translate(0, 0) scale(1); } 20% { transform: translate(-90%, 60%) scale(1.3); } 45% { transform: translate(-150%, -10%) scale(.9); } 70% { transform: translate(-40%, -70%) scale(1.15); } }
@keyframes morph { 0% { border-radius: 42% 58% 63% 37% / 45% 40% 60% 55%; } 50% { border-radius: 63% 37% 41% 59% / 58% 62% 38% 42%; } 100% { border-radius: 35% 65% 55% 45% / 38% 52% 48% 62%; } }
/* легка тінь під білим текстом — підпис лишається читабельним, коли блік пропливає під ним */
.hero .lbl, .hero .num, .hero .sub, .hero .cols .t, .hero .cols b { text-shadow: 0 1px 10px rgba(5, 10, 30, .28); }
.hero .lbl { font-size: 14px; }
.hero .num.cu { animation: numin .7s var(--ease) both .1s; }
@keyframes numin { from { opacity: 0; transform: translateY(8px); filter: blur(4px); } to { opacity: 1; transform: none; filter: none; } }
.hero .num { margin-top: 6px; font-size: 40px; font-weight: 500; letter-spacing: -.02em; line-height: 1.05; }
.hero .sub { margin-top: 6px; font-size: 14px; }
.hero .tag { position: absolute; top: 18px; right: 18px; height: 24px; padding: 0 8px; border-radius: 12px; background: rgba(255,255,255,.92); color: var(--ink3); font-size: 12px; font-weight: 500; display: flex; align-items: center; }
.hero .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 20px; padding-top: 16px; border-top: 1px solid rgba(255,255,255,.35); }
.hero .cols a { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-height: 44px; }
.hero .cols .t { font-size: 13px; }
.hero .cols b { font-size: 18px; font-weight: 500; }
.hero .cols i { font-style: normal; font-size: 12px; color: var(--hero-on); }
.hero .meter { height: 8px; margin: 14px 0 8px; border-radius: 4px; background: rgba(255,255,255,.25); overflow: hidden; }
.hero .meter b { display: block; height: 100%; width: 0; border-radius: 4px; background: #fff; transition: width 1.6s var(--ease); }
.hero .hint { display: flex; gap: 10px; align-items: flex-start; margin-top: 16px; padding-top: 14px; border-top: 1px solid rgba(255,255,255,.35); font-size: 14px; line-height: 1.4; }
.hero .hint svg { flex: none; width: 18px; height: 18px; margin-top: 1px; }
/* бейджі: маленькі заокруглені плашки, стрілка діагональна */
.bd { flex: none; display: inline-flex; align-items: center; gap: 3px; height: 24px; padding: 0 8px; border-radius: 12px; background: var(--soft); color: var(--ink3); font-size: 12px; font-weight: 500; white-space: nowrap; }
.bd.good { background: var(--good-bg); color: var(--good); }
.bd.bad { background: var(--bad-bg); color: var(--bad); }
.bd.blue { height: auto; padding: 0; background: none; color: var(--ink2); font-size: 13px; font-weight: 400; }
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
.fx { display: inline-block; padding: 0 8px; border-radius: 10px; line-height: 20px; background: var(--soft); color: var(--ink3); font-size: 12px; }
.fx.cash { background: var(--cash-bg); color: var(--cash); }
.fx.now { background: var(--blue); color: #fff; }
/* «Вересень коротко» */
.ins { display: flex; justify-content: space-between; align-items: center; gap: 12px; min-height: 56px; padding: 14px 0; border-top: 1px solid var(--soft); font-size: 14px; line-height: 1.45; color: var(--ink4); }
.fadein { animation: rise .8s var(--ease) both; }
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
/* категорії: кольорова іконка, назва, сума, частка, шкала */
.crow { display: grid; grid-template-columns: 36px 1fr auto auto; gap: 8px 12px; align-items: center; padding: 12px 0; }
.crow + .crow { border-top: 1px solid var(--soft); }
.crow .nm { font-size: 15px; display: flex; align-items: center; gap: 6px; min-width: 0; }
.crow .am { font-size: 14px; font-weight: 500; }
.crow .bar6 { grid-column: 2 / -1; height: 6px; border-radius: 999px; background: var(--track); overflow: hidden; }
.crow .bar6 b { display: block; height: 100%; width: 0; border-radius: 999px; background: linear-gradient(90deg, #3B82F6, #1D5FE0); transition: width 1.6s var(--ease); }
.cic { flex: none; width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; background: var(--soft); }
.cic svg { width: 18px; height: 18px; }
.cic.inc { background: var(--good-bg); }
.more { display: flex; justify-content: space-between; align-items: center; min-height: 52px; margin-top: 4px; border-top: 1px solid var(--soft); font-size: 15px; color: var(--ink2); }
/* операції */
.op { display: flex; align-items: center; gap: 12px; min-height: 64px; padding: 12px 0; border-top: 1px solid var(--soft); }
.op .cic { width: 40px; height: 40px; border-radius: 20px; }
.op .cic svg { width: 20px; height: 20px; }
.op .t { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.op .t1 { font-size: 15px; }
.op .t1.one { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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
.op .ask button { min-height: 44px; padding: 0 12px; border: 0; border-radius: 22px; background: var(--soft); font-size: 13px; font-weight: 500; cursor: pointer; }
.op .ask button.yes { background: var(--bad); color: #fff; }
.toast button { min-height: 36px; margin-left: 4px; padding: 0 12px; border: 0; border-radius: 18px; background: rgba(255,255,255,.16); color: #fff; font-size: 13px; font-weight: 500; cursor: pointer; }

/* подушка безпеки (Оля) */
.goal .gbig { display: flex; align-items: flex-start; gap: 10px; margin: 6px 0 14px; }
.goal .gbig b { font-size: 32px; font-weight: 500; line-height: 1; color: var(--blue); }
.goal .gbig span { font-size: 14px; line-height: 1.35; color: var(--ink4); padding-top: 3px; }
.segs { display: grid; grid-template-columns: repeat(6, 1fr); gap: 4px; }
.segs i { position: relative; height: 8px; border-radius: 4px; background: var(--track); overflow: hidden; }
.segs i b { position: absolute; inset: 0; width: 0; border-radius: 4px; background: var(--blue-g); transition: width 1.6s var(--ease); }
.seglab { display: grid; grid-template-columns: repeat(6, 1fr); margin-top: 6px; font-size: 12px; color: var(--ink2); }
.seglab span:last-child { text-align: right; }
.gcols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 16px 0 12px; padding-top: 14px; border-top: 1px solid var(--soft); }
.gcols span { display: block; font-size: 12px; color: var(--ink2); }
.gcols b { display: block; margin: 3px 0 2px; font-size: 18px; font-weight: 500; }
.gcols small { display: block; font-size: 12px; color: var(--ink2); }
.gpace { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 12px 14px; border-radius: 14px; background: #F8F9FB; font-size: 13px; line-height: 1.4; color: var(--ink4); }
/* помилка обліку */
.alert { margin-top: 12px; padding: 16px; border-radius: 20px; background: var(--bad-bg); color: #7A1D15; font-size: 14px; line-height: 1.45; }
.alert b { display: block; margin-bottom: 4px; color: var(--bad); font-weight: 500; }
.alert a { display: inline-flex; align-items: center; min-height: 44px; margin-top: 6px; padding: 0 16px; border-radius: 22px; background: var(--bad); color: #fff; font-weight: 500; }

/* сегменти */
.seg { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 4px; margin-top: 12px; padding: 4px; border-radius: 24px; background: var(--seg); }
.seg button { min-height: 44px; border: 0; border-radius: 20px; background: transparent; color: var(--ink3); font-size: 14px; cursor: pointer; }
.seg button[aria-pressed="true"] { background: var(--card); color: var(--ink); font-weight: 500; box-shadow: 0 1px 3px rgba(17,19,24,.08); }
.seg.sm { margin: 0; padding: 3px; gap: 2px; border-radius: 14px; background: var(--soft); }
.seg.sm button { min-height: 36px; padding: 0 10px; border-radius: 11px; font-size: 12px; white-space: nowrap; }

/* капсульні графіки: сіра доріжка, синя заливка, неповний місяць — штрих */
.chart { display: flex; gap: 8px; margin-top: 28px; }
.ticks { display: flex; flex-direction: column; justify-content: space-between; height: 150px; min-width: 54px; font-size: 12px; color: var(--ink2); text-align: right; }
.ticks span { line-height: 1; }
.caps { flex: 1; display: flex; justify-content: space-around; }
.cap { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.cap.pick { cursor: pointer; }
.cap .trk { width: 40px; height: 150px; border-radius: 10px; background: var(--track); display: flex; flex-direction: column; justify-content: flex-end; overflow: hidden; }
.cap .fill { display: flex; flex-direction: column; height: 0; border-radius: 10px; overflow: hidden; transition: height 1.6s var(--ease); }
.cap .fill .rent { background: repeating-linear-gradient(135deg, #A9C6F8 0 5px, #D7E4FC 5px 10px); }
.cap .fill .main { flex: 1; background: var(--blue-g); }
.cap.dim .fill .main { background: linear-gradient(180deg, #A9C6F8, #7FA9F1); }
.cap.part .fill .main { background: repeating-linear-gradient(135deg, #A9C6F8 0 5px, #D7E4FC 5px 10px); }
.cap .r1 { font-size: 12px; color: var(--ink3); }
.cap .r2 { font-size: 12px; color: var(--ink2); }
.cap.on .r1 { color: var(--ink); font-weight: 500; }
/* фіксовані й змінні */
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
.share { display: flex; gap: 3px; height: 10px; margin: 6px 0 12px; }
.share i { display: block; height: 100%; border-radius: 5px; min-width: 6px; }
.leg { display: grid; grid-template-columns: 12px 1fr auto auto; gap: 10px; align-items: center; min-height: 36px; font-size: 14px; }
.leg i { width: 8px; height: 8px; border-radius: 4px; }
.cmp { display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 12px 0; border-top: 1px solid var(--soft); font-size: 15px; }
.cmp .n { flex: 1; min-width: 0; }
.cmp .v { color: var(--ink2); }
.cmp .bd { min-width: 64px; justify-content: center; }
.fava { display: inline-flex; align-items: center; gap: 2px; min-height: 44px; margin: 10px 4px 0; font-size: 14px; font-weight: 500; color: var(--blue); }

/* журнал */
.filters { display: flex; gap: 8px; margin-top: 12px; overflow-x: auto; scrollbar-width: none; }
.filters::-webkit-scrollbar { display: none; }
.filters button { flex: none; min-height: 44px; padding: 0 16px; border: 1px solid var(--border); border-radius: 22px; background: var(--card); font-size: 14px; cursor: pointer; }
.filters button[aria-pressed="true"] { border-color: transparent; background: var(--blue-g); color: #fff; font-weight: 500; }
.jsum { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
.jsum .card { margin: 0; padding: 14px 16px; display: flex; align-items: center; gap: 12px; }
.jsum .ji { flex: none; width: 36px; height: 36px; border-radius: 18px; display: grid; place-items: center; }
.jsum .ji svg { width: 18px; height: 18px; }
.jsum span { display: block; font-size: 12px; color: var(--ink2); }
.jsum b { display: block; margin-top: 2px; font-size: 17px; font-weight: 500; white-space: nowrap; }
.jsum .card > div { min-width: 0; }
.dh { display: flex; justify-content: space-between; gap: 10px; margin: 16px 4px 6px; font-size: 14px; }
.dh b { font-weight: 500; }
.dh b span { font-weight: 400; color: var(--ink2); }
.dh > span { color: var(--ink2); white-space: nowrap; }
.day { scroll-margin-top: 12px; }
.day .card { margin-top: 0; }
.day .card .op:first-child { border-top: 0; }
.empty { padding: 26px 10px; text-align: center; color: var(--ink2); }

/* шторка «Витрата готівкою» */
.sheet-bg { position: fixed; inset: 0; z-index: 40; background: rgba(17,19,24,.38); opacity: 0; transition: opacity .55s var(--ease); }
.sheet { position: fixed; left: 0; right: 0; bottom: 0; z-index: 41; max-width: 440px; max-height: 92vh; margin: 0 auto; display: flex; flex-direction: column;
  background: var(--card); border-radius: 28px 28px 0 0; transform: translateY(100%); transition: transform .6s var(--ease); }
.sheet.open { transform: none; }
.sheet-bg.open { opacity: 1; }
.sheet-head { display: grid; grid-template-columns: 1fr 44px; align-items: center; padding: 8px 16px 0; touch-action: none; }
.sheet-head::before { content: ""; grid-column: 1 / -1; justify-self: center; width: 36px; height: 4px; margin-bottom: 10px; border-radius: 2px; background: #D9DDE3; }
.sheet-head h2 { margin: 0; font-size: 18px; font-weight: 500; }
.sheet-head button { width: 44px; height: 44px; border: 0; border-radius: 22px; background: var(--soft); display: grid; place-items: center; cursor: pointer; }
.sheet-head button svg { width: 16px; height: 16px; }
.sheet-scroll { overflow-y: auto; padding: 0 16px 12px; }
.amount { display: flex; justify-content: center; align-items: baseline; gap: 6px; padding: 22px 0 0; cursor: text; }
.amount input { width: 1.6ch; min-width: 1.6ch; max-width: 100%; border: 0; outline: 0; background: transparent; text-align: right;
  font: 500 48px/1.1 var(--font); letter-spacing: -0.02em; color: var(--ink); caret-color: var(--blue); }
.amount input::placeholder { color: var(--mute); opacity: 1; }
.amount input::-webkit-input-placeholder { color: var(--mute); opacity: 1; }
.amount span { font-size: 22px; color: var(--ink2); }
.amount-sub { margin: 4px 0 14px; text-align: center; font-size: 12px; color: var(--ink2); }
.lab { margin: 14px 2px 8px; font-size: 13px; font-weight: 500; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chips button { min-height: 44px; padding: 0 14px; border: 1px solid var(--border); border-radius: 22px; background: var(--card); font-size: 14px; cursor: pointer; }
.chips button[aria-pressed="true"] { border-color: transparent; background: var(--blue-g); color: #fff; font-weight: 500; }
.field { width: 100%; height: 48px; margin-top: 14px; padding: 0 14px; border: 0; border-radius: 14px; background: var(--soft); font: 15px var(--font); color: var(--ink); }
.field:focus { outline: 2px solid var(--blue); outline-offset: 0; }
.dates { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.dates button { min-height: 52px; border: 0; border-radius: 14px; background: var(--soft); font-size: 14px; font-weight: 500; line-height: 1.2; cursor: pointer; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.dates button small { font-size: 12px; font-weight: 400; color: var(--ink2); }
.dates button.other { flex-direction: row; gap: 6px; }
.dates button[aria-pressed="true"] { background: var(--blue-g); color: #fff; }
.dates button[aria-pressed="true"] small { color: rgba(255,255,255,.8); }
.vh { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.info { margin-top: 14px; padding: 12px 14px; border-radius: 14px; background: #FFF7E8; color: var(--cash); font-size: 12px; line-height: 1.5; }
.sheet-foot { padding: 10px 16px calc(14px + env(safe-area-inset-bottom, 0px)); }
.msg { margin: 0 4px 8px; font-size: 14px; text-align: center; color: var(--bad); }
.msg:empty { display: none; }
.save { width: 100%; height: 52px; border: 0; border-radius: 26px; background: var(--blue-g); box-shadow: 0 8px 18px rgba(29,95,224,.3); color: #fff; font: 500 16px var(--font); cursor: pointer; }
.save:disabled { background: #D9DDE3; box-shadow: none; cursor: default; }
.toast { position: fixed; left: 50%; bottom: calc(104px + env(safe-area-inset-bottom, 0px)); z-index: 30; transform: translateX(-50%); display: flex; align-items: center; gap: 10px;
  padding: 12px 18px; border-radius: 16px; background: var(--ink); color: #fff; font-size: 14px; white-space: nowrap; }
.toast i { width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; background: var(--good); font-style: normal; font-size: 12px; }

/* нижня навігація v2: біла плаваюча панель, активний пункт — синя капсула; fade під нею */
.fade { z-index: 19; position: fixed; left: 0; right: 0; bottom: 0; height: 130px; pointer-events: none; background: linear-gradient(rgba(245,246,248,0), var(--bg) 70%); }
.nav { z-index: 20; position: fixed; left: 50%; bottom: calc(22px + env(safe-area-inset-bottom, 0px)); transform: translateX(-50%); width: min(342px, calc(100vw - 48px)); height: 64px;
  display: flex; align-items: center; padding: 0 8px; border: 1px solid var(--border); border-radius: 32px; background: rgba(255,255,255,.96); box-shadow: 0 10px 30px rgba(17,19,24,.10); }
.nav a { position: relative; flex: 1; height: 48px; border-radius: 24px; display: flex; align-items: center; justify-content: center; font-size: 15px; color: var(--ink3); transition: color .35s var(--ease); }
.nav a.on { color: var(--blue); font-weight: 500; }
/* плашка активної вкладки — одна на всіх, переїжджає з пружинкою */
.nav .ind { position: absolute; left: 8px; top: 7px; width: calc((100% - 16px) / 3); height: 48px; border-radius: 24px; background: var(--blue-soft);
  transform: translateX(calc(var(--n, 0) * 100%)); transition: transform .7s var(--spring); }
.nav { padding: 0 8px; }
/* ---------- рух ---------- */
/* екран завантаження: знак із градієнтом, назва, три крапки; зникає, щойно готові шрифт і дані */
.splash { position: fixed; inset: 0; z-index: 100; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; background: var(--bg);
  transition: opacity .6s var(--ease), visibility 0s linear .6s; }
.splash.out { opacity: 0; visibility: hidden; }
.splash.out .sp-mark { transform: scale(.9); }
.sp-mark { position: relative; width: 84px; height: 84px; border-radius: 30px; display: grid; place-items: center; overflow: hidden; isolation: isolate;
  background: var(--blue-g); box-shadow: 0 10px 24px rgba(29,95,224,.34), inset 0 1px 0 rgba(255,255,255,.3);
  animation: breathe 2.4s ease-in-out infinite; transition: transform .6s var(--ease); }
/* відблиск, що проходить по кнопці */
.sp-mark::before { content: ""; position: absolute; inset: 0; z-index: -1; background: linear-gradient(110deg, transparent 35%, rgba(255,255,255,.28) 50%, transparent 65%);
  transform: translateX(-100%); animation: shine 1.8s ease-in-out infinite; }
.sp-mark svg { width: 40px; height: 40px; }
.sp-name { font-size: 16px; font-weight: 500; color: var(--ink); opacity: 0; animation: rise .5s .15s var(--ease) both; }
/* повзунок: швидко доходить до ~85 %, а коли все готово — до кінця */
.sp-bar { width: 148px; height: 6px; margin-top: 4px; border-radius: 3px; background: var(--track); overflow: hidden; }
.sp-bar b { display: block; height: 100%; width: 0; border-radius: 3px; background: var(--blue-g); transition: width 1.2s var(--ease); }
.splash.done .sp-bar b { transition-duration: .3s; }
@keyframes shine { 0% { transform: translateX(-100%); } 60%, 100% { transform: translateX(100%); } }
@keyframes breathe { 50% { transform: scale(1.05); } }
/* поява екрана: картки виїжджають знизу одна за одною */
.stag > *:not([hidden]), .stag .stagk:not([hidden]) { animation: lift 1s var(--ease) both; animation-delay: calc(var(--i, 0) * 45ms); }
@keyframes lift { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
/* натискання: кнопка трохи «вдавлюється» */
button, .cashbtn, .ch a, .hero .cols a, .more, .op { transition: transform .45s var(--spring), background-color .4s var(--ease), box-shadow .4s var(--ease), color .4s var(--ease); }
button:active:not(:disabled), .cashbtn:active, .ch a:active, .more:active { transition-duration: .12s; }
button:active:not(:disabled), .cashbtn:active, .nav a:active, .ch a:active, .more:active { transform: scale(.96); }
.cashbtn:hover { box-shadow: 0 10px 22px rgba(29,95,224,.4), inset 0 1px 0 rgba(255,255,255,.3); }
/* спливаючі меню розкриваються від кнопки */
.pop:not([hidden]) { animation: popin .45s var(--ease) both; transform-origin: top left; }
.mmenu:not([hidden]) { transform-origin: top right; }
@keyframes popin { from { opacity: 0; transform: translateY(-6px) scale(.96); } to { opacity: 1; transform: none; } }
/* тост виринає знизу */
.toast { animation: toastin .7s var(--spring) both; }
@keyframes toastin { from { opacity: 0; transform: translate(-50%, 16px) scale(.96); } to { opacity: 1; transform: translateX(-50%); } }
@media (prefers-reduced-motion: reduce) {
  .splash, .sp-mark, .sp-mark::before, .sp-bar b, .sp-name, .stag > *, .hero .num.cu, .pop:not([hidden]), .toast { animation: none !important; transition: none !important; }
  .nav .ind { transition: none; }
}
@media (prefers-reduced-motion: reduce) { .cap .fill, .crow .bar6 b, .segs i b, .hero .meter b { transition: none; } .fadein, .liquid i { animation: none; } }
</style>
</head>
<body class="p-marko">
<div class="splash" id="splash" role="progressbar" aria-label="Завантаження">
  <div class="sp-mark"><svg viewBox="0 0 24 24" fill="#fff" aria-hidden="true">
    <rect x="5" y="4.5" width="16" height="10" rx="2.6" opacity=".45"/>
    <path fill-rule="evenodd" d="M5.6 8.5h12.8a2.6 2.6 0 0 1 2.6 2.6v5.8a2.6 2.6 0 0 1-2.6 2.6H5.6A2.6 2.6 0 0 1 3 16.9v-5.8a2.6 2.6 0 0 1 2.6-2.6zM12 11.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z"/>
  </svg></div>
  <div class="sp-name">Фінанси Олі й Марка</div>
  <div class="sp-bar" aria-hidden="true"><b id="sp-fill"></b></div>
</div>
</div>
<div class="app">
  <div class="top">
    <div class="who">
      <button type="button" id="who-btn" aria-haspopup="menu" aria-expanded="false" aria-controls="who-menu"><span class="av" id="who-av"></span><span id="who-name"></span><svg class="ico16 chev" viewBox="0 0 24 24" fill="none" stroke="#6B7180" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>
      <div class="pop" id="who-menu" role="menu" aria-label="Чиї дані показати" hidden><p>Чиї дані показати</p><div id="who-opts"></div>
        <div class="foot">Вхід спільний: це не акаунт, а лише чиї дані на екрані. Обоє бачать обидва журнали.</div></div>
    </div>
    <div class="mpick" id="mpick">
      <button type="button" id="mcal" aria-haspopup="dialog" aria-expanded="false" aria-controls="mmenu"><svg class="ico16" viewBox="0 0 24 24" fill="none" stroke="#6B7180" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg><span id="mcal-name"></span><svg class="ico16 chev" viewBox="0 0 24 24" fill="none" stroke="#6B7180" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>
      <div class="pop mmenu" id="mmenu" role="dialog" aria-label="Вибір дня" hidden>
        <div class="cal-head"><button type="button" id="cal-prev" aria-label="Попередній місяць"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 6-6 6 6 6"/></svg></button>
          <b id="cal-year"></b><button type="button" id="cal-next" aria-label="Наступний місяць"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg></button></div>
        <div class="cal-wd" aria-hidden="true"><span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span>Сб</span><span>Нд</span></div>
        <div class="cal-grid" id="cal-grid"></div>
        <p class="cal-note" id="cal-note"></p>
      </div>
    </div>
    <span class="mstatic" id="mrange" hidden></span>
  </div>

  <div id="v-report">
    <div id="h-alert"></div>
    <div class="hero-wrap"><div class="liquid" id="liquid" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div><section class="hero" id="hero" aria-live="polite"></section></div>
    <a class="cashbtn" id="cash" href="#add"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/></svg>Додати готівку</a>
    <section class="card goal" id="goal" hidden></section>
    <section class="card list fadein" id="insights" hidden></section>
    <section class="card list"><div class="ch"><h2>Категорії</h2><a href="#analytics" id="cats-all"></a></div><div id="cats"></div></section>
    <section class="card list"><div class="ch"><h2>Останні операції</h2><a href="#journal" id="ops-all">Журнал<span id="arr-j"></span></a></div><div id="recent"></div></section>
  </div>

  <div id="v-analytics" hidden>
    <div class="seg" id="a-mode" role="group" aria-label="Розділ аналітики"></div>
    <div id="an-month">
      <section class="card">
        <div class="ch"><h2>Витрати по тижнях</h2><div class="seg sm" id="rent-mode" role="group" aria-label="Оренда в графіку" hidden></div></div>
        <div class="note" id="wnote" style="margin-top:10px"></div>
        <div class="chart"><div class="ticks" id="wticks" aria-hidden="true"></div><div class="caps" id="bars"></div></div>
      </section>
      <section class="card"><div class="ch" style="min-height:0;margin-bottom:12px"><h2>Фіксовані й змінні</h2></div><div class="split" id="fvsplit" aria-hidden="true"></div><div class="fv2" id="fv"></div><p class="note" id="fvnote" style="margin:14px 0 0"></p></section>
      <section class="card list"><div class="ch"><h2>Усі категорії</h2><span class="sub" id="fcatn"></span></div><div id="fcats"></div></section>
      <a class="fava" id="fava">Детальні запити у Fava<span id="arr-f"></span></a>
    </div>
    <div id="an-trends" hidden>
      <section class="card">
        <div class="seg sm" id="t-metric" role="group" aria-label="Показник"></div>
        <div class="chart"><div class="ticks" id="mticks" aria-hidden="true"></div><div class="caps" id="mbars"></div></div>
      </section>
      <div class="duo" id="t-duo"></div>
      <p class="note" id="t-note" style="margin:8px 4px 0"></p>
      <section class="card list"><div class="ch"><h2 id="t-dmon"></h2><button type="button" class="sub" id="t-chg" style="border:0;background:none;color:var(--blue);font-weight:500;min-height:44px;display:inline-flex;align-items:center;gap:6px;cursor:pointer">Змінити місяць</button></div><div class="share" id="t-share" aria-hidden="true"></div><div id="t-leg" style="padding-bottom:8px"></div></section>
      <section class="card list"><div class="ch"><h2 id="t-cmp"></h2></div><div id="t-cats"></div><p class="note" id="t-cmpnote" style="margin:6px 0 8px"></p></section>
    </div>
  </div>

  <div id="v-journal" hidden>
    <div class="filters" id="j-filters" role="group" aria-label="Фільтр"></div>
    <div class="jsum">
      <div class="card"><span class="ji" style="background:var(--blue-soft);color:var(--blue)" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 2.5h12a1 1 0 0 1 1 1v17.2a.6.6 0 0 1-.95.5L15.5 19.4l-2.9 1.9a1 1 0 0 1-1.1 0l-2.9-1.9-2.55 1.8A.6.6 0 0 1 5 20.7V3.5a1 1 0 0 1 1-1z"/><path fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" d="M9 8h6M9 12h6M9 15.5h3"/></svg></span><div><span>Операцій</span><b id="j-count"></b></div></div>
      <div class="card"><span class="ji" style="background:var(--bad-bg);color:var(--bad)" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 16.5 7.5M9.5 7.5h7v7"/></svg></span><div><span>Витрати</span><b id="j-sum"></b></div></div>
    </div>
    <div id="j-list"></div>
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
      <input class="vh" id="a-date" type="date" tabindex="-1" aria-hidden="true">
      <div class="info">Лише готівка. Оплати карткою вносити не треба: вони прийдуть з банківською випискою, інакше задвояться.</div>
    </div>
    <div class="sheet-foot"><p class="msg" id="a-msg" role="status"></p><button class="save" id="a-save" type="submit" disabled>Зберегти</button></div>
  </form>
</div>
<div class="fade" aria-hidden="true"></div>
<nav class="nav" aria-label="Навігація">
  <i class="ind" id="nav-ind" aria-hidden="true"></i>
  <a id="navr" href="#report">Головна</a>
  <a id="navt" href="#analytics">Аналітика</a>
  <a id="navj" href="#journal">Журнал</a>
</nav>

<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
"use strict";
var D = JSON.parse(document.getElementById("data").textContent);
var FAVA = { marko: "fava/%D1%84%D1%96%D0%BD%D0%B0%D0%BD%D1%81%D0%B8-%D0%BC%D0%B0%D1%80%D0%BA%D0%B0/",
             olya: "fava/%D1%84%D1%96%D0%BD%D0%B0%D0%BD%D1%81%D0%B8-%D0%BE%D0%BB%D1%96/" };
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
function grow(sel, attr) {  // плавне заповнення після першого кадру
  requestAnimationFrame(function () { requestAnimationFrame(function () {
    document.querySelectorAll(sel).forEach(function (e, i) { e.style.transitionDelay = Math.min(i, 8) * 80 + 150 + "ms"; e.style[attr] = e.dataset.to; });
  }); });
}
function plural(n, one, few, many) { var a = n % 10, b = n % 100; return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many; }
function svgI(color, body, size) {
  return '<svg viewBox="0 0 24 24" width="' + (size || 18) + '" height="' + (size || 18) + '" fill="none" stroke="' + color + '" stroke-width="' + (color === "#fff" || color === "currentColor" || color === "#6B7180" ? 1.8 : 2.1) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + "</svg>";
}
var CHR = svgI("currentColor", '<path d="m9 6 6 6-6 6"/>', 16);
var CAL = svgI("#6B7180", '<rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/>', 16);
// кольорові іконки категорій (з макета v2): фон, колір лінії, малюнок
var CATS = {
  "Продукти": ["#E3F7EA", "#16A34A", '<path d="M2 3.25A.75.75 0 0 1 2.75 2.5h1.6c.6 0 1.1.42 1.22 1L5.8 5H20a1 1 0 0 1 .97 1.24l-1.6 6.6A2 2 0 0 1 17.4 14.4H8.1l.3 1.6H18a.9.9 0 0 1 0 1.8H7.7a1 1 0 0 1-.98-.8L4 4H2.75A.75.75 0 0 1 2 3.25z"/><circle cx="9" cy="20.2" r="1.6"/><circle cx="17.5" cy="20.2" r="1.6"/>'],
  "Кафе": ["#FDE8EC", "#E11D48", '<path fill-rule="evenodd" d="M4 7.5a.5.5 0 0 1 .5-.5h12a.5.5 0 0 1 .5.5V9h1.3a3.2 3.2 0 0 1 0 6.4h-1.7A6 6 0 0 1 11.2 19H9.8A5.8 5.8 0 0 1 4 13.2zM17 10.8v2.8h1.3a1.4 1.4 0 0 0 0-2.8z"/><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M8 2.5V4.5M12 2.5V4.5"/>'],
  "Доставка Їжі": ["#E6EFFE", "#0284C7", '<path fill-rule="evenodd" d="M8 7V6a4 4 0 0 1 8 0v1h2.1a1 1 0 0 1 1 .92l.9 11A2 2 0 0 1 18 21H6a2 2 0 0 1-2-2.08l.9-11A1 1 0 0 1 5.9 7zm1.8 0h4.4V6a2.2 2.2 0 0 0-4.4 0z"/>'],
  "Звязок": ["#F1E8FD", "#9333EA", '<path fill-rule="evenodd" d="M8.5 2h7A2.5 2.5 0 0 1 18 4.5v15a2.5 2.5 0 0 1-2.5 2.5h-7A2.5 2.5 0 0 1 6 19.5v-15A2.5 2.5 0 0 1 8.5 2zm2.5 16a1 1 0 0 0 0 2h2a1 1 0 0 0 0-2z"/>'],
  "Дохід": ["#E3F7EA", "#16A34A", '<path fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="M17 7 7.5 16.5M7.5 9.5v7h7"/>'],
  "Комунальні": ["#DFF5F2", "#0D9488", '<path d="M13.6 2.2a.6.6 0 0 1 1 .5L13.5 10H18a.6.6 0 0 1 .5 1l-8.1 10.8a.6.6 0 0 1-1-.5L10.5 14H6a.6.6 0 0 1-.5-1z"/>'],
  "Спорт": ["#FFEDDF", "#EA580C", '<rect x="5" y="5.5" width="3.2" height="13" rx="1.2"/><rect x="15.8" y="5.5" width="3.2" height="13" rx="1.2"/><rect x="2" y="8.5" width="2.6" height="7" rx="1"/><rect x="19.4" y="8.5" width="2.6" height="7" rx="1"/><rect x="8" y="11" width="8" height="2" rx=".5"/>'],
  "Житло": ["#E9EAFD", "#4F46E5", '<path d="M11.3 2.8a1.1 1.1 0 0 1 1.4 0l8 6.6a1 1 0 0 1-.65 1.8H19V20a1 1 0 0 1-1 1h-4v-5h-4v5H6a1 1 0 0 1-1-1v-8.8H3.95a1 1 0 0 1-.65-1.8z"/>'],
  "Транспорт": ["#E0F2FE", "#2563EB", '<path fill-rule="evenodd" d="M8 3h8a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3zm-1 3.5v4h10v-4z"/><circle cx="8.5" cy="14.5" r="1.1" fill="#fff"/><circle cx="15.5" cy="14.5" r="1.1" fill="#fff"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M8 18.5V21M16 18.5V21"/>'],
  "Підписки": ["#FEF6D8", "#D97706", '<circle cx="12" cy="12" r="9.5"/><path fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M8 12a4 4 0 0 1 6.9-2.8M16 12a4 4 0 0 1-6.9 2.8M15.4 6.8v2.6h-2.6M8.6 17.2v-2.6h2.6"/>'],
  "Краса і Побут": ["#FDE8F4", "#DB2777", '<path stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" d="M11 3.5l1.8 5.2L18 10.5l-5.2 1.8L11 17.5l-1.8-5.2L4 10.5l5.2-1.8z"/><path d="M18.5 14.5l.75 1.75L21 17l-1.75.75-.75 1.75-.75-1.75L16 17l1.75-.75z"/>'],
  "Розваги": ["#EDE9FE", "#C026D3", '<path fill-rule="evenodd" d="M7 7h10a5 5 0 0 1 4.9 6l-.5 2.6a3 3 0 0 1-5.1 1.5L14.6 15H9.4l-1.7 2.1a3 3 0 0 1-5.1-1.5L2.1 13A5 5 0 0 1 7 7zm.2 3a.9.9 0 0 0-.9.9v.9h-.9a.9.9 0 0 0 0 1.8h.9v.9a.9.9 0 0 0 1.8 0v-.9h.9a.9.9 0 0 0 0-1.8h-.9v-.9a.9.9 0 0 0-.9-.9zm8.8 1a1 1 0 1 0 0 2 1 1 0 0 0 0-2z"/>'],
  "Покупки": ["#FCE7F3", "#7C3AED", '<path fill-rule="evenodd" d="M8.2 7a3.8 3.8 0 0 1 7.6 0h2.3a1 1 0 0 1 1 .9l1 12A1 1 0 0 1 19 21H5a1 1 0 0 1-1-1.1l1-12A1 1 0 0 1 6 7zm1.8 0h4a2 2 0 0 0-4 0z"/>'],
  "Інші": ["#F1F2F5", "#64748B", '<circle cx="5.5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="18.5" cy="12" r="2"/>']
};
// зсув малюнка до центру кола: виміряно за видимими пікселями (разом із товщиною ліній)
// плюс невелика оптична поправка на «вагу» — щоб сумки, чашка й кошик не здавались зміщеними
var SHIFT = {"Продукти": [0.18, 0.25], "Кафе": [-0.2, 1.08], "Доставка Їжі": [0, -0.19], "Звязок": [0, 0.09], "Дохід": [0.14, -0.15], "Комунальні": [0, 0], "Спорт": [0, 0], "Житло": [0, 0.01], "Транспорт": [0, -0.2], "Підписки": [0, 0], "Краса і Побут": [0.06, 0.89], "Розваги": [-0.15, -0.3], "Покупки": [-0.05, -0.67], "Інші": [0, 0]};
// іконка категорії: світло-сірий круг, залитий малюнок у кольорі категорії, по центру
// яскраві кольори категорій — для шкал, часток і крапок; малюнки іконок беруть темніший відтінок
var BRIGHT = {"Продукти": "#22C55E", "Кафе": "#F43F5E", "Доставка Їжі": "#0EA5E9", "Звязок": "#A855F7", "Дохід": "#22C55E", "Комунальні": "#14B8A6", "Спорт": "#FB923C", "Житло": "#6366F1", "Транспорт": "#3B82F6", "Підписки": "#F59E0B", "Краса і Побут": "#EC4899", "Розваги": "#D946EF", "Покупки": "#8B5CF6", "Інші": "#94A3B8"};
function bright(n) { return BRIGHT[n] || BRIGHT["Інші"]; }
function cic(name, big) {
  var k = CATS[name] ? name : "Інші", c = CATS[k], sh = SHIFT[k] || [0, 0];
  var s = big ? 20 : 18;
  return '<span class="cic' + (k === "Дохід" ? " inc" : "") + '"><svg viewBox="0 0 24 24" width="' + s + '" height="' + s + '" fill="currentColor" style="color:' + c[1] + '" aria-hidden="true">' +
    '<g transform="translate(' + sh[0] + " " + sh[1] + ')">' + c[2] + "</g></svg></span>";
}
// стрілка зміни: діагональна вгору/вниз
function arr(d) { return d > 0 ? "↗" : "↘"; }

function lastFull(P) { for (var i = P.months.length - 1; i >= 0; i--) if (P.months[i].income > 0) return i; return P.months.length - 1; }
var person = 0, month = lastFull(D.people[0]), trendMonth = null, filter = "all", metric = "exp", rentOut = false, amode = "month";
var GOAL = { olya: { months: 6, deadline: [2027, 6], title: "Подушка безпеки" } };  // фіча 5 з роадмапу ДЗ
var VIEWS = { report: 1, analytics: 1, journal: 1, add: 1 };
var saved = null;  // після збереження готівки: сума й дата, щоб підсвітити новий запис
(function () {
  var q = new URLSearchParams(location.search), pi = -1;
  D.people.forEach(function (p, i) { if (p.key === q.get("p")) pi = i; });
  if (pi < 0) { history.replaceState(null, "", location.pathname + location.hash); return; }
  person = pi;
  D.people[pi].months.forEach(function (m, i) { if (m.key === q.get("m")) month = i; });
  if (q.get("saved")) {
    saved = { amount: +q.get("a") || 0, day: q.get("d") || "", line: +q.get("l") || 0, title: q.get("t") || "" };
    setTimeout(function () {
      toast('<i aria-hidden="true">✓</i>Збережено: −' + zl(saved.amount), saved.line ? ["Скасувати", function () {
        delCash(D.people[person], saved.line, saved.day, saved.title, "#report");
      }] : null);
    }, 100);
  }
  if (q.get("deleted")) setTimeout(function () { toast('<i aria-hidden="true">✓</i>Запис видалено'); }, 100);
  history.replaceState(null, "", location.pathname + location.hash);
})();
function view() {
  var v = location.hash.slice(1);
  if (v === "trends") { amode = "trends"; return "analytics"; }
  return VIEWS[v] ? v : "report";
}

// ---------- шапка: людина ----------
var ROLE = { marko: "тут вноситься готівка", olya: "тут подушка безпеки" };
function renderPeople() {
  var P = D.people[person];
  document.body.className = "p-" + P.key;
  $("who-av").className = "av " + P.key;
  $("who-name").textContent = P.name;
  $("who-btn").setAttribute("aria-label", "Дані: " + P.name + ". Змінити");
  var box = $("who-opts"); box.innerHTML = "";
  D.people.forEach(function (p, i) {
    var b = h("button", "opt", '<span class="av ' + p.key + '"></span><span><b>' + esc(p.name) + "</b><small>" + (ROLE[p.key] || "") + '</small></span><span class="radio" aria-hidden="true"></span>');
    b.type = "button"; b.setAttribute("role", "menuitemradio"); b.setAttribute("aria-checked", i === person);
    b.onclick = function () { popMenu("w", false); if (i === person) return; person = i; month = lastFull(D.people[i]); trendMonth = null; selDay = null; render(); };
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
    if (!b.disabled) (function (key) { b.onclick = function () { selDay = key; month = ki; popMenu("m", false); render(); scrollToDay(key); }; })(key);
    grid.appendChild(b);
  }
  var lastM = P.months[P.months.length - 1];
  $("cal-note").textContent = M.incomplete ? "Сірі дні — даних ще немає. Облік до " + M.last_op + "." :
    "Дані є за " + P.months[0].name.toLowerCase() + " – " + lastM.name.toLowerCase() + " " + lastM.key.slice(0, 4) + ".";
}
function scrollToDay(key) {
  if (view() !== "journal") return;
  var el = document.querySelector('[data-day="' + key + '"]');
  if (el) requestAnimationFrame(function () { window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 12); });
}
$("cal-prev").onclick = function () { var P = D.people[person], k = P.months.map(function (m) { return m.key; }).indexOf(calKey); if (k > 0) { calKey = P.months[k - 1].key; renderCal(P); } };
$("cal-next").onclick = function () { var P = D.people[person], k = P.months.map(function (m) { return m.key; }).indexOf(calKey); if (k < P.months.length - 1) { calKey = P.months[k + 1].key; renderCal(P); } };
var MENUS = { w: ["who-menu", "who-btn"], m: ["mmenu", "mcal"] };
function popMenu(k, open) {
  Object.keys(MENUS).forEach(function (x) { var on = x === k && open; $(MENUS[x][0]).hidden = !on; $(MENUS[x][1]).setAttribute("aria-expanded", on); });
  if (open) { var cur = $(MENUS[k][0]).querySelector('[aria-checked="true"], [aria-pressed="true"]'); if (cur) cur.focus(); }
}
$("who-btn").onclick = function () { popMenu("w", $("who-menu").hidden); };
$("mcal").onclick = function () { popMenu("m", $("mmenu").hidden); };
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
function prevWithIncome(P, k) { for (var j = k - 1; j >= 0; j--) if (P.months[j].income > 0) return P.months[j]; return null; }
function fixedCats(M) {
  var fx = {};
  M.ops.forEach(function (o) { if (o.income) return; fx[o.cat] = (fx[o.cat] === undefined ? true : fx[o.cat]) && o.fixed; });
  return fx;
}
function nice(s) { return s === s.toUpperCase() ? s.toLowerCase().replace(/(^|[\s\/*.-])(\S)/g, function (_, a, b) { return a + b.toUpperCase(); }) : s; }
var TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>';
function opRow(o, recent, fresh) {
  return '<div class="op' + (fresh ? " fresh" : "") + '">' + cic(o.income ? "Дохід" : o.cat, true) + '<div class="t"><div class="t1' + (recent ? " one" : "") + '">' + esc(nice(o.title)) + "</div>" +
    '<div class="t2"><span>' + esc(o.income ? "Дохід" : o.cat) + (recent ? '<i class="sep" aria-hidden="true"></i>' + o.date : "") + "</span>" + (o.fixed ? '<span class="fx">фіксована</span>' : "") + (o.cash ? '<span class="fx cash">готівка</span>' : "") +
    (fresh ? '<span class="fx now">щойно</span>' : "") + '</div></div><div class="r"><span class="am' + (o.income ? " inc" : "") + '">' + (o.income ? "+" : "−") + zl(o.amount) + "</span>" +
    "</div>" +
    // у журналі готівку з форми можна видалити (записи з банківської виписки — ні)
    (!recent && o.line ? '<button type="button" class="del" data-line="' + o.line + '" data-day="' + o.day + '" data-title="' + esc(o.title) + '" aria-label="Видалити запис «' + esc(o.title) + '»">' + TRASH + "</button>" : "") + "</div>";
}
function catRows(M, limit) {
  var fx = fixedCats(M), shown = limit ? M.categories.slice(0, limit) : M.categories, out = "";
  var pct = function (a) { var p = M.expenses ? a / M.expenses * 100 : 0; return (limit ? nf0 : nf1).format(p) + " %"; };
  shown.forEach(function (c) {
    var p = M.expenses ? c.amount / M.expenses * 100 : 0;
    out += '<div class="crow">' + cic(c.name) + '<span class="nm">' + esc(c.name) + (fx[c.name] ? '<span class="fx">фіксована</span>' : "") + '</span><span class="am">' + zl(c.amount) +
      '</span><span class="bd blue">' + pct(c.amount) + '</span><div class="bar6"><b style="background:' + bright(c.name) + '" data-to="' + Math.max(p, 1) + '%"></b></div></div>';
  });
  var rest = M.categories.slice(shown.length);
  if (rest.length) {
    var sum = rest.reduce(function (s, c) { return s + c.amount; }, 0);
    out += '<a class="more" href="#analytics"><span>Ще ' + rest.length + " " + plural(rest.length, "категорія", "категорії", "категорій") + "</span><span>" + zl(sum) + "</span></a>";
  }
  return out;
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
function renderReport(P) {
  var M = P.months[month], k = mi(M), prevM = P.months[month - 1] || null, prevI = prevWithIncome(P, month);
  var noIncome = !(M.income > 0), hero = $("hero"), approx = P.ok ? "" : "≈ ";
  $("h-alert").innerHTML = P.ok ? "" : '<div class="alert" role="alert"><b>Журнал не сходиться</b>Перевірка обліку знайшла помилки, тож цифри нижче можуть бути неточні.<br><a href="' +
    FAVA[P.key] + 'errors/">Переглянути у Fava</a></div>';
  var tag = M.incomplete ? '<span class="tag">дані до ' + M.last_op + "</span>" : "";
  if (noIncome) {
    var usual = prevI ? prevI.income : 0, share = usual ? M.expenses / usual * 100 : 0, days = payDays(P);
    hero.innerHTML = tag + '<div class="lbl">Витрачено до зарплати</div><div class="num">' + approx + zl(M.expenses) + "</div>" +
      (usual ? '<div class="meter"><b data-to="' + Math.min(100, share) + '%"></b></div><div class="sub">' + nf1.format(share) + " % звичної зарплати " + nf0.format(usual) + " зл</div>" : "") +
      '<div class="hint"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>' +
      "<span>Зарплата зазвичай приходить " + (days ? days + " числа" : "наприкінці місяця") + ". «Лишилось» порахуємо після неї.</span></div>";
  } else {
    var inc = M.incomplete ? "<i>надійшов</i>" : (prevI ? badge(M.income, prevI.income, false, GEN[mi(prevI)]) : "");
    var exp = M.incomplete ? "<i>порівняємо після кінця місяця</i>" : (prevM ? badge(M.expenses, prevM.expenses, true, GEN[mi(prevM)]) : "");
    var dn = +M.last_op.slice(0, 2);
    hero.innerHTML = tag + '<div class="lbl">Лишилось у ' + LOC[k] + '</div><div class="num">' + approx + zl(M.saved) + "</div>" +
      '<div class="sub">' + nf1.format(M.rate) + " % від доходу" + (M.incomplete ? ", поки що" : "") + (P.ok ? "" : ", потребує перевірки") + "</div>" +
      '<div class="cols"><a href="#journal" data-filter="inc"><span class="t">Дохід</span><b>' + zl(M.income) + "</b>" + inc + "</a>" +
      '<a href="#journal" data-filter="exp"><span class="t">Витрати' + (M.incomplete ? " за " + dn + " " + plural(dn, "день", "дні", "днів") : "") + "</span><b>" + zl(M.expenses) + "</b>" + exp + "</a></div>";
  }
  hero.querySelectorAll("[data-filter]").forEach(function (a) { a.onclick = function () { filter = a.dataset.filter; }; });
  $("cash").hidden = !P.cash;
  renderGoal(P);
  renderInsights(P, M);
  $("cats-all").innerHTML = "<span>Усі " + M.categories.length + "</span>" + CHR;
  $("cats").innerHTML = catRows(M, 3);
  var last = P.months[P.months.length - 1], ops = last ? last.ops.slice(0, 5) : [], freshDone = false;
  $("recent").innerHTML = ops.length ? ops.map(function (o) {
    var fresh = saved && !freshDone && o.cash && Math.abs(o.amount - saved.amount) < 0.005 && (!saved.day || o.day === saved.day);
    if (fresh) freshDone = true;
    return opRow(o, true, fresh);
  }).join("") : '<div class="empty">Операцій поки немає</div>';
  grow("#hero .meter b, #v-report .bar6 b, #goal .segs b", "width");
}
// «Вересень коротко»: факт і бейдж праворуч; у неповному місяці — що вже відомо
function renderInsights(P, M) {
  var box = $("insights"), rows = [], k = mi(M);
  var add = function (text, bd) { rows.push('<div class="ins"><span>' + text + "</span>" + (bd || "") + "</div>"); };
  if (!(M.income > 0)) { box.hidden = true; return; }
  var title = MONTHS_FULL[k] + " коротко";
  if (M.incomplete) {
    title = "Поки що в " + LOC[k];
    add("Фіксовані вже сплачено, далі переважно змінні", '<span class="bd">' + zl(M.fixed) + "</span>");
    add("Змінні за " + (+M.last_op.slice(0, 2)) + " " + plural(+M.last_op.slice(0, 2), "день", "дні", "днів"), '<span class="bd blue">' + zl(M.variable) + "</span>");
    add("Частка ще зменшиться, місяць не закінчився", '<span class="bd new">' + nf1.format(M.rate) + " %</span>");
  } else {
    var pi = prevWithIncome(P, month);
    if (pi) {
      var pp = M.rate - pi.rate;
      add("Лишилось " + nf1.format(M.rate) + " % доходу, у " + LOC[mi(pi)] + " — " + nf1.format(pi.rate) + " %",
        Math.abs(pp) < 0.05 ? '<span class="bd">без змін</span>' : '<span class="bd ' + (pp > 0 ? "good" : "bad") + '">' + arr(pp) + " " + nf1.format(Math.abs(pp)) + " п. п.</span>");
    }
    var top = M.categories[0];
    if (top && M.expenses) {
      var share = top.amount / M.expenses * 100;
      add(share > 50 && top.name === "Житло" ? "Більше половини забрала оренда" : "Найбільше пішло на " + top.name.toLowerCase(), '<span class="bd blue">' + nf0.format(share) + " % витрат</span>");
    }
    var pm = P.months[month - 1];
    if (pm && pm.variable) add("Змінні витрати проти " + GEN[mi(pm)], badge(M.variable, pm.variable, true, "", true));
  }
  box.hidden = !rows.length;
  box.innerHTML = '<div class="ch"><h2>' + title + "</h2></div>" + rows.join("");
  box.classList.remove("fadein"); void box.offsetWidth; box.classList.add("fadein");
}
// подушка: ціль = 6 × середні витрати повних місяців; накопичене — що лишилось у повних місяцях
function renderGoal(P) {
  var G = GOAL[P.key], box = $("goal");
  var full = P.months.filter(function (m) { return !m.incomplete && m.income > 0; });
  box.hidden = !G || !full.length;
  if (box.hidden) return;
  var avgExp = full.reduce(function (s, m) { return s + m.expenses; }, 0) / full.length;
  var target = G.months * avgExp, have = full.reduce(function (s, m) { return s + m.saved; }, 0), life = have / avgExp;
  var pace = have / full.length, last = full[full.length - 1], y = +last.key.slice(0, 4), m = +last.key.slice(5);
  var left = Math.max(0, target - have), monthsLeft = (G.deadline[0] * 12 + G.deadline[1] - 1) - (y * 12 + m);
  var need = monthsLeft > 0 ? left / monthsLeft : left;
  var eta = pace > 0 ? Math.ceil(left / pace) : 0, etaIdx = y * 12 + m - 1 + eta, etaY = Math.floor(etaIdx / 12), etaM = etaIdx % 12;
  var early = etaY * 12 + etaM < G.deadline[0] * 12 + G.deadline[1] - 1;
  var segs = "", labs = "";
  for (var i = 0; i < G.months; i++) { segs += '<i><b data-to="' + Math.max(0, Math.min(1, life - i)) * 100 + '%"></b></i>'; labs += "<span>" + (i === 0 ? "1 міс." : i + 1) + "</span>"; }
  box.innerHTML = '<div class="ch" style="min-height:0"><h2>' + G.title + '</h2><span class="sub" style="font-size:12px">до 1 червня ' + G.deadline[0] + "</span></div>" +
    '<div class="gbig"><b>' + nf1.format(life) + "</b><span>" + (Math.round(life * 10) % 10 ? "місяця" : plural(Math.round(life), "місяць", "місяці", "місяців")) + " спокійного життя<br>з шести запланованих</span></div>" +
    '<div class="segs" role="img" aria-label="' + nf1.format(life) + " з " + G.months + ' місяців">' + segs + '</div><div class="seglab" aria-hidden="true">' + labs + "</div>" +
    '<div class="gcols"><div><span>Накопичено</span><b>' + nf0.format(have) + " зл</b><small>з " + nf0.format(target) + " зл, " + nf0.format(have / target * 100) + " %</small></div>" +
    "<div><span>Відкладаєте на місяць</span><b>" + nf0.format(pace) + " зл</b><small>треба " + nf0.format(need) + " зл</small></div></div>" +
    '<div class="gpace"><span>' + (pace >= need ? "У такому темпі — " + MONTHS_FULL[etaM].toLowerCase() + " " + etaY + (early ? ", раніше терміну" : "") : "Щоб встигнути, треба відкладати більше") + "</span>" +
    '<span class="bd ' + (pace >= need ? "good" : "bad") + '">' + arr(pace - need) + " " + nf0.format(Math.abs(pace - need)) + " зл</span></div>";
}

// ---------- аналітика ----------
// шкала осі: «гарний» максимум і 4 позначки
function niceMax(v) { var s = [400, 800, 1200, 1500, 2000, 3000, 4500, 6000, 8000, 10000, 15000]; for (var i = 0; i < s.length; i++) if (v <= s[i]) return s[i]; return Math.ceil(v / 5000) * 5000; }
function capsules(boxId, tickId, items, max, sel, onPick) {
  var H = 150, box = $(boxId); box.innerHTML = "";
  $(tickId).innerHTML = [1, 2 / 3, 1 / 3, 0].map(function (f) { return "<span>" + nf0.format(Math.round(max * f)) + " зл</span>"; }).join("");
  items.forEach(function (x, i) {
    var c = h("div", "cap" + (onPick ? " pick" : "") + (i === sel ? " on" : "") + (x.part ? " part" : "") + (onPick && i !== sel ? " dim" : ""));
    var hh = Math.max(8, Math.round(x.v / max * H)), rent = x.rent ? Math.round(x.rent / max * H) : 0;
    c.innerHTML = '<div class="trk"><div class="fill" data-to="' + hh + 'px">' + (rent ? '<div class="rent" style="height:' + rent + 'px"></div>' : "") + '<div class="main"></div></div></div>' +
      '<span class="r1">' + esc(x.label) + '</span><span class="r2">' + esc(x.sub) + "</span>";
    c.setAttribute("aria-label", x.label + ": " + zl(x.v));
    if (onPick) { c.setAttribute("role", "button"); c.tabIndex = 0;
      c.onclick = c.onkeydown = function (e) { if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return; onPick(i); }; }
    box.appendChild(c);
  });
  grow("#" + boxId + " .fill", "height");
}
function renderAnalytics(P) {
  var mb = $("a-mode"); mb.innerHTML = "";
  [["month", "Місяць"], ["trends", "Динаміка"]].forEach(function (x) {
    var b = h("button", "", x[1]); b.type = "button"; b.setAttribute("aria-pressed", x[0] === amode);
    b.onclick = function () { amode = x[0]; render(); };
    mb.appendChild(b);
  });
  $("an-month").hidden = amode !== "month"; $("an-trends").hidden = amode !== "trends";
  if (amode === "trends") return renderTrends(P);
  var M = P.months[month];
  var rent = M.ops.filter(function (o) { return !o.income && o.cat === "Житло"; });
  var rentSum = rent.reduce(function (s, o) { return s + o.amount; }, 0), useOut = rent.length && rentOut;
  var weeks = [0, 0, 0, 0, 0], rentW = [0, 0, 0, 0, 0];
  M.ops.forEach(function (o) {
    if (o.income) return;
    var w = Math.min(Math.floor((+o.date.slice(0, 2) - 1) / 7), 4);
    if (o.cat === "Житло") { if (!useOut) { weeks[w] += o.amount; rentW[w] += o.amount; } } else weeks[w] += o.amount;
  });
  var rm = $("rent-mode"); rm.hidden = !rent.length; rm.innerHTML = "";
  if (rent.length) [[false, "З орендою"], [true, "Без оренди"]].forEach(function (x) {
    var b = h("button", "", x[1]); b.type = "button"; b.setAttribute("aria-pressed", x[0] === rentOut);
    b.onclick = function () { rentOut = x[0]; render(); };
    rm.appendChild(b);
  });
  $("wnote").textContent = rent.length ? (useOut ? "Оренду " + nf0.format(rentSum) + " зл, сплачену " + rent[0].date.slice(0, 2).replace(/^0/, "") + "." + rent[0].date.slice(3) + ", винесено з графіка. Разом з орендою — " + zl(M.expenses) + "."
    : "Оренда — світла верхівка першого тижня. Без неї — " + zl(M.expenses - rentSum) + ".") : "Усього за місяць — " + zl(M.expenses) + (M.incomplete ? ", дані до " + M.last_op : "") + ".";
  var labels = ["1–7", "8–14", "15–21", "22–28", "29+"];
  capsules("bars", "wticks", weeks.map(function (w, i) { return { v: w, rent: rentW[i], label: labels[i], sub: nf0.format(w) + " зл" }; }), niceMax(Math.max.apply(null, weeks.concat([1]))), -1, null);

  var total = M.fixed + M.variable, pm = P.months[month - 1], cmp = pm && !M.incomplete, fp = total ? M.fixed / total * 100 : 0;
  var FIXED = { marko: "звʼязок, світло, спортзал, YouTube", olya: "оренда, проїзний ZTM, Netflix, Spotify, Claude.ai, Play" };
  $("fvsplit").innerHTML = '<i style="width:' + fp + '%;background:#A9C6F8"></i><i style="flex:1;background:linear-gradient(90deg,#3B82F6,#1D5FE0)"></i>';
  $("fv").innerHTML = '<div><span><i style="background:#A9C6F8"></i>Фіксовані, ' + nf1.format(fp) + " %</span><b>" + zl(M.fixed) + "</b>" + (cmp ? badge(M.fixed, pm.fixed, true, "", false) : "") + "</div>" +
    '<div><span><i style="background:#1F63E6"></i>Змінні, ' + nf1.format(100 - fp) + " %</span><b>" + zl(M.variable) + "</b>" + (cmp ? badge(M.variable, pm.variable, true, "", false) : "") + "</div>";
  $("fvnote").textContent = "Фіксовані: " + FIXED[P.key] + ". Змінними можна керувати.";
  $("fcatn").textContent = zl(M.expenses);
  $("fcats").innerHTML = catRows(M, 0);
  $("fava").href = FAVA[P.key] + "query/";
  grow("#an-month .bar6 b", "width");
}
function renderTrends(P) {
  var METRICS = [["exp", "Витрати"], ["inc", "Дохід"], ["saved", "Лишилось"]];
  var mb = $("t-metric"); mb.innerHTML = "";
  METRICS.forEach(function (x) {
    var b = h("button", "", x[1]); b.type = "button"; b.setAttribute("aria-pressed", x[0] === metric);
    b.onclick = function () { metric = x[0]; render(); };
    mb.appendChild(b);
  });
  var val = function (m) { return metric === "exp" ? m.expenses : metric === "inc" ? m.income : Math.max(0, m.saved); };
  var list = P.months;
  if (trendMonth === null || trendMonth >= list.length) trendMonth = lastFull(P);
  capsules("mbars", "mticks", list.map(function (m) {
    var skip = metric !== "exp" && (m.incomplete || !(m.income > 0));
    return { v: skip ? 0 : val(m), label: m.name, part: m.incomplete, sub: m.incomplete ? "до " + m.last_op : nf0.format(val(m)) + " зл" };
  }), niceMax(Math.max.apply(null, list.map(val).concat([1]))), trendMonth, function (i) { trendMonth = i; render(); });
  var S = list[trendMonth];

  var full = P.months.filter(function (m) { return m.income > 0 && !m.incomplete; });
  var sum = full.reduce(function (s, m) { return s + m.saved; }, 0);
  var last = full[full.length - 1], before = full[full.length - 2], pp = last && before ? last.rate - before.rate : 0;
  $("t-duo").innerHTML = '<div class="card"><span>Лишилось разом</span><b>' + zl(sum) + "</b><small>у середньому " + zl(full.length ? sum / full.length : 0) + " на місяць</small></div>" +
    '<div class="card"><span>Норма заощаджень</span><b>' + nf1.format(last ? last.rate : 0) + " %</b>" +
    (before ? '<span class="bd ' + (pp >= 0 ? "good" : "bad") + '" style="display:inline-flex">' + arr(pp) + " " + nf1.format(Math.abs(pp)) + " п. п.</span><small>у " + LOC[mi(before)] + " " + nf1.format(before.rate) + " %</small>" : "") + "</div>";
  var out = P.months.filter(function (m) { return !(m.income > 0 && !m.incomplete); });
  $("t-note").textContent = "Враховано лише повні місяці, " + full.map(function (m) { return m.name.toLowerCase(); }).join(" і ") + "." +
    (out.length ? " " + out.map(function (m) { return m.name; }).join(", ") + (out[0].income > 0 ? " ще триває." : " ще без зарплати.") : "");

  $("t-dmon").textContent = "Категорії у " + LOC[mi(S)];
  var sl = slices(S), tot = sl.reduce(function (s, x) { return s + x.amount; }, 0) || 1;
  $("t-share").innerHTML = sl.map(function (x) { return '<i style="flex:' + x.amount + ";background:" + x.color + '"></i>'; }).join("");
  $("t-leg").innerHTML = sl.map(function (x) { return '<div class="leg"><i style="background:' + x.color + '"></i><span>' + esc(x.name) + '</span><span class="v" style="color:var(--ink2)">' + nf2.format(x.amount) + '</span><span class="bd blue">' + nf1.format(x.amount / tot * 100) + " %</span></div>"; }).join("");
  $("t-chg").innerHTML = "Змінити місяць" + CHR;
  $("t-chg").onclick = function () { trendMonth = (trendMonth + list.length - 1) % list.length; render(); };

  var idx = P.months.indexOf(S), prev = P.months[idx - 1], pm = {};
  if (prev) prev.categories.forEach(function (c) { pm[c.name] = c.amount; });
  $("t-cmp").textContent = prev ? S.name + " проти " + GEN[mi(prev)] : S.name;
  $("t-cats").innerHTML = S.categories.map(function (c) {
    var d = "";
    if (prev) { var was = pm[c.name] || 0; d = !was ? '<span class="bd new">нова</span>' : badge(c.amount, was, true, "", true); }
    return '<div class="cmp"><span class="n">' + esc(c.name) + '</span><span class="v">' + nf2.format(c.amount) + "</span>" + d + "</div>";
  }).join("");
  $("t-cmpnote").textContent = S.incomplete ? "Місяць ще триває, тож порівняння неповне." : "";
}

// ---------- журнал ----------
var dayFmt = new Intl.DateTimeFormat("uk-UA", { day: "numeric", month: "long", weekday: "long" });
var FILTERS = [["all", "Усі"], ["exp", "Витрати"], ["inc", "Дохід"], ["fixed", "Фіксовані"], ["cash", "Готівка"]];
function renderJournal(P) {
  var M = P.months[month], fb = $("j-filters"); fb.innerHTML = "";
  FILTERS.forEach(function (f) {
    var b = h("button", "", f[1]); b.type = "button"; b.setAttribute("aria-pressed", f[0] === filter);
    b.onclick = function () { filter = f[0]; render(); };
    fb.appendChild(b);
  });
  var ops = M.ops.filter(function (o) {
    return filter === "all" || (filter === "exp" && !o.income) || (filter === "inc" && o.income) || (filter === "fixed" && o.fixed) || (filter === "cash" && o.cash);
  });
  var out = ops.reduce(function (s, o) { return s + (o.income ? 0 : o.amount); }, 0);
  $("j-count").textContent = ops.length;
  $("j-sum").textContent = zl(out);
  var html = "", day = null, buf = "";
  var flush = function () { if (day) html += buf + "</div></section>"; };
  ops.forEach(function (o) {
    if (o.day !== day) {
      flush(); day = o.day;
      var e = ops.filter(function (x) { return x.day === day && !x.income; }).reduce(function (s, x) { return s + x.amount; }, 0);
      var d = dayFmt.format(new Date(day + "T12:00:00")).split(", ");
      buf = '<section class="day" data-day="' + day + '"><div class="dh"><b>' + esc(d[1] || d[0]) + (d[1] ? ", <span>" + esc(d[0]) + "</span>" : "") + "</b><span>" + (e ? "−" + nf2.format(e) + " зл" : "") +
        '</span></div><div class="card list">';
    }
    buf += opRow(o, false, false);
  });
  flush();
  $("j-list").innerHTML = html || '<section class="card empty">Тут поки порожньо</section>';
  $("j-list").querySelectorAll(".del").forEach(function (b) {
    b.onclick = function () {
      // підтвердження прямо в рядку: «Видалити» / «Ні»
      var ask = h("div", "ask", '<button type="button" class="no">Ні</button><button type="button" class="yes">Видалити</button>');
      b.replaceWith(ask);
      ask.querySelector(".no").focus();
      ask.querySelector(".no").onclick = function () { render(); };
      ask.querySelector(".yes").onclick = function () { delCash(P, +b.dataset.line, b.dataset.day, b.dataset.title, "#journal"); };
    };
  });
}

// видалення одного готівкового запису; після — перезавантаження зі свіжими даними і тостом
function delCash(P, line, day, title, back) {
  fetch("api/cash/delete", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
    body: JSON.stringify({ person: P.key, line: line, date: day, title: title }) })
    .then(function (r) { return r.json().catch(function () { return { ok: false, error: "Сервер не відповів" }; }); })
    .then(function (res) {
      if (!res.ok) throw new Error(res.error || "Не вдалося видалити");
      location.href = location.pathname + "?p=" + P.key + "&m=" + res.month + "&deleted=1" + back;
    })
    .catch(function (err) { toast(err.message || "Немає звʼязку, спробуйте ще раз"); });
}
function toast(html, action) {
  var t = h("div", "toast", html); t.setAttribute("role", "status");
  if (action) { var b = h("button", "", action[0]); b.type = "button"; b.onclick = action[1]; t.appendChild(b); }
  document.body.appendChild(t); setTimeout(function () { t.remove(); }, action ? 7000 : 3200);
}

// ---------- шторка «Витрата готівкою» ----------
var pick = null, dpick = 0;
function amountValue() { var v = parseFloat($("a-amount").value.replace(/\s/g, "").replace(",", ".")); return isFinite(v) ? v : 0; }
function check() { var a = amountValue(); $("a-save").disabled = !(a > 0 && pick); $("a-save").textContent = "Зберегти" + (a > 0 ? " " + zl(a) : ""); }
var wd = new Intl.DateTimeFormat("uk-UA", { weekday: "short" }), dm = new Intl.DateTimeFormat("uk-UA", { day: "2-digit", month: "2-digit" });
function renderDates() {
  var t = new Date(), y = new Date(t.getTime() - 864e5), box = $("a-dates"); box.innerHTML = "";
  var v = $("a-date").value, other = v && v !== iso(t) && v !== iso(y);
  [["Сьогодні", t], ["Вчора", y], ["Інша дата", null]].forEach(function (x, i) {
    var b;
    if (x[1]) b = h("button", "", x[0] + "<small>" + wd.format(x[1]) + ", " + dm.format(x[1]) + "</small>");
    else b = h("button", other ? "" : "other", other ? "Інша дата<small>" + v.slice(8) + "." + v.slice(5, 7) + "</small>" : CAL + "Інша дата");
    b.type = "button"; b.setAttribute("aria-pressed", other ? i === 2 : i === dpick);
    b.onclick = function () {
      if (x[1]) { dpick = i; $("a-date").value = iso(x[1]); renderDates(); }
      else { try { $("a-date").showPicker(); } catch (e) { $("a-date").focus(); } }
    };
    box.appendChild(b);
  });
}
$("a-date").addEventListener("change", function () { dpick = -1; renderDates(); });
function renderAdd(P) {
  if (!$("a-date").value) { $("a-date").value = iso(new Date()); dpick = 0; }
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
  this.style.width = Math.max(1, this.value.length || 1) + 0.6 + "ch";
  check();
});
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
      location.href = location.pathname + "?p=" + P.key + "&m=" + res.month + "&saved=1&a=" + amountValue() + "&d=" + res.date +
        "&l=" + res.line + "&t=" + encodeURIComponent(res.title) + "#report";
    })
    .catch(function (err) { $("a-msg").textContent = err.message || "Немає звʼязку, спробуйте ще раз"; check(); });
});
function sheet(open, P) {
  var s = $("v-add"), bg = $("sheet-bg"), was = !s.hidden;
  if (open) {
    renderAdd(P);
    if (!was) {
      s.hidden = bg.hidden = false; document.body.style.overflow = "hidden";
      $("a-amount").value = ""; $("a-amount").style.width = ""; check();
      requestAnimationFrame(function () { s.classList.add("open"); bg.classList.add("open"); });
      $("a-amount").focus({ preventScroll: true });
    }
  } else if (was) {
    s.classList.remove("open"); bg.classList.remove("open"); document.body.style.overflow = "";
    setTimeout(function () { if (!s.classList.contains("open")) s.hidden = bg.hidden = true; }, 300);
  }
}
var sheetFromApp = false;
function closeSheet() { if (history.length > 1 && sheetFromApp) history.back(); else location.replace("#report"); }
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
$("cash").addEventListener("click", function () { sheetFromApp = true; });
$("cats-all").addEventListener("click", function () { amode = "month"; });
$("ops-all").addEventListener("click", function () { filter = "all"; });
$("arr-j").outerHTML = CHR; $("arr-f").outerHTML = CHR;

// ---------- рендер ----------
function render() {
  var v = view(), P = D.people[person];
  renderPeople();
  if (v === "add" && !P.cash) { location.replace("#report"); return; }
  var base = v === "add" ? "report" : v, M = P.months[month];
  var range = base === "analytics" && amode === "trends";
  $("mpick").hidden = range; $("mrange").hidden = !range;
  if (range) $("mrange").innerHTML = CAL + P.months[0].name + " – " + P.months[P.months.length - 1].name.toLowerCase();
  $("mcal-name").textContent = selDay && base === "journal" ? (+selDay.slice(8)) + " " + GEN[mi(M)] + " " + M.key.slice(0, 4) : M.name + " " + M.key.slice(0, 4);
  $("mcal").setAttribute("aria-label", "Місяць: " + M.name + " " + M.key.slice(0, 4) + ". Змінити");
  calKey = M.key; if (selDay && selDay.slice(0, 7) !== M.key) selDay = null; renderCal(P);
  ["report", "analytics", "journal"].forEach(function (k) { $("v-" + k).hidden = k !== base; });
  [["navr", "report"], ["navt", "analytics"], ["navj", "journal"]].forEach(function (n) {
    $(n[0]).classList.toggle("on", n[1] === base);
    if (n[1] === base) $("nav-ind").style.setProperty("--n", ["report", "analytics", "journal"].indexOf(base));
    if (n[1] === base) $(n[0]).setAttribute("aria-current", "page"); else $(n[0]).removeAttribute("aria-current");
  });
  if (base === "analytics") renderAnalytics(P); else if (base === "journal") renderJournal(P); else renderReport(P);
  if (base !== lastBase) { lastBase = base; enter(base); }
  sheet(v === "add", P);
}
// поява: при першому показі екрана картки виїжджають по черзі, а головна сума «набігає»
var lastBase = null;
function enter(base) {
  var root = $("v-" + base), inner = [].slice.call(root.querySelectorAll("#j-list > *, #an-month > *, #an-trends > *"), 0, 8);
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
$("liquid").querySelectorAll("i").forEach(function (b) {
  var d = parseFloat(getComputedStyle(b).getPropertyValue("--dur")) || 20;
  b.style.animationDelay = -(Math.random() * d).toFixed(1) + "s, " + -(Math.random() * d * .5).toFixed(1) + "s";
});
var lastView = view();
window.addEventListener("hashchange", function () {
  var v = view();
  if (v !== "add" && lastView !== "add") window.scrollTo(0, 0);
  if (lastView === "add" && v !== "add") sheetFromApp = false;
  lastView = v; render();
});
render();
// сплеш зникає, коли готовий шрифт (але не довше 1,2 с і не швидше 0,5 с, щоб не блимав)
(function () {
  var s = $("splash"), t = Date.now(), done = false, fill = $("sp-fill");
  requestAnimationFrame(function () { fill.style.width = "85%"; });
  function hide() { if (done) return; done = true; setTimeout(function () { s.classList.add("done"); fill.style.width = "100%"; setTimeout(function () { s.classList.add("out"); lastBase = null; render(); setTimeout(function () { s.remove(); }, 700); }, 320); }, Math.max(0, 500 - (Date.now() - t))); }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(hide);
  setTimeout(hide, 1200);
})();
})();
</script>
</body>
</html>
"""
