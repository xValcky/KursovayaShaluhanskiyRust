"""Сборка файлов каталога v2 из компактных таблиц tools/catalog_data/*.txt (настоящие данные вместо рандом-версии).

Запуск:  python tools/import_catalog_data.py
Пишет:   data/catalog/phones/<бренд>/<slug>.json, chipsets.json, sensors.json, brands.json, sources.json (дополняет)

Откуда данные и как они помечены (раздел 0 ТЗ):
- таблицы заполнены нейросетью-исполнителем по своим сведениям (до 2026-06) → `_q: "estimated"`, `_src: ["ai_knowledge"]`;
  это НЕ проверка по странице, поэтому `verified` здесь не ставится никогда;
- поля старой базы (data/phones_seed.csv: базовая ОЗУ/ПЗУ/цена) у 282 моделей сохраняются как были (`project_seed`);
- `verified` появляется позже, отдельными скриптами, только со ссылкой на страницу (tools/verify_wikipedia.py,
  tools/fetch_commons_photos.py, tools/match_telegram_prices.py).

Формат строки модели (поля через « ; », пустое поле — «-»):
  0 имя            Galaxy S25 Ultra
  1 выпуск         2025-02 [disc|cn|ann] [ru=off|par|no] [ff=book|flip|tri|rug|other] [os=hm|and] [ml=0]
  2 чип            slug из chipsets (короткий ключ)
  3 экран          6.9 120 LTPO-AMOLED 2600      (складные: 8.0/6.5 — внутренний/внешний)
  4 батарея        5000 45 15                     (мА·ч, провод Вт, беспровод Вт; 0 — нет, - — неизвестно)
  5 корпус         218 8.2 IP68                   (г, мм, IP; - — неизвестно/нет)
  6 флаги          5g nfc esim ir jack sd u7      (u7 — лет обновлений)
  7 память         12/256*$1299 12/512$1419 12/1T  (* — базовая, $ — MSRP, r — цена ₽ оценкой)
  8 камеры         M200/1.7/24/O/pd/@hp2:88 U50/1.9/13/pd:80 P50/3.4/111/O/pd/x5:84 F12/2.2/26:80
                   роли: M main, U ultrawide, T tele, P periscope, C macro, D depth, B mono, F front,
                   G front_ultrawide, V cover; поля: МП/f/ЭФР мм, далее O (OIS), pd|dp|la|cd|ff|na (автофокус),
                   @сенсор, x3 (кратность), v4K60 (видео), mf5 (мин. фокус, см), :88 — обработка 0–100
  9 фишки камеры   uw|ded|tele|none dz100 ll2 mf4
 10 прочее         q:code/2,code/1  s:stylus,compact  a:Псевдоним|Ещё  n:code=текст заметки
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "data" / "catalog"
SRC_DIR = ROOT / "tools" / "catalog_data"
TODAY = "2026-11-21"
AI = "ai_knowledge"

BRANDS = {  # slug: (имя, родитель, страна, доступность в РФ по умолчанию)
    "apple": ("Apple", None, "США", "parallel_import"),
    "samsung": ("Samsung", None, "Южная Корея", "parallel_import"),
    "xiaomi": ("Xiaomi", None, "Китай", "official"),
    "poco": ("POCO", "xiaomi", "Китай", "official"),
    "honor": ("Honor", None, "Китай", "official"),
    "huawei": ("Huawei", None, "Китай", "official"),
    "realme": ("realme", None, "Китай", "official"),
    "oppo": ("OPPO", None, "Китай", "official"),
    "oneplus": ("OnePlus", None, "Китай", "parallel_import"),
    "vivo": ("vivo", None, "Китай", "official"),
    "iqoo": ("iQOO", "vivo", "Китай", "parallel_import"),
    "google": ("Google", None, "США", "parallel_import"),
    "nothing": ("Nothing", None, "Великобритания", "parallel_import"),
    "motorola": ("Motorola", None, "США / Китай", "parallel_import"),
    "sony": ("Sony", None, "Япония", "parallel_import"),
    "asus": ("ASUS", None, "Тайвань", "parallel_import"),
    "zte": ("ZTE", None, "Китай", "official"),
    "nubia": ("nubia", "zte", "Китай", "official"),
    "redmagic": ("REDMAGIC", "nubia", "Китай", "parallel_import"),
    "tecno": ("Tecno", None, "Китай (Transsion)", "official"),
    "infinix": ("Infinix", None, "Китай (Transsion)", "official"),
    "itel": ("itel", None, "Китай (Transsion)", "official"),
    "meizu": ("Meizu", None, "Китай", "official"),
    "lenovo": ("Lenovo", None, "Китай", "parallel_import"),
    "fairphone": ("Fairphone", None, "Нидерланды", "parallel_import"),
    "hmd": ("HMD", None, "Финляндия", "parallel_import"),
    "nokia": ("Nokia", None, "Финляндия", "parallel_import"),
    "tcl": ("TCL", None, "Китай", "official"),
    "blackview": ("Blackview", None, "Китай", "official"),
    "ulefone": ("Ulefone", None, "Китай", "official"),
    "doogee": ("DOOGEE", None, "Китай", "official"),
    "oukitel": ("OUKITEL", None, "Китай", "official"),
    "cubot": ("Cubot", None, "Китай", "official"),
    "unihertz": ("Unihertz", None, "Китай", "parallel_import"),
    "cat": ("Cat", None, "Великобритания (Bullitt)", "parallel_import"),
    "hammer": ("Hammer", None, "Польша", "parallel_import"),
    "agm": ("AGM", None, "Китай", "parallel_import"),
    "umidigi": ("UMIDIGI", None, "Китай", "official"),
    "black-shark": ("Black Shark", None, "Китай", "parallel_import"),
    "sharp": ("Sharp", None, "Япония", "not_sold"),
    "kyocera": ("Kyocera", None, "Япония", "not_sold"),
    "bq": ("BQ", None, "Россия", "official"),
    "vertex": ("Vertex", None, "Россия", "official"),
    "alcatel": ("Alcatel", None, "Китай (TCL)", "official"),
    "oscal": ("Oscal", "blackview", "Китай", "official"),
    "hotwav": ("HOTWAV", None, "Китай", "official"),
    "fossibot": ("FOSSiBOT", None, "Китай", "official"),
    "iiif150": ("IIIF150", None, "Китай", "official"),
}
RUGGED_BRANDS = {"blackview", "ulefone", "doogee", "oukitel", "cat", "hammer", "agm", "hotwav", "fossibot", "iiif150"}

NEW_SOURCES = [
    {"id": AI, "name": "Сведения нейросети-исполнителя (Claude Opus 5.5, знания до 2026-06) — без сверки со страницей",
     "url": None, "kind": "project", "reliability": 2},
    {"id": "price_model", "name": "Оценка цены конфигурации: пересчёт от базовой по MSRP или шагу памяти",
     "url": None, "kind": "project", "reliability": 1},
    {"id": "wikidata", "name": "Wikidata (CC0)", "url": "https://www.wikidata.org", "kind": "wiki", "reliability": 3},
    {"id": "macrumors", "name": "MacRumors", "url": "https://www.macrumors.com", "kind": "review", "reliability": 3},
    {"id": "techrepublic", "name": "TechRepublic", "url": "https://www.techrepublic.com", "kind": "review", "reliability": 3},
    {"id": "mobiles91", "name": "91mobiles", "url": "https://www.91mobiles.com", "kind": "spec_db", "reliability": 3},
    {"id": "beebom", "name": "Beebom", "url": "https://beebom.com", "kind": "review", "reliability": 3},
]

ROLE = {"M": "main", "U": "ultrawide", "T": "tele", "P": "periscope", "C": "macro", "D": "depth", "B": "mono",
        "F": "front", "G": "front_ultrawide", "V": "cover"}
AF = {"pd": "pdaf", "dp": "dual_pixel", "la": "laser", "cd": "contrast", "ff": "fixed", "na": "none"}
MACRO = {"uw": "ultrawide_af", "ded": "dedicated", "tele": "tele_macro", "none": "none"}
FF = {"book": "book_fold", "flip": "flip", "tri": "trifold", "rug": "rugged", "other": "other", "bar": "bar"}
DISPLAY = {"IPS": "IPS LCD", "LCD": "IPS LCD", "TFT": "TFT LCD", "OLED": "OLED", "AMOLED": "AMOLED"}
RAM_OK = {1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 20, 24, 32}
ROM_OK = {16, 32, 64, 128, 256, 512, 1024, 2048}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("+", " plus ")).strip("-")


def full_name(brand_name: str, name: str) -> str:
    return name if name.lower().startswith(brand_name.lower()) else f"{brand_name} {name}"


def num(s: str):
    if s in ("-", "", "?"):
        return None
    v = float(s)
    return int(v) if v.is_integer() else v


class Bad(ValueError):
    pass


# ── справочники ──
def load_table(name: str) -> list[list[str]]:
    rows = []
    for line in (SRC_DIR / name).read_text(encoding="utf-8").splitlines():
        line = line.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            rows.append([c.strip() for c in line.split("|")])
    return rows


def chipsets() -> tuple[list[dict], dict[str, str]]:
    out, key2slug = [], {}
    for r in load_table("chipsets.txt"):
        key, name, vendor, nm, ant, sc, mc, year = (r + ["-"] * 8)[:8]
        slug = slugify(name)
        key2slug[key] = slug
        out.append({"slug": slug, "name": name, "vendor": vendor, "process_nm": num(nm), "antutu_k": num(ant),
                    "geekbench_sc": num(sc), "geekbench_mc": num(mc), "year": num(year), "_q": "estimated",
                    "_src": [f"{AI}:antutu-v10", f"{AI}:geekbench-6"] if num(sc) else [f"{AI}:antutu-v10"]})
    return out, key2slug


def sensors() -> tuple[list[dict], dict[str, dict]]:
    out, key2 = [], {}
    for r in load_table("sensors.txt"):
        key, name, vendor, mp, size, pix, tech = (r + ["-"] * 7)[:7]
        slug = slugify(name)
        item = {"slug": slug, "name": name, "vendor": vendor, "mp": num(mp), "size_inch": size, "diag_mm": None,
                "pixel_um": num(pix), "tech": [t for t in tech.split(",") if t and t != "-"], "_q": "estimated",
                "_src": [AI]}
        key2[key] = item
        out.append(item)
    return out, key2


# ── строки моделей ──
VAR_RE = re.compile(r"^(\d+)/(\d+)(T?)(\*?)(?:\$(\d+))?(?:r(\d+))?(\*?)$")
CAM_RE = re.compile(r"^([MUTPCDBFGV])([\d.]+)$")


def parse_variants(field: str):
    out = []
    for tok in field.split():
        m = VAR_RE.match(tok)
        if not m:
            raise Bad(f"память: «{tok}»")
        ram, rom, tb, star1, usd, rub, star2 = m.groups()
        rom_gb = int(rom) * 1024 if tb else int(rom)
        if int(ram) not in RAM_OK or rom_gb not in ROM_OK:
            raise Bad(f"память: недопустимо {tok}")
        out.append({"ram_gb": int(ram), "rom_gb": rom_gb, "msrp_usd": int(usd) if usd else None,
                    "price_rub": int(rub) if rub else None, "is_base": bool(star1 or star2)})
    if out and not any(v["is_base"] for v in out):
        out[0]["is_base"] = True
    if sum(v["is_base"] for v in out) != 1:
        raise Bad("память: базовая конфигурация должна быть ровно одна")
    return out


def parse_cameras(field: str, sensor_keys: dict):
    cams, pos = [], {}
    for tok in field.split():
        score = None
        if ":" in tok:
            tok, sc = tok.rsplit(":", 1)
            score = int(sc)
        parts = tok.split("/")
        m = CAM_RE.match(parts[0])
        if not m or len(parts) < 3:
            raise Bad(f"камера: «{tok}»")
        role = ROLE[m.group(1)]
        c = {"role": role, "position": pos.get(role, 0), "sensor": None, "mp": num(m.group(2)),
             "aperture": num(parts[1]), "focal_mm": num(parts[2]), "fov_deg": None, "optical_zoom_x": None,
             "ois": False, "af": None, "min_focus_cm": None, "video_max": None, "processing_score": score,
             "processing_basis": "reviews_rubric" if score is not None else None, "_q": "estimated", "_src": [AI]}
        pos[role] = pos.get(role, 0) + 1
        for o in parts[3:]:
            if o == "O":
                c["ois"] = True
            elif o in AF:
                c["af"] = AF[o]
            elif o.startswith("@"):
                k = o[1:]
                if k not in sensor_keys:
                    raise Bad(f"камера: нет сенсора «{k}» в sensors.txt")
                c["sensor"] = sensor_keys[k]["slug"]
            elif re.fullmatch(r"x[\d.]+", o):
                c["optical_zoom_x"] = float(o[1:])
            elif re.fullmatch(r"v(720p|1080p|4K|8K)\d*", o):
                c["video_max"] = o[1:]
            elif re.fullmatch(r"mf[\d.]+", o):
                c["min_focus_cm"] = num(o[2:])
            else:
                raise Bad(f"камера: непонятная опция «{o}» в {tok}")
        if c["af"] is None:
            c["af"] = "fixed" if role in ("depth", "macro", "front", "front_ultrawide", "cover") else "pdaf"
        if role == "depth":
            c["processing_score"], c["processing_basis"] = None, None
        if c["mp"] is not None and c["mp"] < 0.3:
            raise Bad(f"камера: {tok}")
        cams.append(c)
    if sum(c["role"] == "main" for c in cams) != 1:
        raise Bad("камеры: нужна ровно одна main")
    return cams


QUIRK_NOTE = {
    "long_updates": "{u} лет обновлений по официальной политике {b}.",
    "short_updates": "По политике {b} — мало крупных обновлений ОС ({u} г.).",
    "no_google_services": "Нет сервисов Google (Play, Pay, Gmail): приложения — через AppGallery и сторонние магазины.",
    "ads_in_system": "Реклама в системных приложениях прошивки; часть отключается в настройках.",
    "bloatware": "Много предустановленных приложений и игр, часть удаляется.",
    "clean_android": "Почти чистый Android без лишних оболочек.",
    "desktop_mode": "Режим ПК при подключении к монитору.",
    "bypass_charging": "Питание в обход батареи в играх — меньше нагрев и износ.",
    "good_ai_assistant": "Набор ИИ-функций производителя: перевод звонков, правка фото, сводки; часть зависит от региона.",
    "ai_limited_ru": "Часть ИИ-функций не работает в РФ без смены региона/аккаунта.",
    "great_video": "Обзоры отмечают сильное видео (стабилизация, динамический диапазон).",
    "great_portraits": "Обзоры отмечают удачные портреты и отделение фона.",
    "excellent_battery": "В тестах автономности держится заметно дольше конкурентов.",
    "excellent_display": "Яркий и точный экран по замерам обзоров.",
    "loud_stereo": "Громкие стереодинамики.",
    "weak_speaker": "Тихий или один динамик.",
    "overheating": "Заметно греется под нагрузкой и при съёмке.",
    "throttling": "Под долгой нагрузкой сбрасывает частоты.",
    "cool_running": "Под нагрузкой остаётся прохладным.",
    "pwm_flicker": "ШИМ-мерцание на низкой яркости.",
    "yellow_tint": "Экран заметно желтит в стандартном режиме.",
    "green_tint": "Зелёный оттенок на низкой яркости.",
    "dim_display": "Тусклый экран на солнце.",
    "weak_modem": "Жалобы на слабую связь и прыжки сети.",
    "gps_issues": "Жалобы на работу GPS.",
    "buggy_firmware": "Ошибки прошивки на старте продаж.",
    "shutter_lag": "Задержка затвора при съёмке движущихся объектов.",
    "oversharpening": "Перешарп и пересвет в обработке фото.",
    "inconsistent_colors": "Разные камеры снимают в разных цветах.",
    "fragile_build": "Хрупкий корпус или экран.",
    "hinge_crease": "Заметная складка внутреннего экрана.",
    "excellent_haptics": "Чёткая и приятная вибрация.",
    "standby_drain": "Повышенный расход батареи в режиме ожидания.",
}
SPECIAL_NOTE = {
    "trifold": "Два шарнира и три секции экрана.",
    "book_fold": "Складывается книжкой: большой внутренний экран и внешний.",
    "flip": "Раскладушка с внешним экраном.",
    "stylus": "Встроенный стилус с местом в корпусе.",
    "active_cooling": "Встроенный вентилятор охлаждения.",
    "gaming_triggers": "Сенсорные плечевые триггеры для игр.",
    "rugged": "Защищённый корпус.",
    "thermal_camera": "Встроенный тепловизор.",
    "night_vision": "Камера ночного видения с ИК-подсветкой.",
    "projector": "Встроенный проектор.",
    "led_interface": "Светодиодная подсветка на спинке (Glyph).",
    "rear_display": "Второй экран на спинке.",
    "satellite": "Спутниковая связь (сообщения/вызовы); в РФ не работает.",
    "e_ink": "Экран E-Ink.",
    "qwerty": "Физическая клавиатура.",
    "compact": "Компактный моноблок с экраном до 6,1″.",
    "repairable": "Модульная конструкция, легко ремонтировать.",
    "camera_kit": "Официальный фотокит: рукоятка с кнопкой затвора и насадки.",
    "gimbal_camera": "Камера на механическом подвесе, выдвигается из корпуса.",
}


def parse_line(brand: str, line: str, sensor_keys: dict, chip_keys: dict, seed: dict, existing: dict,
               telegram_names: set[str]):
    f = [x.strip() for x in line.split(" ; ")]
    if len(f) < 10:
        raise Bad(f"полей {len(f)}, нужно ≥ 10")
    f += ["-"] * (11 - len(f))
    bname, _, _, avail_default = BRANDS[brand]
    name = f[0]
    fname = full_name(bname, name)
    slug = slugify(fname)
    # выпуск и флаги
    rel = f[1].split()
    date = rel[0]
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", date)
    if not m:
        raise Bad(f"дата «{date}»")
    year = int(m.group(1))
    release = date if m.group(2) else None
    opts = dict(o.split("=", 1) if "=" in o else (o, "1") for o in rel[1:])
    opts.update(o.split("=", 1) for o in f[6].split() if re.fullmatch(r"(ff|ru|ml|os)=\w+", o))  # можно и в поле флагов
    status = "on_sale" if year >= 2024 else "discontinued"
    if slug in existing and year == 2023:
        status = "on_sale"
    for o, st in (("disc", "discontinued"), ("cn", "china_only"), ("ann", "announced"), ("sale", "on_sale")):
        if o in opts:
            status = st
    avail = {"off": "official", "par": "parallel_import", "no": "not_sold"}.get(opts.get("ru"), avail_default)
    if "ru" not in opts and status == "china_only":
        avail = "parallel_import"
    if "seed" in opts:
        opts["src"] = "project_seed"
    form = FF[opts.get("ff", "rug" if brand in RUGGED_BRANDS else "bar")]
    os_ = "iOS" if brand == "apple" else ("HarmonyOS" if opts.get("os") == "hm" else "Android")
    chip = None if f[2] == "-" else chip_keys.get(f[2])
    if f[2] != "-" and chip is None:
        raise Bad(f"нет чипа «{f[2]}» в chipsets.txt")
    # экран
    d = f[3].split()
    scr = d[0].split("/")
    screen, cover = float(scr[0]), (float(scr[1]) if len(scr) > 1 else None)
    hz = int(d[1])
    dtype = DISPLAY.get(d[2], d[2].replace("-", " ")) if len(d) > 2 and d[2] != "-" else None
    nits = num(d[3]) if len(d) > 3 else None
    b = f[4].split()
    bat, chg = int(b[0]), num(b[1]) if len(b) > 1 else None
    wl = num(b[2]) if len(b) > 2 else None
    body = f[5].split()
    weight = num(body[0])
    thick = num(body[1]) if len(body) > 1 else None
    ip = body[2] if len(body) > 2 and body[2] != "-" else None
    fl = {t for t in f[6].split() if "=" not in t}
    upd = next((int(t[1:]) for t in fl if re.fullmatch(r"u\d+", t)), None)
    specs = {"screen_in": screen, "cover_screen_in": cover, "refresh_hz": hz, "display_type": dtype,
             "peak_nits": nits, "battery_mah": bat, "charge_w": chg if chg is not None else 0, "wireless_w": wl,
             "weight_g": weight, "thickness_mm": thick, "ip_rating": ip, "has_5g": "5g" in fl, "nfc": "nfc" in fl,
             "esim": "esim" in fl, "ir_port": "ir" in fl, "jack35": "jack" in fl, "sd_card": "sd" in fl,
             "update_years": upd, "_q": "estimated", "_src": [AI]}
    variants = parse_variants(f[7])
    cams = parse_cameras(f[8], sensor_keys)
    # фишки камеры
    cf = {"macro_mode": "none", "macro_min_focus_cm": None, "lossless_zoom_x": None, "max_digital_zoom_x": None,
          "dxomark": None, "_q": "estimated", "_src": [AI]}
    for t in f[9].split():
        if t in MACRO:
            cf["macro_mode"] = MACRO[t]
        elif re.fullmatch(r"dz\d+", t):
            cf["max_digital_zoom_x"] = int(t[2:])
        elif re.fullmatch(r"ll[\d.]+", t):
            cf["lossless_zoom_x"] = num(t[2:])
        elif re.fullmatch(r"mf[\d.]+", t):
            cf["macro_min_focus_cm"] = num(t[2:])
        elif t != "-":
            raise Bad(f"фишки камеры: «{t}»")
    # прочее
    quirks, special, aliases, notes = [], [], [], {}
    extra = f[10]
    web = []
    for part in re.findall(r"(q|s|a|n|w):((?:[^ ]| (?![qsanw]:))+)", extra):
        k, v = part
        if k == "q":
            for item in v.split(","):
                code, _, sev = item.partition("/")
                quirks.append((code.strip(), int(sev or 1)))
        elif k == "s":
            special += [x.strip() for x in v.split(",") if x.strip()]
        elif k == "a":
            aliases += [x.strip() for x in v.split("|") if x.strip()]
        elif k == "n":
            code, _, text = v.partition("=")
            notes[code.strip()] = text.strip()
        elif k == "w":
            web += [x.strip() for x in v.split(",") if x.strip()]
    if "seed" in opts:  # модель вышла после даты знаний нейросети: только поля старой базы и найденное в сети
        specs["_src"] = ["project_seed"] + web
        for c in cams:
            c["_src"] = ["project_seed"] + web if c["role"] == "main" else (web or ["project_seed"])
        if f[9].strip() == "-":
            cf.update({"_q": "unknown", "_src": []})
    elif "web" in opts and web:  # данные со страниц из сети (поле w:), не по памяти
        q = "verified" if "vf" in opts else "estimated"  # vf — страница производителя загружена и прочитана
        for b in [specs, cf] + cams:
            b["_src"], b["_q"] = list(web), q
        for c in cams:
            if c["processing_score"] is None:
                c["processing_basis"] = None
    elif web:
        specs["_src"] = [AI] + web
    if form in ("book_fold", "flip", "trifold", "rugged") and form not in special:
        special.insert(0, form)
    if form == "bar" and screen <= 6.1 and "compact" not in special:
        special.append("compact")
    return {
        "slug": slug, "brand": brand, "name": name, "full_name": fname, "aliases": aliases, "os": os_,
        "release_date": release, "year": year, "status": status, "availability_ru": avail, "form_factor": form,
        "in_ml": None if opts.get("ml") != "0" else False, "chipset": chip, "specs": specs, "variants": variants,
        "cameras": cams, "camera_features": cf, "quirks_raw": quirks, "special_raw": special, "notes": notes,
    }


def finish(p: dict, tg: dict | None, old: dict | None, quirk_codes: set, special_codes: set, bname: str):
    """Дособирает файл модели: цены, базовая конфигурация из старой базы, «приколы», категории."""
    variants = p["variants"]
    if old is not None:  # существующая модель: имя, slug и in_ml не меняются (правило 11 ТЗ)
        for k in ("slug", "brand", "name", "full_name", "in_ml"):
            p[k] = old[k]
        seed_vars = [v for v in old["variants"] if "project_seed" in (v.get("_src") or [])]
        for sv in seed_vars:
            mine = next((v for v in variants if (v["ram_gb"], v["rom_gb"]) == (sv["ram_gb"], sv["rom_gb"])), None)
            if mine is None:
                mine = {"ram_gb": sv["ram_gb"], "rom_gb": sv["rom_gb"], "msrp_usd": None, "price_rub": None,
                        "is_base": False}
                variants.append(mine)
            mine["seed"] = sv
        if seed_vars:
            base_pair = (seed_vars[0]["ram_gb"], seed_vars[0]["rom_gb"])
            for sv in seed_vars:
                if sv.get("is_base"):
                    base_pair = (sv["ram_gb"], sv["rom_gb"])
            for v in variants:
                v["is_base"] = (v["ram_gb"], v["rom_gb"]) == base_pair
    variants.sort(key=lambda v: (v["ram_gb"], v["rom_gb"]))
    base = next(v for v in variants if v["is_base"])
    out_vars = []
    for v in variants:
        sv = v.get("seed")
        if sv is not None:
            row = {"ram_gb": v["ram_gb"], "rom_gb": v["rom_gb"], "price_rub": sv["price_rub"],
                   "price_date": sv["price_date"], "price_src": sv["price_src"], "msrp_usd": v["msrp_usd"],
                   "is_base": v["is_base"], "_q": sv["_q"], "_src": sv["_src"] + ([AI] if v["msrp_usd"] else [])}
        elif v["price_rub"] is not None:
            row = {"ram_gb": v["ram_gb"], "rom_gb": v["rom_gb"], "price_rub": v["price_rub"], "price_date": None,
                   "price_src": AI, "msrp_usd": v["msrp_usd"], "is_base": v["is_base"], "_q": "estimated",
                   "_src": [AI]}
        else:
            row = {"ram_gb": v["ram_gb"], "rom_gb": v["rom_gb"], "price_rub": None, "price_date": None,
                   "price_src": None, "msrp_usd": v["msrp_usd"], "is_base": v["is_base"], "_q": "unknown",
                   "_src": [AI] if v["msrp_usd"] else []}
        out_vars.append(row)
    # свежие цены из публичных Telegram-каналов проекта — с датой и ссылкой на пост (tools/match_telegram_prices.py)
    for r in out_vars:
        t = (tg or {}).get(f"{r['ram_gb']}/{r['rom_gb']}")
        if t and p["availability_ru"] != "not_sold":
            r.update({"price_rub": t["price"], "price_date": t["date"], "price_src": t["source"], "_q": "verified",
                      "_src": [t["post"]] + ([AI] if r.get("msrp_usd") else [])})
    # цены остальных конфигураций — пересчётом от базовой (честно помечено price_model)
    brow = next(r for r in out_vars if r["is_base"])
    if p["availability_ru"] != "not_sold" and brow["price_rub"]:
        for r, v in zip(out_vars, variants):
            if r["price_rub"] is None:
                if v["msrp_usd"] and base["msrp_usd"]:
                    k = v["msrp_usd"] / base["msrp_usd"]
                else:
                    steps = max(0, (v["rom_gb"].bit_length() - base["rom_gb"].bit_length()))
                    k = 1 + 0.12 * steps + 0.05 * max(0, (v["ram_gb"] - base["ram_gb"]) / 4)
                    if v["rom_gb"] < base["rom_gb"] or v["ram_gb"] < base["ram_gb"]:
                        k = 0.9 ** max(1, base["rom_gb"].bit_length() - v["rom_gb"].bit_length())
                r.update({"price_rub": int(round(brow["price_rub"] * k / 100.0)) * 100 - 10 if k != 1 else brow["price_rub"],
                          "price_date": None, "price_src": "price_model", "_q": "estimated",
                          "_src": ["price_model"] + ([AI] if v["msrp_usd"] else [])})
    if p["availability_ru"] == "not_sold":
        for r in out_vars:
            if r.get("price_src") in (AI, "price_model"):
                r.update({"price_rub": None, "price_date": None, "price_src": None, "_q": "unknown"})
    p["variants"] = out_vars
    if old is None and p["status"] == "discontinued" and not brow["price_rub"] and p["availability_ru"] != "official":
        p["availability_ru"] = "not_sold"
    if old is None and p["status"] == "discontinued" and not brow["price_rub"]:
        p["availability_ru"] = "not_sold"
    if p["in_ml"] is None:
        p["in_ml"] = p["availability_ru"] != "not_sold" and bool(brow["price_rub"]) and p["status"] != "announced"
    # «приколы» — из сведений нейросети, без ссылок: estimated, quirks_checked = false
    quirks = []
    for code, sev in p.pop("quirks_raw"):
        if code not in quirk_codes:
            raise Bad(f"нет «прикола» {code}")
        note = p["notes"].get(code) or QUIRK_NOTE.get(code, code)
        note = note.format(u=p["specs"]["update_years"] or "?", b=bname)
        quirks.append({"code": code, "severity": sev, "note_ru": note, "evidence": [], "_q": "estimated"})
    p["quirks"] = quirks
    p["quirks_checked"] = False
    special = []
    for code in dict.fromkeys(p.pop("special_raw")):
        if code not in special_codes:
            raise Bad(f"нет категории {code}")
        special.append({"code": code, "note_ru": p["notes"].get(code) or SPECIAL_NOTE.get(code), "_q": "estimated"})
    p["special"] = special
    p.pop("notes")
    p["photos"] = []
    p["_meta"] = {"created": (old or {}).get("_meta", {}).get("created", TODAY), "updated": TODAY,
                  "filled_by": "Claude Opus 5.5 (сведения до 2026-06) + tools/import_catalog_data.py"}
    order = ["slug", "brand", "name", "full_name", "aliases", "os", "release_date", "year", "status",
             "availability_ru", "form_factor", "in_ml", "chipset", "specs", "variants", "cameras", "camera_features",
             "quirks", "quirks_checked", "special", "photos", "_meta"]
    return {k: p[k] for k in order}


# ── сверка с Wikipedia (tools/verify_wikipedia.py → data/sources/wikipedia_match.json) ──
WIKI_BOUNDS = {"battery_mah": (1000, 25000), "weight_g": (80, 800), "screen_in": (3, 12), "cover_screen_in": (1, 8),
               "refresh_hz": (50, 240)}


def chip_by_name(soc: str | None, chips: list[dict]) -> str | None:
    if not soc:
        return None
    key = re.sub(r"[^a-z0-9]+", "", soc.lower().replace("+", "plus"))
    best = None
    for c in chips:
        ck = re.sub(r"[^a-z0-9]+", "", c["name"].lower().replace("+", "plus"))
        if ck and ck in key and (best is None or len(ck) > len(best[0])):
            best = (ck, c["slug"])
    return best[1] if best else None


def apply_wiki(d: dict, m: dict | None, chips: list[dict], seed_only: bool) -> None:
    """Подставляет значения карточки статьи Wikipedia: это прямое указание источника → verified со ссылкой."""
    if not m:
        return
    w, src = m["wiki"], f"wikipedia:{m['title'].replace(' ', '_')}"
    sp = d["specs"]
    got = []
    for k, (lo, hi) in WIKI_BOUNDS.items():
        v = w.get(k)
        if v is None or not lo <= v <= hi:
            continue
        if k == "screen_in" and sp.get(k) and abs(v - sp[k]) > 1.0:  # внешний экран вместо основного и т. п.
            continue
        if k == "cover_screen_in" and d["form_factor"] not in ("book_fold", "flip", "trifold"):
            continue
        sp[k] = int(v) if k in ("battery_mah", "refresh_hz") else (round(v, 2) if k.endswith("_in") else
                                                                   (int(v) if float(v).is_integer() else v))
        got.append(k)
    core = {"battery_mah", "weight_g", "screen_in", "refresh_hz"} & set(got)
    if got:
        sp["_src"] = [src] + [x for x in sp["_src"] if x != src]
    if len(core) >= 3:
        sp["_q"] = "verified"
    rd = w.get("release_date")
    if rd and abs(int(rd[:4]) - d["year"]) <= 1:
        d["release_date"], d["year"] = rd, int(rd[:4])
    if d["chipset"] is None:
        d["chipset"] = chip_by_name(w.get("soc"), chips)
    rear, front = w.get("rear") or [], w.get("front") or []
    if seed_only and rear:  # у модели после даты знаний камеры только из статьи
        cams, pos = [], {}
        for c in rear + front[:1]:
            role = c["role"]
            cams.append({"role": role, "position": pos.get(role, 0), "sensor": None, "mp": c["mp"],
                         "aperture": c["aperture"], "focal_mm": c["focal_mm"] if c["focal_mm"] and c["focal_mm"] >= 8 else None,
                         "fov_deg": None, "optical_zoom_x": None, "ois": c["ois"], "af": c["af"] or ("fixed" if role in ("front", "depth", "macro") else "pdaf"),
                         "min_focus_cm": None, "video_max": None, "processing_score": None, "processing_basis": None,
                         "_q": "verified", "_src": [src]})
            pos[role] = pos.get(role, 0) + 1
        if sum(c["role"] == "main" for c in cams) == 1:
            d["cameras"] = cams
        return
    for c in d["cameras"]:
        pool = front if c["role"] in ("front", "front_ultrawide") else rear
        hit = next((x for x in pool if x["role"] == c["role"] and x["mp"] and c["mp"] and abs(x["mp"] - c["mp"]) < 1), None)
        if hit and hit["aperture"] and c["aperture"] and abs(hit["aperture"] - c["aperture"]) <= 0.06:
            if c["focal_mm"] is None and hit["focal_mm"] and hit["focal_mm"] >= 8:
                c["focal_mm"] = hit["focal_mm"]
            c["_q"], c["_src"] = "verified", [src] + [x for x in c["_src"] if x != src]


# ── фото: старая база (Wikimedia) + data/sources/commons_photos.json (tools/fetch_commons_photos.py) ──
def seed_photos() -> dict[str, list[dict]]:
    path = ROOT / "data" / "phone_photos_seed.csv"
    seed_rows = list(csv.DictReader((ROOT / "data" / "phones_seed.csv").open(encoding="utf-8")))
    brand_of = {r["model"]: r["brand"] for r in seed_rows}
    out: dict[str, list[dict]] = {}
    if not path.exists():
        return out
    for r in sorted(csv.DictReader(path.open(encoding="utf-8")), key=lambda r: int(r["position"] or 0)):
        b = brand_of.get(r["model"])
        if not b:
            continue
        bname = {"Oppo": "OPPO", "Vivo": "vivo", "Realme": "realme", "RedMagic": "REDMAGIC"}.get(b, b)
        model = re.sub(r"\s+(\d+)\s*(GB|TB)$", "", r["model"])
        slug = slugify(full_name(bname, model))
        low = r["url"].lower()
        kind = next((k for key, k in (("back", "back"), ("rear", "back"), ("camera", "back"), ("front", "front"),
                                      ("side", "side")) if key in low), "lifestyle")
        out.setdefault(slug, []).append({"url": r["url"], "kind": kind, "color_ru": None, "author": r["author"] or None,
                                         "license": r["license"] or "?", "source_page": r["source_page"] or None,
                                         "width": 960, "height": None, "origin": "wikimedia"})
    return out


def photo_blocklist() -> set[str]:
    path = SRC_DIR / "photo_blocklist.txt"
    if not path.exists():
        return set()
    return {ln.split("|")[0].strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


BLOCKED_PHOTOS = photo_blocklist()


def photos_for(slug: str, seed: dict, commons: dict) -> list[dict]:
    seen, out = set(BLOCKED_PHOTOS), []
    for ph in seed.get(slug, []) + commons.get(slug, []):
        if ph["url"] in seen or ph.get("source_page") in seen:
            continue
        seen.add(ph["url"])
        if ph.get("source_page"):
            seen.add(ph["source_page"])
        out.append(ph)
    return out[:8]



def apply_evidence(d: dict, rules: list[dict]) -> None:
    """«Приколы», подтверждённые официальной страницей (tools/check_quirk_evidence.py) — verified со ссылкой."""
    for r in rules:
        if d["slug"] not in r["slugs"]:
            continue
        q = next((x for x in d["quirks"] if x["code"] == r["code"]), None)
        if q is None:
            q = {"code": r["code"], "severity": r["severity"], "note_ru": "", "evidence": [], "_q": "estimated"}
            d["quirks"].append(q)
        q.update({"severity": r["severity"], "note_ru": r["note"], "evidence": [r["url"]], "_q": "verified"})


def main():
    chips, chip_keys = chipsets()
    sens, sensor_keys = sensors()
    quirk_codes = {q["code"] for q in json.loads((CAT / "quirk_types.json").read_text(encoding="utf-8"))}
    special_codes = {s["code"] for s in json.loads((CAT / "special_categories.json").read_text(encoding="utf-8"))}
    # модели рандом-версии (снимок до первого импорта): их slug, имя и in_ml не меняются (правило 11 ТЗ)
    originals = json.loads((SRC_DIR / "_random_originals.json").read_text(encoding="utf-8"))
    existing = {s: (None, {"slug": s, "brand": o["brand"], "name": o["name"], "full_name": o["full_name"],
                           "in_ml": o["in_ml"], "variants": o["seed_variants"]}) for s, o in originals.items()}
    on_disk = {p.stem: p for p in (CAT / "phones").rglob("*.json")}
    seed = {}
    wm_path = ROOT / "data" / "sources" / "wikipedia_match.json"
    wiki_match = json.loads(wm_path.read_text(encoding="utf-8")) if wm_path.exists() else {}
    cp_path = ROOT / "data" / "sources" / "commons_photos.json"
    commons = json.loads(cp_path.read_text(encoding="utf-8")) if cp_path.exists() else {}
    seed_ph = seed_photos()
    qe_path = ROOT / "data" / "sources" / "quirk_evidence.json"
    evidence_rules = json.loads(qe_path.read_text(encoding="utf-8")) if qe_path.exists() else []
    tp_path = ROOT / "data" / "sources" / "telegram_prices.json"
    tg_prices = json.loads(tp_path.read_text(encoding="utf-8")) if tp_path.exists() else {}
    errors, written, seen = [], {}, {}
    for src in sorted(SRC_DIR.glob("phones_*.txt")):
        brand = None
        for no, raw in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("@brand"):
                brand = line.split()[1]
                if brand not in BRANDS:
                    errors.append(f"{src.name}:{no}: бренд {brand} не описан в BRANDS")
                continue
            try:
                p = parse_line(brand, line, sensor_keys, chip_keys, seed, existing, set())
                if p["slug"] in seen:
                    raise Bad(f"повтор модели (уже в {seen[p['slug']]})")
                seen[p["slug"]] = f"{src.name}:{no}"
                old = existing.get(p["slug"], (None, None))[1]
                seed_only = "seed" in line.split(" ; ")[1].split()
                d = finish(p, tg_prices.get(p["slug"]), old, quirk_codes, special_codes, BRANDS[brand][0])
                apply_wiki(d, wiki_match.get(d["slug"]), chips, seed_only)
                apply_evidence(d, evidence_rules)
                d["photos"] = photos_for(d["slug"], seed_ph, commons)
                written[d["slug"]] = d
            except (Bad, ValueError, IndexError, KeyError) as e:
                errors.append(f"{src.name}:{no}: {e}  ← {line[:70]}")
    if errors:
        print("ОШИБКИ:\n  " + "\n  ".join(errors))
        raise SystemExit(1)
    # модели из рандом-версии, которых нет в таблицах, остаются как были (с пометкой random) — видно в --final
    left = sorted(set(existing) - set(written))
    for slug, path in on_disk.items():  # файлы, которые больше не описаны в таблицах и не из рандом-версии
        if slug not in written and slug not in existing:
            path.unlink()
    for slug, d in written.items():
        path = CAT / "phones" / d["brand"] / f"{slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # история цен: строки старой базы и Telegram для существующих конфигураций + новые точки из Telegram
    hist_path = CAT / "price_history.csv"
    rows = list(csv.DictReader(hist_path.open(encoding="utf-8"))) if hist_path.exists() else []
    th_path = ROOT / "data" / "sources" / "telegram_history.json"
    for slug, ram, rom, dt, src, price in (json.loads(th_path.read_text(encoding="utf-8")) if th_path.exists() else []):
        rows.append({"slug": slug, "ram_gb": str(ram), "rom_gb": str(rom), "date": dt, "source": src, "price_rub": str(price)})
    pairs = {(slug, str(v["ram_gb"]), str(v["rom_gb"])) for slug, d in written.items() for v in d["variants"]}
    pairs |= {(s_, str(v["ram_gb"]), str(v["rom_gb"])) for s_ in left for v in originals[s_]["seed_variants"]}
    uniq = {}
    for r in rows:
        if (r["slug"], r["ram_gb"], r["rom_gb"]) in pairs:
            uniq[(r["slug"], r["ram_gb"], r["rom_gb"], r["date"], r["source"])] = r
    with hist_path.open("w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=["slug", "ram_gb", "rom_gb", "date", "source", "price_rub"], lineterminator="\n")
        wr.writeheader()
        wr.writerows(sorted(uniq.values(), key=lambda r: (r["slug"], int(r["ram_gb"]), int(r["rom_gb"]), r["date"], r["source"])))
    # справочники
    (CAT / "chipsets.json").write_text(json.dumps(chips, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (CAT / "sensors.json").write_text(json.dumps(sens, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    used_brands = {d["brand"] for d in written.values()} | {originals[s]["brand"] for s in left}
    brands = [{"slug": s, "name": n, "parent": par, "country": c} for s, (n, par, c, _) in BRANDS.items()
              if s in used_brands]
    (CAT / "brands.json").write_text(json.dumps(brands, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    srcs = json.loads((CAT / "sources.json").read_text(encoding="utf-8"))
    have = {s["id"] for s in srcs}
    srcs += [s for s in NEW_SOURCES if s["id"] not in have]
    (CAT / "sources.json").write_text(json.dumps(srcs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"моделей записано: {len(written)} (новых {len(set(written) - set(existing))}); "
          f"не тронуто из рандом-версии: {len(left)}")
    if left:
        print("  без строки в таблицах:", ", ".join(left[:40]), "…" if len(left) > 40 else "")


if __name__ == "__main__":
    main()
