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
# Чий журнал і в який файл дописуємо готівку з форми «Додати витрату».
CASH_FILES = {"marko": ("marko.beancount", "marko-cash.beancount")}
MONTHS = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
          "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]


def _ledger(path):
    entries, errors, _ = loader.load_file(str(path))
    months = defaultdict(lambda: {"income": Decimal(0), "fixed": Decimal(0), "variable": Decimal(0),
                                  "categories": defaultdict(Decimal), "weeks": defaultdict(Decimal), "last": None,
                                  "ops": []})
    accounts = []
    for entry in entries:
        if isinstance(entry, data.Open) and entry.account.startswith("Expenses:") and entry.account != "Expenses:Без-Категорії":
            accounts.append(entry.account.split(":", 1)[1])
        if not isinstance(entry, data.Transaction):
            continue
        m = months[(entry.date.year, entry.date.month)]
        fixed = "fixed" in (entry.tags or set())
        cash = any(p.account == "Assets:Готівка" for p in entry.postings)
        # готівку з форми можна видалити: запамʼятовуємо рядок запису у файлі готівки
        in_cash_file = Path(entry.meta.get("filename", "")).name in {f[1] for f in CASH_FILES.values()}
        line = entry.meta.get("lineno") if in_cash_file else None
        touched = False
        for posting in entry.postings:
            number = posting.units.number
            if posting.account.startswith("Income"):
                m["income"] -= number
                op = {"cat": "Дохід", "amount": float(-number), "income": True}
            elif posting.account.startswith("Expenses"):
                m["fixed" if fixed else "variable"] += number
                m["categories"][posting.account.split(":", 1)[1]] += number
                m["weeks"][min((entry.date.day - 1) // 7, 4)] += number
                op = {"cat": posting.account.split(":", 1)[1].replace("-", " "), "amount": float(number), "income": False}
            else:
                continue
            touched = True
            # Журнал: кожна операція доходу чи витрати, нові зверху.
            m["ops"].append({"date": entry.date.strftime("%d.%m"), "day": entry.date.isoformat(),
                             "title": entry.narration or entry.payee or "", "fixed": fixed, "cash": cash, "line": line, **op})
        if touched and (m["last"] is None or entry.date > m["last"]):
            m["last"] = entry.date
        if touched and not in_cash_file and (m.get("last_bank") is None or entry.date > m["last_bank"]):
            m["last_bank"] = entry.date
    return errors, months, accounts


def report():
    people = []
    for key, name, filename in LEDGERS:
        errors, months, accounts = _ledger(ROOT / filename)
        # Місяць без доходів і витрат (лише початковий залишок на 31.07) у звіт не показуємо.
        keys = sorted(k for k, m in months.items() if m["income"] or m["fixed"] or m["variable"])
        out = []
        for year, month in keys:
            m = months[(year, month)]
            spent = m["fixed"] + m["variable"]
            saved = m["income"] - spent
            last = m.get("last_bank") or m["last"]
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
                "ops": sorted(m["ops"], key=lambda o: o["day"], reverse=True),
            })
        people.append({"key": key, "name": name, "ok": not errors, "months": out,
                       # Готівку з телефона поки веде лише Марко (у нього є Assets:Готівка і marko-cash.beancount).
                       "cash": key in CASH_FILES,
                       "accounts": [{"key": a, "name": a.replace("-", " ")} for a in accounts]})
    return {"people": people}
