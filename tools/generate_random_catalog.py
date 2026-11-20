"""Демо-каталог v2 «рандом цифры по камерам и всему-всему-всему на будущее».

Берёт реальные поля из data/phones_seed.csv (бренд, модель, ОС, год, базовая ОЗУ/ПЗУ/цена, батарея, экран,
частота, вес, МП основной камеры, AnTuTu, флаги) и дописывает СЛУЧАЙНЫЕ значения для всего нового:
модули камер, качество обработки, макро и зум, дополнительные конфигурации памяти, «приколы», заглушки фото.
Всё случайное помечено `_q: "random"` — позже заменяется реальными данными по ТЗ (docs/ТЗ_БД_v2_промт_для_нейросети.md).

Результат детерминирован (seed из data/catalog/dataset.json): повторный запуск даёт те же файлы байт в байт.

    python tools/generate_random_catalog.py      # → data/catalog/phones/**.json, brands/chipsets/sensors.json,
                                                 #   price_history.csv
    python tools/build_catalog.py                # → data/catalog.db и rust_backend/catalog.db

Только стандартная библиотека Python.
"""
import csv
import json
import math
import random
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CAT = DATA / "catalog"
DATASET = json.loads((CAT / "dataset.json").read_text(encoding="utf-8"))
SEED = DATASET["random_seed"]
TODAY = DATASET["generated"]

# бренды, которые в РФ продаются в основном параллельным импортом (оценка; проверяется по ТЗ)
PARALLEL_IMPORT = {"apple", "samsung", "google", "oneplus", "nothing", "motorola", "asus", "fairphone", "lenovo"}

# ── синтетические сенсоры: (МП, оптический формат). Имена DEMO-S.., чтобы не выдавать случайные числа за факты ──
MAIN_POOL = {
    8: ["1/4"], 12: ["1/2.55", "1/1.76"], 13: ["1/3.1"], 32: ["1/2.8"], 48: ["1/2", "1/1.56", "1/1.28"],
    50: ["1/2.88", "1/2.76", "1/1.95", "1/1.56", "1/1.3", "1/1.28", "1"], 64: ["1/2", "1/1.73"],
    100: ["1/1.67"], 108: ["1/1.67", "1/1.52"], 200: ["1/1.56", "1/1.4", "1/1.12"],
}
UW_POOL = [(8, "1/4"), (13, "1/3.1"), (12, "1/2.55"), (50, "1/2.88"), (50, "1/2.76"), (48, "1/2"), (50, "1/1.95"),
           (50, "1/1.28")]
TELE_POOL = [(8, "1/4.4"), (10, "1/3.94"), (12, "1/3.5"), (50, "1/2.76"), (64, "1/2"), (50, "1/1.95"), (50, "1/1.56"),
             (200, "1/1.4")]
MACRO_POOL = [(2, "1/5"), (5, "1/5")]
FRONT_POOL = [(8, "1/4"), (12, "1/3.6"), (16, "1/3.1"), (20, "1/3"), (32, "1/3.14"), (50, "1/2.76")]
TECH = ["stacked", "dcg", "lofic", "quad_bayer", "hdr_dol"]

RAM_STEPS = [2, 3, 4, 6, 8, 12, 16, 24]
ROM_STEPS = [64, 128, 256, 512, 1024]


def size_value(size: str) -> float:
    """«1/1.3» → 0.769…, «1» → 1.0 — чем больше, тем крупнее сенсор."""
    return 1.0 / float(size.split("/")[1]) if "/" in size else float(size)


def full_name(brand: str, model: str) -> str:
    """Как src/ml/recommender.py: «Honor» + «Honor 400» → «Honor 400», «Xiaomi» + «Redmi 15» → «Xiaomi Redmi 15»."""
    return model if model.lower().startswith(brand.lower()) else f"{brand} {model}"


MEM_SUFFIX = re.compile(r"\s+(\d+)\s*(GB|TB)$", re.IGNORECASE)


def base_model(model: str) -> tuple[str, int | None]:
    """«iPhone 17 Pro 512GB» → («iPhone 17 Pro», 512): строки-конфигурации склеиваются с базовой моделью."""
    m = MEM_SUFFIX.search(model)
    if not m:
        return model, None
    size = int(m.group(1)) * (1024 if m.group(2).upper() == "TB" else 1)
    return model[:m.start()].strip(), size


def slugify(text: str) -> str:
    """Тот же алгоритм в Rust (catalog::slugify): «+» → «-plus», нижний регистр, всё кроме a-z0-9 → «-».

    «Galaxy S25+» → «galaxy-s25-plus» (иначе совпал бы с «Galaxy S25»).
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("+", " plus ")).strip("-")


def phone_slug(brand: str, model: str) -> str:
    return slugify(full_name(brand, base_model(model)[0]))


def round_price(x: float) -> int:
    return max(990, int(round(x / 1000.0)) * 1000 - 10)


def sensor_catalog() -> tuple[list[dict], dict]:
    combos = set()
    for mp, sizes in MAIN_POOL.items():
        combos.update((mp, s) for s in sizes)
    for pool in (UW_POOL, TELE_POOL, MACRO_POOL, FRONT_POOL):
        combos.update(pool)
    rng = random.Random(f"{SEED}:sensors")
    out, index = [], {}
    for k, (mp, size) in enumerate(sorted(combos, key=lambda c: (c[0], size_value(c[1]))), start=1):
        slug = f"demo-s{k:02d}"
        diag = 16.0 / float(size.split("/")[1]) if "/" in size else 16.0
        px_across = math.sqrt(mp * 1e6 * 4 / 3)
        pixel_um = round(0.8 * diag * 1000 / px_across, 2)
        tech = sorted(rng.sample(TECH, rng.randint(0, 2)))
        if mp >= 48:
            tech = sorted(set(tech) | {"quad_bayer"})
        out.append({"slug": slug, "name": f"DEMO-S{k:02d} ({mp:g} МП, {size}″)", "vendor": "Демо",
                    "mp": mp, "size_inch": size, "diag_mm": None, "pixel_um": pixel_um, "tech": tech,
                    "_q": "random", "_src": ["random"]})
        index[(mp, size)] = slug
    return out, index


def pick_by_tier(rng: random.Random, options: list, tier: int):
    """Из списка по возрастанию «качества» берём ближе к началу для бюджетных и ближе к концу для флагманов."""
    n = len(options)
    lo_f, hi_f = {0: (0.0, 0.5), 1: (0.25, 0.8), 2: (0.5, 1.0)}[tier]
    lo = min(int(lo_f * n), n - 1)
    hi = max(lo + 1, min(n, math.ceil(hi_f * n)))
    return options[rng.randrange(lo, hi)]


def processing(rng: random.Random, tier: int) -> int:
    mu = {0: 48, 1: 61, 2: 74}[tier]
    return max(15, min(98, int(round(rng.gauss(mu, 9)))))


def cam(role, pos, sensor, mp, aperture, focal, fov, ois, af, rng, tier, *, min_focus=None, video=None, proc=True,
        zoom=None):
    return {
        "role": role, "position": pos, "sensor": sensor, "mp": mp, "aperture": aperture, "focal_mm": focal,
        "fov_deg": fov, "optical_zoom_x": zoom, "ois": ois, "af": af, "min_focus_cm": min_focus, "video_max": video,
        "processing_score": processing(rng, tier) if proc else None,
        "processing_basis": "random" if proc else None,
        "_q": "random", "_src": ["random"],
    }


def gen_cameras(rng: random.Random, tier: int, main_mp: float, form: str, sens: dict) -> tuple[list, dict]:
    cams = []
    mp = int(main_mp) if float(main_mp).is_integer() else main_mp
    sizes = sorted(MAIN_POOL.get(int(mp), ["1/2"]), key=size_value)
    main_size = pick_by_tier(rng, sizes, tier)
    if (int(mp), main_size) not in sens:
        main_size = sizes[0]
    ap_lo, ap_hi = {0: (1.75, 2.0), 1: (1.7, 1.9), 2: (1.6, 1.8)}[tier]
    video = rng.choice({0: ["1080p30", "4K30"], 1: ["4K30", "4K60"], 2: ["4K60", "4K120", "8K30"]}[tier])
    cams.append(cam("main", 0, sens.get((int(mp), main_size)), mp, round(rng.uniform(ap_lo, ap_hi), 1),
                    rng.randint(23, 26), rng.randint(80, 86), rng.random() < (0.3, 0.8, 1.0)[tier],
                    rng.choice(["pdaf", "dual_pixel"]) if tier == 2 else "pdaf", rng, tier, video=video))
    # сверхширик
    has_uw = rng.random() < (0.35, 0.8, 1.0)[tier]
    uw_af = False
    if has_uw:
        umpx, usize = pick_by_tier(rng, UW_POOL, tier)
        uw_af = rng.random() < (0.1, 0.35, 0.7)[tier]
        cams.append(cam("ultrawide", 0, sens[(umpx, usize)], umpx, round(rng.uniform(2.0, 2.4), 1),
                        rng.randint(13, 16), rng.randint(112, 123), False, "pdaf" if uw_af else "fixed", rng, tier,
                        min_focus=rng.choice([2.5, 3, 4, 5]) if uw_af else None, zoom=0.6))
    # телевики
    tele_macro = False
    if tier == 2 and rng.random() < 0.85:
        tmp, tsize = pick_by_tier(rng, TELE_POOL[:6], 1)
        focal = rng.randint(65, 75)
        tele_macro = rng.random() < 0.25
        cams.append(cam("tele", 0, sens[(tmp, tsize)], tmp, round(rng.uniform(2.0, 2.6), 1), focal,
                        rng.randint(30, 36), True, "pdaf", rng, tier, min_focus=rng.choice([10, 12, 15]) if tele_macro
                        else None))
    if tier == 2 and rng.random() < 0.5:
        pmp, psize = pick_by_tier(rng, TELE_POOL, 2)
        cams.append(cam("periscope", 0, sens[(pmp, psize)], pmp, round(rng.uniform(2.5, 3.4), 1),
                        rng.randint(110, 135), rng.randint(17, 22), True, "pdaf", rng, tier))
    if tier == 1 and rng.random() < 0.25:
        tmp, tsize = pick_by_tier(rng, TELE_POOL[:5], 0)
        cams.append(cam("tele", 0, sens[(tmp, tsize)], tmp, round(rng.uniform(2.2, 2.6), 1),
                        rng.choice([50, 52, 70]), rng.randint(32, 45), rng.random() < 0.5, "pdaf", rng, tier))
    # макро и датчик глубины (у недорогих)
    has_macro = rng.random() < (0.45, 0.3, 0.0)[tier]
    if has_macro:
        mmp, msize = rng.choice(MACRO_POOL)
        cams.append(cam("macro", 0, sens[(mmp, msize)], mmp, 2.4, rng.randint(22, 25), rng.randint(70, 80), False,
                        "fixed", rng, tier, min_focus=4))
    if rng.random() < (0.4, 0.15, 0.0)[tier]:
        cams.append(cam("depth", 0, sens[(2, "1/5")], 2, 2.4, None, None, False, "fixed", rng, tier, proc=False))
    # фронтальные
    fmp, fsize = pick_by_tier(rng, FRONT_POOL, tier)
    cams.append(cam("front", 0, sens[(fmp, fsize)], fmp, round(rng.uniform(2.0, 2.4), 1), rng.randint(21, 26),
                    rng.randint(78, 90), False, "pdaf" if tier == 2 and rng.random() < 0.6 else "fixed", rng, tier))
    if form == "book_fold":
        fmp2, fsize2 = pick_by_tier(rng, FRONT_POOL, 1)
        cams.append(cam("cover", 0, sens[(fmp2, fsize2)], fmp2, round(rng.uniform(2.2, 2.4), 1), 24, 80, False,
                        "fixed", rng, tier))

    teles = [c for c in cams if c["role"] in ("tele", "periscope")]
    if has_macro:
        macro_mode, macro_cm = "dedicated", 4
    elif uw_af:
        macro_mode, macro_cm = "ultrawide_af", next(c["min_focus_cm"] for c in cams if c["role"] == "ultrawide")
    elif tele_macro:
        macro_mode, macro_cm = "tele_macro", next(c["min_focus_cm"] for c in teles if c["min_focus_cm"])
    else:
        macro_mode, macro_cm = "none", None
    if any(c["role"] == "periscope" for c in cams):
        digital = rng.choice([60, 100, 120])
    elif teles:
        digital = rng.choice([20, 30])
    else:
        digital = rng.choice([8, 10, 20])
    lossless = 4.0 if mp >= 200 else 2.0 if mp >= 50 else None
    features = {"macro_mode": macro_mode, "macro_min_focus_cm": macro_cm, "lossless_zoom_x": lossless,
                "max_digital_zoom_x": digital, "dxomark": None, "_q": "random", "_src": ["random"]}
    return cams, features


def gen_variants(rng: random.Random, os_: str, base: dict, merged: list[dict]) -> list[dict]:
    out = [{"ram_gb": base["ram"], "rom_gb": base["rom"], "price_rub": base["price"], "price_date": base["date"],
            "price_src": "project_seed", "msrp_usd": None, "is_base": True, "_q": "estimated",
            "_src": ["project_seed"]}]
    for m in merged:  # строки вида «… 512GB» из исходной базы — реальные конфигурации
        out.append({"ram_gb": m["ram"], "rom_gb": m["rom"], "price_rub": m["price"], "price_date": m["date"],
                    "price_src": "project_seed", "msrp_usd": None, "is_base": False, "_q": "estimated",
                    "_src": ["project_seed"]})
    have = {(v["ram_gb"], v["rom_gb"]) for v in out}
    price, rom, ram = base["price"], base["rom"], base["ram"]
    for _ in range(rng.choice([0, 1, 1, 2, 2, 3])):
        if rom >= 1024:
            break
        rom = rom * 2
        price = price * (1 + rng.uniform(0.10, 0.18))
        if os_ != "iOS" and rng.random() < 0.4 and ram in RAM_STEPS and RAM_STEPS.index(ram) + 1 < len(RAM_STEPS):
            ram = RAM_STEPS[RAM_STEPS.index(ram) + 1]
            price *= 1 + rng.uniform(0.04, 0.08)
        if (ram, rom) in have:
            continue
        have.add((ram, rom))
        out.append({"ram_gb": ram, "rom_gb": rom, "price_rub": round_price(price), "price_date": TODAY,
                    "price_src": "random", "msrp_usd": None, "is_base": False, "_q": "random", "_src": ["random"]})
    out.sort(key=lambda v: (v["ram_gb"], v["rom_gb"]))
    return out


def form_factor(name: str, weight: float) -> str:
    low = name.lower()
    if "trifold" in low:
        return "trifold"
    if "fold" in low:
        return "book_fold"
    if "flip" in low or "razr" in low:
        return "flip"
    if weight >= 300:
        return "rugged"
    return "bar"


def special_rules(brand: str, name: str, form: str, screen: float) -> list[dict]:
    """Категории «интересных возможностей» по правилам из названия и характеристик (оценка, не случайность)."""
    low, out = name.lower(), []

    def add(code, note=None):
        out.append({"code": code, "note_ru": note, "_q": "estimated"})

    if form in ("trifold", "book_fold", "flip", "rugged"):
        add(form)
    if "redmagic" in low:
        add("active_cooling", "Встроенный вентилятор (по правилу для серии REDMAGIC — проверить модель).")
    if "redmagic" in low or "rog phone" in low:
        add("gaming_triggers")
    if brand.lower() == "nothing" and "phone (" in low and "cmf" not in low:
        add("led_interface", "Glyph-подсветка на спинке (по правилу для серии Nothing Phone — проверить модель).")
    if re.search(r"galaxy s2\d ultra", low):
        add("stylus", "S Pen в корпусе (по правилу для Galaxy S Ultra — проверить модель).")
    if "fairphone" in low:
        add("repairable")
    if form == "bar" and screen <= 6.1:
        add("compact")
    return out


# «приколы» со слов пользователя: записываются как гипотезы, которые надо проверить по ТЗ
def user_hypotheses(brand: str, name: str, year: int) -> list[dict]:
    low, out = name.lower(), []
    if re.search(r"poco x8 pro( max)?$", low):
        out.append({"code": "yellow_tint", "severity": 2,
                    "note_ru": "Гипотеза пользователя: сильно желтит экран. Проверить замером точки белого (≤ 6000 K) "
                               "или ≥ 2 обзорами.", "evidence": [], "_q": "user_hypothesis"})
    if brand.lower() == "google" and "pixel" in low:
        out.append({"code": "overheating", "severity": 2,
                    "note_ru": "Гипотеза пользователя: Pixel сильно греются (чипы Tensor). Проверить по каждой модели "
                               "отдельно.", "evidence": [], "_q": "user_hypothesis"})
    if brand.lower() == "samsung" and year >= 2024:
        out.append({"code": "good_ai_assistant", "severity": 2,
                    "note_ru": "Со слов пользователя: хороший встроенный ИИ-ассистент (Galaxy AI). Проверить список "
                               "функций для модели и что работает в РФ.", "evidence": [], "_q": "user_hypothesis"})
    return out


def gen_quirks(rng: random.Random, qtypes: list[dict], form: str, fixed: list[dict]) -> list[dict]:
    taken_cats = {next(t["category"] for t in qtypes if t["code"] == q["code"]) for q in fixed}
    pool = [t for t in qtypes if t["category"] not in taken_cats
            and (t["code"] != "hinge_crease" or form in ("book_fold", "flip", "trifold"))]
    out = list(fixed)
    for t in rng.sample(pool, rng.choice([0, 1, 1, 2, 2, 3])):
        if any(t["category"] == next(x["category"] for x in qtypes if x["code"] == q["code"]) for q in out):
            continue  # не больше одного «прикола» на категорию — иначе «греется» и «не греется» вместе
        out.append({"code": t["code"], "severity": rng.randint(1, 3),
                    "note_ru": "Демо: случайная особенность для проверки интерфейса.", "evidence": [],
                    "_q": "random"})
    return out


def photo_kind(url: str) -> str:
    low = url.lower()
    for key, kind in (("back", "back"), ("rear", "back"), ("camera", "back"), ("front", "front"), ("side", "side"),
                      ("open", "open")):
        if key in low:
            return kind
    return "angle"


def placeholders(slug: str, start: int, need: int) -> list[dict]:
    return [{"url": f"/placeholder/{slug}/{n}.svg", "kind": "placeholder", "color_ru": None, "author": None,
             "license": "демо", "source_page": None, "width": 960, "height": 1280, "origin": "placeholder"}
            for n in range(start + 1, start + need + 1)]


# модели только для каталога (в подборе нейросетью пока не участвуют): есть категория — но нет в обученной модели
EXTRA_PHONES = [
    {"brand": "Samsung", "model": "Galaxy Z TriFold", "os": "Android", "year": 2025, "release_date": "2025-12",
     "status": "on_sale", "availability_ru": "not_sold", "form": "trifold", "screen": 10.0, "cover": 6.5,
     "variants": [(16, 512, None, 2450), (16, 1024, None, 2650)], "main_mp": 200, "tier": 2,
     "special_note": "Складывается втрое; продажи с 12.12.2025 в Корее, затем Китай, Тайвань, Сингапур, ОАЭ, США."},
    {"brand": "Apple", "model": "iPhone Duo", "os": "iOS", "year": 2026, "release_date": "2026-10",
     "status": "on_sale", "availability_ru": "parallel_import", "form": "book_fold", "screen": 7.6, "cover": 5.4,
     "variants": [(12, 256, 219990, 1999), (12, 512, 244990, None), (12, 1024, 279990, None)], "main_mp": 48,
     "tier": 2, "special_note": "Первый складной iPhone, представлен 9.09.2026; экраны 5,4″ и 7,6″."},
    {"brand": "Honor", "model": "Robot Phone", "os": "Android", "year": 2026, "release_date": "2026-08",
     "status": "china_only", "availability_ru": "not_sold", "form": "bar", "screen": 6.8, "cover": None,
     "variants": [(12, 512, None, 1480), (16, 1024, None, 1930)], "main_mp": 200, "tier": 2,
     "special_note": "Основная камера 200 МП на механическом подвесе; продажи в Китае с августа 2026."},
]


def main():
    qtypes = json.loads((CAT / "quirk_types.json").read_text(encoding="utf-8"))
    seed_rows = list(csv.DictReader((DATA / "phones_seed.csv").open(encoding="utf-8")))
    photos_seed = list(csv.DictReader((DATA / "phone_photos_seed.csv").open(encoding="utf-8")))
    sensors, sens = sensor_catalog()

    # базовые строки и строки-конфигурации («… 512GB») → одна модель
    groups: dict[str, dict] = {}
    for r in seed_rows:
        bmodel, suffix = base_model(r["model"])
        slug = slugify(full_name(r["brand"], bmodel))
        g = groups.setdefault(slug, {"base": None, "merged": [], "brand": r["brand"], "model": bmodel})
        src = r.get("price_source") or ""
        info = {"row": r, "ram": int(float(r["ram_gb"])), "rom": int(float(r["rom_gb"])),
                "price": int(round(float(r["price_rub"]))),
                "date": "2026-11-05" if src.startswith("Telegram") else "2026-08-01"}
        if suffix is None:
            if g["base"] is not None:
                raise SystemExit(f"две базовые строки для {slug}")
            g["base"] = info
        else:
            g["merged"].append(info)
    for slug, g in groups.items():
        if g["base"] is None:  # есть только строка с суффиксом — она и станет базовой
            g["base"] = g["merged"].pop(0)

    photos_by_slug: dict[str, list] = {}
    for p in photos_seed:
        bmodel = base_model(p["model"])[0]
        brand = next((r["brand"] for r in seed_rows if base_model(r["model"])[0] == bmodel), "")
        photos_by_slug.setdefault(slugify(full_name(brand, bmodel)), []).append(p)

    brands: dict[str, dict] = {}
    chipsets: dict[str, dict] = {}
    if (CAT / "phones").exists():
        shutil.rmtree(CAT / "phones")
    written = 0

    def write_phone(doc: dict):
        nonlocal written
        path = CAT / "phones" / doc["brand"] / f"{doc['slug']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        written += 1

    for slug in sorted(groups):
        g = groups[slug]
        r = g["base"]["row"]
        rng = random.Random(f"{SEED}:{slug}")
        brand_slug = slugify(r["brand"])
        brands.setdefault(brand_slug, {"slug": brand_slug, "name": r["brand"], "parent": None, "country": None})
        antutu = int(round(float(r["antutu_k"])))
        chip_slug = f"demo-soc-{antutu}"
        chipsets.setdefault(chip_slug, {
            "slug": chip_slug, "name": f"DEMO SoC · AnTuTu {antutu} тыс.", "vendor": "Демо",
            "process_nm": random.Random(f"{SEED}:{chip_slug}").choice([3, 4, 5, 6]), "antutu_k": antutu,
            "geekbench_sc": None, "geekbench_mc": None, "year": None, "_q": "estimated", "_src": ["project_seed"]})
        fname = full_name(r["brand"], g["model"])
        name = g["model"][len(r["brand"]):].strip() if g["model"].lower().startswith(r["brand"].lower()) else g["model"]
        weight, screen = float(r["weight_g"]), float(r["screen_in"])
        form = form_factor(fname, weight)
        price = g["base"]["price"]
        tier = 0 if price < 20_000 else 1 if price < 50_000 else 2
        cams, features = gen_cameras(rng, tier, float(r["camera_mp"]), form, sens)
        year = int(r["year"])
        fixed = user_hypotheses(r["brand"], fname, year)
        real_photos = [{"url": p["url"], "kind": photo_kind(p["url"]), "color_ru": None,
                        "author": p["author"] or None, "license": p["license"] or "?",
                        "source_page": p["source_page"] or None, "width": None, "height": None,
                        "origin": "wikimedia"}
                       for p in sorted(photos_by_slug.get(slug, []), key=lambda p: int(p["position"] or 0))]
        real_photos = list({p["url"]: p for p in reversed(real_photos)}.values())[::-1]  # без повторов ссылок
        photos = real_photos + placeholders(slug, len(real_photos), max(0, 3 - len(real_photos)))
        b = lambda k: bool(int(float(r[k] or 0)))  # noqa: E731
        write_phone({
            "slug": slug, "brand": brand_slug, "name": name, "full_name": fname,
            "aliases": sorted({x["row"]["model"] for x in g["merged"]}),
            "os": r["os"], "release_date": None, "year": year, "status": "on_sale",
            "availability_ru": "parallel_import" if brand_slug in PARALLEL_IMPORT else "official",
            "form_factor": form, "in_ml": True, "chipset": chip_slug,
            "specs": {
                "screen_in": screen, "cover_screen_in": None, "refresh_hz": int(float(r["refresh_hz"])),
                "display_type": None, "peak_nits": None, "battery_mah": int(float(r["battery_mah"])),
                "charge_w": float(r["charge_w"]), "wireless_w": None, "weight_g": weight, "thickness_mm": None,
                "ip_rating": None, "has_5g": b("has_5g"), "nfc": b("nfc"), "esim": b("esim"), "ir_port": b("ir_port"),
                "jack35": None, "sd_card": None, "update_years": None, "_q": "estimated", "_src": ["project_seed"]},
            "variants": gen_variants(rng, r["os"], g["base"], g["merged"]),
            "cameras": cams, "camera_features": features,
            "quirks": gen_quirks(rng, qtypes, form, fixed), "quirks_checked": False,
            "special": special_rules(r["brand"], fname, form, screen),
            "photos": photos,
            "_meta": {"created": TODAY, "updated": TODAY, "filled_by": "tools/generate_random_catalog.py"},
        })

    for e in EXTRA_PHONES:
        slug = slugify(full_name(e["brand"], e["model"]))
        if slug in groups:
            continue
        rng = random.Random(f"{SEED}:{slug}")
        brand_slug = slugify(e["brand"])
        brands.setdefault(brand_slug, {"slug": brand_slug, "name": e["brand"], "parent": None, "country": None})
        cams, features = gen_cameras(rng, e["tier"], e["main_mp"], e["form"], sens)
        variants = [{"ram_gb": ram, "rom_gb": rom, "price_rub": rub, "price_date": TODAY if rub else None,
                     "price_src": "random" if rub else None, "msrp_usd": usd, "is_base": k == 0,
                     "_q": "random", "_src": ["random"]}
                    for k, (ram, rom, rub, usd) in enumerate(e["variants"])]
        special = [{"code": e["form"] if e["form"] != "bar" else "gimbal_camera", "note_ru": e["special_note"],
                    "_q": "verified"}]
        write_phone({
            "slug": slug, "brand": brand_slug, "name": e["model"], "full_name": full_name(e["brand"], e["model"]),
            "aliases": [], "os": e["os"], "release_date": e["release_date"], "year": e["year"],
            "status": e["status"], "availability_ru": e["availability_ru"], "form_factor": e["form"],
            "in_ml": False, "chipset": None,
            "specs": {
                "screen_in": e["screen"], "cover_screen_in": e["cover"], "refresh_hz": 120, "display_type": None,
                "peak_nits": None, "battery_mah": rng.choice([4800, 5000, 5400, 5600, 6000]),
                "charge_w": float(rng.choice([25, 45, 66, 80])), "wireless_w": None,
                "weight_g": float(rng.randint(230, 320)), "thickness_mm": None, "ip_rating": None,
                "has_5g": True, "nfc": True, "esim": rng.random() < 0.6, "ir_port": False, "jack35": None,
                "sd_card": None, "update_years": None, "_q": "random", "_src": ["random"]},
            "variants": variants, "cameras": cams, "camera_features": features,
            "quirks": gen_quirks(rng, qtypes, e["form"], user_hypotheses(e["brand"], e["model"], e["year"])),
            "quirks_checked": False, "special": special, "photos": placeholders(slug, 0, 3),
            "_meta": {"created": TODAY, "updated": TODAY, "filled_by": "tools/generate_random_catalog.py"},
        })

    def dump(name, obj):
        (CAT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    dump("brands.json", sorted(brands.values(), key=lambda b: b["slug"]))
    dump("chipsets.json", sorted(chipsets.values(), key=lambda c: c["antutu_k"]))
    dump("sensors.json", sensors)

    # история цен: строки исходной базы → конфигурация (базовая или склеенная «… 512GB»)
    mem = {}
    for slug, g in groups.items():
        for info in [g["base"], *g["merged"]]:
            mem[info["row"]["model"]] = (slug, info["ram"], info["rom"])
    src_map = {"telegram:ishopper": "telegram_ishopper", "telegram:polzaskidki": "telegram_polzaskidki"}
    rows = []
    for h in csv.DictReader((DATA / "price_history_seed.csv").open(encoding="utf-8")):
        if h["model"] in mem:
            slug, ram, rom = mem[h["model"]]
            rows.append((slug, ram, rom, h["date"], src_map.get(h["source"], "project_seed"),
                         int(round(float(h["price_rub"])))))
    rows.sort()
    with (CAT / "price_history.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["slug", "ram_gb", "rom_gb", "date", "source", "price_rub"])
        w.writerows(rows)
    print(f"моделей: {written} (из них только в каталоге: {written - len(groups)}), брендов: {len(brands)}, "
          f"сенсоров: {len(sensors)}, чипов: {len(chipsets)}, точек истории цен: {len(rows)}")


if __name__ == "__main__":
    main()
