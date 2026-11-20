"""Сборка каталога v2: data/catalog/**.json → проверка → SQLite (data/catalog.db + копия rust_backend/catalog.db).

JSON-файлы — источник правды; БД пересобирается целиком и руками не правится.
Скрипт сам считает производные величины (раздел 7 ТЗ): площадь сенсора, оценку «железа» модуля, оптический зум,
итог по модулю и сводку камер. Нейросеть-наполнитель эти поля НЕ заполняет.

    python tools/build_catalog.py           # проверить и собрать
    python tools/build_catalog.py --check   # только проверить и напечатать отчёт (код выхода 1 при ошибках)

Только стандартная библиотека; если установлен пакет jsonschema — файлы дополнительно сверяются со схемой.
"""
import argparse
import csv
import hashlib
import json
import math
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "data" / "catalog"
SCHEMA_SQL = CAT / "schema" / "catalog_v2.sql"
OUT_DB = ROOT / "data" / "catalog.db"
RUST_DB = ROOT / "rust_backend" / "catalog.db"

Q = {"verified", "estimated", "user_hypothesis", "random", "unknown"}
OS = {"Android", "iOS", "HarmonyOS"}
STATUS = {"on_sale", "discontinued", "announced", "china_only"}
AVAIL = {"official", "parallel_import", "not_sold"}
FORM = {"bar", "book_fold", "flip", "trifold", "rugged", "other"}
ROLES = {"main", "ultrawide", "tele", "periscope", "macro", "depth", "mono", "front", "front_ultrawide", "cover"}
REAR = {"main", "ultrawide", "tele", "periscope", "macro", "mono"}
AF = {"none", "fixed", "contrast", "pdaf", "dual_pixel", "laser", None}
MACRO = {"none", "dedicated", "ultrawide_af", "tele_macro"}
RAM = {1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 20, 24, 32}
ROM = {16, 32, 64, 128, 256, 512, 1024, 2048}
FOLDABLE = {"book_fold", "flip", "trifold"}

# границы «светосилы» модуля (площадь сенсора / f²) по ролям: нижняя → 0 баллов, верхняя → 100 (раздел 7 ТЗ)
LIGHT_BOUNDS = {
    "main": (1.5, 50.0), "ultrawide": (1.0, 15.0), "tele": (1.0, 15.0), "periscope": (1.0, 15.0),
    "front": (0.8, 8.0), "front_ultrawide": (0.8, 8.0), "cover": (0.8, 8.0), "macro": (0.5, 5.0),
    "mono": (1.0, 15.0),
}
SENSOR_AREA_BOUNDS = (3.0, 125.0)  # мм²: ≈ 1/6″ … 1″
TECH_BONUS = {"lofic": 4, "dcg": 2, "stacked": 2}


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, where: str, msg: str):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str):
        self.warnings.append(f"{where}: {msg}")


def load(name: str):
    return json.loads((CAT / name).read_text(encoding="utf-8"))


def clip(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


def sensor_geometry(s: dict) -> tuple[float, float]:
    size = s["size_inch"]
    diag = s.get("diag_mm") or (16.0 / float(size.split("/")[1]) if "/" in size else 16.0)
    return diag, 0.48 * diag * diag


def sensor_hw(area: float, tech: list[str]) -> int:
    lo, hi = SENSOR_AREA_BOUNDS
    base = 100 * (math.log(area) - math.log(lo)) / (math.log(hi) - math.log(lo))
    return int(round(clip(base + sum(TECH_BONUS.get(t, 0) for t in tech))))


def module_hw(role: str, area: float | None, aperture: float | None, ois, af) -> int | None:
    if role == "depth" or area is None or not aperture:
        return None
    lo, hi = LIGHT_BOUNDS[role]
    light = area / (aperture * aperture)
    base = 100 * (math.log(light) - math.log(lo)) / (math.log(hi) - math.log(lo))
    return int(round(clip(base + (5 if ois else 0) + (3 if af in ("pdaf", "dual_pixel", "laser") else 0))))


def src_ok(rep: Report, where: str, ids, sources: set[str]):
    for s in ids or []:
        if s.split(":")[0] not in sources:
            rep.err(where, f"источник «{s}» не найден в sources.json")


def validate_phone(rep: Report, path: Path, d: dict, refs: dict):
    w = f"{path.parent.name}/{path.name}"
    need = ["slug", "brand", "name", "full_name", "os", "year", "status", "availability_ru", "form_factor", "in_ml",
            "chipset", "specs", "variants", "cameras", "camera_features", "quirks", "quirks_checked", "special",
            "photos"]
    for k in need:
        if k not in d:
            rep.err(w, f"нет поля {k}")
    if rep.errors and rep.errors[-1].startswith(w + ": нет поля"):
        return
    if d["slug"] != path.stem:
        rep.err(w, f"slug «{d['slug']}» не совпадает с именем файла")
    if d["brand"] != path.parent.name:
        rep.err(w, "файл лежит не в папке своего бренда")
    if d["brand"] not in refs["brands"]:
        rep.err(w, f"бренд {d['brand']} не найден в brands.json")
    if d["chipset"] is not None and d["chipset"] not in refs["chipsets"]:
        rep.err(w, f"чип {d['chipset']} не найден в chipsets.json")
    for k, allowed in (("os", OS), ("status", STATUS), ("availability_ru", AVAIL), ("form_factor", FORM)):
        if d[k] not in allowed:
            rep.err(w, f"{k} = {d[k]!r} — недопустимое значение")
    sp = d["specs"]
    if sp.get("_q") not in Q:
        rep.err(w, "specs._q не задан")
    src_ok(rep, w, sp.get("_src"), refs["sources"])
    for k in ("screen_in", "refresh_hz", "battery_mah", "charge_w", "weight_g"):
        if not isinstance(sp.get(k), (int, float)):
            rep.err(w, f"specs.{k} должно быть числом")
    # конфигурации
    vs = d["variants"]
    if not vs:
        rep.err(w, "нет ни одной конфигурации памяти")
    if sum(1 for v in vs if v.get("is_base")) != 1:
        rep.err(w, "должна быть ровно одна конфигурация с is_base=true")
    seen = set()
    for v in vs:
        key = (v.get("ram_gb"), v.get("rom_gb"))
        if key in seen:
            rep.err(w, f"повтор конфигурации {key}")
        seen.add(key)
        if v.get("ram_gb") not in RAM or v.get("rom_gb") not in ROM:
            rep.err(w, f"недопустимая память {key}")
        if v.get("_q") not in Q:
            rep.err(w, f"конфигурация {key}: нет _q")
        if v.get("price_rub") is None and d["availability_ru"] != "not_sold":
            rep.warn(w, f"конфигурация {key}: нет цены, хотя модель продаётся в РФ")
        if v.get("price_src") and v["price_src"] not in refs["sources"]:
            rep.err(w, f"price_src {v['price_src']} не найден в sources.json")
    # камеры
    roles = Counter(c.get("role") for c in d["cameras"])
    if roles["main"] != 1:
        rep.err(w, f"основных камер {roles['main']}, нужна ровно одна")
    if not any(r in roles for r in ("front", "front_ultrawide")):
        rep.warn(w, "нет фронтальной камеры")
    for c in d["cameras"]:
        if c.get("role") not in ROLES:
            rep.err(w, f"роль камеры {c.get('role')!r}")
        if c.get("sensor") is not None and c["sensor"] not in refs["sensors"]:
            rep.err(w, f"сенсор {c['sensor']} не найден в sensors.json")
        if c.get("af") not in AF:
            rep.err(w, f"af = {c.get('af')!r}")
        if c.get("_q") not in Q:
            rep.err(w, f"камера {c.get('role')}: нет _q")
        ps = c.get("processing_score")
        if ps is not None and not 0 <= ps <= 100:
            rep.err(w, f"processing_score {ps} вне 0..100")
        if c.get("role") not in ("depth",) and ps is None:
            rep.warn(w, f"камера {c.get('role')}: нет оценки обработки")
        src_ok(rep, w, c.get("_src"), refs["sources"])
    cf = d["camera_features"]
    if cf.get("macro_mode") not in MACRO:
        rep.err(w, f"macro_mode = {cf.get('macro_mode')!r}")
    # «приколы» и категории
    for q in d["quirks"]:
        t = refs["quirks"].get(q.get("code"))
        if t is None:
            rep.err(w, f"«прикол» {q.get('code')} не найден в quirk_types.json")
            continue
        if not 1 <= int(q.get("severity", 0)) <= 3:
            rep.err(w, f"«прикол» {q['code']}: severity вне 1..3")
        if q.get("_q") not in Q:
            rep.err(w, f"«прикол» {q['code']}: нет _q")
        if q["code"] == "hinge_crease" and d["form_factor"] not in FOLDABLE:
            rep.err(w, "hinge_crease допустим только у складных")
        if t["polarity"] < 0 and q.get("_q") == "verified" and len(q.get("evidence") or []) < 2:
            rep.warn(w, f"отрицательный «прикол» {q['code']} отмечен verified, но ссылок < 2 — нужен замер или 2 источника")
    if len({q.get("code") for q in d["quirks"]}) != len(d["quirks"]):
        rep.err(w, "повтор «прикола»")
    for s in d["special"]:
        if s.get("code") not in refs["special"]:
            rep.err(w, f"категория {s.get('code')} не найдена в special_categories.json")
    # фото
    if not d["photos"]:
        rep.err(w, "нет ни одного фото")
    urls = [p.get("url", "") for p in d["photos"]]
    if len(set(urls)) != len(urls):
        rep.err(w, "повтор ссылки на фото")
    for p in d["photos"]:
        if not str(p.get("url", "")).startswith(("https://", "/")):
            rep.err(w, f"ссылка на фото должна начинаться с https:// или /: {p.get('url')}")
        if not p.get("license"):
            rep.err(w, "у фото нет лицензии")


def jsonschema_check(rep: Report, files: list[tuple[Path, dict]]):
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return False
    schema = json.loads((CAT / "schema" / "phone.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for path, d in files:
        for e in validator.iter_errors(d):
            rep.err(f"{path.parent.name}/{path.name}", f"схема: {'/'.join(map(str, e.path))}: {e.message}")
    return True


def files_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.relative_to(CAT).as_posix().encode())
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))  # git на Windows может выдать файлы с CRLF
    return h.hexdigest()[:16]


def build(files, refs_raw, rep: Report) -> dict:
    tmp = OUT_DB.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    J = lambda x: json.dumps(x, ensure_ascii=False) if x else None  # noqa: E731
    ds = refs_raw["dataset"]
    all_paths = sorted(p for p in CAT.rglob("*") if p.is_file() and p.suffix in (".json", ".csv", ".sql"))
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("schema_version", str(ds["schema_version"])), ("dataset_name", ds["name"]),
        ("dataset_version", ds["version"]), ("generated", ds["generated"]),
        ("random_seed", str(ds.get("random_seed", ""))), ("files_hash", files_hash(all_paths)),
    ])
    conn.executemany("INSERT INTO sources VALUES (?,?,?,?,?)",
                     [(s["id"], s["name"], s.get("url"), s["kind"], s["reliability"]) for s in refs_raw["sources"]])
    brand_id = {}
    for k, b in enumerate(refs_raw["brands"], start=1):
        conn.execute("INSERT INTO brands VALUES (?,?,?,?,?)", (k, b["slug"], b["name"], b.get("parent"), b.get("country")))
        brand_id[b["slug"]] = k
    chip_id = {}
    for k, c in enumerate(refs_raw["chipsets"], start=1):
        conn.execute("INSERT INTO chipsets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (k, c["slug"], c["name"], c["vendor"], c.get("process_nm"), c.get("antutu_k"),
                      c.get("geekbench_sc"), c.get("geekbench_mc"), c.get("year"), c["_q"], J(c.get("_src"))))
        chip_id[c["slug"]] = k
    sensor_id, sensor_area = {}, {}
    for k, s in enumerate(refs_raw["sensors"], start=1):
        diag, area = sensor_geometry(s)
        conn.execute("INSERT INTO camera_sensors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (k, s["slug"], s["name"], s["vendor"], s["mp"], s["size_inch"], round(diag, 3), round(area, 2),
                      s.get("pixel_um"), J(s.get("tech")), sensor_hw(area, s.get("tech") or []), s["_q"],
                      J(s.get("_src"))))
        sensor_id[s["slug"]], sensor_area[s["slug"]] = k, area
    quirk_id = {}
    for k, t in enumerate(refs_raw["quirk_types"], start=1):
        conn.execute("INSERT INTO quirk_types VALUES (?,?,?,?,?,?,?)",
                     (k, t["code"], t["title_ru"], t["category"], t["polarity"], t.get("description_ru"),
                      t.get("measure_rule")))
        quirk_id[t["code"]] = k
    cat_id = {}
    for k, c in enumerate(refs_raw["special_categories"], start=1):
        conn.execute("INSERT INTO special_categories VALUES (?,?,?,?,?,?)",
                     (k, c["code"], c["title_ru"], c.get("description_ru"), c.get("criteria"), c["sort"]))
        cat_id[c["code"]] = k

    # процентиль подоценок DXOMARK по базе — для поправки обработки (раздел 7 ТЗ)
    dxo_vals: dict[str, list[int]] = {}
    for _, d in files:
        dx = d["camera_features"].get("dxomark") or {}
        for key in ("photo", "zoom_wide", "zoom_tele", "selfie"):
            if dx.get(key) is not None:
                dxo_vals.setdefault(key, []).append(dx[key])
    role_dxo = {"main": "photo", "ultrawide": "zoom_wide", "tele": "zoom_tele", "periscope": "zoom_tele",
                "front": "selfie"}

    def pct(key: str, v: int) -> float:
        arr = sorted(dxo_vals[key])
        return 100.0 * sum(1 for x in arr if x <= v) / len(arr)

    variant_key = {}
    for pid, (path, d) in enumerate(files, start=1):
        sp = d["specs"]
        conn.execute(
            "INSERT INTO phones VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, d["slug"], brand_id[d["brand"]], d["name"], d["full_name"], J(d.get("aliases")), d["os"],
             d.get("release_date"), d["year"], d["status"], d["availability_ru"], d["form_factor"], int(d["in_ml"]),
             chip_id.get(d["chipset"]), sp["screen_in"], sp.get("cover_screen_in"), sp["refresh_hz"],
             sp.get("display_type"), sp.get("peak_nits"), sp["battery_mah"], sp["charge_w"], sp.get("wireless_w"),
             sp["weight_g"], sp.get("thickness_mm"), sp.get("ip_rating"), int(sp["has_5g"]), int(sp["nfc"]),
             int(sp["esim"]), int(sp["ir_port"]),
             None if sp.get("jack35") is None else int(sp["jack35"]),
             None if sp.get("sd_card") is None else int(sp["sd_card"]), sp.get("update_years"),
             int(d["quirks_checked"]), sp["_q"], J(sp.get("_src"))))
        for v in d["variants"]:
            cur = conn.execute(
                "INSERT INTO variants (phone_id, ram_gb, rom_gb, price_rub, price_date, price_source_id, msrp_usd, "
                "is_base, q) VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, v["ram_gb"], v["rom_gb"], v.get("price_rub"), v.get("price_date"), v.get("price_src"),
                 v.get("msrp_usd"), int(v["is_base"]), v["_q"]))
            variant_key[(d["slug"], v["ram_gb"], v["rom_gb"])] = cur.lastrowid

        # камеры: «железо», зум, обработка, итог
        cams = sorted(d["cameras"], key=lambda c: (c["role"], c.get("position", 0)))
        main = next(c for c in cams if c["role"] == "main")
        dx = d["camera_features"].get("dxomark") or {}
        totals: dict[str, list[int]] = {}
        for c in cams:
            area = sensor_area.get(c.get("sensor"))
            hw = module_hw(c["role"], area, c.get("aperture"), c.get("ois"), c.get("af"))
            zoom = c.get("optical_zoom_x")
            if c.get("focal_mm") and main.get("focal_mm") and c["role"] not in ("depth", "front", "front_ultrawide",
                                                                                 "cover"):
                zoom = round(c["focal_mm"] / main["focal_mm"], 1)
            proc, basis = c.get("processing_score"), c.get("processing_basis")
            key = role_dxo.get(c["role"])
            if key and dx.get(key) is not None and hw is not None:
                derived = int(round(clip(50 + (pct(key, dx[key]) - hw))))
                proc = derived if proc is None else int(round((proc + derived) / 2))
                basis = "dxomark"
            total = None
            if hw is not None:
                total = hw if proc is None else int(round(0.5 * hw + 0.5 * proc))
            conn.execute(
                "INSERT INTO phone_cameras (phone_id, role, position, sensor_id, mp, aperture, focal_mm, fov_deg, "
                "optical_zoom_x, ois, af, min_focus_cm, video_max, module_hw_score, processing_score, "
                "processing_basis, total_score, q, src) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, c["role"], c.get("position", 0), sensor_id.get(c.get("sensor")), c["mp"], c.get("aperture"),
                 c.get("focal_mm"), c.get("fov_deg"), zoom, None if c.get("ois") is None else int(c["ois"]),
                 c.get("af"), c.get("min_focus_cm"), c.get("video_max"), hw, proc, basis, total, c["_q"],
                 J(c.get("_src"))))
            if total is not None:
                totals.setdefault(c["role"], []).append(total)
            c["_zoom"] = zoom
        cf = d["camera_features"]
        best = lambda *roles: max((t for r in roles for t in totals.get(r, [])), default=None)  # noqa: E731
        main_s, uw_s, tele_s = best("main"), best("ultrawide"), best("tele", "periscope")
        front_s = best("front", "front_ultrawide")
        macro_s = {"dedicated": best("macro"), "ultrawide_af": uw_s and int(round(uw_s * 0.9)),
                   "tele_macro": tele_s, "none": 0}[cf["macro_mode"]] or 0
        overall = int(round(0.5 * (main_s or 0) + 0.2 * (uw_s or 0) + 0.2 * (tele_s or 0) + 0.1 * (front_s or 0)))
        opt = max((c["_zoom"] or 1.0 for c in cams if c["role"] in ("tele", "periscope")), default=1.0)
        conn.execute("INSERT INTO camera_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, sum(1 for c in cams if c["role"] in REAR), opt, cf.get("lossless_zoom_x"),
                      cf.get("max_digital_zoom_x"), cf["macro_mode"], macro_s, main_s, uw_s, tele_s, front_s, overall,
                      dx.get("camera"), J(dx) if dx else None, cf["_q"]))
        for q in d["quirks"]:
            conn.execute("INSERT INTO phone_quirks VALUES (?,?,?,?,?,?)",
                         (pid, quirk_id[q["code"]], q["severity"], q.get("note_ru"), J(q.get("evidence")), q["_q"]))
        for s in d["special"]:
            conn.execute("INSERT INTO phone_special VALUES (?,?,?,?)",
                         (pid, cat_id[s["code"]], s.get("note_ru"), s["_q"]))
        for pos, p in enumerate(d["photos"]):
            conn.execute("INSERT INTO photos (phone_id, position, url, kind, color_ru, author, license, source_page, "
                         "width, height, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (pid, pos, p["url"], p["kind"], p.get("color_ru"), p.get("author"), p["license"],
                          p.get("source_page"), p.get("width"), p.get("height"), p["origin"]))

    # история цен: только изменения цены по каждой паре (конфигурация, источник)
    hist_path = CAT / "price_history.csv"
    hist_rows = 0
    if hist_path.exists():
        last: dict[tuple, int] = {}
        for h in sorted(csv.DictReader(hist_path.open(encoding="utf-8")),
                        key=lambda h: (h["slug"], int(h["ram_gb"]), int(h["rom_gb"]), h["source"], h["date"])):
            vid = variant_key.get((h["slug"], int(h["ram_gb"]), int(h["rom_gb"])))
            if vid is None:
                rep.warn("price_history.csv", f"нет конфигурации {h['slug']} {h['ram_gb']}/{h['rom_gb']}")
                continue
            if h["source"] not in refs_raw["source_ids"]:
                rep.err("price_history.csv", f"источник {h['source']} не найден в sources.json")
                continue
            price = int(h["price_rub"])
            if last.get((vid, h["source"])) == price:
                continue
            last[(vid, h["source"])] = price
            conn.execute("INSERT OR REPLACE INTO price_history VALUES (?,?,?,?)", (vid, h["date"], h["source"], price))
            hist_rows += 1
    conn.commit()
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        rep.err("catalog.db", f"нарушены внешние ключи: {fk[:5]}")
    stats = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("phones", "variants", "phone_cameras", "camera_sensors", "photos", "phone_quirks",
                       "phone_special", "price_history")}
    conn.execute("VACUUM")
    conn.close()
    if not rep.errors:
        # одинаковые данные дают байт в байт одинаковую БД; не трогаем файлы без изменений, иначе меняется дата
        # rust_backend/catalog.db, cargo пересобирает сервер при каждом запуске run.bat и упирается в занятый exe
        fresh = tmp.read_bytes()
        for dst in (OUT_DB, RUST_DB):
            if not dst.exists() or dst.read_bytes() != fresh:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(tmp, dst)
                stats.setdefault("updated", []).append(str(dst.relative_to(ROOT)))
    tmp.unlink()
    stats["price_history_input"] = hist_rows
    return stats


def coverage(files) -> list[str]:
    n = len(files)
    pct = lambda k: f"{100 * k / n:.0f}%"  # noqa: E731
    qs = Counter()
    for _, d in files:
        qs[d["specs"]["_q"]] += 1
        for c in d["cameras"]:
            qs["cam:" + c["_q"]] += 1
    real_photos = sum(1 for _, d in files if sum(p["origin"] != "placeholder" for p in d["photos"]) >= 3)
    any_real = sum(1 for _, d in files if any(p["origin"] != "placeholder" for p in d["photos"]))
    return [
        f"моделей: {n} (в подборе нейросетью: {sum(1 for _, d in files if d['in_ml'])})",
        f"конфигураций памяти: {sum(len(d['variants']) for _, d in files)}, "
        f"моделей с ≥ 2 конфигурациями: {pct(sum(1 for _, d in files if len(d['variants']) > 1))}",
        f"≥ 3 настоящих фото: {pct(real_photos)}; хотя бы одно настоящее: {pct(any_real)}",
        f"с «приколами»: {pct(sum(1 for _, d in files if d['quirks']))}; "
        f"проверено (quirks_checked): {pct(sum(1 for _, d in files if d['quirks_checked']))}",
        f"с интересными возможностями: {sum(1 for _, d in files if d['special'])}",
        "достоверность: " + ", ".join(f"{k}={v}" for k, v in sorted(qs.items())),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="только проверить, БД не писать")
    args = ap.parse_args()
    rep = Report()
    refs_raw = {name: load(f"{name}.json") for name in
                ("brands", "chipsets", "sensors", "quirk_types", "special_categories", "sources", "dataset")}
    refs_raw["source_ids"] = {s["id"] for s in refs_raw["sources"]}
    refs = {"brands": {b["slug"] for b in refs_raw["brands"]}, "chipsets": {c["slug"] for c in refs_raw["chipsets"]},
            "sensors": {s["slug"] for s in refs_raw["sensors"]},
            "quirks": {t["code"]: t for t in refs_raw["quirk_types"]},
            "special": {c["code"] for c in refs_raw["special_categories"]}, "sources": refs_raw["source_ids"]}
    for name, key in (("brands", "slug"), ("chipsets", "slug"), ("sensors", "slug"), ("quirk_types", "code"),
                      ("special_categories", "code"), ("sources", "id")):
        vals = [x[key] for x in refs_raw[name]]
        for dup in {v for v in vals if vals.count(v) > 1}:
            rep.err(f"{name}.json", f"повтор ключа {dup}")
    for s in refs_raw["sensors"]:
        src_ok(rep, "sensors.json", s.get("_src"), refs["sources"])
    files = []
    for path in sorted((CAT / "phones").rglob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            rep.err(path.name, f"битый JSON: {e}")
            continue
        validate_phone(rep, path, d, refs)
        files.append((path, d))
    slugs = [d.get("slug") for _, d in files]
    for dup in {s for s in slugs if slugs.count(s) > 1}:
        rep.err("phones", f"повтор slug {dup}")
    used_schema = jsonschema_check(rep, files)

    stats = None
    if not rep.errors and not args.check:
        stats = build(files, refs_raw, rep)
    print("Каталог v2:", refs_raw["dataset"]["name"])
    for line in coverage(files) if files else []:
        print("  " + line)
    if stats:
        updated = stats.pop("updated", [])
        print("  в БД: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
        if updated:
            print(f"  записано: {' и '.join(updated)} ({OUT_DB.stat().st_size / 1024:.0f} КБ)")
        elif OUT_DB.exists():
            print(f"  данные не изменились — {OUT_DB.relative_to(ROOT)} и {RUST_DB.relative_to(ROOT)} не перезаписаны")
    print(f"  проверка JSON Schema: {'да' if used_schema else 'нет (pip install jsonschema — по желанию)'}")
    print(f"  предупреждений: {len(rep.warnings)}, ошибок: {len(rep.errors)}")
    for line in rep.warnings[:15]:
        print("  ! " + line)
    for line in rep.errors[:40]:
        print("  ✗ " + line)
    sys.exit(1 if rep.errors else 0)


if __name__ == "__main__":
    main()
