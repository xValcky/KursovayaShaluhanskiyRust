-- Каталог смартфонов, схема v2 (SQLite). Собирается скриптом tools/build_catalog.py из data/catalog/**.json.
-- Источник правды — JSON-файлы; эту БД руками не правят, она пересобирается целиком.
-- q (достоверность): verified | estimated | user_hypothesis | random | unknown.
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,           -- schema_version, dataset_name, built_at, files_hash, random_seed
    value TEXT NOT NULL
);

CREATE TABLE sources (
    id          TEXT PRIMARY KEY,     -- 'gsmarena', 'dxomark', 'yandex_market', 'random' …
    name        TEXT NOT NULL,
    url         TEXT,
    kind        TEXT NOT NULL CHECK (kind IN ('manufacturer','press','spec_db','wiki','lab','review','community',
                                              'retailer','project','user','random')),
    reliability INTEGER NOT NULL CHECK (reliability BETWEEN 0 AND 5)
);

CREATE TABLE brands (
    id      INTEGER PRIMARY KEY,
    slug    TEXT NOT NULL UNIQUE,
    name    TEXT NOT NULL,
    parent  TEXT,                     -- slug материнского бренда (poco → xiaomi)
    country TEXT
);

CREATE TABLE chipsets (
    id           INTEGER PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    vendor       TEXT NOT NULL,
    process_nm   REAL,
    antutu_k     INTEGER,
    geekbench_sc INTEGER,
    geekbench_mc INTEGER,
    year         INTEGER,
    q            TEXT NOT NULL,
    src          TEXT                 -- JSON-массив id источников
);

CREATE TABLE phones (
    id              INTEGER PRIMARY KEY,      -- внутренний номер, меняется при пересборке — снаружи используйте slug
    slug            TEXT NOT NULL UNIQUE,
    brand_id        INTEGER NOT NULL REFERENCES brands(id),
    name            TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    aliases         TEXT,                     -- JSON-массив
    os              TEXT NOT NULL CHECK (os IN ('Android','iOS','HarmonyOS')),
    release_date    TEXT,
    year            INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('on_sale','discontinued','announced','china_only')),
    availability_ru TEXT NOT NULL CHECK (availability_ru IN ('official','parallel_import','not_sold')),
    form_factor     TEXT NOT NULL CHECK (form_factor IN ('bar','book_fold','flip','trifold','rugged','other')),
    in_ml           INTEGER NOT NULL DEFAULT 1,
    chipset_id      INTEGER REFERENCES chipsets(id),
    screen_in       REAL NOT NULL,
    cover_screen_in REAL,
    refresh_hz      INTEGER NOT NULL,
    display_type    TEXT,
    peak_nits       INTEGER,
    battery_mah     INTEGER NOT NULL,
    charge_w        REAL NOT NULL,
    wireless_w      REAL,
    weight_g        REAL NOT NULL,
    thickness_mm    REAL,
    ip_rating       TEXT,
    has_5g          INTEGER NOT NULL,
    nfc             INTEGER NOT NULL,
    esim            INTEGER NOT NULL,
    ir_port         INTEGER NOT NULL,
    jack35          INTEGER,
    sd_card         INTEGER,
    update_years    INTEGER,
    quirks_checked  INTEGER NOT NULL DEFAULT 0,
    q               TEXT NOT NULL,            -- достоверность характеристик (specs._q)
    src             TEXT                      -- JSON-массив
);

CREATE TABLE variants (
    id              INTEGER PRIMARY KEY,
    phone_id        INTEGER NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    ram_gb          INTEGER NOT NULL,
    rom_gb          INTEGER NOT NULL,
    price_rub       INTEGER,                  -- NULL — не продаётся в РФ
    price_date      TEXT,
    price_source_id TEXT REFERENCES sources(id),
    msrp_usd        INTEGER,
    is_base         INTEGER NOT NULL DEFAULT 0,
    q               TEXT NOT NULL,
    UNIQUE (phone_id, ram_gb, rom_gb)
);

-- история цен по конкретной конфигурации; хранится только при изменении цены
CREATE TABLE price_history (
    variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    date       TEXT NOT NULL,
    source_id  TEXT NOT NULL REFERENCES sources(id),
    price_rub  INTEGER NOT NULL,
    PRIMARY KEY (variant_id, date, source_id)
) WITHOUT ROWID;

CREATE TABLE camera_sensors (
    id        INTEGER PRIMARY KEY,
    slug      TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    vendor    TEXT NOT NULL,
    mp        REAL NOT NULL,
    size_inch TEXT NOT NULL,
    diag_mm   REAL NOT NULL,                  -- если не задано в файле — 16 / знаменатель формата
    area_mm2  REAL NOT NULL,                  -- 0.48 · diag² (кадр 4:3)
    pixel_um  REAL,
    tech      TEXT,                           -- JSON-массив
    hw_score  INTEGER NOT NULL CHECK (hw_score BETWEEN 0 AND 100),
    q         TEXT NOT NULL,
    src       TEXT
);

CREATE TABLE phone_cameras (
    id               INTEGER PRIMARY KEY,
    phone_id         INTEGER NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('main','ultrawide','tele','periscope','macro','depth','mono',
                                                    'front','front_ultrawide','cover')),
    position         INTEGER NOT NULL DEFAULT 0,
    sensor_id        INTEGER REFERENCES camera_sensors(id),
    mp               REAL NOT NULL,
    aperture         REAL,
    focal_mm         REAL,
    fov_deg          REAL,
    optical_zoom_x   REAL,                    -- считается: focal_mm / focal_mm основной (иначе — из файла)
    ois              INTEGER,
    af               TEXT,
    min_focus_cm     REAL,
    video_max        TEXT,
    module_hw_score  INTEGER,                 -- формула, раздел 7 ТЗ
    processing_score INTEGER,                 -- из файла (рубрика) с поправкой по DXOMARK, если есть
    processing_basis TEXT,
    total_score      INTEGER,                 -- round(0.5·hw + 0.5·processing)
    q                TEXT NOT NULL,
    src              TEXT
);

CREATE TABLE camera_summary (
    phone_id           INTEGER PRIMARY KEY REFERENCES phones(id) ON DELETE CASCADE,
    rear_count         INTEGER NOT NULL,      -- задние модули без depth
    max_optical_zoom_x REAL NOT NULL,         -- 1.0, если телевика нет
    lossless_zoom_x    REAL,
    max_digital_zoom_x INTEGER,
    macro_mode         TEXT NOT NULL CHECK (macro_mode IN ('none','dedicated','ultrawide_af','tele_macro')),
    macro_score        INTEGER NOT NULL,
    main_score         INTEGER,
    ultrawide_score    INTEGER,
    tele_score         INTEGER,
    front_score        INTEGER,
    overall_score      INTEGER NOT NULL,
    dxomark_camera     INTEGER,
    dxomark            TEXT,                  -- JSON с подоценками
    q                  TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE quirk_types (
    id             INTEGER PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,
    title_ru       TEXT NOT NULL,
    category       TEXT NOT NULL CHECK (category IN ('display','thermal','battery','camera','software','ai','build',
                                                     'audio','connectivity','updates','other')),
    polarity       INTEGER NOT NULL CHECK (polarity IN (-1, 0, 1)),
    description_ru TEXT,
    measure_rule   TEXT
);

CREATE TABLE phone_quirks (
    phone_id INTEGER NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    quirk_id INTEGER NOT NULL REFERENCES quirk_types(id),
    severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 3),
    note_ru  TEXT,
    evidence TEXT,                            -- JSON-массив ссылок
    q        TEXT NOT NULL,
    PRIMARY KEY (phone_id, quirk_id)
) WITHOUT ROWID;

CREATE TABLE special_categories (
    id             INTEGER PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,
    title_ru       TEXT NOT NULL,
    description_ru TEXT,
    criteria       TEXT,
    sort           INTEGER NOT NULL
);

CREATE TABLE phone_special (
    phone_id    INTEGER NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES special_categories(id),
    note_ru     TEXT,
    q           TEXT NOT NULL,
    PRIMARY KEY (phone_id, category_id)
) WITHOUT ROWID;

CREATE TABLE photos (
    id          INTEGER PRIMARY KEY,
    phone_id    INTEGER NOT NULL REFERENCES phones(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    url         TEXT NOT NULL,
    kind        TEXT NOT NULL,
    color_ru    TEXT,
    author      TEXT,
    license     TEXT NOT NULL,
    source_page TEXT,
    width       INTEGER,
    height      INTEGER,
    origin      TEXT NOT NULL CHECK (origin IN ('wikimedia','press','manufacturer','user','seed','placeholder')),
    UNIQUE (phone_id, url)
);

CREATE INDEX idx_phones_brand    ON phones(brand_id);
CREATE INDEX idx_phones_year     ON phones(year);
CREATE INDEX idx_variants_phone  ON variants(phone_id, price_rub);
CREATE INDEX idx_variants_mem    ON variants(ram_gb, rom_gb);
CREATE INDEX idx_cameras_phone   ON phone_cameras(phone_id, role, position);
CREATE INDEX idx_quirks_type     ON phone_quirks(quirk_id);
CREATE INDEX idx_special_cat     ON phone_special(category_id);
CREATE INDEX idx_photos_phone    ON photos(phone_id, position);

-- Совместимость с текущим ML (18 признаков, одна строка на модель — базовая конфигурация).
-- active_h / standby_d остаются расчётными (формула estimate_autonomy в src/db.py) и считаются в Python.
CREATE VIEW v_ml_phones AS
SELECT p.id, p.slug, b.name AS brand, p.name AS model, p.full_name, p.os, p.year,
       v.price_rub, v.ram_gb, v.rom_gb, p.battery_mah, p.screen_in, p.refresh_hz, p.weight_g,
       (SELECT c.mp FROM phone_cameras c WHERE c.phone_id = p.id AND c.role = 'main' ORDER BY c.position LIMIT 1) AS camera_mp,
       ch.antutu_k, p.charge_w, p.has_5g, p.nfc, p.esim, p.ir_port
FROM phones p
JOIN brands b ON b.id = p.brand_id
JOIN variants v ON v.phone_id = p.id AND v.is_base = 1
LEFT JOIN chipsets ch ON ch.id = p.chipset_id
WHERE p.in_ml = 1;

-- Лёгкая карточка для ленты: «от N ₽», число конфигураций, сводка камер
CREATE VIEW v_phone_card AS
SELECT p.id, p.slug, p.full_name, p.os, p.year, p.form_factor,
       (SELECT MIN(price_rub) FROM variants v WHERE v.phone_id = p.id) AS price_from,
       (SELECT COUNT(*) FROM variants v WHERE v.phone_id = p.id) AS variants_count,
       (SELECT MAX(ram_gb) FROM variants v WHERE v.phone_id = p.id) AS ram_max,
       s.rear_count, s.max_optical_zoom_x, s.max_digital_zoom_x, s.macro_mode, s.overall_score,
       (SELECT url FROM photos ph WHERE ph.phone_id = p.id ORDER BY position LIMIT 1) AS photo
FROM phones p
LEFT JOIN camera_summary s ON s.phone_id = p.id;
