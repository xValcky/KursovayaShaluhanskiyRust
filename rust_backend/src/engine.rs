//! Подбор телефона по анкете — порт `src/ml/recommender.py` и `src/ml/labeling.py`.
//!
//! Итоговая оценка телефона i:
//!   score_i = û_i + 1.5 · P(класс_i | анкета) + bonus_i
//! где
//!   u_i   — полезность: z_i · w(анкета) − штраф за цену − штрафы за жёсткие требования (+ бонус «броневика»);
//!   û_i   — u_i, нормированная по подходящим телефонам: (u_i − mean) / std;
//!   P(·)  — вероятности классов 0/1/2 от нейросети MLP;
//!   bonus — мягкий бонус группы: 0.4 · Σ_j p_ij a_j / max(a), a_j — насколько группа j подходит анкете.

use crate::model::{Model, Phone};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

pub const CLUSTER_BONUS: f64 = 0.4;
pub const CLASS_BONUS: f64 = 1.5;
pub const MAX_SAME_BRAND_TOP5: usize = 2;
pub const MIN_GROUPS_TOP10: usize = 3;
pub const DEAL_THRESHOLD: f64 = 0.15;
pub const OVERPRICE_THRESHOLD: f64 = 0.20;
const AFFINITY_T: f64 = 0.5;

/// Число жёстких требований: 8 исходных + 5 из каталога v2 (ОЗУ, зум, макро, минусы, интересные возможности).
pub const N_REQ: usize = 13;
/// Ключи жёстких требований (кроме бюджета) в порядке REQUIREMENT_TITLES; новые — в конце, чтобы при нулевых
/// значениях выбор подсказки и все результаты совпадали с Python (golden-тест).
pub const REQ_KEYS: [&str; N_REQ] =
    ["floor", "os", "nfc", "esim", "ir", "screen", "hz", "rom", "ram", "zoom", "macro", "quirks", "special"];
pub const REQ_TITLES: [&str; N_REQ] = [
    "цена от", "система", "NFC", "eSIM — обязательно", "ИК-порт — обязательно", "размер экрана",
    "частота экрана", "объём памяти", "объём ОЗУ", "оптический зум", "макросъёмка", "серьёзные минусы",
    "интересные возможности",
];
/// Диапазон диагонали для screen_pref 0..4 (дюймы).
const SCREEN_RANGE: [(f64, f64); 5] = [(0.0, 99.0), (0.0, 6.3), (6.3, 6.75), (6.7, 99.0), (6.88, 99.0)];
const SEGMENTS: [(f64, f64, &str); 3] =
    [(0.0, 30_000.0, "Бюджетные"), (30_000.0, 60_000.0, "Средний бюджет"), (60_000.0, 1e9, "Флагманы")];
const SEGMENT_MIN_OVERLAP: f64 = 3_000.0;

/// Анкета. Важности imp_* — 0..10; os_pref: 0 неважно, 1 Android, 2 iOS;
/// esim_pref / ir_pref: 0 не нужен, 1 плюс, 2 обязательно; screen_pref: 0..4 (см. SCREEN_RANGE).
#[derive(Deserialize, Serialize, Clone, Debug)]
#[serde(default)]
pub struct Survey {
    pub budget_rub: f64,
    pub budget_min: f64,
    pub strict_range: f64,
    pub imp_camera: f64,
    pub imp_active: f64,
    pub imp_standby: f64,
    pub imp_perf: f64,
    pub imp_compact: f64,
    pub imp_charge: f64,
    pub imp_storage: f64,
    pub imp_new: f64,
    pub imp_value: f64,
    pub imp_rugged: f64,
    pub gaming: f64,
    pub photo: f64,
    pub work: f64,
    pub os_pref: f64,
    pub need_nfc: f64,
    pub esim_pref: f64,
    pub ir_pref: f64,
    pub screen_pref: f64,
    pub min_hz: f64,
    pub min_rom: f64,
    // ── каталог v2 (0 — не учитывать; старые анкеты и golden-тест их не содержат) ──
    /// ОЗУ от, ГБ: подходит модель, у которой есть такая конфигурация
    pub min_ram: f64,
    /// оптический зум от, крат (по телевикам)
    pub min_optical_zoom: f64,
    /// 1 — нужна макросъёмка (отдельный модуль, ширик с автофокусом или телемакро)
    pub need_macro: f64,
    /// 1 — скрыть модели с отрицательными «приколами» тяжести ≥ 2
    pub hide_bad_quirks: f64,
    /// 1 — только «Смартфоны с интересными возможностями»
    pub special_only: f64,
    /// коды категорий (trifold, book_fold, flip…); пусто — любая категория
    pub special: Vec<String>,
}

impl Default for Survey {
    fn default() -> Self {
        Survey {
            budget_rub: 35_000.0, budget_min: 0.0, strict_range: 0.0,
            imp_camera: 5.0, imp_active: 6.0, imp_standby: 4.0, imp_perf: 5.0, imp_compact: 3.0,
            imp_charge: 4.0, imp_storage: 4.0, imp_new: 3.0, imp_value: 5.0, imp_rugged: 0.0,
            gaming: 0.0, photo: 0.0, work: 0.0, os_pref: 0.0, need_nfc: 1.0, esim_pref: 0.0, ir_pref: 0.0,
            screen_pref: 0.0, min_hz: 0.0, min_rom: 128.0,
            min_ram: 0.0, min_optical_zoom: 0.0, need_macro: 0.0, hide_bad_quirks: 0.0, special_only: 0.0,
            special: Vec::new(),
        }
    }
}

impl Survey {
    /// Защита от мусора во входных данных (отрицательный бюджет, неизвестный размер экрана и т. п.).
    pub fn sanitize(mut self) -> Self {
        let fix = |v: f64, lo: f64, hi: f64| if v.is_finite() { v.clamp(lo, hi) } else { lo };
        self.budget_rub = fix(self.budget_rub, 1_000.0, 1e7);
        self.budget_min = fix(self.budget_min, 0.0, 1e7);
        for v in [
            &mut self.imp_camera, &mut self.imp_active, &mut self.imp_standby, &mut self.imp_perf,
            &mut self.imp_compact, &mut self.imp_charge, &mut self.imp_storage, &mut self.imp_new,
            &mut self.imp_value, &mut self.imp_rugged,
        ] {
            *v = fix(*v, 0.0, 10.0);
        }
        for v in [
            &mut self.gaming, &mut self.photo, &mut self.work, &mut self.need_nfc, &mut self.strict_range,
            &mut self.need_macro, &mut self.hide_bad_quirks, &mut self.special_only,
        ] {
            *v = fix(v.round(), 0.0, 1.0);
        }
        self.os_pref = fix(self.os_pref.round(), 0.0, 2.0);
        self.esim_pref = fix(self.esim_pref.round(), 0.0, 2.0);
        self.ir_pref = fix(self.ir_pref.round(), 0.0, 2.0);
        self.screen_pref = fix(self.screen_pref.round(), 0.0, 4.0);
        self.min_hz = fix(self.min_hz, 0.0, 1000.0);
        self.min_rom = fix(self.min_rom, 1.0, 1e5);
        self.min_ram = fix(self.min_ram, 0.0, 64.0);
        self.min_optical_zoom = fix(self.min_optical_zoom, 0.0, 20.0);
        self.special.truncate(32);
        self
    }

    /// Вектор анкеты для нейросети (порядок SURVEY_FIELDS; ln бюджета, log2 памяти).
    fn vector(&self) -> [f64; 21] {
        [
            self.budget_rub.ln(), self.imp_camera, self.imp_active, self.imp_standby, self.imp_perf,
            self.imp_compact, self.imp_charge, self.imp_storage, self.imp_new, self.imp_value, self.imp_rugged,
            self.gaming, self.photo, self.work, self.os_pref, self.need_nfc, self.esim_pref, self.ir_pref,
            self.screen_pref, self.min_hz, self.min_rom.log2(),
        ]
    }

    fn strict(&self) -> bool {
        self.strict_range != 0.0
    }

    /// Нижняя граница цены: «цена от» − 10% (в строгом режиме сегментов — ровно «от»).
    fn floor_price(&self) -> f64 {
        if self.strict() { self.budget_min } else { 0.9 * self.budget_min }
    }
}

/// Данные моделей для одного запроса: цена и память выбранной конфигурации + признаки каталога v2.
///
/// `Ext::base` — ровно данные из model.json: так считает прежний подбор (`recommend`), его сверяет golden-тест.
/// `Catalog::ext` — цена/память конфигурации, подходящей под ОЗУ/ПЗУ анкеты, и камеры/«приколы»/категории.
#[derive(Clone, Debug)]
pub struct Ext {
    pub price: Vec<f64>,
    pub ram: Vec<f64>,
    pub rom: Vec<f64>,
    /// оптический зум (лучший телевик), 1.0 — телевика нет
    pub zoom: Vec<f64>,
    pub macro_ok: Vec<bool>,
    /// есть отрицательный «прикол» тяжести ≥ 2
    pub bad_quirk: Vec<bool>,
    /// битовая маска категорий «интересных возможностей»
    pub special: Vec<u32>,
    /// какие категории ищет пользователь при special_only: 0 — любая
    pub special_req: u32,
    /// одна модель каталога может соответствовать нескольким строкам model.json («iPhone 17 Pro» и «… 512GB»)
    pub group: Vec<usize>,
    /// оставлять в выдаче одну строку на группу (лучшую по оценке)
    pub dedupe: bool,
}

impl Ext {
    pub fn base(m: &Model) -> Ext {
        Ext {
            price: m.phones.iter().map(|p| p.price_rub).collect(),
            ram: m.phones.iter().map(|p| p.ram_gb).collect(),
            rom: m.phones.iter().map(|p| p.rom_gb).collect(),
            zoom: vec![1.0; m.n],
            macro_ok: vec![true; m.n],
            bad_quirk: vec![false; m.n],
            special: vec![0; m.n],
            special_req: 0,
            group: (0..m.n).collect(),
            dedupe: false,
        }
    }
}

/// Веса анкеты по стандартизованным признакам телефона (preference_weights).
pub fn preference_weights(m: &Model, a: &Survey) -> Vec<f64> {
    let f = &m.fi;
    let (cam, perf, comp) = (a.imp_camera / 10.0, a.imp_perf / 10.0, a.imp_compact / 10.0);
    let (act, stb) = (a.imp_active / 10.0, a.imp_standby / 10.0);
    let (chg, sto, new) = (a.imp_charge / 10.0, a.imp_storage / 10.0, a.imp_new / 10.0);
    let big = [0.0, -0.8, 0.0, 0.6, 0.9][a.screen_pref as usize];
    let mut w = vec![0.0; m.nf];
    w[f.camera] = cam + 0.5 * a.photo;
    w[f.antutu] = perf + 0.8 * a.gaming + 0.2;
    w[f.refresh] = 0.4 * perf + 0.4 * a.gaming + if a.min_hz >= 120.0 { 0.4 } else { 0.0 };
    w[f.ram] = 0.4 * perf + 0.3 * a.work + 0.3 * sto;
    w[f.rom] = 0.3 * a.photo + 0.8 * sto + 0.1;
    w[f.battery] = 0.2 * (act + stb);
    w[f.active] = act;
    w[f.standby] = stb;
    w[f.charge] = chg + 0.2 * act;
    w[f.screen] = big - 0.5 * comp;
    w[f.weight] = -0.9 * comp - 0.2;
    w[f.year] = 0.1 + 0.8 * new;
    w[f.g5] = 0.2;
    w[f.nfc] = 0.2;
    w[f.esim] = 0.1 * a.work + if a.esim_pref == 1.0 { 0.4 } else { 0.0 };
    w[f.ir] = if a.ir_pref == 1.0 { 0.4 } else { 0.0 };
    w
}

/// Штрафы за жёсткие требования (кроме бюджета) для одного телефона — порядок REQ_KEYS.
fn penalties(a: &Survey, p: &Phone, floor: f64, e: &Ext, i: usize) -> [f64; N_REQ] {
    let os_bad = match a.os_pref as u8 {
        1 => p.os != "Android",
        2 => p.os != "iOS",
        _ => false,
    };
    let (lo, hi) = SCREEN_RANGE[a.screen_pref as usize];
    let miss = (lo - p.screen_in).max(0.0) + (p.screen_in - hi).max(0.0);
    let b = |c: bool, v: f64| if c { v } else { 0.0 };
    let special_miss = if e.special_req == 0 { e.special[i] == 0 } else { e.special[i] & e.special_req == 0 };
    [
        if floor > 0.0 { 30.0 * (1.0 - e.price[i] / floor).max(0.0) } else { 0.0 },
        b(os_bad, 6.0),
        b(a.need_nfc != 0.0 && p.nfc == 0.0, 6.0),
        b(a.esim_pref == 2.0 && p.esim == 0.0, 6.0),
        b(a.ir_pref == 2.0 && p.ir_port == 0.0, 6.0),
        15.0 * miss + b(miss > 0.0, 3.0),
        b(p.refresh_hz < a.min_hz, 5.0),
        b(e.rom[i] < a.min_rom, 3.0),
        b(e.ram[i] < a.min_ram, 3.0),
        b(a.min_optical_zoom > 0.0 && e.zoom[i] + 1e-9 < a.min_optical_zoom, 4.0),
        b(a.need_macro != 0.0 && !e.macro_ok[i], 3.0),
        b(a.hide_bad_quirks != 0.0 && e.bad_quirk[i], 4.0),
        b(a.special_only != 0.0 && special_miss, 6.0),
    ]
}

/// Промежуточные величины одной анкеты.
pub struct Scored {
    pub proba: Vec<f64>,
    pub score: Vec<f64>,
    pub pen: Vec<[f64; N_REQ]>,
    pub feasible: Vec<bool>,
    pub best_cluster: usize,
    pub weights: Vec<f64>,
}

fn feasible_with(m: &Model, a: &Survey, pen: &[[f64; N_REQ]], mut slack: f64, e: &Ext) -> Vec<bool> {
    if a.strict() && slack < 2.0 {
        slack = 1.0;
    }
    (0..m.n).map(|i| pen[i].iter().sum::<f64>() == 0.0 && e.price[i] <= a.budget_rub * slack).collect()
}

pub fn score(m: &Model, a: &Survey) -> Scored {
    score_ext(m, a, &Ext::base(m))
}

pub fn score_ext(m: &Model, a: &Survey, e: &Ext) -> Scored {
    let w = preference_weights(m, a);
    let floor = a.floor_price();
    let pen: Vec<[f64; N_REQ]> =
        m.phones.iter().enumerate().map(|(i, p)| penalties(a, p, floor, e, i)).collect();
    let value_k = 0.3 + 2.0 * a.imp_value / 10.0;
    let rugged_k = a.imp_rugged / 10.0 * 1.2;
    // полезность u_i
    let u: Vec<f64> = (0..m.n)
        .map(|i| {
            let zi = &m.z[i * m.nf..(i + 1) * m.nf];
            let ratio = e.price[i] / a.budget_rub;
            let mut v: f64 = zi.iter().zip(&w).map(|(z, w)| z * w).sum();
            v -= value_k * ratio;
            if m.rugged[i] {
                v += rugged_k;
            }
            v - 30.0 * (ratio - 1.0).max(0.0) - pen[i].iter().sum::<f64>()
        })
        .collect();

    // признаки групп для нейросети: лучшая полезность в группе и доля телефонов по карману
    let mut input = a.vector().to_vec();
    for mem in &m.members {
        input.push(mem.iter().map(|&i| u[i]).fold(f64::NEG_INFINITY, f64::max));
    }
    for mem in &m.members {
        let ok = mem.iter().filter(|&&i| e.price[i] <= a.budget_rub).count();
        input.push(ok as f64 / mem.len() as f64);
    }
    let proba = m.mlp.predict_proba(&input);

    let feasible = feasible_with(m, a, &pen, 1.1, e);
    let nfeas = feasible.iter().filter(|&&f| f).count();
    let refv: Vec<f64> = if nfeas >= 5 {
        (0..m.n).filter(|&i| feasible[i]).map(|i| u[i]).collect()
    } else {
        u.clone()
    };
    let mean = refv.iter().sum::<f64>() / refv.len() as f64;
    let std = (refv.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / refv.len() as f64).sqrt();
    let un: Vec<f64> = u.iter().map(|v| (v - mean) / (std + 1e-9)).collect();

    // a_j — сродство группы анкете (softmax по лучшей нормированной полезности в группе)
    let best_u: Vec<f64> =
        m.members.iter().map(|mem| mem.iter().map(|&i| un[i]).fold(f64::NEG_INFINITY, f64::max)).collect();
    let mx = best_u.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let mut aff: Vec<f64> = best_u.iter().map(|b| ((b - mx) / AFFINITY_T).exp()).collect();
    let s: f64 = aff.iter().sum();
    aff.iter_mut().for_each(|v| *v /= s);
    let amax = aff.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let best_cluster = aff.iter().position(|&v| v == amax).unwrap_or(0);

    let score = (0..m.n)
        .map(|i| {
            let pi = &m.p[i * m.k..(i + 1) * m.k];
            let bonus = CLUSTER_BONUS * pi.iter().zip(&aff).map(|(p, a)| p * a).sum::<f64>() / amax;
            un[i] + CLASS_BONUS * proba[m.labels[i]] + bonus
        })
        .collect();
    Scored { proba, score, pen, feasible, best_cluster, weights: w }
}

/// Индексы по убыванию оценки (стабильно, как np.argsort(-score, kind="stable")).
fn order_desc(score: &[f64]) -> Vec<usize> {
    let mut o: Vec<usize> = (0..score.len()).collect();
    o.sort_by(|&a, &b| score[b].partial_cmp(&score[a]).unwrap_or(std::cmp::Ordering::Equal));
    o
}

/// Разнообразие: ≤ 2 модели бренда в топ-5 и ≥ 3 группы в топ-10.
fn diversify(m: &Model, order: &[usize], n: usize, limit_brands: bool) -> Vec<usize> {
    let mut chosen: Vec<usize> = Vec::with_capacity(n);
    let mut rest: Vec<usize> = order.to_vec();
    while !rest.is_empty() && chosen.len() < n {
        let groups: HashSet<usize> = chosen.iter().map(|&j| m.clusters[j]).collect();
        let pick = rest.iter().position(|&i| {
            if limit_brands
                && chosen.len() < 5
                && chosen.iter().filter(|&&j| m.phones[j].brand == m.phones[i].brand).count() >= MAX_SAME_BRAND_TOP5
            {
                return false;
            }
            !((7..10).contains(&chosen.len())
                && groups.len() < MIN_GROUPS_TOP10
                && groups.contains(&m.clusters[i]))
        });
        chosen.push(rest.remove(pick.unwrap_or(0)));
    }
    chosen
}

#[derive(Serialize, Clone, Debug)]
pub struct Row {
    pub id: i64,
    pub name: String,
    pub brand: String,
    pub os: String,
    pub price: i64,
    pub fair: Option<i64>,
    pub class_id: usize,
    pub class_name: String,
    pub cluster: String,
    pub ram_gb: f64,
    pub rom_gb: f64,
    pub screen_in: f64,
    pub refresh_hz: f64,
    pub battery_mah: f64,
    pub charge_w: f64,
    pub weight_g: f64,
    pub camera_mp: f64,
    pub antutu_k: f64,
    pub active_h: f64,
    pub standby_d: f64,
    pub year: f64,
    pub nfc: bool,
    pub esim: bool,
    pub ir: bool,
    pub g5: bool,
    pub score: f64,
    pub reasons: String,
    pub pros: Vec<String>,
    pub badges: Vec<String>,
    pub unmet: Vec<String>,
    pub ok: bool,
    /// поля каталога v2 (slug, конфигурации, камеры, «приколы», категории) — дописывает `Catalog::decorate`
    #[serde(flatten, skip_serializing_if = "Option::is_none")]
    pub extra: Option<serde_json::Map<String, serde_json::Value>>,
}

#[derive(Serialize, Clone, Debug)]
pub struct Block {
    pub title: String,
    pub note: Option<String>,
    pub rows: Vec<Row>,
}

#[derive(Serialize, Clone, Debug)]
pub struct Hint {
    pub key: String,
    pub count: usize,
    pub text: String,
    /// Цена самого дешёвого телефона, который проходит все условия, кроме бюджета (для кнопки «поднять бюджет»).
    pub cheapest: Option<i64>,
}

#[derive(Serialize, Clone, Debug)]
pub struct Recommendation {
    pub proba: Vec<f64>,
    pub pred_class: usize,
    pub class_name: String,
    pub best_cluster: usize,
    pub cluster_name: String,
    pub feasible: usize,
    pub table: Vec<Row>,
    pub blocks: Vec<Block>,
    pub hint: Option<Hint>,
}

#[derive(Serialize, Clone, Debug)]
pub struct Segment {
    pub title: String,
    pub range: String,
    pub lo: f64,
    pub hi: f64,
    pub result: Recommendation,
}

/// 12345.6 → «12 346» (как f"{x:,.0f}".replace(",", " ")).
#[allow(clippy::manual_is_multiple_of)] // % вместо is_multiple_of — чтобы собиралось и на старых Rust
pub fn rub(x: f64) -> String {
    let r = x.round() as i64;
    let digits = r.abs().to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(' ');
        }
        out.push(ch);
    }
    if r < 0 { format!("-{out}") } else { out }
}

fn plain_pros(p: &Phone, top: &[&str], ram: f64, rom: f64) -> Vec<String> {
    let mut out: Vec<String> = top
        .iter()
        .filter_map(|f| {
            Some(match *f {
                "camera_mp" => format!("камера {:.0} МП", p.camera_mp),
                "active_h" => format!("≈{:.0} ч активной работы", p.active_h),
                "standby_d" => format!("≈{:.0} дней в ожидании", p.standby_d),
                "battery_mah" => format!("батарея {:.0} мА·ч", p.battery_mah),
                "antutu_k" => format!("мощный чип (AnTuTu {:.0} тыс.)", p.antutu_k),
                "refresh_hz" => format!("плавный экран {:.0} Гц", p.refresh_hz),
                "charge_w" => format!("зарядка {:.0} Вт", p.charge_w),
                "rom_gb" => format!("память {:.0} ГБ", rom),
                "ram_gb" => format!("ОЗУ {:.0} ГБ", ram),
                "screen_in" => format!("экран {}″", p.screen_in),
                "weight_g" => format!("лёгкий, {:.0} г", p.weight_g),
                "year" => format!("модель {:.0} года", p.year),
                "esim" => "есть eSIM".into(),
                "ir_port" => "есть ИК-порт (пульт)".into(),
                "nfc" => "есть NFC".into(),
                "has_5g" => "есть 5G".into(),
                _ => return None,
            })
        })
        .take(3)
        .collect();
    if out.is_empty() {
        out.push("сбалансированный вариант".into());
    }
    out
}

fn price_badges(p: &Phone, fair: Option<f64>) -> Vec<String> {
    let mut b = Vec::new();
    if p.price_source.starts_with("Telegram") {
        b.push("цена из канала, без пошлины".to_string());
    } else if p.price_source.starts_with("маркетплейсы") {
        b.push("цена — оценка".to_string());
    }
    if let Some(f) = fair.filter(|f| *f > 0.0) {
        let diff = p.price_rub / f - 1.0;
        if diff <= -DEAL_THRESHOLD {
            b.push(format!("выгодно {:+.0}%", diff * 100.0));
        } else if diff >= OVERPRICE_THRESHOLD {
            b.push(format!("переплата {:+.0}%", diff * 100.0));
        }
    }
    if p.weight_g >= crate::model::RUGGED_WEIGHT_G {
        b.push("защищённый".into());
    }
    b
}

/// Какие жёсткие требования телефон не выполняет (подпись «не подходит: …»).
fn unmet(a: &Survey, price: f64, pen: &[f64; N_REQ]) -> Vec<String> {
    let mut v: Vec<String> =
        pen.iter().zip(REQ_TITLES).filter(|(x, _)| **x > 0.0).map(|(_, t)| t.to_string()).collect();
    if price > a.budget_rub * 1.1 {
        v.push("бюджет".into());
    }
    v
}

fn make_row(m: &Model, s: &Scored, a: &Survey, i: usize, with_unmet: bool, e: &Ext) -> Row {
    let p = &m.phones[i];
    let price = e.price[i];
    let zi = &m.z[i * m.nf..(i + 1) * m.nf];
    let contrib: Vec<f64> = zi.iter().zip(&s.weights).map(|(z, w)| z * w).collect();
    let mut idx: Vec<usize> = (0..m.nf).collect();
    idx.sort_by(|&x, &y| contrib[y].partial_cmp(&contrib[x]).unwrap_or(std::cmp::Ordering::Equal));
    let top: Vec<usize> = idx.into_iter().take(4).filter(|&j| contrib[j] > 0.0).collect();
    let mut reasons =
        top.iter().take(3).map(|&j| m.feature_names_ru[j].as_str()).collect::<Vec<_>>().join(", ");
    if price > a.budget_rub {
        reasons += &format!("; дороже бюджета на {} ₽", rub(price - a.budget_rub));
    }
    let unmet_list = if with_unmet { unmet(a, price, &s.pen[i]) } else { Vec::new() };
    if !unmet_list.is_empty() {
        reasons += &format!("; не подходит: {}", unmet_list.join(", "));
    }
    if reasons.is_empty() {
        reasons = "сбалансированный вариант".into();
    }
    let fair = m.fair.get(&p.id).copied();
    let top_names: Vec<&str> = top.iter().map(|&j| m.features[j].as_str()).collect();
    Row {
        id: p.id,
        name: p.full_name.clone(),
        brand: p.brand.clone(),
        os: p.os.clone(),
        price: price.round() as i64,
        fair: fair.map(|f| ((f / 100.0).round() * 100.0) as i64),
        class_id: m.labels[i],
        class_name: m.class_names[m.labels[i]].clone(),
        cluster: m.cluster_names[m.clusters[i]].clone(),
        ram_gb: e.ram[i], rom_gb: e.rom[i], screen_in: p.screen_in, refresh_hz: p.refresh_hz,
        battery_mah: p.battery_mah, charge_w: p.charge_w, weight_g: p.weight_g, camera_mp: p.camera_mp,
        antutu_k: p.antutu_k, active_h: p.active_h, standby_d: p.standby_d, year: p.year,
        nfc: p.nfc != 0.0, esim: p.esim != 0.0, ir: p.ir_port != 0.0, g5: p.has_5g != 0.0,
        score: (s.score[i] * 100.0).round() / 100.0,
        reasons,
        pros: plain_pros(p, &top_names, e.ram[i], e.rom[i]),
        badges: price_badges(p, fair),
        ok: unmet_list.is_empty(),
        unmet: unmet_list,
        extra: None,
    }
}

fn weak_result_hint(m: &Model, a: &Survey, s: &Scored, e: &Ext) -> Option<Hint> {
    let count = s.feasible.iter().filter(|&&f| f).count();
    if count >= 8 {
        return None;
    }
    let mut cut = [0usize; N_REQ];
    for i in 0..m.n {
        if e.price[i] <= a.budget_rub * 1.1 {
            for (c, v) in cut.iter_mut().zip(&s.pen[i]) {
                if *v > 0.0 {
                    *c += 1;
                }
            }
        }
    }
    let best = cut.iter().enumerate().fold(0, |b, (j, &c)| if c > cut[b] { j } else { b });
    // самый дешёвый телефон, который проходит все требования, кроме бюджета и «цены от»
    let cheapest_ok = (0..m.n)
        .filter(|&i| s.pen[i][1..].iter().all(|v| *v == 0.0))
        .map(|i| e.price[i])
        .fold(f64::INFINITY, f64::min);
    let cheapest = cheapest_ok.is_finite().then(|| cheapest_ok.round() as i64);
    if cut[best] == 0 {
        let any = feasible_with(m, a, &s.pen, 99.0, e);
        let cheap_any = (0..m.n).filter(|&i| any[i]).map(|i| e.price[i]).fold(f64::INFINITY, f64::min);
        let text = if cheap_any.is_finite() {
            format!("Подходящих телефонов всего {count}: мал бюджет. Самый дешёвый подходящий — {} ₽.", rub(cheap_any))
        } else {
            "Под эти требования телефонов нет.".into()
        };
        return Some(Hint { key: "budget".into(), count, text, cheapest });
    }
    let mut text = format!(
        "Подходящих телефонов всего {count}. Больше всего моделей в бюджете отсекло требование «{}» ({} шт.).",
        REQ_TITLES[best], cut[best]
    );
    if let Some(c) = cheapest.filter(|&c| c as f64 > a.budget_rub * 1.1) {
        text += &format!(" Самый дешёвый телефон, который проходит все остальные условия, стоит {} ₽.", rub(c as f64));
    }
    Some(Hint { key: REQ_KEYS[best].into(), count, text, cheapest })
}

fn result_blocks(m: &Model, a: &Survey, s: &Scored, picked: &[usize], e: &Ext) -> Vec<Block> {
    let any_feas = s.feasible.iter().any(|&f| f);
    let allowed: Vec<bool> = if any_feas { s.feasible.clone() } else { vec![true; m.n] };
    let allowed_premium = if any_feas { feasible_with(m, a, &s.pen, 1.25, e) } else { allowed.clone() };
    let order = order_desc(&s.score);
    let best = picked[0];
    let price = |i: usize| e.price[i];
    // занятые группы (в базовом режиме группа = сама модель, как раньше)
    let mut used: HashSet<usize> = HashSet::from([e.group[best]]);
    let mut blocks =
        vec![Block { title: "Лучший выбор".into(), note: None, rows: vec![make_row(m, s, a, best, false, e)] }];

    let mut take = |blocks: &mut Vec<Block>, mask: &dyn Fn(usize) -> bool, ok: &[bool], title: &str, note: &str| {
        let mut cand: Vec<usize> = Vec::new();
        for i in order.iter().copied() {
            if cand.len() == 2 {
                break;
            }
            if mask(i) && ok[i] && !used.contains(&e.group[i]) && !cand.iter().any(|&c| e.group[c] == e.group[i]) {
                cand.push(i);
            }
        }
        if !cand.is_empty() {
            used.extend(cand.iter().map(|&i| e.group[i]));
            blocks.push(Block {
                title: title.into(),
                note: Some(note.into()),
                rows: cand.iter().map(|&i| make_row(m, s, a, i, false, e)).collect(),
            });
        }
    };
    let cb = m.clusters[best];
    take(&mut blocks, &|i| m.clusters[i] == cb && price(i) <= price(best) * 0.85, &allowed,
         "Дешевле с тем же характером", "та же группа, цена ниже минимум на 15%");
    let lb = m.labels[best];
    take(&mut blocks, &|i| price(i) > price(best) && price(i) <= a.budget_rub * 1.25 && m.labels[i] >= lb,
         &allowed_premium, "Премиум-альтернатива", "дороже, но до +25% к бюджету");
    let top3: HashSet<usize> = picked.iter().take(3).map(|&i| m.clusters[i]).collect();
    take(&mut blocks, &|i| !top3.contains(&m.clusters[i]) && price(i) <= a.budget_rub * 1.1, &allowed,
         "Из другой группы", "другой характер железа");
    blocks
}

pub fn recommend(m: &Model, a: &Survey, top_n: usize, diverse: bool, pad: bool) -> Recommendation {
    recommend_ext(m, a, top_n, diverse, pad, &Ext::base(m))
}

/// Подбор с данными каталога v2 (конфигурации памяти, камеры, «приколы», категории).
pub fn recommend_ext(m: &Model, a: &Survey, top_n: usize, diverse: bool, pad: bool, e: &Ext) -> Recommendation {
    let s = score_ext(m, a, e);
    let pred_class = (0..s.proba.len()).fold(0, |b, j| if s.proba[j] > s.proba[b] { j } else { b });
    let order = order_desc(&s.score);
    let mut ok: Vec<usize> = order.iter().copied().filter(|&i| s.feasible[i]).collect();
    let mut bad: Vec<usize> = order.iter().copied().filter(|&i| !s.feasible[i]).collect();
    if e.dedupe {
        let mut seen = HashSet::new();
        ok.retain(|&i| seen.insert(e.group[i]));
        bad.retain(|&i| seen.insert(e.group[i]));
    }
    let limit_brands = a.os_pref != 2.0; // iOS — это только Apple
    let mut picked =
        if diverse { diversify(m, &ok, top_n, limit_brands) } else { ok.iter().copied().take(top_n).collect() };
    if pad || picked.is_empty() {
        let need = top_n.saturating_sub(picked.len());
        picked.extend(bad.iter().take(need));
    }
    let table = picked.iter().map(|&i| make_row(m, &s, a, i, true, e)).collect();
    let blocks = if picked.is_empty() { Vec::new() } else { result_blocks(m, a, &s, &picked, e) };
    let feasible = if e.dedupe { ok.len() } else { s.feasible.iter().filter(|&&f| f).count() };
    Recommendation {
        pred_class,
        class_name: m.class_names[pred_class].clone(),
        best_cluster: s.best_cluster,
        cluster_name: m.cluster_names[s.best_cluster].clone(),
        feasible,
        hint: weak_result_hint(m, a, &s, e),
        proba: s.proba,
        table,
        blocks,
    }
}

/// Ценовые сегменты, которые вилка «от–до» задевает хотя бы на 3 000 ₽.
pub fn price_segments(bmin: f64, bmax: f64) -> Vec<(f64, f64, &'static str)> {
    SEGMENTS
        .iter()
        .filter_map(|&(lo, hi, name)| {
            let (p_lo, p_hi) = (bmin.max(lo), bmax.min(hi));
            (p_hi - p_lo >= SEGMENT_MIN_OVERLAP)
                .then_some((p_lo + if p_lo == lo && lo > 0.0 { 1.0 } else { 0.0 }, p_hi, name))
        })
        .collect()
}

/// Если вилка задевает 2+ сегмента — отдельная выдача по каждому (recommend_by_segments).
pub fn recommend_by_segments(m: &Model, a: &Survey, top_n: usize, diverse: bool) -> Vec<Segment> {
    recommend_by_segments_ext(m, a, top_n, diverse, &Ext::base(m))
}

pub fn recommend_by_segments_ext(m: &Model, a: &Survey, top_n: usize, diverse: bool, e: &Ext) -> Vec<Segment> {
    let segs = price_segments(a.budget_min, a.budget_rub);
    if segs.len() < 2 {
        return Vec::new();
    }
    segs.into_iter()
        .map(|(lo, hi, name)| {
            let sa = Survey { budget_min: lo, budget_rub: hi, strict_range: 1.0, ..a.clone() };
            let hi_txt = if hi >= 1e9 { "и дороже".to_string() } else { format!("{} ₽", rub(hi)) };
            let lo_v = if lo % 1000.0 == 1.0 { (lo - 1.0).max(0.0) } else { lo };
            Segment {
                title: name.into(),
                range: format!("{} – {}", rub(lo_v), hi_txt),
                lo,
                hi,
                result: recommend_ext(m, &sa, top_n, diverse, false, e),
            }
        })
        .collect()
}

/// Для сайта: в выдаче только телефоны, которые проходят условия анкеты.
/// Python-версия, если подходящих нет, добивает выдачу ближайшими неподходящими (с пометкой «не подходит»),
/// из-за чего при iOS в дешёвом сегменте показывались Android-телефоны дороже бюджета. Здесь вместо этого —
/// пустая выдача и подсказка, какое условие ослабить.
pub fn drop_unfit(r: &mut Recommendation) {
    if r.feasible == 0 {
        r.table.clear();
        r.blocks.clear();
    } else {
        r.table.retain(|row| row.ok);
    }
}
