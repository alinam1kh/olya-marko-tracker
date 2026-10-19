"""Будує report.html: виконує збережені запити 1–3 з main.beancount і складає односторінковий звіт.

Запуск: uv run python report.py
Сторінка автономна: усе (стилі, скрипт, графіки, іконки) лежить в одному файлі, інтернет не потрібен.
"""
import json
import sys
from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import beanquery
from beancount import loader
from beancount.core import data

ROOT = Path(__file__).parent
LEDGER = ROOT / "main.beancount"
OUT_FILE = ROOT / "report.html"

UNCATEGORIZED = "Expenses:Без-Категорії"

# Заголовки колонок кожного запиту: якщо запит у main.beancount змінять, звіт зупиниться з поясненням.
EXPECTED_COLUMNS = {
    "1": ("рік", "місяць", "категорія", "сума"),
    "2": ("рік", "місяць", "дохід", "витрати", "відкладено", "відсоток", "остання операція"),
    "3": ("рік", "місяць", "фіксовані", "сума"),
}

MONTH_NOMINATIVE = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
MONTH_LOCATIVE = ["січні", "лютому", "березні", "квітні", "травні", "червні",
                  "липні", "серпні", "вересні", "жовтні", "листопаді", "грудні"]


def fail(message):
    sys.exit(f"Помилка: {message}")


def load_saved_queries():
    """Збережені запити з main.beancount: номер (перший символ назви) -> текст запиту."""
    entries, errors, _ = loader.load_file(str(LEDGER))
    if errors:
        fail("main.beancount має помилки, спочатку виправте їх (uv run bean-check main.beancount)")
    by_number = {}
    for entry in entries:
        if isinstance(entry, data.Query):
            by_number[entry.name.split()[0]] = entry.query_string
    missing = [n for n in EXPECTED_COLUMNS if n not in by_number]
    if missing:
        fail(f"у main.beancount немає збережених запитів з номерами: {', '.join(missing)}")
    return by_number


def run_query(connection, number, sql):
    cursor = connection.execute(sql)
    columns = tuple(column.name for column in cursor.description)
    if columns != EXPECTED_COLUMNS[number]:
        fail(f"запит {number} повертає колонки {columns}, а звіт чекає {EXPECTED_COLUMNS[number]}")
    return cursor.fetchall()


def pretty_category(account):
    """Expenses:Краса-і-Побут -> «Краса і побут»."""
    name = account.split(":", 1)[1].replace("-", " ")
    return name[0] + name[1:].lower()


def build_months():
    queries = load_saved_queries()
    connection = beanquery.connect(f"beancount:{LEDGER.as_posix()}")
    rows1 = run_query(connection, "1", queries["1"])
    rows2 = sorted(run_query(connection, "2", queries["2"]), key=lambda r: (r[0], r[1]))
    rows3 = run_query(connection, "3", queries["3"])
    if not rows2:
        fail("у запиті 2 немає жодного місяця: спершу запустіть import.py")

    categories = defaultdict(list)
    for year, month, account, amount in rows1:
        categories[(year, month)].append((account, amount))

    split = defaultdict(lambda: {"fixed": Decimal(0), "variable": Decimal(0)})
    for year, month, is_fixed, amount in rows3:
        split[(year, month)]["fixed" if is_fixed else "variable"] += amount

    several_years = len({r[0] for r in rows2}) > 1
    last_key = (rows2[-1][0], rows2[-1][1])

    months = []
    for year, month, income, expenses, saved, rate, last_op in rows2:
        key = (year, month)
        cats = sorted(categories[key], key=lambda c: c[1], reverse=True)

        # Три запити мають давати однакові суми: інакше в звіті були б цифри, що не сходяться.
        if sum(amount for _, amount in cats) != expenses:
            fail(f"{year}-{month:02}: сума категорій (запит 1) не дорівнює витратам (запит 2)")
        if split[key]["fixed"] + split[key]["variable"] != expenses:
            fail(f"{year}-{month:02}: фіксовані + керовані (запит 3) не дорівнюють витратам (запит 2)")

        suffix = f" {year}" if several_years else ""
        month_end = date(year, month, monthrange(year, month)[1])
        months.append({
            "key": f"{year}-{month:02}",
            "name": MONTH_NOMINATIVE[month - 1] + suffix,
            "loc": MONTH_LOCATIVE[month - 1] + suffix,
            # Неповним вважаємо лише останній місяць у даних, якщо операції закінчуються раніше за його кінець.
            "incomplete": key == last_key and last_op < month_end,
            "last_op": last_op.isoformat(),
            "last_op_short": f"{last_op:%d.%m}",
            "income": float(income),
            "expenses": float(expenses),
            "saved": float(saved),
            "rate": float(rate),
            "fixed": float(split[key]["fixed"]),
            "variable": float(split[key]["variable"]),
            "uncategorized": float(sum((a for acc, a in cats if acc == UNCATEGORIZED), Decimal(0))),
            "categories": [
                {"account": account, "name": pretty_category(account), "amount": float(amount)}
                for account, amount in cats
            ],
        })
    data_until = max(r[6] for r in rows2)
    return {"months": months, "data_until": f"{data_until:%d.%m.%Y}"}


def main():
    # На Windows консоль часто не UTF-8: без цього українські повідомлення читаються як кракозябри.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    report = build_months()
    payload = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
    OUT_FILE.write_text(TEMPLATE.replace("__DATA__", payload), encoding="utf-8", newline="\n")
    names = ", ".join(m["name"] + (" (неповний)" if m["incomplete"] else "") for m in report["months"])
    print(f"Місяці: {names}")
    print(f"Записано {OUT_FILE.name}")


TEMPLATE = r"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Фінанси Олі — місячний звіт</title>
<style>
:root {
  color-scheme: light dark;
  --page: #f3f4f7; --page-glass: rgba(243,244,247,0.85); --surface: #ffffff;
  --ink: #101828; --ink2: #475467; --muted: #667085;
  --grid: #eaecf0; --axis: #d0d5dd; --line: #eaecf0; --track: #eef0f4;
  --tab-on: #ffffff; --tint: #f2f6fd; --chip: #e8f0fc; --shadow: 0 1px 2px rgba(16,24,40,0.05), 0 8px 24px rgba(16,24,40,0.06);
  --s1: #2a78d6; --s2: #eb6834; --gray: #d0d5dd;
  --on-s1: #ffffff; --on-s2: #0b0b0b;
  --tip-bg: #101828; --tip-ink: #f2f4f7;
  --hero-from: #0f2a63; --hero-to: #2158c4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0b0c0e; --page-glass: rgba(11,12,14,0.85); --surface: #16181c;
    --ink: #f2f4f7; --ink2: #c0c5cf; --muted: #98a2b3;
    --grid: #262a31; --axis: #3a4049; --line: #262a31; --track: #262a31;
    --tab-on: #343a45; --tint: #1c2432; --chip: #1f2b40; --shadow: 0 0 0 1px rgba(255,255,255,0.06);
    --s1: #3987e5; --s2: #d95926; --gray: #4a4f58;
    --on-s1: #0b0b0b; --on-s2: #0b0b0b;
    --tip-bg: #f2f4f7; --tip-ink: #101828;
    --hero-from: #0c1c40; --hero-to: #17418f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.app { max-width: 600px; margin: 0 auto; padding: 0 16px 56px; }

/* шапка */
.bar { display: flex; align-items: center; gap: 12px; padding: 22px 2px 6px; }
.avatar {
  width: 44px; height: 44px; border-radius: 50%; flex: none; display: grid; place-items: center;
  background: linear-gradient(135deg, var(--hero-from), var(--hero-to)); color: #fff; font-weight: 600; font-size: 18px;
}
h1 { margin: 0; font-size: 22px; line-height: 1.2; font-weight: 650; letter-spacing: -0.01em; }
.period { color: var(--ink2); font-size: 14px; }

/* перемикач місяців: липкий, як сегментований контрол */
.tabs-wrap {
  position: sticky; top: 0; z-index: 5; padding: 10px 0 12px; margin: 0 -16px; padding-left: 16px; padding-right: 16px;
  background: var(--page); background: var(--page-glass); -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
}
.tabs { display: flex; gap: 4px; padding: 4px; background: var(--track); border-radius: 16px; }
.tabs button {
  flex: 1; font: inherit; font-size: 15px; color: var(--ink2); background: transparent; border: 0; border-radius: 12px;
  padding: 8px 6px; cursor: pointer; line-height: 1.2; transition: background .2s, color .2s, box-shadow .2s;
}
.tabs button small { display: block; font-size: 11px; opacity: 0.85; margin-top: 1px; }
.tabs button:hover { color: var(--ink); }
.tabs button[aria-pressed="true"] {
  background: var(--tab-on); color: var(--ink); font-weight: 650;
  box-shadow: 0 1px 2px rgba(16,24,40,0.12), 0 1px 6px rgba(16,24,40,0.08);
}
button:focus-visible, .hit:focus-visible, summary:focus-visible { outline: 2px solid var(--s1); outline-offset: 2px; }

.notice {
  display: flex; gap: 10px; align-items: flex-start; margin: 4px 0 0; padding: 12px 14px; border-radius: 14px;
  background: var(--tint); color: var(--ink2); font-size: 14px;
}
.notice[hidden], .delta[hidden], .note[hidden] { display: none; }
.notice svg, .callout svg, .note svg { flex: none; margin-top: 2px; }

/* картки */
.card { margin-top: 14px; padding: 22px 20px 18px; background: var(--surface); border-radius: 24px; box-shadow: var(--shadow); }
.head { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 18px; }
.num {
  flex: none; width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center;
  background: var(--chip); color: var(--s1); font-size: 14px; font-weight: 650;
}
h2 { margin: 3px 0 0; font-size: 17px; line-height: 1.3; font-weight: 600; }
.stat-label { color: var(--ink2); font-size: 14px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.stat-value { font-size: 38px; line-height: 1.12; font-weight: 650; letter-spacing: -0.02em; margin-top: 2px; }
.stat-value .unit { font-size: 0.5em; font-weight: 500; color: var(--ink2); margin-left: 6px; letter-spacing: 0; }
.stat-value.big { font-size: 56px; line-height: 1.04; }
.stat-sub { color: var(--ink2); font-size: 15px; margin-top: 4px; }
.delta {
  display: inline-flex; gap: 4px; align-items: center; margin-top: 10px; padding: 3px 10px; border-radius: 999px;
  background: var(--chip); color: var(--ink2); font-size: 13px;
}
.badge { font-size: 12px; padding: 1px 9px; border: 1px solid var(--axis); border-radius: 999px; color: var(--ink2); white-space: nowrap; }
.caption { margin: 22px 0 4px; font-size: 13px; color: var(--ink2); }

/* список категорій */
.cats { list-style: none; margin: 18px 0 0; padding: 0; }
.cat {
  display: grid; grid-template-columns: 40px 1fr auto; column-gap: 12px; align-items: center;
  padding: 10px 6px; margin: 0 -6px; border-radius: 14px; transition: background .15s;
}
.cat:hover { background: var(--track); }
.chip { grid-row: 1 / span 2; width: 40px; height: 40px; border-radius: 14px; display: grid; place-items: center; background: var(--chip); color: var(--s1); }
.cat.unc .chip { background: transparent; color: var(--muted); box-shadow: inset 0 0 0 1.5px var(--axis); }
.cat-name { font-size: 15px; font-weight: 550; line-height: 1.3; }
.cat-name small { font-size: 13px; font-weight: 400; color: var(--muted); margin-left: 6px; }
.cat-amount { font-size: 15px; font-weight: 650; white-space: nowrap; }
.cat-bar { grid-column: 2 / span 2; height: 6px; margin-top: 7px; border-radius: 999px; background: var(--track); overflow: hidden; }
.cat-bar i { display: block; height: 100%; border-radius: 999px; background: var(--s1); transition: width .6s cubic-bezier(.2,.8,.2,1); }
.cat.unc .cat-bar i { background: var(--axis); }

/* кільце в героїчній картці */
.hero-top { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.ring { flex: none; width: 112px; height: 112px; }
.ring circle { fill: none; stroke-width: 10; }
.ring .track { stroke: rgba(255,255,255,0.16); }
.ring .prog { stroke: #9cc8fa; stroke-linecap: round; transition: stroke-dasharray .8s cubic-bezier(.2,.8,.2,1); }
.ring text { fill: #fff; font-family: inherit; text-anchor: middle; }
.ring .rv { font-size: 19px; font-weight: 650; }
.ring .rl { font-size: 11px; fill: rgba(255,255,255,0.72); }

.legend { display: flex; gap: 16px; margin: 4px 0 8px; font-size: 13px; color: var(--ink2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { display: inline-block; width: 12px; height: 8px; border-radius: 2px; }
.sw-s1 { background: var(--s1); } .sw-s2 { background: var(--s2); }
.chart { width: 100%; }
.chart svg { display: block; max-width: 100%; overflow: visible; }

.callout { display: flex; gap: 12px; margin-top: 18px; padding: 14px 16px; border-radius: 16px; background: var(--tint); }
.callout svg { color: var(--s1); }
.callout p { margin: 0; font-size: 15px; line-height: 1.45; }
.note { display: flex; gap: 8px; margin: 10px 2px 0; font-size: 13px; color: var(--muted); }
details { margin-top: 14px; font-size: 14px; color: var(--ink2); }
summary { cursor: pointer; width: fit-content; padding: 4px 2px; font-weight: 500; }
.table-wrap { overflow-x: auto; margin-top: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; color: var(--ink); }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
th { font-weight: 600; color: var(--ink2); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.sel td { font-weight: 650; }
tr.total td { font-weight: 650; border-bottom: 0; }

/* графіки (SVG) */
svg text { font-family: inherit; }
.lbl { fill: var(--ink2); font-size: 13px; }
.sublbl { fill: var(--muted); font-size: 12px; }
.val { fill: var(--ink); font-size: 13px; }
.val.sel { font-weight: 650; }
.inside { font-size: 12px; font-weight: 650; }
.on-s1 { fill: var(--on-s1); } .on-s2 { fill: var(--on-s2); }
.axis { stroke: var(--axis); stroke-width: 1; }
.mark-s1 { fill: var(--s1); } .mark-s2 { fill: var(--s2); } .mark-gray { fill: var(--gray); }
.hit { cursor: default; outline: none; }
.hit.click { cursor: pointer; }
.hit:hover .mark, .hit:focus .mark { opacity: 0.78; }
.hit.dim .mark, .hit.dim .inside { opacity: 0.4; }
.hit.dim:hover .mark, .hit.dim:focus .mark { opacity: 0.7; }

/* героїчна картка: норма заощаджень */
.hero { background: linear-gradient(135deg, var(--hero-from), var(--hero-to)); color: #fff; box-shadow: 0 10px 30px rgba(15,42,99,0.28); }
.hero .num { background: rgba(255,255,255,0.16); color: #fff; }
.hero .stat-label, .hero .stat-sub, .hero .caption { color: rgba(255,255,255,0.78); }
.hero .stat-value .unit { color: rgba(255,255,255,0.78); }
.hero .badge { border-color: rgba(255,255,255,0.4); color: rgba(255,255,255,0.88); }
.hero .delta { background: rgba(255,255,255,0.14); color: rgba(255,255,255,0.92); }
.hero .callout { background: rgba(255,255,255,0.12); }
.hero .callout svg { color: #cfe3fc; }
.hero details { color: rgba(255,255,255,0.82); }
.hero table { color: #fff; }
.hero th { color: rgba(255,255,255,0.72); }
.hero th, .hero td { border-color: rgba(255,255,255,0.16); }
.hero .axis { stroke: rgba(255,255,255,0.28); }
.hero .lbl { fill: rgba(255,255,255,0.82); }
.hero .sublbl { fill: rgba(255,255,255,0.64); }
.hero .val { fill: #fff; }
.hero .mark-s1 { fill: #9cc8fa; }
.hero .mark-gray { fill: rgba(255,255,255,0.28); }
.hero button:focus-visible, .hero .hit:focus-visible, .hero summary:focus-visible { outline-color: #fff; }

.tip {
  position: fixed; z-index: 10; pointer-events: none; max-width: 280px; padding: 8px 12px;
  background: var(--tip-bg); color: var(--tip-ink); border-radius: 10px; font-size: 13px;
  line-height: 1.4; white-space: pre-line; box-shadow: 0 4px 16px rgba(0,0,0,0.25);
}
.tip strong { display: block; font-size: 16px; }
.tip[hidden] { display: none; }
@media (prefers-reduced-motion: reduce) {
  .cat-bar i, .ring .prog, .tabs button, .cat { transition: none; }
}
@media (forced-colors: active) {
  .mark-s1, .cat-bar i { fill: Highlight; background: Highlight; } .mark-s2 { fill: LinkText; } .mark-gray { fill: GrayText; }
  .hero { border: 1px solid CanvasText; }
}
@media (max-width: 480px) {
  .card { padding: 20px 16px 16px; border-radius: 22px; }
  .stat-value { font-size: 34px; } .stat-value.big { font-size: 48px; }
  .ring { width: 92px; height: 92px; } .ring .rv { font-size: 16px; } .ring .rl { font-size: 10px; }
}
</style>
</head>
<body>
<div class="app">
  <header class="bar">
    <div class="avatar" aria-hidden="true">О</div>
    <div>
      <h1>Привіт, Олю</h1>
      <div class="period" id="sub"></div>
    </div>
  </header>
  <div class="tabs-wrap"><div class="tabs" id="months" role="group" aria-label="Місяць"></div></div>
  <p class="notice" id="notice" hidden></p>

  <section class="card" aria-labelledby="h-cat">
    <div class="head"><span class="num" aria-hidden="true">1</span><h2 id="h-cat">Скільки грошей іде на кожну категорію витрат?</h2></div>
    <div class="stat">
      <div class="stat-label" id="s1-label"></div>
      <div class="stat-value" id="s1-value"></div>
      <div class="stat-sub" id="s1-sub"></div>
      <span class="delta" id="d1" hidden></span>
    </div>
    <ul class="cats" id="cats" aria-label="Витрати за категоріями"></ul>
    <div class="callout"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg><p id="c1"></p></div>
    <details><summary>Показати таблицею</summary><div class="table-wrap" id="t1"></div></details>
  </section>

  <section class="card hero" aria-labelledby="h-save">
    <div class="head"><span class="num" aria-hidden="true">2</span><h2 id="h-save">Яку частку доходу я відкладаю?</h2></div>
    <div class="hero-top">
      <div class="stat">
        <div class="stat-label" id="s2-label"></div>
        <div class="stat-value big" id="s2-value"></div>
        <div class="stat-sub" id="s2-sub"></div>
        <span class="delta" id="d2" hidden></span>
      </div>
      <div class="ring" id="ring"></div>
    </div>
    <p class="caption">Норма заощаджень за місяцями, %</p>
    <div class="chart" id="chart2"></div>
    <div class="callout"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg><p id="c2"></p></div>
    <details><summary>Показати таблицею</summary><div class="table-wrap" id="t2"></div></details>
  </section>

  <section class="card" aria-labelledby="h-fix">
    <div class="head"><span class="num" aria-hidden="true">3</span><h2 id="h-fix">Скільки витрат фіксовані, а скільки я можу змінювати?</h2></div>
    <div class="stat">
      <div class="stat-label" id="s3-label"></div>
      <div class="stat-value" id="s3-value"></div>
      <div class="stat-sub" id="s3-sub"></div>
      <span class="delta" id="d3" hidden></span>
    </div>
    <p class="caption">Фіксовані та керовані витрати за місяцями, зл</p>
    <div class="legend"><span><i class="sw-s1"></i>Фіксовані</span><span><i class="sw-s2"></i>Керовані</span></div>
    <div class="chart" id="chart3"></div>
    <div class="callout"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg><p id="c3"></p></div>
    <p class="note" id="n3" hidden><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 11v5M12 8h.01"/></svg><span id="n3t"></span></p>
    <details><summary>Показати таблицею</summary><div class="table-wrap" id="t3"></div></details>
  </section>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
"use strict";
var SVG_NS = "http://www.w3.org/2000/svg";
var UNCATEGORIZED = "Expenses:Без-Категорії";
var DATA = JSON.parse(document.getElementById("data").textContent);
var months = DATA.months;
var sel = months.length - 1;

var nf2 = new Intl.NumberFormat("uk-UA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
var nf1 = new Intl.NumberFormat("uk-UA", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
var nf0 = new Intl.NumberFormat("uk-UA", { maximumFractionDigits: 0 });
function money(v) { return nf2.format(v) + " зл"; }
function pct1(v) { return nf1.format(v) + " %"; }
function pct0(v) { return nf0.format(v) + " %"; }
function $(id) { return document.getElementById(id); }
var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function mk(ns, tag, props, text) {
  var e = ns ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  if (props) for (var k in props) e.setAttribute(k, props[k]);
  if (text !== undefined) e.textContent = text;
  return e;
}
function h(tag, props, text) { return mk(false, tag, props, text); }
function s(tag, props, text) { return mk(true, tag, props, text); }

var measureCtx = document.createElement("canvas").getContext("2d");
function textWidth(text, font) { measureCtx.font = font; return measureCtx.measureText(text).width; }
var FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';

/* ---------- іконки категорій (прості контурні, вбудовані: без інтернету) ---------- */
var ICONS = {
  "Житло": ["M3 10.5 12 3l9 7.5V21H3z", "M9 21v-6h6v6"],
  "Продукти": ["M3 4h2l2.4 11.2a1 1 0 0 0 1 .8h8.9a1 1 0 0 0 1-.8L20 8H6", "M7.5 20a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0", "M16.5 20a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0"],
  "Кафе": ["M4 8h12v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z", "M16 10h2a2 2 0 0 1 0 4h-2", "M8 3v2M12 3v2"],
  "Доставка-Їжі": ["M5 8h14l-1 12H6z", "M9 8V6a3 3 0 0 1 6 0v2"],
  "Транспорт": ["M6 4h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M4 11h16", "M7 17v2M17 17v2"],
  "Підписки": ["M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5", "M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"],
  "Краса-і-Побут": ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z", "M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"],
  "Спорт": ["M6 7v10M18 7v10M3 10v4M21 10v4M6 12h12"],
  "Звязок": ["M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z", "M11 18h2"],
  "Без-Категорії": ["M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0", "M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7", "M12 17h.01"],
  "_": ["M3 12V4h8l10 10-8 8z", "M7.5 8.5h.01"]
};
function icon(key) {
  var svg = s("svg", { viewBox: "0 0 24 24", width: 20, height: 20, fill: "none", stroke: "currentColor",
    "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
  (ICONS[key] || ICONS._).forEach(function (d) { svg.appendChild(s("path", { d: d })); });
  return svg;
}

/* ---------- підказка (tooltip): значення зверху, назва нижче ---------- */
var tip = h("div", { "class": "tip", role: "tooltip", hidden: "" });
document.body.appendChild(tip);
function showTip(value, label, x, y) {
  tip.replaceChildren(h("strong", null, value), document.createTextNode(label));
  tip.hidden = false;
  var r = tip.getBoundingClientRect();
  var left = x + 14, top = y - r.height - 10;
  if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
  if (left < 8) left = 8;
  if (top < 8) top = y + 18;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
function hideTip() { tip.hidden = true; }

/* Рядок/стовпчик графіка: наведення, фокус з клавіатури, за потреби клік. */
function bindHit(node, value, label, onSelect) {
  node.setAttribute("tabindex", "0");
  node.setAttribute("role", onSelect ? "button" : "img");
  node.setAttribute("aria-label", label.replace(/\n/g, ", ") + ": " + value);
  if (onSelect) node.setAttribute("class", node.getAttribute("class") + " click");
  node.addEventListener("pointerenter", function (e) { showTip(value, label, e.clientX, e.clientY); });
  node.addEventListener("pointermove", function (e) { showTip(value, label, e.clientX, e.clientY); });
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", function () {
    var r = node.getBoundingClientRect();
    showTip(value, label, r.left + r.width / 2, r.top);
  });
  node.addEventListener("blur", hideTip);
  if (onSelect) {
    node.addEventListener("click", onSelect);
    node.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(); }
    });
  }
}

/* Стовпчик із закругленим кінцем 4px і квадратною основою. */
function barPath(x, y, w, hgt, dir) {
  var r = Math.min(4, (dir === "right" ? hgt : w) / 2, (dir === "right" ? w : hgt) / 2);
  if (dir === "right")
    return "M" + x + "," + y + " H" + (x + w - r) + " A" + r + "," + r + " 0 0 1 " + (x + w) + "," + (y + r) +
      " V" + (y + hgt - r) + " A" + r + "," + r + " 0 0 1 " + (x + w - r) + "," + (y + hgt) + " H" + x + " Z";
  if (dir === "up")
    return "M" + x + "," + (y + hgt) + " V" + (y + r) + " A" + r + "," + r + " 0 0 1 " + (x + r) + "," + y +
      " H" + (x + w - r) + " A" + r + "," + r + " 0 0 1 " + (x + w) + "," + (y + r) + " V" + (y + hgt) + " Z";
  return "M" + x + "," + y + " H" + (x + w) + " V" + (y + hgt - r) + " A" + r + "," + r + " 0 0 1 " +
    (x + w - r) + "," + (y + hgt) + " H" + (x + r) + " A" + r + "," + r + " 0 0 1 " + x + "," + (y + hgt - r) + " Z";
}

function chartWidth(el) { return Math.max(240, Math.floor(el.clientWidth)); }

function monthLabel(m, x, y, anchor) {
  var t = s("text", { x: x, y: y, "text-anchor": anchor, "class": "lbl" }, m.name);
  if (m.incomplete) {
    t.appendChild(s("tspan", { x: x, dy: 15, "class": "sublbl" }, "до " + m.last_op_short));
  }
  return t;
}

/* ---------- 1: категорії витрат (список з іконками і тонкими смужками) ---------- */
function drawCategories(ul, m, animate) {
  var max = Math.max.apply(null, m.categories.map(function (c) { return c.amount; }).concat([1]));
  var fills = [];
  ul.replaceChildren();
  m.categories.forEach(function (c) {
    var key = c.account.split(":")[1];
    var share = m.expenses ? c.amount / m.expenses * 100 : 0;
    var li = h("li", { "class": "cat" + (c.account === UNCATEGORIZED ? " unc" : "") });
    var chip = h("span", { "class": "chip" });
    chip.appendChild(icon(key));
    var name = h("div", { "class": "cat-name" }, c.name);
    name.appendChild(h("small", null, pct0(share)));
    var bar = h("div", { "class": "cat-bar", role: "img", "aria-label": c.name + ": " + money(c.amount) + ", " + pct0(share) });
    var fill = h("i");
    var width = (c.amount / max * 100) + "%";
    fill.style.width = animate ? "0%" : width;
    fills.push([fill, width]);
    bar.appendChild(fill);
    li.append(chip, name, h("div", { "class": "cat-amount" }, money(c.amount)), bar);
    ul.appendChild(li);
  });
  if (animate) requestAnimationFrame(function () { requestAnimationFrame(function () {
    fills.forEach(function (f) { f[0].style.width = f[1]; });
  }); });
}

/* ---------- 2а: кільце норми заощаджень ---------- */
function drawRing(root, m, animate) {
  var R = 44, C = 2 * Math.PI * R, share = Math.max(0, Math.min(100, m.rate)) / 100 * C;
  var svg = s("svg", { viewBox: "0 0 112 112", width: "100%", height: "100%", role: "img",
    "aria-label": "Відкладено " + pct1(m.rate) + " доходу" });
  svg.appendChild(s("circle", { cx: 56, cy: 56, r: R, "class": "track" }));
  var prog = s("circle", { cx: 56, cy: 56, r: R, "class": "prog", transform: "rotate(-90 56 56)",
    "stroke-dasharray": (animate ? 0 : share) + " " + C });
  svg.appendChild(prog);
  svg.appendChild(s("text", { x: 56, y: 56, "class": "rv" }, nf0.format(m.saved)));
  svg.appendChild(s("text", { x: 56, y: 72, "class": "rl" }, "зл відкладено"));
  root.replaceChildren(svg);
  if (animate) requestAnimationFrame(function () { requestAnimationFrame(function () {
    prog.setAttribute("stroke-dasharray", share + " " + C);
  }); });
}

/* ---------- 2б: норма заощаджень по місяцях (стовпчики) ---------- */
function drawRates(root) {
  var W = chartWidth(root), padX = 8, top = 30, plotH = 140, bottom = 48, H = top + plotH + bottom;
  var rates = months.map(function (m) { return m.rate; });
  var hi = Math.max(10, Math.ceil(Math.max.apply(null, rates) / 10) * 10);
  var lo = Math.min(0, Math.floor(Math.min.apply(null, rates) / 10) * 10);
  function y(v) { return top + (hi - v) / (hi - lo) * plotH; }
  var band = (W - padX * 2) / months.length;
  var svg = s("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "group", "aria-label": "Норма заощаджень за місяцями" });
  svg.appendChild(s("line", { x1: padX, x2: W - padX, y1: y(0), y2: y(0), "class": "axis" }));
  months.forEach(function (m, i) {
    var cx = padX + band * (i + 0.5), bw = Math.min(24, band * 0.5), x = cx - bw / 2;
    var y0 = y(0), y1 = y(m.rate), on = i === sel;
    var g = s("g", { "class": "hit" });
    g.appendChild(s("rect", { x: padX + band * i, y: 0, width: band, height: H, fill: "transparent" }));
    if (Math.abs(y1 - y0) > 0.5) {
      g.appendChild(s("path", {
        d: m.rate >= 0 ? barPath(x, y1, bw, y0 - y1, "up") : barPath(x, y0, bw, y1 - y0, "down"),
        "class": "mark " + (on ? "mark-s1" : "mark-gray")
      }));
    }
    g.appendChild(s("text", {
      x: cx, y: m.rate >= 0 ? y1 - 8 : y1 + 18, "text-anchor": "middle", "class": "val" + (on ? " sel" : "")
    }, pct1(m.rate)));
    g.appendChild(monthLabel(m, cx, top + plotH + 20, "middle"));
    var label = m.name + "\nвідкладено " + money(m.saved) + " із " + money(m.income) +
      (m.incomplete ? "\nдані до " + m.last_op_short : "");
    bindHit(g, pct1(m.rate), label, function () { select(i); });
    svg.appendChild(g);
  });
  root.replaceChildren(svg);
}

/* ---------- 3: фіксовані і керовані (складені горизонтальні стовпчики) ---------- */
function drawSplit(root) {
  var W = chartWidth(root), left = W < 440 ? 92 : 108, right = 104, rowH = 46, barH = 20, top = 4;
  var H = top * 2 + months.length * rowH;
  var svg = s("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "group", "aria-label": "Фіксовані та керовані витрати за місяцями" });
  var max = Math.max.apply(null, months.map(function (m) { return m.fixed + m.variable; }).concat([1]));
  var avail = W - left - right;
  svg.appendChild(s("line", { x1: left, x2: left, y1: top, y2: H - top, "class": "axis" }));
  months.forEach(function (m, i) {
    var y = top + i * rowH, by = y + (rowH - barH) / 2, on = i === sel, total = m.fixed + m.variable;
    var g = s("g", { "class": "hit" + (on ? "" : " dim") });
    g.appendChild(s("rect", { x: 0, y: y, width: W, height: rowH, fill: "transparent" }));
    g.appendChild(monthLabel(m, left - 10, y + rowH / 2 - (m.incomplete ? 6 : -4), "end"));
    var segs = [
      { v: m.fixed, mark: "mark-s1", on: "on-s1" },
      { v: m.variable, mark: "mark-s2", on: "on-s2" }
    ].filter(function (sg) { return sg.v > 0; });
    var x = left;
    segs.forEach(function (sg, j) {
      var w = avail * sg.v / max, first = j === 0, last = j === segs.length - 1;
      var sx = x + (first ? 0 : 1), sw = Math.max(0, w - (first ? 0 : 1) - (last ? 0 : 1));
      if (sw > 0) {
        g.appendChild(last
          ? s("path", { d: barPath(sx, by, sw, barH, "right"), "class": "mark " + sg.mark })
          : s("rect", { x: sx, y: by, width: sw, height: barH, "class": "mark " + sg.mark }));
        var txt = nf2.format(sg.v);
        // Підпис усередині смуги лише для вибраного місяця й лише якщо влізає; решту покаже підказка і таблиця.
        if (on && textWidth(txt, "600 12px " + FONT) + 20 <= sw) {
          g.appendChild(s("text", {
            x: sx + sw / 2, y: by + barH / 2, "text-anchor": "middle", "dominant-baseline": "central",
            "class": "inside " + sg.on
          }, txt));
        }
      }
      x += w;
    });
    g.appendChild(s("text", { x: x + 8, y: y + rowH / 2, "dominant-baseline": "central", "class": "val" + (on ? " sel" : "") }, money(total)));
    var label = m.name + "\nФіксовані: " + money(m.fixed) + " (" + pct0(total ? m.fixed / total * 100 : 0) + ")" +
      "\nКеровані: " + money(m.variable) + " (" + pct0(total ? m.variable / total * 100 : 0) + ")" +
      (m.incomplete ? "\nдані до " + m.last_op_short : "");
    bindHit(g, "Усього " + money(total), label, function () { select(i); });
    svg.appendChild(g);
  });
  root.replaceChildren(svg);
}

/* ---------- таблиці (те саме, що на графіках, без наведення) ---------- */
function table(head, rows, selIndex, totalRow) {
  var t = h("table"), thead = h("thead"), trh = h("tr"), tbody = h("tbody");
  head.forEach(function (c, i) { trh.appendChild(h("th", { "class": i ? "num" : "", scope: "col" }, c)); });
  thead.appendChild(trh);
  rows.forEach(function (r, ri) {
    var tr = h("tr", ri === selIndex ? { "class": "sel" } : null);
    r.forEach(function (c, i) { tr.appendChild(h("td", i ? { "class": "num" } : null, c)); });
    tbody.appendChild(tr);
  });
  if (totalRow) {
    var tr = h("tr", { "class": "total" });
    totalRow.forEach(function (c, i) { tr.appendChild(h("td", i ? { "class": "num" } : null, c)); });
    tbody.appendChild(tr);
  }
  t.append(thead, tbody);
  return t;
}
function monthWithDate(m) { return m.name + (m.incomplete ? " (дані до " + m.last_op_short + ")" : ""); }

/* ---------- висновки: одне речення ---------- */
function conclusion1(m) {
  var top = m.categories[0];
  if (!top || !m.expenses) return "Витрат за цей місяць у виписці немає.";
  return "Найбільше грошей іде на «" + top.name + "» — " + money(top.amount) + ", це " +
    pct0(top.amount / m.expenses * 100) + " усіх витрат місяця.";
}
function conclusion2(m, i) {
  if (m.saved < 0) return "У " + m.loc + " витрати перевищили дохід на " + money(-m.saved) + ", тож відкласти не вдалося.";
  if (m.incomplete) return "Поки що відкладено " + pct1(m.rate) + " доходу, але дані лише до " + m.last_op_short + ", тож норма ще зміниться.";
  if (i > 0) {
    var diff = m.rate - months[i - 1].rate, prev = months[i - 1];
    if (Math.abs(diff) < 0.05) return "У " + m.loc + " відкладено " + pct1(m.rate) + " доходу — стільки ж, як у " + prev.loc + ".";
    return "У " + m.loc + " відкладено " + pct1(m.rate) + " доходу — на " + nf1.format(Math.abs(diff)) + " п.п. " +
      (diff > 0 ? "більше" : "менше") + ", ніж у " + prev.loc + ".";
  }
  return "У " + m.loc + " відкладено " + pct1(m.rate) + " доходу.";
}
function conclusion3(m) {
  var total = m.fixed + m.variable;
  if (!total) return "Витрат за цей місяць у виписці немає.";
  return "Фіксовані витрати — " + money(m.fixed) + " (" + pct0(m.fixed / total * 100) + "), решта " +
    money(m.variable) + " — керовані, саме їх можна скоротити.";
}

/* Зміна проти попереднього місяця: лише для повних місяців, нейтральним кольором (без «добре/погано»). */
var PREV = "порівняно з попереднім місяцем";
function setDelta(n, text) {
  var e = $("d" + n);
  e.hidden = !text;
  e.textContent = text || "";
}
function relDelta(cur, prev) {
  if (!(prev > 0)) return "";
  var p = (cur / prev - 1) * 100;
  return (p >= 0 ? "↑ " : "↓ ") + nf1.format(Math.abs(p)) + " % " + PREV;
}

function setStat(n, label, num, unit, sub, m) {
  var l = $("s" + n + "-label");
  l.replaceChildren(document.createTextNode(label));
  if (m.incomplete) l.appendChild(h("span", { "class": "badge" }, "дані до " + m.last_op_short));
  var v = $("s" + n + "-value");
  v.textContent = num;
  v.appendChild(h("span", { "class": "unit" }, unit));
  $("s" + n + "-sub").textContent = sub;
}

/* ---------- збірка сторінки ---------- */
function renderCharts(animate) {
  hideTip();
  var go = animate && !reduceMotion;
  drawCategories($("cats"), months[sel], go);
  drawRing($("ring"), months[sel], go);
  drawRates($("chart2"));
  drawSplit($("chart3"));
}

function update() {
  var m = months[sel], prev = sel > 0 ? months[sel - 1] : null, total = m.fixed + m.variable;
  Array.prototype.forEach.call($("months").children, function (b, i) {
    b.setAttribute("aria-pressed", i === sel ? "true" : "false");
  });
  var notice = $("notice");
  notice.hidden = !m.incomplete;
  notice.replaceChildren();
  if (m.incomplete) {
    var ic = s("svg", { viewBox: "0 0 24 24", width: 18, height: 18, fill: "none", stroke: "currentColor",
      "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
    ic.appendChild(s("path", { d: "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 11v5M12 8h.01" }));
    notice.append(ic, h("span", null, m.name + " — неповний місяць: у виписці є операції лише до " +
      m.last_op.split("-").reverse().join(".") + "."));
  }

  setStat(1, "Усього витрат, " + m.loc, nf2.format(m.expenses), "зл", "Категорій: " + m.categories.length, m);
  setStat(2, "Норма заощаджень, " + m.loc, nf1.format(m.rate), "%",
    "Відкладено " + money(m.saved) + " із доходу " + money(m.income), m);
  setStat(3, "Керовані витрати, " + m.loc, nf2.format(m.variable), "зл",
    "Фіксовані: " + money(m.fixed) + (total ? " (" + pct0(m.fixed / total * 100) + ")" : ""), m);

  var canCompare = prev && !m.incomplete;
  setDelta(1, canCompare ? relDelta(m.expenses, prev.expenses) : "");
  setDelta(2, canCompare ? ((m.rate - prev.rate >= 0 ? "↑ " : "↓ ") + nf1.format(Math.abs(m.rate - prev.rate)) + " п.п. " + PREV) : "");
  setDelta(3, canCompare ? relDelta(m.variable, prev.variable) : "");

  $("c1").textContent = conclusion1(m);
  $("c2").textContent = conclusion2(m, sel);
  $("c3").textContent = conclusion3(m);
  var note = $("n3");
  note.hidden = !(m.uncategorized > 0);
  $("n3t").textContent = m.uncategorized > 0
    ? "У керовані входять " + money(m.uncategorized) + " операцій без категорії: частина з них може бути фіксованою."
    : "";

  $("t1").replaceChildren(table(["Категорія", "Сума", "Частка"],
    m.categories.map(function (c) { return [c.name, money(c.amount), pct0(m.expenses ? c.amount / m.expenses * 100 : 0)]; }),
    -1, ["Разом", money(m.expenses), m.expenses ? "100 %" : "—"]));
  $("t2").replaceChildren(table(["Місяць", "Дохід", "Витрати", "Відкладено", "Норма"],
    months.map(function (x) { return [monthWithDate(x), money(x.income), money(x.expenses), money(x.saved), pct1(x.rate)]; }), sel));
  $("t3").replaceChildren(table(["Місяць", "Фіксовані", "Керовані", "Усього", "Частка фіксованих"],
    months.map(function (x) {
      var t = x.fixed + x.variable;
      return [monthWithDate(x), money(x.fixed), money(x.variable), money(t), t ? pct0(x.fixed / t * 100) : "—"];
    }), sel));
  renderCharts(true);
}

function select(i) { sel = i; update(); }

function init() {
  $("sub").textContent = "Дані виписки до " + DATA.data_until + " · злоті (PLN)";
  var box = $("months");
  months.forEach(function (m, i) {
    var b = h("button", { type: "button", "aria-pressed": "false" });
    b.appendChild(document.createTextNode(m.name));
    if (m.incomplete) b.appendChild(h("small", null, "до " + m.last_op_short));
    b.addEventListener("click", function () { select(i); });
    box.appendChild(b);
  });
  update();
  var raf = 0;
  window.addEventListener("resize", function () {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(function () { renderCharts(false); });
  });
}
init();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
