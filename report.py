"""Будує report.html: виконує збережені запити 1–3 з main.beancount і складає односторінковий звіт.

Запуск: uv run python report.py
Сторінка автономна: усе (стилі, скрипт, графіки) лежить в одному файлі, інтернет не потрібен.
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
<title>Фінанси Олі — місячний звіт</title>
<style>
:root {
  color-scheme: light dark;
  --page: #f9f9f7; --surface: #fcfcfb;
  --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --s1: #2a78d6; --s2: #eb6834; --gray: #c3c2b7;
  --on-s1: #ffffff; --on-s2: #0b0b0b;
  --tip-bg: #0b0b0b; --tip-ink: #f0efec;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0d0d0d; --surface: #1a1a19;
    --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --gray: #4a4a46;
    --on-s1: #0b0b0b; --on-s2: #0b0b0b;
    --tip-bg: #f0efec; --tip-ink: #0b0b0b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 28px; line-height: 1.2; margin: 0 0 4px; font-weight: 600; }
h2 { font-size: 20px; line-height: 1.3; margin: 0 0 16px; font-weight: 600; }
.sub { margin: 0 0 20px; color: var(--ink2); }
.switch { display: flex; flex-wrap: wrap; gap: 8px; }
.switch button {
  font: inherit; font-size: 15px; color: var(--ink); background: var(--surface);
  border: 1px solid var(--border); border-radius: 999px; padding: 8px 16px; cursor: pointer;
}
.switch button:hover { border-color: var(--axis); }
.switch button[aria-pressed="true"] { background: var(--ink); color: var(--surface); border-color: var(--ink); font-weight: 600; }
.switch small { font-size: 13px; opacity: 0.8; }
button:focus-visible, .hit:focus-visible, summary:focus-visible { outline: 2px solid var(--s1); outline-offset: 2px; }
.notice {
  margin: 12px 0 0; padding: 10px 14px; border: 1px solid var(--axis); border-radius: 8px;
  background: var(--surface); color: var(--ink2); font-size: 15px;
}
.card {
  margin-top: 16px; padding: 20px; background: var(--surface);
  border: 1px solid var(--border); border-radius: 12px;
}
.kicker { margin: 0 0 4px; font-size: 13px; color: var(--ink2); }
.stat-label { color: var(--ink2); font-size: 14px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.stat-value { font-size: 36px; line-height: 1.15; font-weight: 600; margin-top: 2px; }
.stat-value.hero { font-size: 56px; line-height: 1.05; }
.stat-sub { color: var(--ink2); font-size: 15px; margin-top: 2px; min-height: 1.5em; }
.badge {
  font-size: 12px; padding: 1px 8px; border: 1px solid var(--axis); border-radius: 999px;
  color: var(--ink2); white-space: nowrap;
}
.caption { margin: 20px 0 4px; font-size: 13px; color: var(--ink2); }
.chart { width: 100%; }
.chart svg { display: block; max-width: 100%; overflow: visible; }
.legend { display: flex; gap: 16px; margin: 4px 0 8px; font-size: 13px; color: var(--ink2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { display: inline-block; width: 12px; height: 8px; border-radius: 2px; }
.sw-s1 { background: var(--s1); } .sw-s2 { background: var(--s2); }
.conclusion { margin: 16px 0 0; font-size: 17px; }
.note { margin: 6px 0 0; font-size: 13px; color: var(--ink2); }
details { margin-top: 14px; font-size: 14px; color: var(--ink2); }
summary { cursor: pointer; width: fit-content; }
.table-wrap { overflow-x: auto; margin-top: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; color: var(--ink); }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--grid); text-align: left; white-space: nowrap; }
th { font-weight: 600; color: var(--ink2); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.sel td { font-weight: 600; }
tr.total td { font-weight: 600; border-bottom: 0; }
svg text { font-family: inherit; }
.lbl { fill: var(--ink2); font-size: 13px; }
.lbl small, .sublbl { fill: var(--muted); font-size: 12px; }
.val { fill: var(--ink); font-size: 13px; }
.val.sel { font-weight: 600; }
.inside { font-size: 12px; font-weight: 600; }
.on-s1 { fill: var(--on-s1); } .on-s2 { fill: var(--on-s2); }
.axis { stroke: var(--axis); stroke-width: 1; }
.mark-s1 { fill: var(--s1); } .mark-s2 { fill: var(--s2); } .mark-gray { fill: var(--gray); }
.hit { cursor: default; outline: none; }
.hit.click { cursor: pointer; }
.hit:hover .mark, .hit:focus .mark { opacity: 0.78; }
.hit.dim .mark, .hit.dim .inside { opacity: 0.4; }
.hit.dim:hover .mark, .hit.dim:focus .mark { opacity: 0.7; }
.tip {
  position: fixed; z-index: 10; pointer-events: none; max-width: 280px; padding: 8px 12px;
  background: var(--tip-bg); color: var(--tip-ink); border-radius: 8px; font-size: 13px;
  line-height: 1.4; white-space: pre-line; box-shadow: 0 2px 8px rgba(0,0,0,0.25);
}
.tip strong { display: block; font-size: 16px; }
.tip[hidden] { display: none; }
@media (forced-colors: active) {
  .mark-s1 { fill: Highlight; } .mark-s2 { fill: LinkText; } .mark-gray { fill: GrayText; }
}
@media (max-width: 480px) {
  h1 { font-size: 24px; } .card { padding: 16px; }
  .stat-value { font-size: 32px; } .stat-value.hero { font-size: 48px; }
}
</style>
</head>
<body>
<main>
  <header>
    <h1>Фінанси Олі</h1>
    <p class="sub" id="sub"></p>
    <div class="switch" id="months" role="group" aria-label="Місяць"></div>
    <p class="notice" id="notice" hidden></p>
  </header>

  <section class="card" aria-labelledby="h-cat">
    <p class="kicker">Питання 1</p>
    <h2 id="h-cat">Скільки грошей іде на кожну категорію витрат?</h2>
    <div class="stat">
      <div class="stat-label" id="s1-label"></div>
      <div class="stat-value" id="s1-value"></div>
      <div class="stat-sub" id="s1-sub"></div>
    </div>
    <p class="caption">Витрати за категоріями, зл</p>
    <div class="chart" id="chart1"></div>
    <p class="conclusion" id="c1"></p>
    <details><summary>Показати таблицею</summary><div class="table-wrap" id="t1"></div></details>
  </section>

  <section class="card" aria-labelledby="h-save">
    <p class="kicker">Питання 2</p>
    <h2 id="h-save">Яку частку доходу я відкладаю?</h2>
    <div class="stat">
      <div class="stat-label" id="s2-label"></div>
      <div class="stat-value hero" id="s2-value"></div>
      <div class="stat-sub" id="s2-sub"></div>
    </div>
    <p class="caption">Норма заощаджень за місяцями, %</p>
    <div class="chart" id="chart2"></div>
    <p class="conclusion" id="c2"></p>
    <details><summary>Показати таблицею</summary><div class="table-wrap" id="t2"></div></details>
  </section>

  <section class="card" aria-labelledby="h-fix">
    <p class="kicker">Питання 3</p>
    <h2 id="h-fix">Скільки витрат фіксовані, а скільки я можу змінювати?</h2>
    <div class="stat">
      <div class="stat-label" id="s3-label"></div>
      <div class="stat-value" id="s3-value"></div>
      <div class="stat-sub" id="s3-sub"></div>
    </div>
    <p class="caption">Фіксовані та керовані витрати за місяцями, зл</p>
    <div class="legend"><span><i class="sw-s1"></i>Фіксовані</span><span><i class="sw-s2"></i>Керовані</span></div>
    <div class="chart" id="chart3"></div>
    <p class="conclusion" id="c3"></p>
    <p class="note" id="n3" hidden></p>
    <details><summary>Показати таблицею</summary><div class="table-wrap" id="t3"></div></details>
  </section>
</main>

<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
"use strict";
var SVG_NS = "http://www.w3.org/2000/svg";
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

function chartWidth(el) { return Math.max(280, Math.floor(el.clientWidth)); }

function monthLabel(m, x, y, anchor) {
  var t = s("text", { x: x, y: y, "text-anchor": anchor, "class": "lbl" }, m.name);
  if (m.incomplete) {
    t.appendChild(s("tspan", { x: x, dy: 15, "class": "sublbl" }, "до " + m.last_op_short));
  }
  return t;
}

/* ---------- графік 1: витрати за категоріями (горизонтальні стовпчики) ---------- */
function drawCategories(root, m) {
  var W = chartWidth(root), left = W < 440 ? 112 : 150, right = 96, rowH = 34, barH = 18, top = 4;
  var cats = m.categories;
  var H = top * 2 + cats.length * rowH;
  var svg = s("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "group", "aria-label": "Витрати за категоріями" });
  var max = Math.max.apply(null, cats.map(function (c) { return c.amount; }).concat([1]));
  var avail = W - left - right;
  svg.appendChild(s("line", { x1: left, x2: left, y1: top, y2: H - top, "class": "axis" }));
  cats.forEach(function (c, i) {
    var y = top + i * rowH, by = y + (rowH - barH) / 2, w = avail * c.amount / max;
    var g = s("g", { "class": "hit" });
    g.appendChild(s("rect", { x: 0, y: y, width: W, height: rowH, fill: "transparent" }));
    g.appendChild(s("text", { x: left - 10, y: y + rowH / 2, "text-anchor": "end", "dominant-baseline": "central", "class": "lbl" }, c.name));
    if (w > 0) g.appendChild(s("path", { d: barPath(left, by, w, barH, "right"), "class": "mark mark-s1" }));
    g.appendChild(s("text", { x: left + w + 8, y: y + rowH / 2, "dominant-baseline": "central", "class": "val" }, money(c.amount)));
    bindHit(g, money(c.amount), c.name + "\n" + pct0(c.amount / m.expenses * 100) + " витрат місяця");
    svg.appendChild(g);
  });
  root.replaceChildren(svg);
}

/* ---------- графік 2: норма заощаджень по місяцях (стовпчики) ---------- */
function drawRates(root) {
  var W = chartWidth(root), padX = 8, top = 30, plotH = 150, bottom = 48, H = top + plotH + bottom;
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

/* ---------- графік 3: фіксовані і керовані (складені горизонтальні стовпчики) ---------- */
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

function setStat(n, label, value, sub, m) {
  var l = $("s" + n + "-label");
  l.replaceChildren(document.createTextNode(label));
  if (m.incomplete) l.appendChild(h("span", { "class": "badge" }, "дані до " + m.last_op_short));
  $("s" + n + "-value").textContent = value;
  $("s" + n + "-sub").textContent = sub;
}

/* ---------- збірка сторінки ---------- */
function renderCharts() {
  hideTip();
  drawCategories($("chart1"), months[sel]);
  drawRates($("chart2"));
  drawSplit($("chart3"));
}

function update() {
  var m = months[sel], total = m.fixed + m.variable;
  Array.prototype.forEach.call($("months").children, function (b, i) {
    b.setAttribute("aria-pressed", i === sel ? "true" : "false");
  });
  var notice = $("notice");
  notice.hidden = !m.incomplete;
  notice.textContent = m.incomplete
    ? m.name + " — неповний місяць: у виписці є операції лише до " + m.last_op.split("-").reverse().join(".") + "."
    : "";

  setStat(1, "Усього витрат, " + m.loc, money(m.expenses), "Категорій: " + m.categories.length, m);
  setStat(2, "Норма заощаджень, " + m.loc, pct1(m.rate), "Відкладено " + money(m.saved) + " із доходу " + money(m.income), m);
  setStat(3, "Керовані витрати, " + m.loc, money(m.variable),
    "Фіксовані: " + money(m.fixed) + (total ? " (" + pct0(m.fixed / total * 100) + ")" : ""), m);

  $("c1").textContent = conclusion1(m);
  $("c2").textContent = conclusion2(m, sel);
  $("c3").textContent = conclusion3(m);
  var note = $("n3");
  note.hidden = !(m.uncategorized > 0);
  note.textContent = m.uncategorized > 0
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
  renderCharts();
}

function select(i) { sel = i; update(); }

function init() {
  $("sub").textContent = "Звіт за банківською випискою, дані до " + DATA.data_until + ". Усі суми в злотих (PLN).";
  var box = $("months");
  months.forEach(function (m, i) {
    var b = h("button", { type: "button", "aria-pressed": "false" });
    b.appendChild(document.createTextNode(m.name));
    if (m.incomplete) b.appendChild(h("small", null, " · до " + m.last_op_short));
    b.addEventListener("click", function () { select(i); });
    box.appendChild(b);
  });
  update();
  var raf = 0;
  window.addEventListener("resize", function () {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(renderCharts);
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
