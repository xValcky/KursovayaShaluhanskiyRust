"""Поиск свободных фото моделей на Wikimedia Commons (раздел 10.1 ТЗ).

Запуск:  python tools/fetch_commons_photos.py [--only slug1,slug2] [--refresh]
Результат: data/sources/commons_photos.json — {slug: [фото…]}; tools/import_catalog_data.py добавляет их в файлы моделей.

Правила отбора:
- лицензия CC0 / CC BY / CC BY-SA / Public domain, автор указан;
- ширина оригинала ≥ 600 px, формат JPEG/PNG/WebP;
- в имени файла или его описании есть ВСЕ слова названия модели и нет «чужих» слов вариантов
  (Pro, Max, Ultra, Plus, Lite, FE, mini, Fold, Flip, 5G…), чтобы фото Galaxy S24 Ultra не попало к Galaxy S24;
- не больше 6 фото на модель; ссылка — превью 960 px с upload.wikimedia.org (отдаёт 200 и image/*).
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "data" / "catalog"
OUT = ROOT / "data" / "sources" / "commons_photos.json"
API = "https://commons.wikimedia.org/w/api.php"
PAUSE = 2.5  # секунд между запросами: правила Wikimedia для ботов — последовательно и без спешки
UA = {"User-Agent": "PhonePickerCatalog/0.3 (student course project; free-licensed phone photos)"}
FREE = re.compile(r"^(cc0|cc[- ]by(-sa)?( [\d.]+)?( \w+)?|public domain|pd\b.*)", re.I)
VARIANT = {"pro", "max", "ultra", "plus", "lite", "fe", "mini", "edge", "neo", "fold", "flip", "prime", "5g", "4g",
           "x", "s", "t", "r", "a", "e", "air", "power", "turbo", "gt", "se", "premier", "fusion", "stylus", "trifold",
           "duo", "xl", "special", "explorer", "racing", "speed"}
DISTINCT = {"iphone", "galaxy", "pixel", "redmi", "xperia", "rog", "zenfone", "razr", "moto", "poco", "reno", "find",
            "magic", "mate", "pura", "nova", "realme", "oneplus", "nord", "redmagic", "nubia", "fairphone", "nothing",
            "cmf", "vivo", "iqoo", "honor", "oppo", "xiaomi", "huawei", "tecno", "infinix", "spark", "camon", "pova",
            "phantom", "blackview", "ulefone", "armor", "meizu", "doogee", "oukitel", "motorola", "edge", "legion"}


def norm(text: str) -> list[str]:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)).lower().replace("+", " plus ")
    text = re.sub(r"(\d)(gb|tb)\b", r"\1 \2", text)
    return re.findall(r"[a-z]+|\d+[a-z]*", re.sub(r"([a-z])(\d)", r"\1 \2", text))


def tokens(name: str) -> list[str]:
    return [t for t in norm(name) if t not in ("the",)]


def matches(title_desc: str, need: list[str], brand: str) -> bool:
    have = norm(title_desc)
    hs = set(have)
    if not all(t in hs for t in need):
        return False
    if not (set(need) & DISTINCT) and brand.lower() not in hs:
        return False
    extra = (hs & VARIANT) - set(need)
    # «S» и «T» как отдельные слова часто встречаются в описаниях — считаем вариантом, только если стоят сразу после номера
    for v in list(extra):
        if v in ("s", "t", "r", "a", "e", "x"):
            pos = [i for i, t in enumerate(have) if t == v]
            if not any(i > 0 and have[i - 1].isdigit() for i in pos):
                extra.discard(v)
    return not extra


def other_models(title: str, need: list[str]) -> bool:
    """В названии файла есть номер другой модели («…S24 Ultra, iPhone 13 Pro…»)."""
    t = re.sub(r"\(\s*\d+\s*\)", " ", title.rsplit(".", 1)[0])  # «(5)» — номер снимка
    toks = [x for x in norm(t) if re.fullmatch(r"\d+[a-z]*", x)]
    toks = [x for x in toks if not re.fullmatch(r"\d{3,}|20\d\d", x)]  # номера файлов и годы
    return any(x not in need for x in toks)


def kind_of(text: str, folding: bool) -> str:
    low = text.lower()
    for key, kind in (("unfold", "open"), ("open", "open"), ("back", "back"), ("rear", "back"), ("camera", "back"),
                      ("front", "front"), ("screen", "front"), ("display", "front"), ("side", "side")):
        if key in low and (kind != "open" or folding):
            return kind
    return "lifestyle"


def api(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            ra = getattr(e, "headers", None) and e.headers.get("Retry-After")
            wait = max(int(ra) if ra and ra.isdigit() else 0, 30 * (attempt + 1))
            print(f"    Commons просит подождать: {type(e).__name__} {getattr(e, 'code', '')}, пауза {wait} с", flush=True)
            time.sleep(wait)
    return {}


def search(query: str, need: list[str], brand: str) -> list[dict]:
    """Сначала лёгкий поиск по именам файлов, потом подробности (лицензия, автор) только по подходящим."""
    d = api({"action": "query", "list": "search", "srnamespace": 6, "srlimit": 40, "srprop": "",
             "srsearch": f'"{query}" filetype:bitmap'})
    titles = [x["title"] for x in d.get("query", {}).get("search", [])]
    titles = [t for t in titles if matches(t.removeprefix("File:"), need, brand)
              and not other_models(t.removeprefix("File:"), need)][:12]
    if not titles:
        return []
    time.sleep(PAUSE)
    d = api({"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
             "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 960})
    return d.get("query", {}).get("pages", [])


def pick(pages: list[dict], need: list[str], brand: str, folding: bool, by_name: bool = True) -> list[dict]:
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = (meta.get("LicenseShortName", {}).get("value") or "").strip()
        author = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", meta.get("Artist", {}).get("value") or ""))).strip()
        desc = meta.get("ImageDescription", {}).get("value") or ""
        title = p["title"].removeprefix("File:")
        if not FREE.match(lic) or not author or info.get("width", 0) < 600:
            continue
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        if by_name and not (matches(title, need, brand) or matches(title + " " + desc[:300], need, brand)
                            and matches(title, need[:1], brand)):
            continue
        if other_models(title, need):  # групповое фото с другими моделями
            continue
        url = info.get("thumburl") or info.get("url")
        if not url or not url.startswith(("https://upload.wikimedia.org/", "https://thumb.wikimedia.org/")):
            continue
        w, h = info.get("thumbwidth") or info["width"], info.get("thumbheight") or info["height"]
        out.append({"url": url, "kind": kind_of(title + " " + desc[:200], folding), "color_ru": None,
                    "author": author[:120], "license": lic, "source_page": info.get("descriptionurl"),
                    "width": w, "height": h, "origin": "wikimedia"})
    return out


def by_categories(items: list[tuple[str, dict, str]], cache: dict) -> int:
    """Категории Commons с именем модели («Category:Samsung Galaxy S24 Ultra»): проверка по 50 за запрос,
    файлы категории — только для существующих. Файл в категории модели — сама модель; групповые фото отсеиваются."""
    cand: dict[str, str] = {}
    for slug, d, _ in items:
        for n in dict.fromkeys([d["full_name"], d["name"], d["full_name"].replace("Apple iPhone", "IPhone"),
                                d["name"].replace("iPhone", "IPhone")]):
            cand.setdefault("Category:" + n[:1].upper() + n[1:], slug)
    titles, found = list(cand), {}
    for k in range(0, len(titles), 50):
        batch = titles[k:k + 50]
        d = api({"action": "query", "prop": "categoryinfo", "titles": "|".join(batch)})
        q = d.get("query", {})
        norm_map = {x["to"]: x["from"] for x in q.get("normalized", [])}
        for pg in q.get("pages", []):
            files = (pg.get("categoryinfo") or {}).get("files", 0)
            if not pg.get("missing") and files:
                found[pg["title"]] = cand.get(norm_map.get(pg["title"], pg["title"]), cand.get(pg["title"]))
        time.sleep(PAUSE)
    print(f"  категорий с файлами: {len(found)}", flush=True)
    meta = {slug: (d, brand) for slug, d, brand in items}
    added = 0
    for title, slug in found.items():
        if not slug:
            continue
        d, brand = meta[slug]
        need = tokens(d["name"])
        r = api({"action": "query", "list": "categorymembers", "cmtitle": title, "cmtype": "file", "cmlimit": 40})
        files = [m["title"] for m in r.get("query", {}).get("categorymembers", [])
                 if not other_models(m["title"].removeprefix("File:"), need)][:12]
        time.sleep(PAUSE)
        if not files:
            continue
        r = api({"action": "query", "titles": "|".join(files), "prop": "imageinfo",
                 "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 960})
        time.sleep(PAUSE)
        folding = d["form_factor"] in ("book_fold", "flip", "trifold")
        have = {ph["url"] for ph in cache.get(slug, [])}
        new = [ph for ph in pick(r.get("query", {}).get("pages", []), need, brand, folding, by_name=False)
               if ph["url"] not in have]
        order = {"front": 0, "back": 1, "side": 2, "angle": 3, "open": 4, "color": 5, "lifestyle": 6}
        cache[slug] = sorted(cache.get(slug, []) + new, key=lambda p: order.get(p["kind"], 9))[:6]
        added += len(new)
    return added


def main():
    cache = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    refresh = "--refresh" in sys.argv
    brands = {b["slug"]: b["name"] for b in json.loads((CAT / "brands.json").read_text(encoding="utf-8"))}
    files = sorted((CAT / "phones").rglob("*.json"))
    refreshed = set(cache.pop("__refreshed__", [])) if refresh and not only else set()
    if "--categories" in sys.argv:  # быстрый проход по категориям Commons
        items = []
        for path in files:
            d = json.loads(path.read_text(encoding="utf-8"))
            if not only or d["slug"] in only:
                items.append((d["slug"], d, brands.get(d["brand"], d["brand"])))
        added = by_categories(items, cache)
        OUT.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"по категориям добавлено фото: {added}; моделей с фото {sum(1 for v in cache.values() if v)}")
        return
    done = 0
    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        slug = d["slug"]
        if only and slug not in only or (slug in cache and not refresh) or (refresh and slug in refreshed):
            continue
        brand = brands.get(d["brand"], d["brand"])
        need = tokens(d["name"])
        folding = d["form_factor"] in ("book_fold", "flip", "trifold")
        found: dict[str, dict] = {}
        queries = [d["full_name"]] + ([d["name"]] if d["name"] != d["full_name"] else []) + d.get("aliases", [])[:1]
        for q in queries:
            for ph in pick(search(q, need, brand), need, brand, folding):
                found.setdefault(ph["url"], ph)
            time.sleep(PAUSE)
            if found:  # следующий запрос (имя без бренда, псевдоним) — только если по полному имени ничего нет
                break
        order = {"front": 0, "back": 1, "side": 2, "angle": 3, "open": 4, "color": 5, "lifestyle": 6}
        cache[slug] = sorted(found.values(), key=lambda p: order.get(p["kind"], 9))[:6]
        done += 1
        refreshed.add(slug)
        if done % 10 == 0:
            OUT.write_text(json.dumps({**cache, "__refreshed__": sorted(refreshed)}, ensure_ascii=False, indent=1,
                                      sort_keys=True) + "\n", encoding="utf-8")
            print(f"  {done} моделей, с фото: {sum(1 for v in cache.values() if v)}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    cache.pop("__refreshed__", None)
    print(f"готово: {len(cache)} моделей, с фото {sum(1 for v in cache.values() if v)}, "
          f"фото всего {sum(len(v) for v in cache.values())}")


if __name__ == "__main__":
    main()
