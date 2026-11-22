"""Цены конфигураций из публичных Telegram-каналов проекта (data/sources/*.json, tools/scrape_telegram.py).

Запуск:  python tools/match_telegram_prices.py
Пишет:   data/sources/telegram_prices.json — {slug: {"ram/rom": {price, date, n, source, post}}}
         tools/import_catalog_data.py ставит эти цены конфигурациям (_q verified, ссылка на последний пост).

Сопоставление: самое длинное название модели из каталога, целиком входящее в строку поста
(как в tools/merge_sources.py), память — «12/256», «12 256», «256GB», «1TB». Без памяти в посте цена идёт
только модели с одной конфигурацией. Цена — медиана за последние 30 дней (иначе за 60), выбросы
дальше 0,6–1,7 медианы модели отбрасываются (другая память, опечатки, аксессуары).
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "data" / "catalog"
SRC = ROOT / "data" / "sources"
OUT = SRC / "telegram_prices.json"
CHANNELS = {"ishopper": ("telegram_ishopper", "t.me/ishopper"), "polzaskidki": ("telegram_polzaskidki", "t.me/polzaSKIDKI")}
SKIP = re.compile(r"harry\s*potter|чехол|стекло|watch|buds|pad\b|\btab\b|б/у|восстанов|refurb|витрин", re.I)


def key(text: str) -> str:
    t = text.lower().replace("ё", "е").replace("+", "plus")
    t = re.sub(r"\b(5g|4g|global|глобальн\w*|ru|рст|cn|китай\w*|смартфон|телефон)\b", " ", t)
    return re.sub(r"[^a-z0-9а-я]", "", t)


def memory(text: str):
    t = text.lower().replace("тб", "tb").replace("гб", "gb")
    m = re.search(r"\b(\d{1,2})\s*[/+ ]\s*(\d{2,4}|1|2)\s*(tb|gb)?\b", t)
    if m:
        ram, rom = int(m.group(1)), int(m.group(2))
        rom = rom * 1024 if (m.group(3) == "tb" or rom in (1, 2)) else rom
        if ram in (2, 3, 4, 6, 8, 10, 12, 16, 18, 20, 24) and rom in (32, 64, 128, 256, 512, 1024, 2048):
            return ram, rom
    m = re.search(r"\b(\d{2,4}|1|2)\s*(gb|tb)\b", t)
    if m:
        rom = int(m.group(1)) * (1024 if m.group(2) == "tb" else 1)
        if rom in (64, 128, 256, 512, 1024, 2048):
            return None, rom
    return None


def main():
    models, base_of = [], {}
    brands = {b["slug"]: b["name"] for b in json.loads((CAT / "brands.json").read_text(encoding="utf-8"))}
    for path in sorted((CAT / "phones").rglob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        names = {d["full_name"], d["name"], f"{brands.get(d['brand'], '')} {d['name']}", *d.get("aliases", [])}
        for n in names:
            k = key(n)
            if len(k) >= 5 and not k.isdigit():
                models.append((k, d["slug"], [(v["ram_gb"], v["rom_gb"]) for v in d["variants"]]))
        base_of[d["slug"]] = next((v["ram_gb"], v["rom_gb"]) for v in d["variants"] if v["is_base"])
    obs = defaultdict(list)
    unmatched = Counter()
    for ch, (src_id, _) in CHANNELS.items():
        path = SRC / f"{ch}.json"
        if not path.exists():
            continue
        for it in json.loads(path.read_text(encoding="utf-8")):
            raw = f"{it['name']} {it.get('context', '')[:40]}"
            if SKIP.search(raw):
                continue
            k = key(it["name"])
            best = None
            for mk, slug, vars_ in models:
                if mk in k and (best is None or len(mk) > len(best[0])):
                    best = (mk, slug, vars_)
            if best is None:
                unmatched[" ".join(it["name"].split()[:4])] += 1
                continue
            _, slug, vars_ = best
            mem = memory(raw)
            if mem is None:
                # без памяти в посте: «от N ₽» — цена самой дешёвой (базовой) конфигурации; иначе только у единственной
                if len(vars_) == 1:
                    pair = vars_[0]
                elif re.search(r"(^|\s)от(\s|$)|от\s*\d", raw.lower()):
                    pair = base_of[slug]
                else:
                    continue
            else:
                ram, rom = mem
                cand = [v for v in vars_ if v[1] == rom and (ram is None or v[0] == ram)]
                if len(cand) != 1:
                    continue
                pair = cand[0]
            obs[(slug, pair)].append((it["date"], it["price"], ch, it["post"]))
    last_day = max((o[0] for v in obs.values() for o in v), default=str(date.today()))
    d30 = str(date.fromisoformat(last_day) - timedelta(days=30))
    d60 = str(date.fromisoformat(last_day) - timedelta(days=60))
    by_model = defaultdict(list)
    for (slug, pair), xs in obs.items():
        by_model[slug] += [p for _, p, _, _ in xs]
    out: dict[str, dict] = defaultdict(dict)
    history = []
    for (slug, pair), xs in obs.items():
        vals = sorted(p for _, p, _, _ in xs)
        med = vals[len(vals) // 2]
        xs = [x for x in xs if 0.6 * med <= x[1] <= 1.7 * med]
        fresh = [x for x in xs if x[0] >= d30] or [x for x in xs if x[0] >= d60]
        if not fresh:
            continue
        v = sorted(p for _, p, _, _ in fresh)
        last = max(fresh)
        ch = Counter(c for *_, c, _ in fresh).most_common(1)[0][0]
        src_id, base = CHANNELS[last[2]]
        out[slug][f"{pair[0]}/{pair[1]}"] = {"price": int(v[len(v) // 2]), "date": last[0], "n": len(fresh),
                                             "source": CHANNELS[ch][0], "post": f"{src_id}:{base}/{last[3]}"}
        history += [(slug, pair[0], pair[1], dt, CHANNELS[c][0], p) for dt, p, c, _ in xs]
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (SRC / "telegram_history.json").write_text(json.dumps(sorted(set(history))), encoding="utf-8")
    print(f"цены из Telegram: {sum(len(v) for v in out.values())} конфигураций у {len(out)} моделей "
          f"(окно {d30}…{last_day}); точек истории {len(set(history))}")
    print("частые несопоставленные:", ", ".join(f"{n} ×{c}" for n, c in unmatched.most_common(25)))


if __name__ == "__main__":
    main()
