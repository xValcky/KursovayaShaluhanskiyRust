//! HTTP-сервер подбора смартфона — версия «каталог v2 — настоящие данные».
//!
//!   GET  /                         — веб-страница (static/index.html, встроена в бинарник)
//!   GET  /api/meta                 — справочник для интерфейса: ползунки, пресеты, метрики модели, каталог v2
//!   POST /api/recommend            — подбор по анкете (JSON) → выдача, блоки, сегменты, время расчёта
//!   GET  /api/phone/{slug}         — карточка модели целиком: конфигурации, история цен, камеры, «приколы», фото
//!   GET  /api/special              — «Смартфоны с интересными возможностями» по категориям
//!   GET  /api/bench?n=…            — замер скорости на n случайных анкетах
//!   GET  /api/photos               — фото по id модели: своя папка photos/ + каталог (Wikimedia, заглушки)
//!   GET  /photos/…                 — файлы из папки photos/ (например photos/57.jpg, photos/57-2.webp)
//!   GET  /placeholder/{slug}/{n}   — SVG-заглушка «демо-фото», пока нет настоящего снимка
//!   GET  /fonts/…                  — шрифты страницы (встроены в бинарник)
//!
//! Переменные окружения: PORT (по умолчанию 8080), MODEL_PATH (иначе — встроенный model.json),
//! CATALOG_PATH (иначе catalog.db рядом с exe / в текущей папке / встроенный), PHOTOS_DIR (по умолчанию photos).

use axum::{
    extract::{Path, Query, State},
    http::{header, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use phonepicker_server::{bench, catalog::Catalog, engine, model::Model};
use serde::{Deserialize, Serialize};
use std::{sync::Arc, time::Instant};
use tower_http::{compression::CompressionLayer, cors::CorsLayer};

const INDEX_HTML: &str = include_str!("../static/index.html");
const EMBEDDED_MODEL: &str = include_str!("../model.json");

/// Шрифты Unbounded и Onest (SIL OFL) встроены в бинарник: страница выглядит одинаково и без интернета.
macro_rules! fonts {
    ($($name:literal),* $(,)?) => { &[$(($name, include_bytes!(concat!("../static/fonts/", $name)) as &[u8])),*] };
}
const FONTS: &[(&str, &[u8])] = fonts![
    "unbounded-cyrillic-500-normal.woff2", "unbounded-latin-500-normal.woff2",
    "unbounded-cyrillic-700-normal.woff2", "unbounded-latin-700-normal.woff2",
    "unbounded-cyrillic-800-normal.woff2", "unbounded-latin-800-normal.woff2",
    "onest-cyrillic-400-normal.woff2", "onest-latin-400-normal.woff2",
    "onest-cyrillic-500-normal.woff2", "onest-latin-500-normal.woff2",
    "onest-cyrillic-600-normal.woff2", "onest-latin-600-normal.woff2",
    "onest-cyrillic-700-normal.woff2", "onest-latin-700-normal.woff2",
];

struct App {
    m: Model,
    cat: Catalog,
}
type AppState = State<Arc<App>>;

#[derive(Deserialize)]
struct RecommendRequest {
    #[serde(flatten)]
    survey: engine::Survey,
    #[serde(default = "default_top")]
    top_n: usize,
    #[serde(default = "yes")]
    diverse: bool,
    /// разбить выдачу на ценовые категории (Бюджетные / Средний бюджет / Флагманы)
    #[serde(default)]
    split: bool,
    /// учитывать конфигурации памяти: цена и память — подходящей конфигурации, одна строка на модель
    #[serde(default)]
    variants: bool,
}
fn default_top() -> usize {
    10
}
fn yes() -> bool {
    true
}

#[derive(Serialize)]
struct RecommendResponse {
    #[serde(flatten)]
    result: engine::Recommendation,
    segments: Vec<engine::Segment>,
    total: usize,
    compute_us: f64,
}

async fn recommend(State(app): AppState, Json(req): Json<RecommendRequest>) -> Json<RecommendResponse> {
    let t0 = Instant::now();
    let (m, cat) = (&app.m, &app.cat);
    let a = req.survey.sanitize();
    let top_n = req.top_n.clamp(1, 50);
    let e = cat.ext(m, &a, req.variants);
    let mut result = engine::recommend_ext(m, &a, top_n, req.diverse, false, &e);
    engine::drop_unfit(&mut result);
    let mut segments =
        if req.split { engine::recommend_by_segments_ext(m, &a, top_n, req.diverse, &e) } else { Vec::new() };
    for seg in &mut segments {
        engine::drop_unfit(&mut seg.result);
        let sa = engine::Survey { budget_min: seg.lo, budget_rub: seg.hi, ..a.clone() };
        cat.decorate(&mut seg.result, &sa);
    }
    cat.decorate(&mut result, &a);
    let compute_us = t0.elapsed().as_secs_f64() * 1e6;
    let total = if req.variants { cat.linked } else { m.n };
    Json(RecommendResponse { result, segments, total, compute_us })
}

async fn meta(State(app): AppState) -> impl IntoResponse {
    let m = &app.m;
    let mut sizes = vec![0usize; m.k];
    m.clusters.iter().for_each(|&c| sizes[c] += 1);
    let groups: Vec<_> = m
        .cluster_names
        .iter()
        .zip(&sizes)
        .map(|(n, s)| serde_json::json!({"name": n, "size": s}))
        .collect();
    let (pmin, pmax) = m.phones.iter().fold((f64::MAX, 0.0f64), |(a, b), p| (a.min(p.price_rub), b.max(p.price_rub)));
    let mut brands: Vec<&str> = m.phones.iter().map(|p| p.brand.as_str()).collect();
    brands.sort();
    brands.dedup();
    Json(serde_json::json!({
        "phones": m.n, "groups": groups, "classes": m.class_names, "features": m.nf,
        "metrics": m.metrics, "ui": m.ui, "trained_at": m.trained_at,
        "price_min": pmin, "price_max": pmax, "python_bench_ms": m.python_bench_ms,
        "brands": brands.len(),
        "mlp_layers": std::iter::once(m.mlp.layers.first().map_or(0, |l| l.n_in))
            .chain(m.mlp.layers.iter().map(|l| l.n_out)).collect::<Vec<_>>(),
        "version": env!("CARGO_PKG_VERSION"),
        "version_name": app.cat.dataset_name(),
        "catalog": app.cat.meta_json(),
    }))
}

async fn phone(State(app): AppState, Path(slug): Path<String>) -> Response {
    match app.cat.by_slug.get(&slug) {
        Some(&c) => Json(app.cat.phone_json(c)).into_response(),
        None => (StatusCode::NOT_FOUND, Json(serde_json::json!({"error": "нет такой модели"}))).into_response(),
    }
}

async fn special(State(app): AppState) -> impl IntoResponse {
    Json(app.cat.special_list())
}

#[derive(Deserialize)]
struct BenchQuery {
    n: Option<usize>,
}

async fn bench_handler(State(app): AppState, Query(q): Query<BenchQuery>) -> impl IntoResponse {
    let n = q.n.unwrap_or(2000).clamp(10, 50_000);
    let app2 = app.clone();
    let res = tokio::task::spawn_blocking(move || {
        let m2 = &app2.m;
        let mut rng = bench::Rng::new(42);
        let surveys: Vec<_> = (0..n).map(|_| bench::random_survey(&mut rng)).collect();
        let mut times = Vec::with_capacity(n);
        let t_all = Instant::now();
        for a in &surveys {
            let t = Instant::now();
            let r = engine::recommend(m2, a, 10, true, true);
            std::hint::black_box(&r);
            times.push(t.elapsed().as_secs_f64() * 1e6);
        }
        let total = t_all.elapsed().as_secs_f64();
        times.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let pct = |q: f64| times[((times.len() - 1) as f64 * q).round() as usize];
        serde_json::json!({
            "n": n, "mean_us": times.iter().sum::<f64>() / n as f64,
            "p50_us": pct(0.5), "p99_us": pct(0.99), "per_second": n as f64 / total,
            "python_ms": m2.python_bench_ms,
        })
    })
    .await
    .unwrap_or_else(|e| serde_json::json!({"error": e.to_string()}));
    Json(res)
}

fn photos_dir() -> std::path::PathBuf {
    std::env::var("PHOTOS_DIR").map(Into::into).unwrap_or_else(|_| "photos".into())
}

/// Имя файла своего фото: `<id>.jpg` или `<id>-<номер>.jpg` (jpg, jpeg, png, webp, avif).
fn local_photo_id(name: &str) -> Option<i64> {
    let (stem, ext) = name.rsplit_once('.')?;
    if !matches!(ext.to_ascii_lowercase().as_str(), "jpg" | "jpeg" | "png" | "webp" | "avif") {
        return None;
    }
    stem.split('-').next()?.parse().ok()
}

/// Свои фото из папки идут первыми, затем — фото из каталога (с автором и лицензией, в конце — демо-заглушки).
/// Папка перечитывается при каждом запросе: новые файлы видны без перезапуска сервера.
async fn photos(State(app): AppState) -> impl IntoResponse {
    let mut map: serde_json::Map<String, serde_json::Value> = serde_json::Map::new();
    if let Ok(entries) = std::fs::read_dir(photos_dir()) {
        let mut files: Vec<String> = entries.flatten().filter_map(|e| e.file_name().into_string().ok()).collect();
        files.sort();
        for f in files {
            if let Some(id) = local_photo_id(&f) {
                let list = map.entry(id.to_string()).or_insert_with(|| serde_json::json!([]));
                list.as_array_mut().unwrap().push(serde_json::json!({"src": format!("/photos/{f}"), "local": true}));
            }
        }
    }
    let cat = &app.cat;
    for (i, link) in cat.link.iter().enumerate() {
        let Some(c) = *link else { continue };
        let list = map.entry(cat.model_ids[i].to_string()).or_insert_with(|| serde_json::json!([]));
        let arr = list.as_array_mut().unwrap();
        let has_local = !arr.is_empty();
        // свои фото есть — заглушки не нужны
        arr.extend(cat.photos_of(c).into_iter().filter(|p| !(has_local && p["placeholder"] == true)));
    }
    ([(header::CACHE_CONTROL, "no-cache")], Json(serde_json::Value::Object(map)))
}

async fn photo_file(Path(name): Path<String>) -> Response {
    // только плоские имена вида 57.jpg / 57-2.webp — без подкаталогов и «..»
    if local_photo_id(&name).is_none() || name.contains(['/', '\\']) || name.contains("..") {
        return StatusCode::NOT_FOUND.into_response();
    }
    let ext = name.rsplit_once('.').map(|(_, e)| e.to_ascii_lowercase()).unwrap_or_default();
    let mime = match ext.as_str() {
        "png" => "image/png",
        "webp" => "image/webp",
        "avif" => "image/avif",
        _ => "image/jpeg",
    };
    match std::fs::read(photos_dir().join(&name)) {
        Ok(bytes) => ([(header::CONTENT_TYPE, mime), (header::CACHE_CONTROL, "public, max-age=3600")], bytes).into_response(),
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

/// Демо-заглушка вместо фото: силуэт телефона (1 — спереди, 2 — сзади, 3 — сбоку), название и пометка «демо».
fn placeholder_svg(name: &str, slug: &str, n: u32) -> String {
    let hash = slug.bytes().fold(0x811c9dc5u32, |h, b| (h ^ b as u32).wrapping_mul(16777619));
    let hue = hash % 360;
    let hue2 = (hue + 40) % 360;
    let body = match n % 3 {
        1 => r##"<rect x="300" y="180" width="360" height="760" rx="58" fill="#0d1118" stroke="#fff" stroke-opacity=".55" stroke-width="6"/>
<rect x="322" y="204" width="316" height="712" rx="40" fill="url(#scr)"/>
<circle cx="480" cy="232" r="10" fill="#0d1118"/>"##,
        2 => r##"<rect x="300" y="180" width="360" height="760" rx="58" fill="url(#back)" stroke="#fff" stroke-opacity=".55" stroke-width="6"/>
<rect x="330" y="214" width="170" height="230" rx="44" fill="#0d1118" fill-opacity=".55" stroke="#fff" stroke-opacity=".35" stroke-width="3"/>
<circle cx="390" cy="272" r="34" fill="#11161f" stroke="#fff" stroke-opacity=".5" stroke-width="5"/>
<circle cx="390" cy="384" r="34" fill="#11161f" stroke="#fff" stroke-opacity=".5" stroke-width="5"/>
<circle cx="458" cy="328" r="22" fill="#11161f" stroke="#fff" stroke-opacity=".4" stroke-width="4"/>"##,
        _ => r##"<rect x="440" y="180" width="80" height="760" rx="34" fill="url(#back)" stroke="#fff" stroke-opacity=".55" stroke-width="6"/>
<rect x="514" y="330" width="10" height="90" rx="4" fill="#fff" fill-opacity=".6"/>
<rect x="514" y="450" width="10" height="150" rx="4" fill="#fff" fill-opacity=".6"/>"##,
    };
    format!(
        r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 1280" width="960" height="1280">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl({hue},38%,24%)"/><stop offset="1" stop-color="hsl({hue2},42%,11%)"/></linearGradient>
<linearGradient id="scr" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl({hue},60%,55%)"/><stop offset="1" stop-color="hsl({hue2},60%,30%)"/></linearGradient>
<linearGradient id="back" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl({hue},30%,42%)"/><stop offset="1" stop-color="hsl({hue2},30%,22%)"/></linearGradient>
</defs>
<rect width="960" height="1280" fill="url(#bg)"/>
{body}
<text x="480" y="1060" text-anchor="middle" font-family="system-ui,Segoe UI,sans-serif" font-size="44" font-weight="700" fill="#fff">{name}</text>
<text x="480" y="1120" text-anchor="middle" font-family="system-ui,Segoe UI,sans-serif" font-size="30" fill="#fff" fill-opacity=".7">демо-фото {n} · настоящего снимка пока нет</text>
</svg>"##,
        name = xml_escape(name)
    )
}

async fn placeholder(State(app): AppState, Path((slug, file)): Path<(String, String)>) -> Response {
    let n: u32 = file.trim_end_matches(".svg").parse().unwrap_or(0);
    match (app.cat.by_slug.get(&slug), n) {
        (Some(&c), 1..=9) => (
            [(header::CONTENT_TYPE, "image/svg+xml"), (header::CACHE_CONTROL, "public, max-age=86400")],
            placeholder_svg(&app.cat.phones[c].full_name, &slug, n),
        )
            .into_response(),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn font(Path(name): Path<String>) -> Response {
    match FONTS.iter().find(|(n, _)| *n == name) {
        Some((_, bytes)) => (
            [(header::CONTENT_TYPE, "font/woff2"), (header::CACHE_CONTROL, "public, max-age=31536000, immutable")],
            *bytes,
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn index() -> impl IntoResponse {
    ([(header::CACHE_CONTROL, "no-cache")], Html(INDEX_HTML))
}

#[tokio::main]
async fn main() {
    let text = match std::env::var("MODEL_PATH") {
        Ok(path) => std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("не читается {path}: {e}")),
        Err(_) => EMBEDDED_MODEL.to_string(),
    };
    let model = Model::from_json(&text).unwrap_or_else(|e| panic!("ошибка модели: {e}"));
    println!("модель: {} телефонов, {} групп, обучена {}", model.n, model.k, model.trained_at);
    let cat = Catalog::open_default(&model).unwrap_or_else(|e| panic!("ошибка каталога: {e}"));
    let unlinked = cat.link.iter().filter(|l| l.is_none()).count();
    println!(
        "каталог «{}»: {} моделей ({} в подборе), {} конфигураций, {} камер, {} фото; источник: {}",
        cat.dataset_name(), cat.phones.len(), cat.linked, cat.variants.len(), cat.cameras.len(), cat.photos.len(),
        cat.origin
    );
    if unlinked > 0 {
        println!("внимание: {unlinked} телефонов из model.json не найдены в каталоге — для них нет конфигураций и камер");
    }
    let app_state = Arc::new(App { m: model, cat });

    let app = Router::new()
        .route("/", get(index))
        .route("/api/meta", get(meta))
        .route("/api/recommend", post(recommend))
        .route("/api/phone/{slug}", get(phone))
        .route("/api/special", get(special))
        .route("/api/bench", get(bench_handler))
        .route("/fonts/{name}", get(font))
        .route("/api/photos", get(photos))
        .route("/photos/{name}", get(photo_file))
        .route("/placeholder/{slug}/{file}", get(placeholder))
        .layer(CorsLayer::permissive()) // страницу можно открыть отдельно (файлом или с другого хоста)
        .layer(CompressionLayer::new())
        .with_state(app_state);

    let port = std::env::var("PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8080u16);
    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await.expect("порт занят");
    println!("PhonePicker: http://localhost:{port}");
    axum::serve(listener, app).await.unwrap();
}
