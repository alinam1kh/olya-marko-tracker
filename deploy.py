"""Розгортання на PythonAnywhere без браузера: git pull + check.py у консолі, перезапуск сайту, перевірка, що він відповідає.

Токен API лежить лише в локальному .env (PA_API_TOKEN=...), у git він не потрапляє.
Запуск: .venv/Scripts/python.exe deploy.py
"""
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USER, DOMAIN, CONSOLE = "alinam1kh", "alinam1kh.pythonanywhere.com", 48849465
API = f"https://www.pythonanywhere.com/api/v0/user/{USER}"
CMD = "cd ~/olya-marko-tracker && git pull -q && git log --oneline -1 && ~/.virtualenvs/tracker/bin/python check.py | tail -1"


def token():
    env = pathlib.Path(__file__).with_name(".env")
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "PA_API_TOKEN" and v.strip():
            return v.strip()
    sys.exit("Немає PA_API_TOKEN у .env")


def api(path, data=None):
    req = urllib.request.Request(API + path, headers={"Authorization": "Token " + TOKEN},
                                 data=urllib.parse.urlencode(data).encode() if data is not None else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
        return json.loads(body) if body.strip().startswith("{") else body


def status():
    try:
        return urllib.request.urlopen(f"https://{DOMAIN}/login", timeout=30).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


TOKEN = token()

# 1. git pull + перевірка даних у консолі; кінець виводу ловимо за унікальною міткою
mark = f"DEPLOY-DONE-{int(time.time())}"
api(f"/consoles/{CONSOLE}/send_input/", {"input": f"clear; {CMD}; echo {mark}\n"})
out = ""
for _ in range(40):
    time.sleep(2)
    out = api(f"/consoles/{CONSOLE}/get_latest_output/").get("output", "")
    if out.count(mark) >= 2:  # перша — у самій команді, друга — від echo
        break
else:
    sys.exit("Консоль не відповіла вчасно:\n" + out[-800:])
tail = out[out.rfind("check.py | tail -1"):].split("\n", 1)[-1]
print(re.sub(r"\x1b(\[[0-9;?]*[A-Za-z]|[=>])", "", tail.split(mark)[0]).strip())  # без кольорів і службових кодів терміналу
if "ЗБІГАЄТЬСЯ" not in tail:
    sys.exit("check.py не підтвердив дані — сайт не перезапускаю")

# 2. перезапуск сайту
api(f"/webapps/{DOMAIN}/reload/", {})
print("Сайт перезапущено")

# 3. чекаємо, доки сайт відповість (502 одразу після перезапуску — нормально)
for _ in range(8):
    code = status()
    print("HTTP", code)
    if code == 200:
        break
    time.sleep(15)
else:
    sys.exit("Сайт не відповів 200")
