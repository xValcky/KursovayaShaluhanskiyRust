//! Каталог v2: конфигурации памяти, модули камер, «приколы», категории интересных возможностей, фото.
//!
//! Источник — SQLite `catalog.db`, его собирает `tools/build_catalog.py` из `data/catalog/**.json`.
//! База читается ОДИН раз при старте в компактные структуры в памяти; во время запросов к SQLite не обращаемся.
//! Связь с обученной моделью (`model.json`) — по slug: «Samsung Galaxy S25+» → `samsung-galaxy-s25-plus`,
//! строки-конфигурации «Apple iPhone 17 Pro 512GB» ведут на модель `apple-iphone-17-pro`.

use crate::engine::{Ext, Recommendation, Row, Survey, REQ_KEYS, REQ_TITLES};
use crate::model::Model;
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};
use std::ops::Range;
use std::path::{Path, PathBuf};

/// Копия каталога внутри бинарника: сервер запускается и без файла catalog.db рядом.
pub const EMBEDDED_DB: &[u8] = include_bytes!("../catalog.db");

/// Конфигурация памяти.
pub struct Variant {
    pub db_id: i64,
    pub ram: u8,
    pub rom: u16,
    pub price: Option<u32>,
    pub date: Option<String>,
    pub source: Option<String>,
    pub msrp_usd: Option<u32>,
    pub is_base: bool,
    pub q: String,
}

/// Модуль камеры (сведения о сенсоре — прямо здесь, чтобы не искать по справочнику при выдаче).
pub struct Camera {
    pub role: String,
    pub position: u8,
    pub sensor: Option<String>,
    pub sensor_size: Option<String>,
    pub sensor_pixel_um: Option<f32>,
    pub sensor_hw: Option<u8>,
    pub mp: f32,
    pub aperture: Option<f32>,
    pub focal_mm: Option<f32>,
    pub fov_deg: Option<f32>,
    pub zoom: Option<f32>,
    pub ois: Option<bool>,
    pub af: Option<String>,
    pub min_focus_cm: Option<f32>,
    pub video: Option<String>,
    pub hw: Option<u8>,
    pub processing: Option<u8>,
    pub basis: Option<String>,
    pub total: Option<u8>,
    pub q: String,
}

/// Сводка камер (считает скрипт сборки по формулам раздела 7 ТЗ).
#[derive(Default)]
pub struct Summary {
    pub rear: u8,
    pub optical: f32,
    pub lossless: Option<f32>,
    pub digital: Option<u16>,
    pub macro_mode: String,
    pub macro_score: u8,
    pub main: Option<u8>,
    pub ultrawide: Option<u8>,
    pub tele: Option<u8>,
    pub front: Option<u8>,
    pub overall: Option<u8>,
    pub dxomark: Option<u16>,
    pub q: String,
}

pub struct QuirkType {
    pub code: String,
    pub title: String,
    pub category: String,
    pub polarity: i8,
    pub description: Option<String>,
    pub rule: Option<String>,
}

pub struct PhoneQuirk {
    pub kind: usize,
    pub severity: u8,
    pub note: Option<String>,
    pub evidence: Vec<String>,
    pub q: String,
}

pub struct Category {
    pub code: String,
    pub title: String,
    pub description: Option<String>,
    pub criteria: Option<String>,
}

pub struct PhoneSpecial {
    pub bit: u8,
    pub note: Option<String>,
    pub q: String,
}

pub struct Photo {
    pub url: String,
    pub kind: String,
    pub color: Option<String>,
    pub author: Option<String>,
    pub license: String,
    pub page: Option<String>,
    pub origin: String,
}

pub struct CatPhone {
    pub slug: String,
    pub brand: String,
    pub name: String,
    pub full_name: String,
    pub os: String,
    pub year: u16,
    pub status: String,
    pub availability: String,
    pub form: String,
    pub in_ml: bool,
    pub chipset: Option<String>,
    pub antutu_k: Option<u32>,
    pub screen_in: f32,
    pub refresh_hz: u16,
    pub battery_mah: u32,
    pub charge_w: f32,
    pub weight_g: f32,
    pub has_5g: bool,
    pub nfc: bool,
    pub esim: bool,
    pub ir_port: bool,
    pub quirks_checked: bool,
    pub q: String,
    /// редко нужные поля (окно «Все характеристики») — готовым JSON
    pub more: Value,
    pub variants: Range<usize>,
    pub cameras: Range<usize>,
    pub quirks: Range<usize>,
    pub special: Range<usize>,
    pub photos: Range<usize>,
    pub summary: Summary,
    pub special_mask: u32,
    /// есть отрицательный «прикол» тяжести ≥ 2
    pub bad_quirk: bool,
}

pub struct Catalog {
    pub meta: HashMap<String, String>,
    pub phones: Vec<CatPhone>,
    pub variants: Vec<Variant>,
    pub cameras: Vec<Camera>,
    pub quirk_types: Vec<QuirkType>,
    pub quirks: Vec<PhoneQuirk>,
    pub categories: Vec<Category>,
    pub specials: Vec<PhoneSpecial>,
    pub photos: Vec<Photo>,
    /// история цен по db-id конфигурации: (дата, источник, цена)
    pub history: HashMap<i64, Vec<(String, String, u32)>>,
    pub by_slug: HashMap<String, usize>,
    /// индекс телефона model.json → индекс модели каталога
    pub link: Vec<Option<usize>>,
    /// id телефона model.json → индекс в model.json и обратно
    pub model_index: HashMap<i64, usize>,
    pub model_ids: Vec<i64>,
    /// сколько разных моделей каталога участвуют в подборе
    pub linked: usize,
    pub ram_options: Vec<u8>,
    /// откуда прочитан каталог (для лога)
    pub origin: String,
}

/// Как `slugify` в tools/generate_random_catalog.py: «+» → « plus », нижний регистр, всё кроме a-z0-9 → «-».
pub fn slugify(text: &str) -> String {
    let low = text.to_lowercase().replace('+', " plus ");
    let mut out = String::with_capacity(low.len());
    let mut dash = false;
    for ch in low.chars() {
        if ch.is_ascii_lowercase() || ch.is_ascii_digit() {
            if dash && !out.is_empty() {
                out.push('-');
            }
            dash = false;
            out.push(ch);
        } else {
            dash = true;
        }
    }
    out
}

/// «Apple iPhone 17 Pro 512GB» → («Apple iPhone 17 Pro», Some(512)); 1 TB = 1024 ГБ.
pub fn strip_mem_suffix(name: &str) -> (&str, Option<u32>) {
    let t = name.trim_end();
    let low = t.to_ascii_lowercase();
    let mult = if low.ends_with("gb") {
        1
    } else if low.ends_with("tb") {
        1024
    } else {
        return (name, None);
    };
    let body = t[..t.len() - 2].trim_end();
    let digits = body.len() - body.trim_end_matches(|c: char| c.is_ascii_digit()).len();
    if digits == 0 {
        return (name, None);
    }
    let rest = &body[..body.len() - digits];
    if !rest.ends_with(char::is_whitespace) {
        return (name, None);
    }
    let size: u32 = body[body.len() - digits..].parse().unwrap_or(0);
    (rest.trim_end(), Some(size * mult))
}

/// slug модели каталога для строки model.json.
pub fn model_slug(full_name: &str) -> String {
    slugify(strip_mem_suffix(full_name).0)
}

fn err(e: rusqlite::Error) -> String {
    format!("catalog.db: {e}")
}

fn f32o(v: Option<f64>) -> Option<f32> {
    v.map(|x| x as f32)
}

/// f32 → число для JSON без «хвостов» вида 5.400000095367432.
fn r2(v: f32) -> f64 {
    (v as f64 * 100.0).round() / 100.0
}

fn r2o(v: Option<f32>) -> Option<f64> {
    v.map(r2)
}

fn u8o(v: Option<i64>) -> Option<u8> {
    v.map(|x| x.clamp(0, 255) as u8)
}

/// Строки одной таблицы в порядке phone_id → плоский массив + диапазон на каждую модель.
fn grouped<T>(rows: Vec<(i64, T)>, idx: &HashMap<i64, usize>, n: usize) -> (Vec<T>, Vec<Range<usize>>) {
    let mut items = Vec::with_capacity(rows.len());
    let mut ranges = vec![0..0; n];
    for (pid, item) in rows {
        if let Some(&c) = idx.get(&pid) {
            if ranges[c].is_empty() {
                ranges[c] = items.len()..items.len();
            }
            items.push(item);
            ranges[c].end = items.len();
        }
    }
    (items, ranges)
}

impl Catalog {
    /// CATALOG_PATH → catalog.db рядом с exe → catalog.db в текущей папке → встроенная копия.
    pub fn open_default(m: &Model) -> Result<Catalog, String> {
        if let Ok(p) = std::env::var("CATALOG_PATH") {
            return Catalog::load(Path::new(&p), m);
        }
        let mut cands: Vec<PathBuf> = Vec::new();
        if let Some(dir) = std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf)) {
            cands.push(dir.join("catalog.db"));
        }
        cands.push(PathBuf::from("catalog.db"));
        for c in cands {
            if c.is_file() {
                return Catalog::load(&c, m);
            }
        }
        // встроенная копия: SQLite читает файл, поэтому кладём её во временную папку (имя — по содержимому)
        let hash = EMBEDDED_DB.iter().fold(0xcbf29ce484222325u64, |h, &b| (h ^ b as u64).wrapping_mul(0x100000001b3));
        let path = std::env::temp_dir().join(format!("phonepicker-catalog-{hash:016x}.db"));
        if std::fs::metadata(&path).map(|md| md.len() as usize).ok() != Some(EMBEDDED_DB.len()) {
            std::fs::write(&path, EMBEDDED_DB).map_err(|e| format!("не записать {}: {e}", path.display()))?;
        }
        let mut cat = Catalog::load(&path, m)?;
        cat.origin = "встроенный в программу".into();
        Ok(cat)
    }

    pub fn load(path: &Path, m: &Model) -> Result<Catalog, String> {
        let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
            .map_err(err)?;
        let mut cat = Catalog::read(&conn)?;
        cat.origin = path.display().to_string();
        cat.link(m);
        Ok(cat)
    }

    fn read(conn: &Connection) -> Result<Catalog, String> {
        let meta: HashMap<String, String> = conn
            .prepare("SELECT key, value FROM meta")
            .map_err(err)?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(err)?
            .collect::<Result<_, _>>()
            .map_err(err)?;
        if meta.get("schema_version").map(String::as_str) != Some("2") {
            return Err("catalog.db: ожидалась схема v2".into());
        }

        // модели
        let mut idx: HashMap<i64, usize> = HashMap::new();
        let mut phones: Vec<CatPhone> = Vec::new();
        {
            let mut st = conn
                .prepare(
                    "SELECT p.id, p.slug, b.name, p.name, p.full_name, p.os, p.year, p.release_date, p.status, \
                     p.availability_ru, p.form_factor, p.in_ml, ch.name, ch.antutu_k, p.screen_in, p.cover_screen_in, \
                     p.refresh_hz, p.display_type, p.peak_nits, p.battery_mah, p.charge_w, p.wireless_w, p.weight_g, \
                     p.thickness_mm, p.ip_rating, p.has_5g, p.nfc, p.esim, p.ir_port, p.jack35, p.sd_card, \
                     p.update_years, p.quirks_checked, p.q, p.aliases \
                     FROM phones p JOIN brands b ON b.id = p.brand_id LEFT JOIN chipsets ch ON ch.id = p.chipset_id \
                     ORDER BY p.id",
                )
                .map_err(err)?;
            let rows = st
                .query_map([], |r| {
                    let more = json!({
                        "release_date": r.get::<_, Option<String>>(7)?,
                        "cover_screen_in": r.get::<_, Option<f64>>(15)?,
                        "display_type": r.get::<_, Option<String>>(17)?,
                        "peak_nits": r.get::<_, Option<i64>>(18)?,
                        "wireless_w": r.get::<_, Option<f64>>(21)?,
                        "thickness_mm": r.get::<_, Option<f64>>(23)?,
                        "ip_rating": r.get::<_, Option<String>>(24)?,
                        "jack35": r.get::<_, Option<i64>>(29)?.map(|v| v != 0),
                        "sd_card": r.get::<_, Option<i64>>(30)?.map(|v| v != 0),
                        "update_years": r.get::<_, Option<i64>>(31)?,
                        "aliases": r.get::<_, Option<String>>(34)?
                            .and_then(|s| serde_json::from_str::<Value>(&s).ok()).unwrap_or(json!([])),
                    });
                    Ok((
                        r.get::<_, i64>(0)?,
                        CatPhone {
                            slug: r.get(1)?,
                            brand: r.get(2)?,
                            name: r.get(3)?,
                            full_name: r.get(4)?,
                            os: r.get(5)?,
                            year: r.get::<_, i64>(6)? as u16,
                            status: r.get(8)?,
                            availability: r.get(9)?,
                            form: r.get(10)?,
                            in_ml: r.get::<_, i64>(11)? != 0,
                            chipset: r.get(12)?,
                            antutu_k: r.get::<_, Option<i64>>(13)?.map(|v| v as u32),
                            screen_in: r.get::<_, f64>(14)? as f32,
                            refresh_hz: r.get::<_, f64>(16)? as u16,
                            battery_mah: r.get::<_, f64>(19)? as u32,
                            charge_w: r.get::<_, f64>(20)? as f32,
                            weight_g: r.get::<_, f64>(22)? as f32,
                            has_5g: r.get::<_, i64>(25)? != 0,
                            nfc: r.get::<_, i64>(26)? != 0,
                            esim: r.get::<_, i64>(27)? != 0,
                            ir_port: r.get::<_, i64>(28)? != 0,
                            quirks_checked: r.get::<_, i64>(32)? != 0,
                            q: r.get(33)?,
                            more,
                            variants: 0..0,
                            cameras: 0..0,
                            quirks: 0..0,
                            special: 0..0,
                            photos: 0..0,
                            summary: Summary::default(),
                            special_mask: 0,
                            bad_quirk: false,
                        },
                    ))
                })
                .map_err(err)?;
            for row in rows {
                let (id, p) = row.map_err(err)?;
                idx.insert(id, phones.len());
                phones.push(p);
            }
        }
        let n = phones.len();

        // конфигурации памяти
        let rows: Vec<(i64, Variant)> = conn
            .prepare(
                "SELECT phone_id, id, ram_gb, rom_gb, price_rub, price_date, price_source_id, msrp_usd, is_base, q \
                 FROM variants ORDER BY phone_id, ram_gb, rom_gb",
            )
            .map_err(err)?
            .query_map([], |r| {
                Ok((
                    r.get(0)?,
                    Variant {
                        db_id: r.get(1)?,
                        ram: r.get::<_, i64>(2)? as u8,
                        rom: r.get::<_, i64>(3)? as u16,
                        price: r.get::<_, Option<i64>>(4)?.map(|v| v as u32),
                        date: r.get(5)?,
                        source: r.get(6)?,
                        msrp_usd: r.get::<_, Option<i64>>(7)?.map(|v| v as u32),
                        is_base: r.get::<_, i64>(8)? != 0,
                        q: r.get(9)?,
                    },
                ))
            })
            .map_err(err)?
            .collect::<Result<_, _>>()
            .map_err(err)?;
        let (variants, vr) = grouped(rows, &idx, n);

        // камеры: роли в порядке показа
        let rows: Vec<(i64, Camera)> = conn
            .prepare(
                "SELECT c.phone_id, c.role, c.position, s.name, s.size_inch, s.pixel_um, s.hw_score, c.mp, c.aperture, \
                 c.focal_mm, c.fov_deg, c.optical_zoom_x, c.ois, c.af, c.min_focus_cm, c.video_max, c.module_hw_score, \
                 c.processing_score, c.processing_basis, c.total_score, c.q \
                 FROM phone_cameras c LEFT JOIN camera_sensors s ON s.id = c.sensor_id \
                 ORDER BY c.phone_id, CASE c.role WHEN 'main' THEN 0 WHEN 'ultrawide' THEN 1 WHEN 'tele' THEN 2 \
                 WHEN 'periscope' THEN 3 WHEN 'macro' THEN 4 WHEN 'mono' THEN 5 WHEN 'depth' THEN 6 \
                 WHEN 'front' THEN 7 WHEN 'front_ultrawide' THEN 8 ELSE 9 END, c.position",
            )
            .map_err(err)?
            .query_map([], |r| {
                Ok((
                    r.get(0)?,
                    Camera {
                        role: r.get(1)?,
                        position: r.get::<_, i64>(2)? as u8,
                        sensor: r.get(3)?,
                        sensor_size: r.get(4)?,
                        sensor_pixel_um: f32o(r.get(5)?),
                        sensor_hw: u8o(r.get(6)?),
                        mp: r.get::<_, f64>(7)? as f32,
                        aperture: f32o(r.get(8)?),
                        focal_mm: f32o(r.get(9)?),
                        fov_deg: f32o(r.get(10)?),
                        zoom: f32o(r.get(11)?),
                        ois: r.get::<_, Option<i64>>(12)?.map(|v| v != 0),
                        af: r.get(13)?,
                        min_focus_cm: f32o(r.get(14)?),
                        video: r.get(15)?,
                        hw: u8o(r.get(16)?),
                        processing: u8o(r.get(17)?),
                        basis: r.get(18)?,
                        total: u8o(r.get(19)?),
                        q: r.get(20)?,
                    },
                ))
            })
            .map_err(err)?
            .collect::<Result<_, _>>()
            .map_err(err)?;
        let (cameras, cr) = grouped(rows, &idx, n);

        let summaries: Vec<(i64, Summary)> = conn
            .prepare(
                "SELECT phone_id, rear_count, max_optical_zoom_x, lossless_zoom_x, max_digital_zoom_x, macro_mode, \
                 macro_score, main_score, ultrawide_score, tele_score, front_score, overall_score, dxomark_camera, q \
                 FROM camera_summary",
            )
            .map_err(err)?
            .query_map([], |r| {
                Ok((
                    r.get(0)?,
                    Summary {
                        rear: r.get::<_, i64>(1)? as u8,
                        optical: r.get::<_, f64>(2)? as f32,
                        lossless: f32o(r.get(3)?),
                        digital: r.get::<_, Option<i64>>(4)?.map(|v| v as u16),
                        macro_mode: r.get(5)?,
                        macro_score: r.get::<_, i64>(6)? as u8,
                        main: u8o(r.get(7)?),
                        ultrawide: u8o(r.get(8)?),
                        tele: u8o(r.get(9)?),
                        front: u8o(r.get(10)?),
                        overall: u8o(r.get(11)?),
                        dxomark: r.get::<_, Option<i64>>(12)?.map(|v| v as u16),
                        q: r.get(13)?,
                    },
                ))
            })
            .map_err(err)?
            .collect::<Result<_, _>>()
            .map_err(err)?;

        // «приколы»
        let mut qt_index: HashMap<i64, usize> = HashMap::new();
        let mut quirk_types = Vec::new();
        for row in conn
            .prepare("SELECT id, code, title_ru, category, polarity, description_ru, measure_rule FROM quirk_types ORDER BY id")
            .map_err(err)?
            .query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    QuirkType {
                        code: r.get(1)?,
                        title: r.get(2)?,
                        category: r.get(3)?,
                        polarity: r.get::<_, i64>(4)? as i8,
                        description: r.get(5)?,
                        rule: r.get(6)?,
                    },
                ))
            })
            .map_err(err)?
        {
            let (id, t) = row.map_err(err)?;
            qt_index.insert(id, quirk_types.len());
            quirk_types.push(t);
        }
        let rows: Vec<(i64, PhoneQuirk)> = conn
            .prepare("SELECT phone_id, quirk_id, severity, note_ru, evidence, q FROM phone_quirks ORDER BY phone_id, severity DESC, quirk_id")
            .map_err(err)?
            .query_map([], |r| {
                let qid: i64 = r.get(1)?;
                Ok((
                    r.get(0)?,
                    PhoneQuirk {
                        kind: qt_index.get(&qid).copied().unwrap_or(0),
                        severity: r.get::<_, i64>(2)? as u8,
                        note: r.get(3)?,
                        evidence: r
                            .get::<_, Option<String>>(4)?
                            .and_then(|s| serde_json::from_str(&s).ok())
                            .unwrap_or_default(),
                        q: r.get(5)?,
                    },
                ))
            })
            .map_err(err)?
            .collect::<Result<_, _>>()
            .map_err(err)?;
        let (quirks, qr) = grouped(rows, &idx, n);

        // категории интересных возможностей: номер бита = порядок сортировки
        let mut cat_bit: HashMap<i64, u8> = HashMap::new();
        let mut categories = Vec::new();
        for row in conn
            .prepare("SELECT id, code, title_ru, description_ru, criteria FROM special_categories ORDER BY sort, id")
            .map_err(err)?
            .query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    Category { code: r.get(1)?, title: r.get(2)?, description: r.get(3)?, criteria: r.get(4)? },
                ))
            })
            .map_err(err)?
        {
            let (id, c) = row.map_err(err)?;
            if categories.len() >= 32 {
                return Err("catalog.db: больше 32 категорий — не помещаются в битовую маску".into());
            }
            cat_bit.insert(id, categories.len() as u8);
            categories.push(c);
        }
        let rows: Vec<(i64, PhoneSpecial)> = conn
            .prepare("SELECT phone_id, category_id, note_ru, q FROM phone_special ORDER BY phone_id, category_id")
            .map_err(err)?
            .query_map([], |r| {
                let cid: i64 = r.get(1)?;
                Ok((
                    r.get(0)?,
                    PhoneSpecial { bit: cat_bit.get(&cid).copied().unwrap_or(0), note: r.get(2)?, q: r.get(3)? },
                ))
            })
            .map_err(err)?
            .collect::<Result<_, _>>()
            .map_err(err)?;
        let (specials, sr) = grouped(rows, &idx, n);

        let rows: Vec<(i64, Photo)> = conn
            .prepare(
                "SELECT phone_id, url, kind, color_ru, author, license, source_page, origin FROM photos \
                 ORDER BY phone_id, position",
            )
            .map_err(err)?
            .query_map([], |r| {
                Ok((
                    r.get(0)?,
                    Photo {
                        url: r.get(1)?,
                        kind: r.get(2)?,
                        color: r.get(3)?,
                        author: r.get(4)?,
                        license: r.get(5)?,
                        page: r.get(6)?,
                        origin: r.get(7)?,
                    },
                ))
            })
            .map_err(err)?
            .collect::<Result<_, _>>()
            .map_err(err)?;
        let (photos, pr) = grouped(rows, &idx, n);

        let mut history: HashMap<i64, Vec<(String, String, u32)>> = HashMap::new();
        for row in conn
            .prepare("SELECT variant_id, date, source_id, price_rub FROM price_history ORDER BY variant_id, date, source_id")
            .map_err(err)?
            .query_map([], |r| {
                Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, i64>(3)?))
            })
            .map_err(err)?
        {
            let (vid, date, src, price) = row.map_err(err)?;
            history.entry(vid).or_default().push((date, src, price as u32));
        }

        for (pid, s) in summaries {
            if let Some(&c) = idx.get(&pid) {
                phones[c].summary = s;
            }
        }
        let mut ram_set: Vec<u8> = Vec::new();
        for (c, p) in phones.iter_mut().enumerate() {
            p.variants = vr[c].clone();
            p.cameras = cr[c].clone();
            p.quirks = qr[c].clone();
            p.special = sr[c].clone();
            p.photos = pr[c].clone();
            p.special_mask = specials[p.special.clone()].iter().fold(0, |m, s| m | (1u32 << s.bit));
            p.bad_quirk =
                quirks[p.quirks.clone()].iter().any(|q| quirk_types[q.kind].polarity < 0 && q.severity >= 2);
            ram_set.extend(variants[p.variants.clone()].iter().map(|v| v.ram));
        }
        ram_set.sort_unstable();
        ram_set.dedup();
        let by_slug = phones.iter().enumerate().map(|(i, p)| (p.slug.clone(), i)).collect();
        Ok(Catalog {
            meta,
            phones,
            variants,
            cameras,
            quirk_types,
            quirks,
            categories,
            specials,
            photos,
            history,
            by_slug,
            link: Vec::new(),
            model_index: HashMap::new(),
            model_ids: Vec::new(),
            linked: 0,
            ram_options: ram_set,
            origin: String::new(),
        })
    }

    /// Сопоставить телефоны model.json с моделями каталога по slug.
    pub fn link(&mut self, m: &Model) {
        self.link = m.phones.iter().map(|p| self.by_slug.get(&model_slug(&p.full_name)).copied()).collect();
        self.model_index = m.phones.iter().enumerate().map(|(i, p)| (p.id, i)).collect();
        self.model_ids = m.phones.iter().map(|p| p.id).collect();
        self.linked = self.link.iter().flatten().collect::<HashSet<_>>().len();
    }

    /// id телефона model.json для модели каталога (первая строка; у «… 512GB» — та же модель).
    pub fn model_id(&self, c: usize) -> Option<i64> {
        self.link.iter().position(|l| *l == Some(c)).map(|i| self.model_ids[i])
    }

    pub fn dataset_name(&self) -> &str {
        self.meta.get("dataset_name").map(String::as_str).unwrap_or("")
    }

    /// Битовая маска категорий по кодам.
    pub fn special_mask(&self, codes: &[String]) -> u32 {
        codes
            .iter()
            .filter_map(|code| self.categories.iter().position(|c| &c.code == code))
            .fold(0, |m, b| m | (1u32 << b))
    }

    /// Конфигурация под анкету: самая дешёвая с ОЗУ ≥ min_ram и ПЗУ ≥ min_rom;
    /// если такой нет — самая «большая» (требование всё равно не выполнится и даст штраф).
    pub fn choose(&self, c: usize, min_ram: f64, min_rom: f64) -> Option<&Variant> {
        let vs = &self.variants[self.phones[c].variants.clone()];
        let priced = || vs.iter().filter(|v| v.price.is_some());
        priced()
            .filter(|v| v.ram as f64 >= min_ram && v.rom as f64 >= min_rom)
            .min_by_key(|v| (v.price, v.ram, v.rom))
            .or_else(|| priced().max_by_key(|v| (v.ram, v.rom, std::cmp::Reverse(v.price))))
    }

    /// Данные каталога для движка подбора. `variants = true` — цена и память берутся из подходящей конфигурации,
    /// а строки model.json одной модели склеиваются; `false` — цены и память как в model.json.
    pub fn ext(&self, m: &Model, a: &Survey, variants: bool) -> Ext {
        let mut e = Ext::base(m);
        e.dedupe = variants;
        e.special_req = self.special_mask(&a.special);
        for i in 0..m.n {
            let Some(c) = self.link[i] else {
                if variants {
                    e.group[i] = self.phones.len() + i;
                }
                continue;
            };
            let p = &self.phones[c];
            e.zoom[i] = p.summary.optical as f64;
            e.macro_ok[i] = p.summary.macro_mode != "none";
            e.bad_quirk[i] = p.bad_quirk;
            e.special[i] = p.special_mask;
            if variants {
                e.group[i] = c;
                if let Some(v) = self.choose(c, a.min_ram, a.min_rom) {
                    e.price[i] = v.price.unwrap_or(0) as f64;
                    e.ram[i] = v.ram as f64;
                    e.rom[i] = v.rom as f64;
                }
            }
        }
        e
    }

    fn photo_json(p: &Photo) -> Value {
        json!({
            "src": p.url, "artist": p.author, "license": p.license, "page": p.page, "kind": p.kind,
            "color": p.color, "placeholder": p.origin == "placeholder",
        })
    }

    pub fn photos_of(&self, c: usize) -> Vec<Value> {
        self.photos[self.phones[c].photos.clone()].iter().map(Catalog::photo_json).collect()
    }

    fn quirk_json(&self, q: &PhoneQuirk, full: bool) -> Value {
        let t = &self.quirk_types[q.kind];
        let mut v = json!({"code": t.code, "title": t.title, "polarity": t.polarity, "severity": q.severity, "q": q.q});
        if full {
            v["category"] = json!(t.category);
            v["note"] = json!(q.note);
            v["evidence"] = json!(q.evidence);
            v["description"] = json!(t.description);
            v["rule"] = json!(t.rule);
        }
        v
    }

    fn camera_summary_json(&self, c: usize) -> Value {
        let s = &self.phones[c].summary;
        json!({
            "rear": s.rear, "optical_x": r2(s.optical), "lossless_x": r2o(s.lossless), "digital_x": s.digital,
            "macro": s.macro_mode, "macro_score": s.macro_score, "main": s.main, "ultrawide": s.ultrawide,
            "tele": s.tele, "front": s.front, "overall": s.overall, "dxomark": s.dxomark, "q": s.q,
        })
    }

    fn special_json(&self, c: usize) -> Vec<Value> {
        self.specials[self.phones[c].special.clone()]
            .iter()
            .map(|s| {
                let cat = &self.categories[s.bit as usize];
                json!({"code": cat.code, "title": cat.title, "note": s.note, "q": s.q})
            })
            .collect()
    }

    /// «Приколы» для карточки: сначала минусы, потом плюсы, по тяжести.
    fn quirks_sorted(&self, c: usize) -> Vec<&PhoneQuirk> {
        let mut list: Vec<&PhoneQuirk> = self.quirks[self.phones[c].quirks.clone()].iter().collect();
        list.sort_by_key(|q| (self.quirk_types[q.kind].polarity, std::cmp::Reverse(q.severity)));
        list
    }

    /// Поля каталога для строки выдачи (карточки): slug, конфигурации, камеры, «приколы», категории.
    fn row_extra(&self, c: usize, a: &Survey, row: &Row) -> Map<String, Value> {
        let p = &self.phones[c];
        let variants: Vec<Value> = self.variants[p.variants.clone()]
            .iter()
            .map(|v| {
                let mem_ok = v.ram as f64 >= a.min_ram && v.rom as f64 >= a.min_rom;
                json!({
                    "ram": v.ram, "rom": v.rom, "price": v.price, "msrp_usd": v.msrp_usd, "base": v.is_base, "q": v.q,
                    "fits": mem_ok && v.price.is_some_and(|x| x as f64 <= a.budget_rub),
                    "chosen": v.ram as f64 == row.ram_gb && v.rom as f64 == row.rom_gb,
                })
            })
            .collect();
        let quirks = self.quirks_sorted(c);
        let mut out = Map::new();
        out.insert("slug".into(), json!(p.slug));
        out.insert("q".into(), json!(p.q));
        out.insert("form_factor".into(), json!(p.form));
        out.insert("availability_ru".into(), json!(p.availability));
        out.insert("variants".into(), json!(variants));
        out.insert("camera".into(), self.camera_summary_json(c));
        out.insert("quirks".into(), json!(quirks.iter().take(2).map(|q| self.quirk_json(q, false)).collect::<Vec<_>>()));
        out.insert("quirks_total".into(), json!(quirks.len()));
        out.insert("special".into(), json!(self.special_json(c)));
        out.insert("chipset".into(), json!(p.chipset));
        out
    }

    /// Дописать поля каталога во все строки выдачи (топ и блоки).
    pub fn decorate(&self, r: &mut Recommendation, a: &Survey) {
        let rows = r.table.iter_mut().chain(r.blocks.iter_mut().flat_map(|b| b.rows.iter_mut()));
        for row in rows {
            let c = self.model_index.get(&row.id).and_then(|&i| self.link[i]);
            if let Some(c) = c {
                let extra = self.row_extra(c, a, row);
                row.extra = Some(extra);
            }
        }
    }

    /// Карточка модели целиком (окно «Все характеристики»).
    pub fn phone_json(&self, c: usize) -> Value {
        let p = &self.phones[c];
        let variants: Vec<Value> = self.variants[p.variants.clone()]
            .iter()
            .map(|v| {
                let hist: Vec<Value> = self
                    .history
                    .get(&v.db_id)
                    .map(|h| h.iter().map(|(d, s, x)| json!({"date": d, "source": s, "price": x})).collect())
                    .unwrap_or_default();
                json!({
                    "ram": v.ram, "rom": v.rom, "price": v.price, "date": v.date, "source": v.source,
                    "msrp_usd": v.msrp_usd, "base": v.is_base, "q": v.q, "history": hist,
                })
            })
            .collect();
        let cameras: Vec<Value> = self.cameras[p.cameras.clone()]
            .iter()
            .map(|k| {
                json!({
                    "role": k.role, "position": k.position, "sensor": k.sensor, "sensor_size": k.sensor_size,
                    "pixel_um": r2o(k.sensor_pixel_um), "sensor_hw": k.sensor_hw, "mp": r2(k.mp),
                    "aperture": r2o(k.aperture), "focal_mm": r2o(k.focal_mm), "fov_deg": r2o(k.fov_deg),
                    "zoom": r2o(k.zoom), "ois": k.ois, "af": k.af, "min_focus_cm": r2o(k.min_focus_cm), "video": k.video, "hw": k.hw, "processing": k.processing,
                    "basis": k.basis, "total": k.total, "q": k.q,
                })
            })
            .collect();
        let quirks: Vec<Value> = self.quirks_sorted(c).iter().map(|q| self.quirk_json(q, true)).collect();
        let model_id = self.model_id(c);
        json!({
            "slug": p.slug, "brand": p.brand, "name": p.name, "full_name": p.full_name, "os": p.os, "year": p.year,
            "status": p.status, "availability_ru": p.availability, "form_factor": p.form, "in_ml": p.in_ml,
            "model_id": model_id, "chipset": p.chipset, "antutu_k": p.antutu_k, "q": p.q,
            "quirks_checked": p.quirks_checked,
            "specs": {
                "screen_in": r2(p.screen_in), "refresh_hz": p.refresh_hz, "battery_mah": p.battery_mah,
                "charge_w": r2(p.charge_w), "weight_g": r2(p.weight_g), "has_5g": p.has_5g, "nfc": p.nfc, "esim": p.esim,
                "ir_port": p.ir_port, "more": p.more,
            },
            "variants": variants,
            "cameras": cameras,
            "camera": self.camera_summary_json(c),
            "quirks": quirks,
            "special": self.special_json(c),
            "photos": self.photos_of(c),
        })
    }

    /// Раздел «Смартфоны с интересными возможностями»: категории и модели в них.
    pub fn special_list(&self) -> Value {
        let cats: Vec<Value> = self
            .categories
            .iter()
            .enumerate()
            .map(|(bit, cat)| {
                let mut phones: Vec<(u32, Value)> = self
                    .phones
                    .iter()
                    .enumerate()
                    .filter(|(_, p)| p.special_mask & (1u32 << bit) != 0)
                    .map(|(c, p)| {
                        let vs = &self.variants[p.variants.clone()];
                        let price_from = vs.iter().filter_map(|v| v.price).min();
                        let msrp = vs.iter().filter_map(|v| v.msrp_usd).min();
                        let note = self.specials[p.special.clone()]
                            .iter()
                            .find(|s| s.bit as usize == bit)
                            .and_then(|s| s.note.clone());
                        let model_id = self.model_id(c);
                        (
                            price_from.unwrap_or(u32::MAX),
                            json!({
                                "slug": p.slug, "full_name": p.full_name, "brand": p.brand, "year": p.year,
                                "price_from": price_from, "msrp_usd": msrp, "status": p.status,
                                "availability_ru": p.availability, "in_ml": p.in_ml, "model_id": model_id,
                                "note": note, "camera": p.summary.overall, "photos": self.photos_of(c), "q": p.q,
                            }),
                        )
                    })
                    .collect();
                phones.sort_by_key(|(price, _)| *price);
                json!({
                    "code": cat.code, "title": cat.title, "description": cat.description, "criteria": cat.criteria,
                    "phones": phones.into_iter().map(|(_, v)| v).collect::<Vec<_>>(),
                })
            })
            .collect();
        json!({"categories": cats})
    }

    /// Справочник для формы и шапки сайта.
    pub fn meta_json(&self) -> Value {
        let mut counts = vec![0usize; self.categories.len()];
        for p in &self.phones {
            for (b, c) in counts.iter_mut().enumerate() {
                if p.special_mask & (1u32 << b) != 0 {
                    *c += 1;
                }
            }
        }
        let real_photos = self
            .phones
            .iter()
            .filter(|p| self.photos[p.photos.clone()].iter().any(|ph| ph.origin != "placeholder"))
            .count();
        let random = self.phones.iter().filter(|p| self.cameras[p.cameras.clone()].iter().any(|k| k.q == "random")).count();
        json!({
            "name": self.dataset_name(),
            "version": self.meta.get("dataset_version"),
            "schema_version": self.meta.get("schema_version"),
            "origin": self.origin,
            "phones": self.phones.len(),
            "phones_in_ml": self.linked,
            "variants": self.variants.len(),
            "cameras": self.cameras.len(),
            "photos": self.photos.len(),
            "phones_with_real_photo": real_photos,
            "phones_with_random_data": random,
            "ram_options": self.ram_options,
            "categories": self.categories.iter().zip(&counts).map(|(c, n)| json!({
                "code": c.code, "title": c.title, "description": c.description, "count": n,
            })).collect::<Vec<_>>(),
            "quirk_types": self.quirk_types.iter().map(|t| json!({
                "code": t.code, "title": t.title, "category": t.category, "polarity": t.polarity,
            })).collect::<Vec<_>>(),
            "req_titles": REQ_KEYS.iter().zip(REQ_TITLES).map(|(k, t)| (k.to_string(), json!(t))).collect::<Map<_, _>>(),
        })
    }
}
