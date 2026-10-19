"""Перезаписує transactions.beancount з усіх CSV у data/ і правил з rules.csv.

Запуск: uv run python import.py
data/ лише читаємо. Рядки беремо в порядку банку, не сортуємо.
"""
import csv
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RULES_FILE = ROOT / "rules.csv"
OUT_FILE = ROOT / "transactions.beancount"

BANK = "Assets:Банк"
EQUITY = "Equity:Початковий-Залишок"
INCOME_OTHER = "Income:Інше"
UNCATEGORIZED = "Expenses:Без-Категорії"
CURRENCY = "PLN"


def fail(message):
    sys.exit(f"Помилка: {message}")


def read_rules():
    with open(RULES_FILE, encoding="utf-8-sig", newline="") as f:
        return [
            (row["шаблон"].strip().lower(), row["рахунок"].strip(), row["фіксована"].strip() == "так")
            for row in csv.DictReader(f)
        ]


def read_rows():
    """Усі рядки з усіх CSV у порядку банку; однакові рядки рахуємо один раз."""
    files = sorted(DATA_DIR.glob("*.csv"))
    if not files:
        fail("у data/ немає жодного CSV")
    rows, seen, duplicates = [], set(), 0
    for path in files:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row["State"] != "COMPLETED":
                    fail(f"{path.name}: операція зі статусом {row['State']} ({row['Description']}), такого ще не вміємо")
                if row["Currency"] != CURRENCY:
                    fail(f"{path.name}: валюта {row['Currency']}, очікували лише {CURRENCY}")
                if Decimal(row["Fee"]) != 0:
                    fail(f"{path.name}: ненульова комісія ({row['Description']}), такого ще не вміємо")
                key = (row["Completed Date"], row["Description"], row["Amount"], row["Balance"])
                if key in seen:
                    duplicates += 1
                    continue
                seen.add(key)
                rows.append({
                    "date": datetime.strptime(row["Completed Date"], "%Y-%m-%d %H:%M:%S").date(),
                    "description": row["Description"],
                    "amount": Decimal(row["Amount"]),
                    "balance": Decimal(row["Balance"]),
                })
    return rows, duplicates


def categorize(description, amount, rules):
    """Перше правило, що підійшло. Без правила: дохід у Income:Інше, витрата в Без-Категорії."""
    text = description.lower()
    for pattern, account, fixed in rules:
        if pattern in text:
            return account, fixed
    return (INCOME_OTHER if amount > 0 else UNCATEGORIZED), False


def esc(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def main():
    # На Windows консоль часто не UTF-8: без цього українські повідомлення читаються як кракозябри.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    rules = read_rules()
    rows, duplicates = read_rows()
    if not rows:
        fail("у виписках немає жодної операції")

    # Початковий залишок = залишок після першого рядка мінус його сума.
    opening = rows[0]["balance"] - rows[0]["amount"]
    opening_date = rows[0]["date"] - timedelta(days=1)

    # Ланцюжок залишків у порядку банку має зводитися.
    running = opening
    for r in rows:
        running += r["amount"]
        if running != r["balance"]:
            fail(f"{r['date']} {r['description']}: залишок за банком {r['balance']}, за розрахунком {running}")

    lines = [
        "; Файл генерує import.py з data/*.csv і rules.csv. Руками не правити: він перезаписується.",
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

    # balance: для кожного місяця залишок з його останнього рядка (порядок банку).
    # Дата: перше число наступного місяця, для останнього місяця день після останньої операції.
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
    print(f"Операцій: {len(rows)}, пропущено дублів: {duplicates}, без категорії: {uncategorized}")
    print(f"Початковий залишок: {opening} {CURRENCY} на {opening_date}")
    print(f"Записано {OUT_FILE.name}")


if __name__ == "__main__":
    main()
