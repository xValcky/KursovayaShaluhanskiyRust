"""Выгрузка карточек (infobox) статей Wikipedia о смартфонах 2021–2026 гг. — для сверки каталога (verified со ссылкой).

Запуск:  python tools/fetch_wikipedia.py [--titles "Samsung Galaxy S24" ...]
Результат: data/sources/wikipedia_phones.json — {title: {url, revid, fields: {ключ: текст}}}.
Текст Wikipedia распространяется по CC BY-SA 4.0; в файле — только поля карточек со ссылкой на статью и номер правки.
Через API MediaWiki с паузами, по 50 статей за запрос (правила Wikimedia для ботов: User-Agent и умеренная частота).
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sources" / "wikipedia_phones.json"
API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "PhonePickerCatalog/0.3 (student course project; catalog verification)"}
YEARS = range(2021, 2027)


def api(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 — сеть: повторить с паузой
            print("  повтор:", type(e).__name__, str(e)[:100], flush=True)
            ra = getattr(e, "headers", None) and e.headers.get("Retry-After")
            time.sleep(int(ra) if ra and ra.isdigit() else 20 * (attempt + 1))
    raise RuntimeError(f"Wikipedia API не ответил: {url[:120]}")


def category_titles() -> list[str]:
    titles = []
    for y in YEARS:
        cont = {}
        while True:
            d = api({"action": "query", "list": "categorymembers", "cmtitle": f"Category:Mobile phones introduced in {y}",
                     "cmlimit": 500, "cmtype": "page", **cont})
            titles += [m["title"] for m in d["query"]["categorymembers"]]
            if "continue" not in d:
                break
            cont = d["continue"]
            time.sleep(0.5)
    return titles


def extract_infobox(text: str) -> dict | None:
    m = re.search(r"\{\{\s*Infobox (?:mobile phone|phone|smartphone)", text, re.I)
    if not m:
        return None
    depth, i = 0, m.start()
    while i < len(text):
        if text.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if text.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                break
            continue
        i += 1
    body = text[m.start() + 2: i - 2]
    fields, key, buf, d2 = {}, None, [], 0
    for part in re.split(r"(\{\{|\}\}|\[\[|\]\]|\|)", body):
        if part in ("{{", "[["):
            d2 += 1
        elif part in ("}}", "]]"):
            d2 -= 1
        if part == "|" and d2 == 0:
            if key is not None:
                fields[key] = "".join(buf).strip()
            key, buf = "", []
            continue
        if key == "" and "=" in part and d2 == 0:
            k, _, rest = part.partition("=")
            key = k.strip().lower()
            buf = [rest]
            continue
        buf.append(part)
    if key:
        fields[key] = "".join(buf).strip()
    return {k: v for k, v in fields.items() if k and v}


def fetch(titles: list[str], out: dict) -> None:
    todo = [t for t in dict.fromkeys(titles) if t not in out]
    for k in range(0, len(todo), 10):
        batch = todo[k:k + 10]
        d = api({"action": "query", "prop": "revisions", "rvprop": "content|ids", "rvslots": "main",
                 "titles": "|".join(batch), "redirects": 1})
        for p in d["query"].get("pages", []):
            if "revisions" not in p:
                continue
            rev = p["revisions"][0]
            box = extract_infobox(rev["slots"]["main"]["content"])
            if box:
                out[p["title"]] = {"url": "https://en.wikipedia.org/wiki/" + p["title"].replace(" ", "_"),
                                   "revid": rev["revid"], "fields": box}
        print(f"  {min(k + 10, len(todo))}/{len(todo)}", flush=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        time.sleep(4.0)


def main():
    out = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    extra = sys.argv[sys.argv.index("--titles") + 1:] if "--titles" in sys.argv else []
    titles = extra or category_titles()
    print(f"статей: {len(titles)}")
    fetch(titles, out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"карточек в {OUT.relative_to(ROOT)}: {len(out)}")


if __name__ == "__main__":
    main()
