"""Одна команда перевірки: імпорт обох виписок, bean-check обох журналів і контрольні цифри.

Запуск: uv run python check.py
Код виходу 0 — усе зійшлося; 1 — є помилки обліку або цифри не збігаються з умовою.
"""
import subprocess
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data

ROOT = Path(__file__).parent

# Контрольні змінні витрати з умови завдання (злоті).
LEDGERS = {
    "Оля": ("main.beancount", "import.py", {8: Decimal("2195.21"), 9: Decimal("2582.42")}),
    "Марко": ("marko.beancount", "import_marko.py", {8: Decimal("1855.57"), 9: Decimal("2432.83")}),
}
MONTHS = {8: "серпень", 9: "вересень"}


def month_totals(ledger):
    entries, errors, _ = loader.load_file(str(ROOT / ledger))
    totals = defaultdict(lambda: {"income": Decimal(0), "fixed": Decimal(0), "variable": Decimal(0)})
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        key = (entry.date.year, entry.date.month)
        fixed = "fixed" in (entry.tags or set())
        for posting in entry.postings:
            number = posting.units.number
            if posting.account.startswith("Income"):
                totals[key]["income"] -= number
            elif posting.account.startswith("Expenses"):
                totals[key]["fixed" if fixed else "variable"] += number
    return errors, totals


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ok = True
    for name, (ledger, importer, expected) in LEDGERS.items():
        result = subprocess.run([sys.executable, str(ROOT / importer)], capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            print(f"{name}: імпорт упав\n{result.stdout}{result.stderr}")
            ok = False
            continue
        errors, totals = month_totals(ledger)
        print(f"\n{name} ({ledger})")
        if errors:
            ok = False
            print(f"  bean-check: {len(errors)} помилок")
            for error in errors[:5]:
                print(f"    {error.message}")
        else:
            print("  bean-check: мовчить (облік зійшовся з банком)")
        for month, want in expected.items():
            t = totals[(2026, month)]
            got = t["variable"]
            spent = t["fixed"] + t["variable"]
            rate = (t["income"] - spent) / t["income"] * 100 if t["income"] else Decimal(0)
            mark = "OK" if got == want else f"НЕ ЗБІГАЄТЬСЯ (очікували {want})"
            ok &= got == want
            print(f"  {MONTHS[month]:8}  змінні {got:>9} зл  {mark}   "
                  f"фіксовані {t['fixed']:>8} зл, дохід {t['income']:>8} зл, норма заощаджень {rate:.1f} %")
    print("\nУСЕ ЗБІГАЄТЬСЯ" if ok else "\nЄ РОЗБІЖНОСТІ")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
