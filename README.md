# Подбор смартфона с помощью машинного обучения

> **Ветка `catalog-real` — каталог v2 с настоящими данными** вместо случайных чисел ветки `random-future`.
> Наполнение по ТЗ [docs/ТЗ_БД_v2_промт_для_нейросети.md](docs/ТЗ_БД_v2_промт_для_нейросети.md): модели всех
> заметных брендов 2022–2026 (Apple, Samsung, Xiaomi/Redmi/POCO, Honor, Huawei, realme, OPPO, OnePlus, vivo/iQOO,
> Google, Nothing, Motorola, Sony, ASUS, nubia/REDMAGIC, Tecno, Infinix, itel, Meizu, Fairphone, защищённые и др.).
> Порядок сборки:
> ```
> python tools/fetch_wikipedia.py && python tools/verify_wikipedia.py   # сверка с карточками Wikipedia
> python tools/scrape_telegram.py && python tools/match_telegram_prices.py   # свежие цены с датой и ссылкой
> python tools/fetch_commons_photos.py        # свободные фото Wikimedia Commons
> python tools/check_quirk_evidence.py        # «приколы» по официальным страницам
> python tools/import_catalog_data.py         # таблицы tools/catalog_data/*.txt → data/catalog/phones/**.json
> python tools/build_catalog.py --check --final   # проверка и критерии готовности раздела 16 ТЗ
> ```
> Честность данных: значения из сведений нейросети помечены `_q: estimated` (источник `ai_knowledge`),
> подтверждённые страницей — `verified` со ссылкой.

## Переобучение нейросети на каталоге

Источник правды — каталог `data/catalog/`. Обучающая база Python (`data/phones_seed.csv`, история цен и фото)
теперь **выгружается из каталога**, руками её не правят. Порядок после любых изменений каталога (новые модели, цены):

```
python tools/import_catalog_data.py        # 1. таблицы tools/catalog_data/*.txt → файлы моделей (если правили таблицы)
python tools/build_catalog.py              # 2. проверка и сборка data/catalog.db + rust_backend/catalog.db
python tools/catalog_to_seed.py            # 3. каталог → phones_seed.csv, price_history_seed.csv, phone_photos_seed.csv
python tools/export_for_rust.py --golden 60   # 4. обучение (метки, KMeans, MLP, справедливая цена) → model.json, golden.json
cd rust_backend && cargo test --release    # 5. Rust совпадает с Python (60 анкет из 60) + тесты каталога
python -m pytest -q                        # 6. тесты Python-приложения
```

- В подбор попадают модели с `in_ml: true` и ценой базовой конфигурации; одна строка обучающей базы — одна модель
  (признаки по базовой конфигурации). Чтобы модель попала в подбор, ей нужна цена в РФ.
- Шаг 4 переобучает сам, если число моделей изменилось. Если число то же, а данные другие, — удалите сохранённый
  конвейер `models/pipeline.joblib` — тогда шаг 4 обучит заново.
- Число групп KMeans подбирается заново при каждом обучении (на 429 моделях — 12), поэтому выдача меняется.
- Прежняя база до перехода на каталог сохранена в `data/sources/phones_seed_v1.csv` (из неё берётся AnTuTu,
  если у чипа в справочнике его нет).

## Запуск
```
pip install -r requirements.txt
python main.py                      # приложение
python -m src.ml.experiments --grid # все эксперименты в консоли + reports/results_Comparison.xlsx
python -m src.ml.evaluation         # NDCG@5 на эталонных запросах (docs/reference_queries.json)
pip install -r requirements-dev.txt && python -m pytest -q   # sanity-тесты
```

## Конвейер
1. **Данные** — `data/phones_seed.csv` (287 моделей) и `data/price_history_seed.csv` (история цен) собираются
   из трёх источников:
   ```
   python tools/scrape_telegram.py   # посты t.me/ishopper и t.me/polzaSKIDKI → data/sources/*.json
   python tools/merge_sources.py     # слияние → phones_seed.csv + price_history_seed.csv
   ```
   За основу берётся база августа 2026 (`data/sources/base_aug2026.csv`, все 211 моделей остаются).
   Характеристики из постов (`ishopper_specs.csv`, `extra_specs.csv`) заменяют старые значения и добавляют
   новые модели. Цена — медиана цен из обоих каналов за 06.09–05.11.2026 (без пошлины), иначе последняя
   цена из каналов, иначе августовская оценка; источник — в `price_source`.
   Автономность `active_h` / `standby_d` — оценочная модель `estimate_autonomy` в `src/db.py` (не замер).
2. **Метки** `0 бюджетный / 1 средний / 2 флагман` («шпили») — взвешенный скоринг (`src/ml/labeling.py`).
3. **Признаки** (`src/ml/features.py`) — логарифм для скошенных признаков (вкл. камеру), подрезка выбросов
   p5–p95, `StandardScalerCustom`, веса (эксперт + mutual information + штраф корреляций). Частота экрана
   выше 120 Гц — с коэффициентом 0,2.
4. **Группы** (`src/ml/classifier.py`, `clustering.py`) — KMeans **внутри каждого шпиля**, k по силуэту
   (3–5, группа ≥ 8 моделей), стабильность — ARI по 10 сидам. Защищённые «броневики» (вес ≥ 300 г) —
   отдельная группа по правилу. Крупнейшая группа — 18% базы (было 62%).
5. **Нейросеть** — анкета + признаки групп → `MLPClassifier` (64-32) → класс. Обучение на 6000 синтетических
   анкетах + отзывы «подходит» и «не подходит».
6. **Оценка** (`src/ml/recommender.py`) — полезность (нормировка по подходящим телефонам) + 1,5·P(класс) +
   **мягкий бонус группы** (softmax по расстояниям до центров, ≤ 0,4). Затем разнообразие: ≤ 2 модели бренда
   в топ-5, ≥ 3 группы в топ-10. Блоки: лучший выбор / дешевле с тем же характером / премиум / другая группа.
   Подсказка, какое требование отсекло больше всего моделей.
7. **Справедливая цена** (`src/ml/price_model.py`) — регрессия по лабе 3: парная, множественная, нелинейная
   (`curve_fit`), KNN, SVR, дерево, случайный лес, бэггинг, градиентный бустинг, персептроны; с масштабированием
   и без. Значки «выгодно / переплата», режим «недооценённые», список подозрительных цен.
8. **Прогноз цен** (`src/ml/price_forecast.py`) — индекс цен по шпилям из истории Telegram, прогноз на 1 и 7
   дней (наивный, скользящее среднее, линейная регрессия, лес, бустинг, MLP), совет «покупать / ждать».
9. **Ансамбли и проверки** — MajorityVote, Bagging, AdaBoost, кривые ROC, границы решений, теоретическая
   ошибка ансамбля, эталон UCI Wine (`src/ml/benchmark.py`), AHP для весов (`src/ml/ahp.py`), NDCG@5.
10. **Цены из магазинов** (`src/providers/`) — API продавцов Яндекс Маркета, Ozon, Wildberries и Avito.

## Интерфейс
- **Простой режим** — 6 пресетов (в т.ч. «Такси и курьер»), бюджет, 4 ползунка; выдача блоками карточек
  со значками, «Показать ещё».
- **Про-режим** — 10 ползунков с подсказками и все требования, таблица 10/20/30 с сортировкой,
  «Почему он?», аналитика ML (13 вкладок), дуэль, игра, песочница, данные/API, реестр моделей.
- **Профили** опроса (сохранить / загрузить), **сброс настроек**, **избранное** со сравнением и экспортом,
  **экспорт подборки** в Excel/CSV, карточка телефона с **историей цены** и советом.
- **Каталог** — фильтры, «только защищённые», «только выгодно», архив снятых моделей.

## База данных
SQLite `phones.db`: `phones` (+ `archived`), `price_history` (Telegram + API), `survey_answers`, `feedback`,
`meta`. Индексы по цене, бренду, истории цен; `ANALYZE` после синхронизации; в окне — кэш в памяти вместо
чтения базы на каждый фильтр. При изменении датасетов база синхронизируется сама (хэш в `meta`).

Учебные файлы курса: `src/data_loader.py`, `src/preprocess.py` (используются), `reference/` (исходные примеры).

## Сборка exe
```
python build_exe.py                  # всё сам: зависимости → чистая база → обучение → exe
python build_exe.py --skip-install --no-experiments --run   # быстрее и сразу запустить
```
Готовый файл — `dist/PhonePicker.exe`. При первом запуске рядом с exe создаётся папка `PhonePicker_data`
(база, модели, `.env`, отчёты); если рядом писать нельзя — `%LOCALAPPDATA%\PhonePicker`.
