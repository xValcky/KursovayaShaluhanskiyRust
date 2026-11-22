"""Проверка доказательств «приколов» по официальным страницам (раздел 8 ТЗ: verified только со ссылкой).

Запуск:  python tools/check_quirk_evidence.py
Пишет:   data/sources/quirk_evidence.json — правила, для которых страница загружена и нужная фраза на ней найдена.
         tools/import_catalog_data.py ставит этим «приколам» _q: "verified" и ссылку в evidence.

Каждое правило: код «прикола», список моделей (slug), страница-доказательство и фраза, которая должна на ней быть
(и, если нужно, фраза, которой быть НЕ должно). Страница не открылась или фраза не нашлась — правило не применяется.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "data" / "catalog"
OUT = ROOT / "data" / "sources" / "quirk_evidence.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; PhonePickerCatalog/0.3; course project)"}


def slugs(prefixes: list[str], brand: str) -> list[str]:
    out = []
    for p in sorted((CAT / "phones" / brand).glob("*.json")):
        if any(p.stem == x or p.stem.startswith(x + "-") for x in prefixes):
            out.append(p.stem)
    return out


def rules() -> list[dict]:
    apple_ai = ["apple-iphone-15-pro", "apple-iphone-15-pro-max", "apple-iphone-16", "apple-iphone-16-plus",
                "apple-iphone-16-pro", "apple-iphone-16-pro-max", "apple-iphone-16e", "apple-iphone-17",
                "apple-iphone-17-pro", "apple-iphone-17-pro-max", "apple-iphone-17e", "apple-iphone-air",
                "apple-iphone-18-pro", "apple-iphone-18-pro-max", "apple-iphone-duo"]
    return [
        {"code": "long_updates", "severity": 3,
         "slugs": slugs(["google-pixel-8", "google-pixel-8a", "google-pixel-9", "google-pixel-9a", "google-pixel-10", "google-pixel-10a", "google-pixel-11"], "google"),
         "url": "https://support.google.com/pixelphone/answer/4457705?hl=en",
         "must": "Pixel 8 and later phones will get updates for 7 years",
         "note": "7 лет обновлений Android и безопасности с начала продаж — официальная политика Google для Pixel 8 и новее."},
        {"code": "long_updates", "severity": 3, "slugs": ["samsung-galaxy-z-fold6", "samsung-galaxy-z-flip6"],
         "url": "https://www.samsung.com/ae/support/mobile-devices/how-many-years-of-updates-can-we-receive-if-we-purchase-the-fold6flip6/",
         "must": "eligible for 7 years of Android OS upgrades and 7 years of security updates",
         "note": "7 лет обновлений Android и 7 лет патчей безопасности — страница поддержки Samsung."},
        {"code": "ai_limited_ru", "severity": 2, "slugs": [s for s in apple_ai if (CAT / "phones" / "apple" / f"{s}.json").exists()],
         "url": "https://support.apple.com/en-us/121115", "must": "iPhone 16 models or later", "must_not": "Russian",
         "note": "Русского языка нет среди языков Apple Intelligence на странице поддержки Apple: в РФ функции недоступны без смены языка и региона."},
        {"code": "no_google_services", "severity": 3, "slugs": slugs([""], "huawei") or [],
         "url": "https://en.wikipedia.org/wiki/Huawei_Mobile_Services", "must": "Google Mobile Services",
         "note": "Нет сервисов Google: с 2019 г. Huawei выпускает смартфоны с Huawei Mobile Services и AppGallery вместо Google Play."},
    ]


def fetch(url: str) -> str | None:
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"  не открылась: {url} ({type(e).__name__})")
        return None
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def main():
    out = []
    for r in rules():
        if r["code"] == "no_google_services":
            r["slugs"] = sorted(p.stem for p in (CAT / "phones" / "huawei").glob("*.json"))
        text = fetch(r["url"])
        ok = bool(text) and r["must"] in text and not (r.get("must_not") and r["must_not"] in text)
        print(f"  {'✓' if ok else '✗'} {r['code']}: {len(r['slugs'])} моделей ← {r['url']}")
        if ok:
            out.append({**r, "checked": str(date.today())})
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"подтверждено правил: {len(out)}, моделей: {sum(len(r['slugs']) for r in out)}")


if __name__ == "__main__":
    main()
