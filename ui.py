"""Новий звіт трекера (мобільний, світлий): головна сторінка «/».

Цифри рахує report_data.py з журналів при кожному відкритті, тож витрата, додана через «+» у Fava
(/fava), одразу видна і тут. Шрифт Outfit (OFL) лежить у static/, інтернет для сторінки не потрібен.
"""
import json
from pathlib import Path

from flask import Flask, send_from_directory

import report_data

ROOT = Path(__file__).parent
ui_app = Flask(__name__, static_folder=None)


@ui_app.route("/static/<path:name>")
def static_file(name):
    return send_from_directory(ROOT / "static", name, max_age=86400)


@ui_app.route("/")
def index():
    payload = json.dumps(report_data.report(), ensure_ascii=False).replace("<", "\\u003c")
    return PAGE.replace("__DATA__", payload)


PAGE = r"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#E9E6DD">
<title>Фінанси Олі й Марка</title>
<style>
@font-face { font-family: "Outfit"; src: url("static/Outfit.ttf") format("truetype"); font-weight: 100 900; font-display: swap; }
:root {
  --bg: #E9E6DD; --card: #FFFFFF; --soft: #F4F2EC; --ink: #1D1C1A; --ink2: #5F5D58; --muted: #9A978F;
  --line: #E2DED3; --yellow: #F7C443; --yellow-soft: #FBEBAE; --dark: #1F1E1C;
  --r: 20px; --font: "Outfit", system-ui, -apple-system, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.4 var(--font); -webkit-font-smoothing: antialiased; }
.app { max-width: 440px; margin: 0 auto; padding: 14px 18px 140px; }
button { font: inherit; color: inherit; }
a { color: inherit; text-decoration: none; }

/* верх */
.top { display: grid; grid-template-columns: 44px 1fr 44px; align-items: center; height: 52px; }
.top h1 { margin: 0; text-align: center; font-size: 16px; font-weight: 500; }
.circle { width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; background: var(--line); color: var(--ink); }
.circle svg { width: 16px; height: 16px; }

/* перемикач людей — як Income / Expense */
.seg { display: flex; padding: 4px; margin: 12px 0 18px; background: #DCD8CD; border-radius: 999px; }
.seg button { flex: 1; min-height: 44px; border: 0; background: transparent; padding: 11px 0; border-radius: 999px; font-size: 15px; color: var(--ink2); cursor: pointer; transition: background .25s, color .25s; }
.seg button[aria-pressed="true"] { background: var(--card); color: var(--ink); box-shadow: 0 1px 2px rgba(0,0,0,.06); }

.label { font-size: 16px; color: var(--ink); font-weight: 400; }
.big { font-size: 46px; font-weight: 400; letter-spacing: -0.02em; line-height: 1.1; margin: 4px 0 12px; }
.big .cents { color: var(--muted); }
.big .cur { font-size: 26px; color: var(--muted); margin-left: 6px; }
.meta { display: flex; gap: 8px; flex-wrap: wrap; margin: -2px 0 16px; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px; background: var(--card); font-size: 13px; color: var(--ink2); }
.pill b { color: var(--ink); font-weight: 600; }
.pill.dark { background: var(--dark); color: #fff; }
.btn { display: flex; align-items: center; justify-content: center; gap: 8px; height: 48px; border-radius: 999px; background: var(--dark); color: #fff; font-size: 15px; }
.btn svg { width: 16px; height: 16px; }

/* чипси місяців — як All / Savings */
.chips { display: flex; gap: 2px; padding: 5px; margin: 18px 0; background: var(--card); border-radius: 999px; overflow-x: auto; scrollbar-width: none; }
.chips::-webkit-scrollbar { display: none; }
.chips button { flex: 1 0 auto; min-height: 44px; border: 0; background: transparent; padding: 9px 14px; border-radius: 999px; font-size: 14px; color: var(--ink); cursor: pointer; white-space: nowrap; transition: background .25s; }
.chips button small { color: var(--muted); font-size: 12px; margin-left: 4px; }
.chips button[aria-pressed="true"] { background: var(--yellow); }
.chips button[aria-pressed="true"] small { color: var(--ink2); }

/* жовта картка з графіком */
.chart { background: var(--yellow); border-radius: var(--r); padding: 16px 16px 12px; }
.chart-head { display: flex; justify-content: space-between; align-items: flex-start; }
.chart-head .t { font-size: 14px; color: var(--ink2); }
.chart-head .v { font-size: 22px; font-weight: 500; margin-top: 2px; }
.bars { display: grid; grid-template-columns: repeat(5, 1fr); grid-auto-rows: 160px; gap: 8px; align-items: end; margin-top: 40px; }
.bar { position: relative; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 160px; }
/* стовпчик: обгортка росте, всередині світла підкладка-«тінь» і сам штрихований стовпчик */
.stack { position: relative; width: 100%; max-width: 52px; height: 0; transition: height .9s cubic-bezier(.2,.8,.2,1); }
.ghost { position: absolute; left: -5px; right: 5px; top: -9px; bottom: 0; border-radius: 16px 16px 6px 6px; background: #F2CC6B; }
.col { position: absolute; inset: 0; border-radius: 14px 14px 6px 6px;
  background: repeating-linear-gradient(45deg, rgba(29,28,26,.82) 0 3px, transparent 3px 9px), #DFA83C; }
/* жовта крапка з темним обідком у верхньому лівому куті */
.col::before { content: ""; position: absolute; top: -5px; left: 4px; width: 11px; height: 11px; border-radius: 50%;
  background: var(--yellow); border: 2px solid #8C6516; }
.bar.on .col { background: var(--dark); }
.bar.on .col::before { left: 50%; margin-left: -5.5px; top: -6px; border-color: var(--dark); }
.bubble { position: absolute; bottom: calc(100% + 16px); left: 50%; transform: translateX(-50%); background: #fff; border-radius: 999px;
  padding: 5px 10px; font-size: 12px; font-weight: 500; white-space: nowrap; box-shadow: 0 2px 6px rgba(0,0,0,.08); z-index: 3; }
.bubble::after { content: ""; position: absolute; left: 50%; bottom: -5px; margin-left: -5px; border: 5px solid transparent; border-bottom: 0; border-top-color: #fff; }
.chart-btn { width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; background: var(--dark); color: var(--yellow); }
.chart-btn svg { width: 16px; height: 16px; }
.wk { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-top: 8px; text-align: center; font-size: 12px; color: var(--ink2); }

/* секції */
.sec { display: flex; justify-content: space-between; align-items: baseline; margin: 26px 2px 12px; }
.sec h2 { margin: 0; font-size: 19px; font-weight: 500; }
.sec span { font-size: 13px; color: var(--ink2); }

/* фіксовані / змінні — як Budgets */
.budget { background: var(--card); border-radius: var(--r); padding: 16px; margin-bottom: 10px; }
.budget.hi { background: var(--yellow-soft); }
.budget .row1 { display: flex; justify-content: space-between; align-items: baseline; }
.budget .name { font-size: 17px; font-weight: 500; }
.budget .when { font-size: 13px; color: var(--ink2); }
.tag { display: inline-block; margin: 10px 0 12px; padding: 7px 13px; border-radius: 999px; background: var(--dark); color: #fff; font-size: 13px; }
.row2 { display: flex; justify-content: space-between; align-items: baseline; font-size: 13px; color: var(--ink2); }
.row2 .amt { font-size: 17px; color: var(--ink); font-weight: 500; }
.row2 .amt i { font-style: normal; color: var(--muted); font-weight: 400; }
.track { height: 6px; border-radius: 999px; background: rgba(29,28,26,.08); margin-top: 10px; overflow: hidden; }
.track b { display: block; height: 100%; width: 0; border-radius: 999px; background: var(--dark); transition: width 1s cubic-bezier(.2,.8,.2,1); }
.track.y b { background: var(--yellow); }

/* категорії — як Saving Goals */
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.cat { background: var(--card); border-radius: var(--r); padding: 14px; }
/* іконка: чорне коло в широкому напівпрозорому кільці, по якому жовтим заповнюється частка категорії */
.ico { position: relative; width: 54px; height: 54px; margin-bottom: 12px; }
.ico .ring { position: absolute; inset: 0; width: 54px; height: 54px; transform: rotate(-90deg); }
.ico .ring circle { fill: none; stroke-width: 6; }
.ico .ring .bg { stroke: rgba(29,28,26,.10); }
.ico .ring .fg { stroke: var(--yellow); stroke-linecap: round; transition: stroke-dasharray 1.1s cubic-bezier(.2,.8,.2,1); }
.ico .dot { position: absolute; inset: 7px; border-radius: 50%; display: grid; place-items: center; background: var(--dark); color: var(--yellow); }
.ico .dot svg { width: 17px; height: 17px; }
.cat .n { font-size: 17px; font-weight: 400; line-height: 1.2; }
.cat .s { font-size: 12px; color: var(--ink2); margin: 4px 0 10px; }
.cat .a { font-size: 14px; font-weight: 600; }
.cat .a i { font-style: normal; font-weight: 400; color: var(--muted); }

.note { margin: 16px 4px 0; font-size: 12px; color: var(--muted); text-align: center; }

/* нижня навігація + fade */
.fade { position: fixed; left: 0; right: 0; bottom: 0; height: 150px; pointer-events: none; background: linear-gradient(rgba(233,230,221,0), var(--bg) 70%); }
.nav { position: fixed; left: 50%; bottom: calc(16px + env(safe-area-inset-bottom, 0px)); transform: translateX(-50%); display: flex; gap: 10px; padding: 8px; border-radius: 999px; background: var(--dark); }
.nav a { width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center; background: #fff; color: var(--ink); }
.nav a.on { background: var(--yellow); }
.nav svg { width: 19px; height: 19px; }
@media (prefers-reduced-motion: reduce) { .stack, .track b, .ring .fg { transition: none; } }
</style>
</head>
<body>
<div class="app">
  <div class="top">
    <a class="circle" href="fava/" aria-label="Відкрити Fava"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg></a>
    <h1>Звіт за місяць</h1>
    <span class="circle" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg></span>
  </div>

  <div class="seg" id="people" role="group" aria-label="Чий журнал"></div>

  <div class="label" id="lbl"></div>
  <div class="big" id="saved"></div>
  <div class="meta" id="meta"></div>
  <a class="btn" id="add" href="fava/"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>Додати витрату</a>

  <div class="chips" id="months" role="group" aria-label="Місяць"></div>

  <section class="chart" aria-labelledby="ch">
    <div class="chart-head"><div><div class="t" id="ch">Витрати за місяць</div><div class="v" id="spent"></div></div><a class="chart-btn" id="chartq" href="fava/" aria-label="Запити у Fava"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg></a></div>
    <div class="bars" id="bars"></div>
    <div class="wk"><span>1–7</span><span>8–14</span><span>15–21</span><span>22–28</span><span>29+</span></div>
  </section>

  <div class="sec"><h2>Фіксовані і змінні</h2><span id="fvsum"></span></div>
  <div id="fv"></div>

  <div class="sec"><h2>Категорії</h2><span id="catn"></span></div>
  <div class="grid" id="cats"></div>
  <p class="note" id="note"></p>
</div>
<div class="fade" aria-hidden="true"></div>
<nav class="nav" aria-label="Навігація">
  <a class="on" href="./" aria-label="Звіт"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5V21H3z"/><path d="M9 21v-6h6v6"/></svg></a>
  <a id="navq" href="fava/" aria-label="Запити у Fava"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 20V10M12 20V4M19 20v-7"/></svg></a>
  <a id="nava" href="fava/" aria-label="Додати витрату"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></a>
  <a id="navj" href="fava/" aria-label="Журнал операцій"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/></svg></a>
</nav>

<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
"use strict";
var D = JSON.parse(document.getElementById("data").textContent);
var FAVA = { marko: "fava/%D1%84%D1%96%D0%BD%D0%B0%D0%BD%D1%81%D0%B8-%D0%BC%D0%B0%D1%80%D0%BA%D0%B0/",
             olya: "fava/%D1%84%D1%96%D0%BD%D0%B0%D0%BD%D1%81%D0%B8-%D0%BE%D0%BB%D1%96/" };
var LOC = ["січні","лютому","березні","квітні","травні","червні","липні","серпні","вересні","жовтні","листопаді","грудні"];
var ICON = {
  "Житло": ["M3 10.5 12 3l9 7.5V21H3z", "M9 21v-6h6v6"],
  "Продукти": ["M3 4h2l2.4 11.2a1 1 0 0 0 1 .8h8.9a1 1 0 0 0 1-.8L20 8H6", "M8 20.5a1 1 0 1 0 2 0a1 1 0 1 0-2 0", "M16.5 20.5a1 1 0 1 0 2 0a1 1 0 1 0-2 0"],
  "Кафе": ["M4 8h12v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z", "M16 10h2a2 2 0 0 1 0 4h-2", "M8 3v2M12 3v2"],
  "Доставка Їжі": ["M5 8h14l-1 12H6z", "M9 8V6a3 3 0 0 1 6 0v2"],
  "Транспорт": ["M6 4h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M4 11h16", "M7 17v2M17 17v2"],
  "Підписки": ["M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5", "M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"],
  "Краса і Побут": ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"],
  "Спорт": ["M6 7v10M18 7v10M3 10v4M21 10v4M6 12h12"],
  "Звязок": ["M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z", "M11 18h2"],
  "Комунальні": ["M13 2 4 14h7l-1 8 9-12h-7z"],
  "Розваги": ["M6 9h12a3 3 0 0 1 3 3v2a3 3 0 0 1-5.2 2L14 14h-4l-1.8 2A3 3 0 0 1 3 14v-2a3 3 0 0 1 3-3z", "M8 11v3M6.5 12.5h3"],
  "Покупки": ["M6 7h12l1 13H5z", "M9 7a3 3 0 0 1 6 0"],
  "_": ["M3 12V4h8l10 10-8 8z", "M7.5 8.5h.01"]
};
var nf2 = new Intl.NumberFormat("uk-UA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
var nf0 = new Intl.NumberFormat("uk-UA", { maximumFractionDigits: 0 });
var nf1 = new Intl.NumberFormat("uk-UA", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
function zl(v) { return nf2.format(v) + " зл"; }
function $(id) { return document.getElementById(id); }
function h(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; }
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
function icon(name) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    (ICON[name] || ICON._).map(function (d) { return '<path d="' + d + '"/>'; }).join("") + "</svg>";
}
function grow(sel, attr) {  // плавне заповнення після першого кадру
  requestAnimationFrame(function () { requestAnimationFrame(function () {
    document.querySelectorAll(sel).forEach(function (e) { e.style[attr] = e.dataset.to; });
  }); });
}

// за замовчуванням — останній місяць, у якому вже є дохід (у Марка зарплата приходить наприкінці місяця)
function lastFull(P) { for (var i = P.months.length - 1; i >= 0; i--) if (P.months[i].income > 0) return i; return P.months.length - 1; }
var person = 0, month = lastFull(D.people[0]);

function renderPeople() {
  var box = $("people"); box.innerHTML = "";
  D.people.forEach(function (p, i) {
    var b = h("button", "", esc(p.name)); b.type = "button"; b.setAttribute("aria-pressed", i === person);
    b.onclick = function () { person = i; month = Math.min(month, D.people[i].months.length - 1); render(); };
    box.appendChild(b);
  });
}

function renderMonths(P) {
  var box = $("months"); box.innerHTML = "";
  P.months.forEach(function (m, i) {
    var b = h("button", "", esc(m.name) + (m.incomplete ? "<small>до " + m.last_op + "</small>" : ""));
    b.type = "button"; b.setAttribute("aria-pressed", i === month);
    b.onclick = function () { month = i; render(); };
    box.appendChild(b);
  });
}

function render() {
  var P = D.people[person], M = P.months[month], mi = parseInt(M.key.slice(5), 10) - 1;
  renderPeople(); renderMonths(P);
  var link = FAVA[P.key];
  $("add").href = link + "journal/"; $("nava").href = link + "journal/";
  $("navq").href = link + "query/"; $("navj").href = link + "journal/";
  $("chartq").href = link + "query/";

  var noIncome = !(M.income > 0);
  $("lbl").textContent = (noIncome ? "Витрачено у " : "Відкладено у ") + LOC[mi];
  var parts = nf2.format(noIncome ? M.expenses : M.saved).split(",");
  $("saved").innerHTML = parts[0] + '<span class="cents">,' + parts[1] + '</span><span class="cur">зл</span>';
  $("meta").innerHTML = (noIncome ? '<span class="pill dark">зарплата ще не надійшла</span>'
      : '<span class="pill dark">' + nf1.format(M.rate) + ' % доходу</span><span class="pill">Дохід <b>' + zl(M.income) + '</b></span>') +
    (M.incomplete ? '<span class="pill">дані до ' + M.last_op + '</span>' : "");

  // графік: витрати по тижнях, найбільший тиждень — чорний з бульбашкою
  $("spent").textContent = zl(M.expenses);
  var max = Math.max.apply(null, M.weeks.concat([1])), top = M.weeks.indexOf(Math.max.apply(null, M.weeks));
  var bars = $("bars"); bars.innerHTML = "";
  M.weeks.forEach(function (w, i) {
    var b = h("div", "bar" + (i === top ? " on" : ""));
    var stack = h("div", "stack"); stack.dataset.to = Math.max(8, w / max * 100) + "%";
    stack.appendChild(h("div", "ghost")); stack.appendChild(h("div", "col"));
    if (i === top) stack.appendChild(h("div", "bubble", nf0.format(w) + " зл"));
    b.appendChild(stack);
    b.title = zl(w);
    bars.appendChild(b);
  });
  grow(".bar .stack", "height");

  // фіксовані і змінні
  var total = M.fixed + M.variable;
  $("fvsum").textContent = "усього " + zl(total);
  var fv = $("fv"); fv.innerHTML = "";
  var FIXED = { marko: "звʼязок, світло, спортзал, YouTube", olya: "оренда, проїзний, підписки, телефон" };
  [["Фіксовані", FIXED[P.key], M.fixed, "hi", ""],
   ["Змінні", "можна скоротити", M.variable, "", "y"]].forEach(function (r) {
    var pct = total ? r[2] / total * 100 : 0;
    var c = h("div", "budget " + r[3]);
    c.innerHTML = '<div class="row1"><span class="name">' + r[0] + '</span><span class="when">' + esc(M.name) + (M.incomplete ? " · до " + M.last_op : "") + '</span></div>' +
      '<span class="tag">' + r[1] + '</span>' +
      '<div class="row2"><span>' + nf0.format(pct) + ' % витрат</span><span class="amt">' + nf2.format(r[2]) + '<i>/' + nf2.format(total) + '</i></span></div>' +
      '<div class="track ' + r[4] + '"><b data-to="' + pct + '%"></b></div>';
    fv.appendChild(c);
  });

  // категорії
  $("catn").textContent = M.categories.length + " категорій";
  var grid = $("cats"); grid.innerHTML = "";
  M.categories.forEach(function (c) {
    var pct = M.expenses ? c.amount / M.expenses * 100 : 0;
    var e = h("div", "cat");
    var C = 2 * Math.PI * 24;
    e.innerHTML = '<div class="ico"><svg class="ring" viewBox="0 0 54 54" aria-hidden="true"><circle class="bg" cx="27" cy="27" r="24"/>' +
      '<circle class="fg" cx="27" cy="27" r="24" style="stroke-dasharray: 0 ' + C + '" data-to="' + (Math.max(pct, 1.5) / 100 * C) + ' ' + C + '"/></svg>' +
      '<span class="dot">' + icon(c.name) + '</span></div><div class="n">' + esc(c.name) + '</div>' +
      '<div class="s">' + nf0.format(pct) + ' % витрат місяця</div>' +
      '<div class="a">' + nf2.format(c.amount) + '<i>/' + nf2.format(M.expenses) + '</i></div>';
    grid.appendChild(e);
  });
  grow(".track b", "width");
  requestAnimationFrame(function () { requestAnimationFrame(function () {
    document.querySelectorAll(".ring .fg").forEach(function (c) { c.style.strokeDasharray = c.dataset.to; });
  }); });
  $("note").textContent = P.ok ? "Облік звірено з банком (bean-check без помилок)" : "Увага: у журналі є помилки обліку";
}
render();
})();
</script>
</body>
</html>
"""
