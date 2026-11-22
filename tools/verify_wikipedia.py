"""Сверка каталога с карточками Wikipedia (data/sources/wikipedia_phones.json, см. tools/fetch_wikipedia.py).

Запуск:  python tools/verify_wikipedia.py
Пишет:   data/sources/wikipedia_match.json — {slug: {title, url, revid, wiki: {поле: значение}, agree: [...], differ: {...}}}
         и печатает расхождения. tools/import_catalog_data.py берёт отсюда значения со ссылкой на статью
         (батарея, вес, экран, частота, зарядка, дата выхода, камеры) и ставит у блока _q: "verified".

Сопоставление строгое: название статьи (или подпись внутри общей статьи «'''Galaxy S24+:'''») должно совпасть
с названием модели без пробелов и регистра — «Galaxy Z Flip 8» = «Galaxy Z Flip8», но «Galaxy S24» ≠ «Galaxy S24 FE».
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "data" / "catalog"
WIKI = ROOT / "data" / "sources" / "wikipedia_phones.json"
OUT = ROOT / "data" / "sources" / "wikipedia_match.json"


LABEL = re.compile("'''([^']{2,60}?):?'''")


def compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower().replace("+", "plus"))


def clean(v: str) -> str:
    """Вики-разметка → простой текст (переводы строк сохраняются)."""
    v = re.sub(r"<!--.*?-->", "", v, flags=re.S)
    v = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    v = re.sub(r"<br\s*/?>", "\n", v)
    v = re.sub(r"\{\{\s*(?:convert|cvt)\s*\|([^|}]+)\|([^|}]+)[^}]*\}\}", r"\1 \2", v, flags=re.I)
    v = re.sub(r"\{\{\s*val\s*\|\s*([\d.,]+)\s*\|\s*u\s*=\s*([^|}]+)[^}]*\}\}", r"\1 \2", v, flags=re.I)
    v = re.sub(r"\{\{\s*(?:nbsp|nowrap)\s*\}\}", " ", v, flags=re.I)
    v = re.sub(r"\{\{\s*nowrap\s*\|([^}]*)\}\}", r"\1", v, flags=re.I)
    v = re.sub(r"\{\{\s*(?:start date|release date|dts)[^|}]*\|(\d{4})\|(\d{1,2})\|(\d{1,2})[^}]*\}\}",
               lambda m: f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}", v, flags=re.I)
    v = re.sub(r"\{\{\s*(?:start date|release date|dts)[^|}]*\|(\d{4})\|(\d{1,2})[^}]*\}\}",
               lambda m: f"{m[1]}-{int(m[2]):02d}", v, flags=re.I)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"\{\{\s*(?:ubl|unbulleted list|plainlist|flatlist|hlist)\s*\|", "\n", v, flags=re.I)
    v = re.sub(r"\{\{[^{}]*\}\}", " ", v)
    v = v.replace("{{", " ").replace("}}", " ").replace("|", "\n")
    v = html.unescape(re.sub(r"<[^>]+>", " ", v)).replace(" ", " ")
    return re.sub(r"[ \t]+", " ", v)


def segment(text: str, names: set[str], multi: bool) -> str | None:
    """Из общей статьи берёт часть, подписанную именем модели: «'''Galaxy S24+:''' 4900 mAh».

    names — варианты названия модели без пробелов (полное, без бренда, короткое «15ultra»);
    multi — статья описывает несколько моделей: тогда поле без подписи нашей модели не используется.
    """
    labels = list(re.finditer(r"'''([^']{1,60}?):?'''\s*:?", text))
    if not labels:
        return None if multi else text
    best = None
    for i, m in enumerate(labels):
        lab = compact(m.group(1))
        end = labels[i + 1].start() if i + 1 < len(labels) else len(text)
        exact = lab in names
        tail = any(n.endswith(lab) for n in names) and len(lab) >= 4 and lab not in ("pro", "ultra", "plus", "max")
        if exact or tail:
            if best is None or exact:
                best = text[m.end():end]
    # подписи есть, но нашей модели среди них нет — значения относятся к другим моделям
    return best


def first(pattern: str, text: str | None, cast=float):
    if not text:
        return None
    m = re.search(pattern, text, re.I)
    return cast(m.group(1).replace(",", "")) if m else None


CAM_ROLE = (("periscope", "periscope"), ("telephoto", "tele"), ("tele", "tele"), ("ultrawide", "ultrawide"),
            ("ultra-wide", "ultrawide"), ("ultra wide", "ultrawide"), ("macro", "macro"), ("depth", "depth"),
            ("monochrome", "mono"), ("wide", "main"))


def cameras(text: str | None, front: bool = False) -> list[dict]:
    out = []
    if not text:
        return out
    for line in text.split("\n"):
        mp = first(r"([\d.]+)\s*MP", line)
        if not mp:
            continue
        low = line.lower()
        role = "front" if front else next((r for k, r in CAM_ROLE if k in low), "main" if not out else None)
        if role is None:
            continue
        out.append({"role": role, "mp": mp, "aperture": first(r"f/\s*([\d.]+)", line),
                    "focal_mm": first(r"(\d+(?:\.\d+)?)\s*mm", line), "ois": "ois" in low,
                    "format": (re.search(r"(1/[\d.]+|1\.0)\s*[\"″”']{1,2}", line) or [None, None])[1],
                    "af": "dual_pixel" if "dual pixel" in low else "pdaf" if "pdaf" in low else
                    "laser" if "laser" in low else None})
    return out


def parse(fields: dict, names: set[str]) -> dict:
    multi = len([x for x in re.split(r"<br\s*/?>|\n", fields.get("name", "")) if x.strip()]) > 1
    f = {k: clean(v) for k, v in fields.items()}  # clean() сохраняет жирные подписи '''…''' для segment()
    get = lambda k: segment(f[k], names, multi) if k in f else None  # noqa: E731
    disp, ext = get("display"), get("external_display") or get("second_display") or get("secondary_display")
    if disp is None and "display" in f:  # складные: «'''Cover:''' … '''Folding:''' …»
        disp = segment(f["display"], {"folding", "inner", "main", "maindisplay", "innerdisplay"}, True)
        ext = ext or segment(f["display"], {"cover", "outer", "external", "coverdisplay", "outerdisplay"}, True)

    def inches(text):
        v = first(r"([\d.]+)\s*(?:in\b|inch|″|\")", text)
        if v is None:
            mm = first(r"([\d.]+)\s*mm", text)
            v = round(mm / 25.4, 2) if mm and 90 <= mm <= 330 else None
        return v
    rel = get("first_released") or get("released") or get("release_date") or get("availability")
    return {
        "battery_mah": first(r"([\d,]{4,6})\s*mAh\s*\((?:global|international|europe|eu)", get("battery"), int)
                       or first(r"([\d,]{4,6})\s*mA", get("battery"), int),
        "weight_g": first(r"([\d.]+)\s*g\b", get("weight")),
        "screen_in": inches(disp),
        "cover_screen_in": inches(ext),
        "refresh_hz": first(r"(\d{2,3})\s*Hz", disp, int),
        "charge_w": first(r"([\d.]+)\s*W", get("charging") or get("battery")),
        "soc": (get("soc") or "").strip().split("\n")[0].strip() or None,
        "release_date": (re.search(r"(20\d\d-\d\d(?:-\d\d)?)", rel or "") or [None, None])[1],
        "rear": cameras(get("rear_camera")),
        "front": cameras(get("front_camera"), front=True),
        "ram": sorted({int(x) for x in re.findall(r"(\d+)\s*GB", get("memory") or "")}),
    }


def close(a, b, tol):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


def main():
    wiki = json.loads(WIKI.read_text(encoding="utf-8"))
    index: dict[str, str] = {}
    for title in wiki:
        index.setdefault(compact(title), title)
        for part in re.split(r"\s*/\s*|\s+and\s+", title):  # «Nothing Phone 4a/4a Pro»
            index.setdefault(compact(part), title)
    brands = {b["slug"]: b["name"] for b in json.loads((CAT / "brands.json").read_text(encoding="utf-8"))}
    res, differ_total, agree_total = {}, 0, 0
    for path in sorted((CAT / "phones").rglob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        keys = [compact(d["full_name"]), compact(d["name"]), compact(brands.get(d["brand"], "") + d["name"])]
        keys += [compact(a) for a in d.get("aliases", [])]
        title = next((index[k] for k in keys if k in index), None)
        if title is None:
            # общая статья «Samsung Galaxy S24» с подписью «S24 Ultra» внутри
            base = re.sub(r"\s*(\+|plus|pro max|pro|ultra|fe|lite|mini|edge)$", "", d["full_name"], flags=re.I)
            t2 = index.get(compact(base))
            labels = {compact(x) for x in LABEL.findall(json.dumps(wiki[t2]["fields"]))} if t2 else set()
            if t2 and labels & {compact(d["name"]), compact(d["full_name"])}:
                title = t2
        if title is None:
            continue
        brand = brands.get(d["brand"], "")
        short = re.sub("^" + re.escape(brand) + r"\s+", "", d["name"], flags=re.I)
        names = {compact(d["full_name"]), compact(d["name"]), compact(short)}
        w = parse(wiki[title]["fields"], names)
        sp = d["specs"]
        checks = {"battery_mah": (w["battery_mah"], sp["battery_mah"], max(15, 0.02 * sp["battery_mah"])),
                  "weight_g": (w["weight_g"], sp["weight_g"], 3), "screen_in": (w["screen_in"], sp["screen_in"], 0.06),
                  "refresh_hz": (w["refresh_hz"], sp["refresh_hz"], 1)}  # зарядку из карточек надёжно не разобрать
        agree = [k for k, (a, b, t) in checks.items() if close(a, b, t)]
        differ = {k: {"wiki": a, "catalog": b} for k, (a, b, t) in checks.items() if a is not None and not close(a, b, t)}
        agree_total += len(agree)
        differ_total += len(differ)
        res[d["slug"]] = {"title": title, "url": wiki[title]["url"], "revid": wiki[title]["revid"], "wiki": w,
                          "agree": agree, "differ": differ}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"найдено статей для {len(res)} моделей; совпало полей {agree_total}, расходится {differ_total}")
    for slug, r in sorted(res.items()):
        if r["differ"]:
            print(f"  {slug} ← {r['title']}: " + ", ".join(f"{k} {v['catalog']}→{v['wiki']}" for k, v in r["differ"].items()))


if __name__ == "__main__":
    main()
