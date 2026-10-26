"""Дані для нового звіту: рахуємо з журналів при кожному запиті, тож готівка з «+» видна одразу.

Повертає по кожному журналу (Марко, Оля) і місяцю: дохід, витрати, відкладено, норму заощаджень,
фіксовані й змінні, витрати за категоріями. Неповний місяць позначаємо датою останньої операції.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data

ROOT = Path(__file__).parent
LEDGERS = [("marko", "Марко", "marko.beancount"), ("olya", "Оля", "main.beancount")]
MONTHS = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
          "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]


def _ledger(path):
    entries, errors, _ = loader.load_file(str(path))
    months = defaultdict(lambda: {"income": Decimal(0), "fixed": Decimal(0), "variable": Decimal(0),
                                  "categories": defaultdict(Decimal), "weeks": defaultdict(Decimal), "last": None})
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        m = months[(entry.date.year, entry.date.month)]
        fixed = "fixed" in (entry.tags or set())
        touched = False
        for posting in entry.postings:
            number = posting.units.number
            if posting.account.startswith("Income"):
                m["income"] -= number
                touched = True
            elif posting.account.startswith("Expenses"):
                m["fixed" if fixed else "variable"] += number
                m["categories"][posting.account.split(":", 1)[1]] += number
                m["weeks"][min((entry.date.day - 1) // 7, 4)] += number
                touched = True
        if touched and (m["last"] is None or entry.date > m["last"]):
            m["last"] = entry.date
    return errors, months


def report():
    people = []
    for key, name, filename in LEDGERS:
        errors, months = _ledger(ROOT / filename)
        # Місяць без доходів і витрат (лише початковий залишок на 31.07) у звіт не показуємо.
        keys = sorted(k for k, m in months.items() if m["income"] or m["fixed"] or m["variable"])
        out = []
        for year, month in keys:
            m = months[(year, month)]
            spent = m["fixed"] + m["variable"]
            saved = m["income"] - spent
            last = m["last"]
            incomplete = (year, month) == keys[-1] and last < date(year, month, monthrange(year, month)[1])
            out.append({
                "key": f"{year}-{month:02}",
                "name": MONTHS[month - 1],
                "incomplete": incomplete,
                "last_op": last.strftime("%d.%m") if last else "",
                "income": float(m["income"]),
                "expenses": float(spent),
                "saved": float(saved),
                "rate": float(round(saved / m["income"] * 100, 1)) if m["income"] else 0.0,
                "fixed": float(m["fixed"]),
                "variable": float(m["variable"]),
                # Тижні місяця: 1–7, 8–14, 15–21, 22–28, 29+ (для графіка).
                "weeks": [float(m["weeks"][i]) for i in range(5)],
                "categories": sorted(({"name": k.replace("-", " "), "amount": float(v)}
                                      for k, v in m["categories"].items() if v), key=lambda c: -c["amount"]),
            })
        people.append({"key": key, "name": name, "ok": not errors, "months": out})
    return {"people": people}
