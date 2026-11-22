"""Выгрузка постов публичных Telegram-каналов (веб-превью t.me/s/...) с ценами на смартфоны.

Запуск:  python tools/scrape_telegram.py [--since 2026-07-18]
Результат: data/sources/<канал>.json — список {date, post, text, name, price}
  name  — «сырое» название товара до цены (как в посте),
  price — цена в рублях.
Сопоставление названий с моделями делает tools/merge_sources.py.
"""
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sources"
CHANNELS = ["ishopper", "polzaSKIDKI"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PhonePicker-coursework"}


class _Posts(HTMLParser):
    """Достаёт из HTML страницы t.me/s/... посты: id, дату и текст (с переводами строк)."""

    def __init__(self):
        super().__init__()
        self.posts, self._cur, self._depth, self._in_text = [], None, 0, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "div" and "tgme_widget_message " in cls + " " and "data-post" in a:
            self._cur = {"post": int(a["data-post"].split("/")[1]), "date": "", "text": ""}
            self.posts.append(self._cur)
        elif tag == "div" and "tgme_widget_message_text" in cls and self._cur is not None:
            self._in_text, self._depth = True, 1
        elif self._in_text and tag == "div":
            self._depth += 1
        elif tag == "br" and self._in_text:
            self._cur["text"] += "\n"
        elif tag == "time" and self._cur is not None and a.get("datetime"):
            self._cur["date"] = a["datetime"][:10]

    def handle_endtag(self, tag):
        if self._in_text and tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._in_text = False

    def handle_data(self, data):
        if self._in_text:
            self._cur["text"] += data


def fetch_channel(channel: str, since: str) -> list[dict]:
    posts, before = {}, None
    while True:
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        for attempt in range(6):  # t.me иногда долго отвечает — повторить с паузой
            try:
                r = requests.get(url, headers=HEADERS, timeout=60)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                print(f"  повтор {attempt + 1}: {type(e).__name__}", flush=True)
                time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError(f"t.me не ответил: {url}")
        p = _Posts()
        p.feed(r.text)
        page = [x for x in p.posts if x["date"]]
        if not page:
            break
        for x in page:
            posts[x["post"]] = x
        oldest = min(page, key=lambda x: x["post"])
        if before is not None and oldest["post"] >= before:
            break
        before = oldest["post"]
        print(f"  {channel}: до поста {before}, {oldest['date']}, всего {len(posts)}", flush=True)
        if oldest["date"] < since:
            break
        time.sleep(0.4)
    return [x for x in sorted(posts.values(), key=lambda x: x["post"]) if x["date"] >= since]


PRICE_RE = re.compile(r"(\d{1,3}(?:[   ]\d{3})+|\d+(?:[.,]\d+)?\s*[KkКк]|\d{4,6})\s*(?:RUB|руб\.?|р\.?|₽)", re.I)
NOT_PHONE = re.compile(r"(watch|buds|наушник|часы|band |браслет|планшет|\bpad\b|tab\b|пылес|робосос|колонк|powerbank|"
                       r"power bank|роутер|телевиз|чехол|стекл|монитор|ноутбук|book|кабел|airpods|\btv\b|"
                       r"видеорегистр|самокат|кроссов|зарядн|адаптер|бритв|щетк|фен\b|ps5|приставк|джойстик|"
                       r"клавиатур|мышь|кофевар|утюг|пылик|термос|рюкзак|мини-пк|gpu|видеокарт)", re.I)


MODEL_LINE = re.compile(r"(смартфон|iphone|galaxy|xiaomi|redmi|poco|realme|oneplus|nothing|vivo|iqoo|oppo|honor|moto|"
                        r"pixel|tecno|infinix|nubia|redmagic|lenovo|zte|meizu|huawei|xperia|asus|rog|cmf)", re.I)


def extract_items(post: dict) -> list[dict]:
    """Строки вида «🛒 Модель от 30.7K RUB» или «▪️ Модель 8 256 25 467 ₽»."""
    items = []
    prev = ""  # строка перед ценой: в постах вида «Смартфон Vivo X500, 12/256ГБ» + «ЦЕНА 58 400 ₽» модель стоит выше
    for chunk in re.split(r"🛒|▪️|▪|•|\n", post["text"]):
        chunk = " ".join(chunk.split())
        m = PRICE_RE.search(chunk)
        if not m or m.start() < 3:
            if MODEL_LINE.search(chunk):
                prev = chunk[:140]
            continue
        name = chunk[:m.start()].strip(" -—:·")
        if re.fullmatch(r"(?i)(цена|цена со скидкой|от|price|стоимость)[:\s]*", name) and prev:
            name = f"{prev} {name}"
        if NOT_PHONE.search(name) or len(name) > 140:
            continue
        raw = m.group(1).replace(" ", " ").replace(" ", " ")
        if re.search(r"[KkКк]", raw):
            price = float(re.sub(r"[^\d.,]", "", raw).replace(",", ".")) * 1000
        else:
            price = float(re.sub(r"\D", "", raw))
        if not 3000 <= price <= 400000:
            continue
        items.append({"date": post["date"], "post": post["post"], "name": name, "price": int(round(price)),
                      "context": chunk[m.end():m.end() + 300]})
    return items


def main():
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2026-07-18"
    OUT.mkdir(parents=True, exist_ok=True)
    for ch in CHANNELS:
        posts = fetch_channel(ch, since)
        items = [it for p in posts for it in extract_items(p)]
        (OUT / f"{ch.lower()}.json").write_text(json.dumps(items, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"{ch}: постов {len(posts)}, позиций с ценой {len(items)}")


if __name__ == "__main__":
    main()
