"""Перезаписує marko-transactions.beancount з виписки Марка (data/marko/*.csv) і правил з rules.csv.

Запуск: uv run python import_marko.py
Формат банку Марка: шапка з метаданими, роздільник «;», кома в сумах, пробіли в тисячах,
рядки від новіших до старіших. data/ лише читаємо.
Готівку, яку Марко додає з телефона, сюди не пишемо: вона лежить у marko-cash.beancount.
"""
import csv
import importlib.util
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data" / "marko"
OUT_FILE = ROOT / "marko-transactions.beancount"

# Ті самі правила й категоризація, що й в import.py Олі (один rules.csv на двох).
_spec = importlib.util.spec_from_file_location("olya_import", ROOT / "import.py")
_olya = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_olya)
read_rules, categorize, esc, fail = _olya.read_rules, _olya.categorize, _olya.esc, _olya.fail
UNCATEGORIZED = _olya.UNCATEGORIZED

BANK = "Assets:Банк"
EQUITY = "Equity:Початковий-Залишок"
CURRENCY = "PLN"
HEADER = ["Data operacji", "Data księgowania", "Typ operacji", "Opis", "Kwota", "Saldo po operacji"]


def money(text):
    """«-1 234,56» -> Decimal('-1234.56')."""
    return Decimal(text.replace(" ", "").replace(" ", "").replace(",", "."))


def read_statement(path):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    meta, start = {}, None
    for i, line in enumerate(lines):
        cells = line.split(";")
        if cells == HEADER:
            start = i + 1
            break
        if len(cells) == 2:
            meta[cells[0].strip()] = cells[1].strip()
    if start is None:
        fail(f"{path.name}: не знайдено рядок заголовків {';'.join(HEADER)}")
    if meta.get("Waluta") != CURRENCY:
        fail(f"{path.name}: валюта {meta.get('Waluta')}, очікували лише {CURRENCY}")
    if "Saldo początkowe" not in meta:
        fail(f"{path.name}: у шапці немає «Saldo początkowe»")
    rows = []
    for cells in csv.reader(lines[start:], delimiter=";"):
        if not cells:
            continue
        rows.append({
            "date": datetime.strptime(cells[0], "%d.%m.%Y").date(),
            "description": cells[3].strip(),
            "amount": money(cells[4]),
            "balance": money(cells[5]),
        })
    rows.reverse()  # банк дає від новіших до старіших
    return money(meta["Saldo początkowe"]), rows


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    files = sorted(DATA_DIR.glob("*.csv"))
    if len(files) != 1:
        fail(f"у {DATA_DIR.relative_to(ROOT)} має бути рівно одна виписка, знайдено {len(files)}")
    rules = read_rules()
    opening, rows = read_statement(files[0])
    if not rows:
        fail("у виписці немає жодної операції")
    opening_date = rows[0]["date"] - timedelta(days=1)

    running = opening
    for r in rows:
        running += r["amount"]
        if running != r["balance"]:
            fail(f"{r['date']} {r['description']}: залишок за банком {r['balance']}, за розрахунком {running}")

    lines = [
        "; Файл генерує import_marko.py з data/marko/*.csv і rules.csv. Руками не правити: він перезаписується.",
        "",
        f'{opening_date} * "Початковий залишок"',
        f"  {BANK}  {opening} {CURRENCY}",
        f"  {EQUITY}",
        "",
    ]
    uncategorized = 0
    for r in rows:
        account, fixed = categorize(r["description"], r["amount"], rules)
        if account == UNCATEGORIZED:
            uncategorized += 1
        tag = " #fixed" if fixed else ""
        lines += [
            f'{r["date"]} * "{esc(r["description"])}"{tag}',
            f"  {BANK}  {r['amount']} {CURRENCY}",
            f"  {account}  {-r['amount']} {CURRENCY}",
            "",
        ]

    last_per_month = {}
    for r in rows:
        last_per_month[(r["date"].year, r["date"].month)] = r
    months = list(last_per_month)
    lines.append("; Звірка з банком")
    for i, month in enumerate(months):
        r = last_per_month[month]
        if i < len(months) - 1:
            year, mon = month
            check_date = date(year + (mon == 12), mon % 12 + 1, 1)
        else:
            check_date = r["date"] + timedelta(days=1)
        lines.append(f"{check_date} balance {BANK}  {r['balance']} {CURRENCY}")

    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Марко: операцій {len(rows)}, без категорії: {uncategorized}")
    print(f"Початковий залишок: {opening} {CURRENCY} на {opening_date}")
    print(f"Записано {OUT_FILE.name}")


if __name__ == "__main__":
    main()
