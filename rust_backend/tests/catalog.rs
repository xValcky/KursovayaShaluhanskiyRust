//! Каталог v2: связь с model.json, целостность данных и новые фильтры подбора.
//! Каталог собирается командой `python tools/build_catalog.py` (→ rust_backend/catalog.db).

use phonepicker_server::{
    bench,
    catalog::{model_slug, slugify, strip_mem_suffix, Catalog},
    engine::{self, Survey},
    model::Model,
};
use std::collections::HashSet;

fn load() -> (Model, Catalog) {
    let dir = env!("CARGO_MANIFEST_DIR");
    let model = Model::from_json(&std::fs::read_to_string(format!("{dir}/model.json")).unwrap()).unwrap();
    let cat = Catalog::load(std::path::Path::new(&format!("{dir}/catalog.db")), &model).unwrap();
    (model, cat)
}

fn survey(budget: f64) -> Survey {
    Survey { budget_rub: budget, strict_range: 1.0, ..Default::default() }
}

/// Подбор как на сайте: с конфигурациями, только подходящие модели, поля каталога в строках.
fn run(m: &Model, cat: &Catalog, a: &Survey) -> engine::Recommendation {
    let a = a.clone().sanitize();
    let e = cat.ext(m, &a, true);
    let mut r = engine::recommend_ext(m, &a, 10, true, false, &e);
    engine::drop_unfit(&mut r);
    cat.decorate(&mut r, &a);
    r
}

fn extra(row: &engine::Row) -> &serde_json::Map<String, serde_json::Value> {
    row.extra.as_ref().expect("у строки нет полей каталога")
}

#[test]
fn slug_rules_match_python() {
    assert_eq!(slugify("Samsung Galaxy S25+"), "samsung-galaxy-s25-plus");
    assert_eq!(slugify("Xiaomi Redmi Note 14 Pro+ 5G"), "xiaomi-redmi-note-14-pro-plus-5g");
    assert_eq!(slugify("Nothing Phone (3a) Pro"), "nothing-phone-3a-pro");
    assert_eq!(strip_mem_suffix("Apple iPhone 17 Pro 512GB"), ("Apple iPhone 17 Pro", Some(512)));
    assert_eq!(strip_mem_suffix("Apple iPhone 17 Pro 1TB"), ("Apple iPhone 17 Pro", Some(1024)));
    assert_eq!(strip_mem_suffix("Galaxy S25"), ("Galaxy S25", None));
    assert_eq!(model_slug("Samsung Galaxy A56 128GB"), "samsung-galaxy-a56");
}

#[test]
fn every_model_phone_is_in_catalog() {
    let (m, cat) = load();
    let missing: Vec<&str> =
        m.phones.iter().zip(&cat.link).filter(|(_, l)| l.is_none()).map(|(p, _)| p.full_name.as_str()).collect();
    assert!(missing.is_empty(), "нет в каталоге: {missing:?}");
    // одна строка обучающей базы — одна модель каталога (база собирается из каталога: tools/catalog_to_seed.py)
    assert_eq!(cat.linked, m.n);
    assert!(cat.phones.iter().any(|p| !p.in_ml), "должны быть модели только для каталога");
}

#[test]
fn catalog_integrity() {
    let (_, cat) = load();
    for p in &cat.phones {
        let vs = &cat.variants[p.variants.clone()];
        assert_eq!(vs.iter().filter(|v| v.is_base).count(), 1, "{}: одна базовая конфигурация", p.slug);
        let cams = &cat.cameras[p.cameras.clone()];
        assert_eq!(cams.iter().filter(|c| c.role == "main").count(), 1, "{}: одна основная камера", p.slug);
        // итог есть у каждого модуля, кроме датчика глубины и модулей без оценки обработки (модели после даты знаний)
        assert!(cams.iter().all(|c| c.role == "depth" || c.total.is_some() || c.processing.is_none()),
                "{}: итог у модулей", p.slug);
        assert!(p.summary.optical >= 1.0, "{}: оптический зум ≥ 1", p.slug);
    }
    let with_photo = cat.phones.iter().filter(|p| !p.photos.is_empty()).count();
    assert!(with_photo * 10 >= cat.phones.len(), "фото меньше чем у 10% моделей: {with_photo}");
    assert_eq!(cat.dataset_name(), "каталог v2 — настоящие данные");
}

#[test]
fn without_new_fields_selection_is_unchanged() {
    // каталог без конфигураций и без новых фильтров не меняет прежний подбор (тот, что сверяется с Python)
    let (m, cat) = load();
    let mut rng = bench::Rng::new(7);
    for _ in 0..200 {
        let a = bench::random_survey(&mut rng);
        let base = engine::recommend(&m, &a, 10, true, true);
        let ext = engine::recommend_ext(&m, &a, 10, true, true, &cat.ext(&m, &a, false));
        let ids = |r: &engine::Recommendation| r.table.iter().map(|x| x.id).collect::<Vec<_>>();
        assert_eq!(ids(&base), ids(&ext));
        assert_eq!(base.feasible, ext.feasible);
        assert_eq!(base.hint.map(|h| h.key), ext.hint.map(|h| h.key));
    }
}

#[test]
fn ram_filter_picks_a_fitting_configuration() {
    let (m, cat) = load();
    let a = Survey { min_ram: 12.0, ..survey(45_000.0) };
    let r = run(&m, &cat, &a);
    assert!(!r.table.is_empty());
    for row in &r.table {
        assert!(row.ram_gb >= 12.0, "{}: ОЗУ {}", row.name, row.ram_gb);
        assert!(row.price as f64 <= 45_000.0, "{}: цена {}", row.name, row.price);
        let chosen: Vec<_> = extra(row)["variants"].as_array().unwrap().iter().filter(|v| v["chosen"] == true).collect();
        assert_eq!(chosen.len(), 1, "{}: ровно одна выбранная конфигурация", row.name);
        assert_eq!(chosen[0]["fits"], true);
    }
}

#[test]
fn one_row_per_catalog_model() {
    let (m, cat) = load();
    let a = Survey { min_rom: 256.0, os_pref: 2.0, ..survey(150_000.0) };
    let r = run(&m, &cat, &a);
    let slugs: Vec<&str> = r.table.iter().map(|row| extra(row)["slug"].as_str().unwrap()).collect();
    assert_eq!(slugs.len(), slugs.iter().collect::<HashSet<_>>().len(), "повтор модели в выдаче: {slugs:?}");
}

#[test]
fn camera_filters() {
    let (m, cat) = load();
    let r = run(&m, &cat, &Survey { min_optical_zoom: 3.0, ..survey(200_000.0) });
    assert!(!r.table.is_empty());
    assert!(r.table.iter().all(|row| extra(row)["camera"]["optical_x"].as_f64().unwrap() >= 3.0));
    let r = run(&m, &cat, &Survey { need_macro: 1.0, ..survey(60_000.0) });
    assert!(!r.table.is_empty());
    assert!(r.table.iter().all(|row| extra(row)["camera"]["macro"] != "none"));
}

#[test]
fn hide_bad_quirks_and_special() {
    let (m, cat) = load();
    let r = run(&m, &cat, &Survey { hide_bad_quirks: 1.0, ..survey(60_000.0) });
    assert!(!r.table.is_empty());
    for row in &r.table {
        let c = cat.by_slug[extra(row)["slug"].as_str().unwrap()];
        assert!(!cat.phones[c].bad_quirk, "{}: есть серьёзный минус", row.name);
    }
    let r = run(&m, &cat, &Survey { special_only: 1.0, ..survey(200_000.0) });
    assert!(!r.table.is_empty());
    assert!(r.table.iter().all(|row| !extra(row)["special"].as_array().unwrap().is_empty()));
    let r = run(&m, &cat, &Survey { special_only: 1.0, special: vec!["flip".into()], ..survey(200_000.0) });
    assert!(!r.table.is_empty());
    assert!(r.table.iter().all(|row| extra(row)["form_factor"] == "flip"));
}

#[test]
fn phone_card_and_special_list() {
    let (_, cat) = load();
    let c = cat.by_slug["apple-iphone-17-pro"];
    let v = cat.phone_json(c);
    assert!(v["variants"].as_array().unwrap().len() >= 2, "512 ГБ склеена с базовой моделью");
    assert!(v["variants"].as_array().unwrap().iter().any(|x| !x["history"].as_array().unwrap().is_empty()));
    let s = cat.special_list();
    let codes: Vec<&str> = s["categories"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| !c["phones"].as_array().unwrap().is_empty())
        .map(|c| c["code"].as_str().unwrap())
        .collect();
    for need in ["trifold", "book_fold", "flip", "gimbal_camera"] {
        assert!(codes.contains(&need), "нет категории {need}");
    }
}
