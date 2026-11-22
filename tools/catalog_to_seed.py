"""Каталог v2 → обучающая база Python (переобучение нейросети на новом каталоге).

Запуск:  python tools/build_catalog.py        # сначала собрать data/catalog.db
         python tools/catalog_to_seed.py      # → data/phones_seed.csv, price_history_seed.csv, phone_photos_seed.csv
         python tools/export_for_rust.py --golden 60   # обучение + rust_backend/model.json, golden.json

В обучение идут модели с in_ml = 1 и ценой базовой конфигурации (как в v_ml_phones): одна строка — одна модель,
признаки по базовой конфигурации. Название модели в строке совпадает с name каталога, поэтому Rust связывает
обученную модель с каталогом по тому же slug (бренд + модель, раздел 4.2 ТЗ).

AnTuTu берётся из справочника чипов; если у чипа его нет — из прежней базы (data/sources/phones_seed_v1.csv),
иначе пусто: очистка данных курса (src/db.py, handle_missing) подставит среднее.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "catalog.db"
SEED = ROOT / "data" / "phones_seed.csv"
OLD_SEED = ROOT / "data" / "sources" / "phones_seed_v1.csv"  # база до перехода на каталог (для AnTuTu)
HISTORY = ROOT / "data" / "price_history_seed.csv"
PHOTOS = ROOT / "data" / "phone_photos_seed.csv"
TODAY = __import__("datetime").date.today().isoformat()
COLUMNS = ["brand", "model", "os", "year", "price_rub", "ram_gb", "rom_gb", "battery_mah", "screen_in",
           "refresh_hz", "weight_g", "camera_mp", "antutu_k", "charge_w", "has_5g", "nfc", "esim", "ir_port",
           "price_source"]
SRC_RU = {"telegram_polzaskidki": "Telegram (polzaskidki)", "telegram_ishopper": "Telegram (ishopper)",
          "project_seed": "прежняя база проекта", "ai_knowledge": "оценка нейросети", "price_model": "пересчёт от базовой"}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("+", " plus ")).strip("-")


def model_slug(brand: str, model: str) -> str:
    model = re.sub(r"\s+(\d+)\s*(GB|TB)$", "", model)
    full = model if model.lower().startswith(brand.lower()) else f"{brand} {model}"
    return slugify(full)


def main():
    if not DB.exists():
        raise SystemExit("нет data/catalog.db — сначала python tools/build_catalog.py")
    old_antutu = {}
    if OLD_SEED.exists():
        for r in csv.DictReader(OLD_SEED.open(encoding="utf-8")):
            old_antutu.setdefault(model_slug(r["brand"], r["model"]), float(r["antutu_k"]))
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT p.id, p.slug, b.name AS brand, p.name AS model, p.full_name, p.os, p.year, v.id AS vid,
               v.price_rub, v.price_date, v.price_source_id, v.q AS price_q, v.ram_gb, v.rom_gb,
               p.battery_mah, p.screen_in, p.refresh_hz, p.weight_g, p.charge_w, p.has_5g, p.nfc, p.esim, p.ir_port,
               ch.antutu_k,
               (SELECT c.mp FROM phone_cameras c WHERE c.phone_id = p.id AND c.role = 'main' LIMIT 1) AS camera_mp
        FROM phones p JOIN brands b ON b.id = p.brand_id
        JOIN variants v ON v.phone_id = p.id AND v.is_base = 1
        LEFT JOIN chipsets ch ON ch.id = p.chipset_id
        WHERE p.in_ml = 1 AND v.price_rub IS NOT NULL
        ORDER BY b.name, p.name""").fetchall()
    names = [r["model"] for r in rows]
    out, no_antutu = [], []
    for r in rows:
        model = r["model"] if names.count(r["model"]) == 1 else r["full_name"]  # в phones.db имя модели уникально
        assert model_slug(r["brand"], model) == r["slug"], (r["slug"], model)
        antutu = r["antutu_k"] or old_antutu.get(r["slug"])
        if antutu is None:
            no_antutu.append(r["slug"])
        src = SRC_RU.get(r["price_source_id"], r["price_source_id"] or "?")
        note = f"{src}, {r['price_date']}" if r["price_date"] else src
        out.append({"brand": r["brand"], "model": model, "os": r["os"] if r["os"] != "HarmonyOS" else "Android",
                    "year": r["year"], "price_rub": r["price_rub"], "ram_gb": r["ram_gb"], "rom_gb": r["rom_gb"],
                    "battery_mah": r["battery_mah"], "screen_in": r["screen_in"], "refresh_hz": r["refresh_hz"],
                    "weight_g": r["weight_g"], "camera_mp": r["camera_mp"], "antutu_k": antutu if antutu else "",
                    "charge_w": r["charge_w"], "has_5g": r["has_5g"], "nfc": r["nfc"], "esim": r["esim"],
                    "ir_port": r["ir_port"], "price_source": f"{note} ({'проверено' if r['price_q'] == 'verified' else 'оценка'})",
                    "_pid": r["id"], "_vid": r["vid"], "_date": r["price_date"]})
    with SEED.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    # история цен базовой конфигурации (источники как в прежней базе: telegram:… / оценка…)
    hist = []
    for r in out:
        for h in conn.execute("SELECT date, source_id, price_rub FROM price_history WHERE variant_id = ? ORDER BY date",
                              (r["_vid"],)):
            src = "telegram:" + h["source_id"].split("_", 1)[1] if h["source_id"].startswith("telegram_") else \
                "оценка: прежняя база проекта"
            hist.append({"model": r["model"], "date": h["date"], "source": src, "price_rub": h["price_rub"]})
    # стартовая точка для моделей без истории — текущая цена базовой конфигурации (график есть у каждой модели)
    have = {h["model"] for h in hist}
    for r in out:
        if r["model"] not in have:
            hist.append({"model": r["model"], "date": r["_date"] or TODAY, "source": "оценка: каталог v2",
                         "price_rub": r["price_rub"]})
    seen, uniq = set(), []
    for h in hist:  # одна точка на модель, дату и источник
        k = (h["model"], h["date"], h["source"])
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    hist = sorted(uniq, key=lambda h: (h["model"], h["date"]))
    with HISTORY.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "date", "source", "price_rub"], lineterminator="\n")
        w.writeheader()
        w.writerows(hist)
    # фото каталога → таблица phone_photos настольного приложения
    photos = []
    for r in out:
        for pos, ph in enumerate(conn.execute("SELECT url, author, license, source_page FROM photos "
                                              "WHERE phone_id = ? AND origin != 'placeholder' ORDER BY position",
                                              (r["_pid"],))):
            photos.append({"model": r["model"], "position": pos, "url": ph["url"], "author": ph["author"] or "",
                           "license": ph["license"] or "", "source_page": ph["source_page"] or ""})
    with PHOTOS.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "position", "url", "author", "license", "source_page"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(photos)
    print(f"{SEED.relative_to(ROOT)}: {len(out)} моделей; {HISTORY.name}: {len(hist)} точек; "
          f"{PHOTOS.name}: {len(photos)} фото")
    if no_antutu:
        print(f"  без AnTuTu (подставится среднее): {len(no_antutu)} — {', '.join(no_antutu[:12])}"
              f"{' …' if len(no_antutu) > 12 else ''}")


if __name__ == "__main__":
    main()
