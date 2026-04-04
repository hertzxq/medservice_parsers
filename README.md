# Reviews Parsers — Yandex Maps + Google Maps + 2GIS + ПроДокторов

Парсеры отзывов с Яндекс Карт, Google Maps, 2GIS (2ГИС) и ПроДокторов. Собирают все доступные отзывы организации и сохраняют в JSON.

## Стек

- **Playwright** — headless Chromium для загрузки страниц
- **BeautifulSoup + lxml** — парсинг HTML
- **Pydantic** — валидация и сериализация данных
- **asyncpg** — сохранение в PostgreSQL

## Установка

```bash
pip install poetry
poetry install
playwright install chromium
```

---

## Yandex Maps — Использование

### CLI

```bash
# По URL страницы
python -m yandex_reviews --url "https://yandex.ru/maps/org/schastlivy_vzglyad/1543198007/reviews/" -o reviews.json

# По ID организации
python -m yandex_reviews --org-id 1543198007 -o reviews.json

# Массовый парсинг из файла (ссылки или ID по одному на строку)
python -m yandex_reviews --input-file targets.txt -o batch_result.json

# Тонкая настройка скроллинга и таймаутов
python -m yandex_reviews --org-id 1543198007 --scroll-pause 0.2 --scroll-step 8000 --timeout 20

# Отладка — видимый браузер + подробные логи
python -m yandex_reviews --org-id 1543198007 --no-headless --log-level DEBUG
```

**Где взять URL или ID:**
1. Открыть [Яндекс Карты](https://yandex.ru/maps/)
2. Найти организацию
3. Скопировать URL из адресной строки — число в URL и есть `org-id`

---

## Google Maps — Использование

### CLI

```bash
# По URL страницы Google Maps
python -m google_reviews --url "https://www.google.com/maps/place/..." -o reviews.json

# По place_id
python -m google_reviews --place-id ChIJN1t_tDeuEmsRUsoyG83frY4 -o reviews.json

# Массовый парсинг из файла (ссылки или place_id по одному на строку)
python -m google_reviews --input-file google_targets.txt -o batch_result.json

# Тонкая настройка скроллинга и таймаутов
python -m google_reviews --place-id ChIJN1t_tDeuEmsRUsoyG83frY4 --scroll-pause 0.3 --scroll-step 5000 --timeout 30

# Отладка — видимый браузер + подробные логи
python -m google_reviews --place-id ChIJN1t_tDeuEmsRUsoyG83frY4 --no-headless --log-level DEBUG
```

**Где взять place_id:**
1. Открыть [Google Maps](https://www.google.com/maps/)
2. Найти организацию
3. В URL будет строка вида `/place/...` — из неё можно извлечь `place_id`
4. Или использовать [Place ID Finder](https://developers.google.com/maps/documentation/places/web-service/place-id)

---

## 2GIS (2ГИС) — Использование

### CLI

```bash
# По URL страницы организации
python -m twogis_reviews --url "https://2gis.ru/moscow/firm/70000001019342551/tab/reviews" -o reviews.json

# По ID организации (город по умолчанию — moscow)
python -m twogis_reviews --firm-id 70000001019342551 -o reviews.json

# Организация в другом городе
python -m twogis_reviews --firm-id 70000001019342551 --city spb -o reviews.json

# Массовый парсинг из файла
python -m twogis_reviews --input-file twogis_targets.txt -o batch_result.json

# Тонкая настройка таймаутов
python -m twogis_reviews --firm-id 70000001019342551 --scroll-pause 0.5 --timeout 30

# Отладка — видимый браузер + подробные логи
python -m twogis_reviews --firm-id 70000001019342551 --no-headless --log-level DEBUG
```

**Где взять URL или ID:**
1. Открыть [2GIS](https://2gis.ru/)
2. Найти организацию через поиск
3. Скопировать URL — формат: `2gis.ru/{город}/firm/{firm_id}`
4. `firm-id` — это числовой ID из URL (например: `70000001019342551`)

---

## ПроДокторов — Использование

### CLI

```bash
# По URL страницы клиники
python -m prodoctorov_reviews --url "https://prodoctorov.ru/moskva/lpu/69674-medpraym/otzivi/" -o reviews.json

# По ID клиники (город по умолчанию — Москва)
python -m prodoctorov_reviews --lpu-id 69674-medpraym -o reviews.json

# Клиника в другом городе
python -m prodoctorov_reviews --lpu-id 12345-klinika --city spb -o reviews.json

# Массовый парсинг из файла
python -m prodoctorov_reviews --input-file prodoctorov_targets.txt -o batch_result.json

# Ограничить количество страниц
python -m prodoctorov_reviews --url "https://prodoctorov.ru/moskva/lpu/69674-medpraym/" --max-pages 5

# Отладка — видимый браузер + подробные логи
python -m prodoctorov_reviews --lpu-id 69674-medpraym --no-headless --log-level DEBUG
```

**Где взять URL или ID:**
1. Открыть [ПроДокторов](https://prodoctorov.ru/)
2. Найти клинику через поиск
3. Скопировать URL — формат: `prodoctorov.ru/{город}/lpu/{id}-{slug}/`
4. `lpu-id` — это `{id}-{slug}` из URL (например: `69674-medpraym`)

---

## Общие параметры CLI

| Параметр | Описание |
|----------|----------|
| `--url` | Прямая ссылка на страницу организации |
| `--org-id` / `--place-id` / `--firm-id` / `--lpu-id` | ID организации (Yandex / Google / 2GIS / ПроДокторов) |
| `--city` | Город (2GIS и ПроДокторов, по умолчанию `moscow` / `moskva`) |
| `--input-file` | Файл с URL или ID (по одному на строку) |
| `-o`, `--output` | Путь к выходному JSON-файлу |
| `--no-headless` | Показать окно браузера |
| `--log-level` | Уровень логирования: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--scroll-pause` | Пауза между скроллами (секунды) |
| `--scroll-step` | Шаг скролла (пиксели) |
| `--timeout` | Таймаут загрузки страницы (секунды) |
| `--initial-delay` | Задержка перед началом скроллинга (мс) |
| `--max-pages` | Максимальное количество страниц (только ПроДокторов) |
| `--save-db` | Сохранить результаты в PostgreSQL |
| `--db-dsn` | DSN для подключения к БД |

---

## PostgreSQL БД

Все парсеры могут автоматически сохранять данные в PostgreSQL (создают таблицы `organizations` и `reviews`, делают upsert и дедупликацию).

**1. Запуск БД через Docker:**
```bash
docker compose up -d
```

**2. Парсинг с сохранением в БД:**
```bash
# Yandex
python -m yandex_reviews --org-id 1543198007 --save-db

# Google
python -m google_reviews --place-id ChIJN1t_tDeuEmsRUsoyG83frY4 --save-db

# 2GIS
python -m twogis_reviews --firm-id 70000001019342551 --save-db

# ПроДокторов
python -m prodoctorov_reviews --lpu-id 69674-medpraym --save-db
```

**3. Как посмотреть данные в БД:**
```bash
# Войти в консоль БД:
docker exec -it parsers_postgres psql -U parsers -d parsers_db

# Полезные SQL-запросы внутри консоли:
\dt                                  -- показать все таблицы
SELECT * FROM organizations;         -- посмотреть организации
SELECT count(*) FROM reviews;        -- узнать общее число отзывов
SELECT author, rating, text FROM reviews LIMIT 5;  -- посмотреть тексты
\q                                   -- выйти из консоли
```
Также можно использовать любую программу вроде **DBeaver**, **DataGrip** или **PgAdmin**:
*   **Host:** `localhost`
*   **Port:** `5432`
*   **User:** `parsers`
*   **Password:** `parsers_secret`
*   **Database:** `parsers_db`

## Формат JSON

```json
{
  "business_info": {
    "name": "Счастливый взгляд",
    "address": "",
    "overall_rating": null,
    "total_reviews_on_page": null
  },
  "reviews": [
    {
      "author": "Юлия Гусева",
      "rating": 5,
      "date": "2025-09-24T18:11:33.806Z",
      "text": "Отличная оптика...",
      "response": null
    }
  ],
  "parsed_at": "2026-03-10T15:00:00",
  "total_parsed": 263,
  "source_url": "https://yandex.ru/maps/org/1543198007/reviews/"
}
```

## Структура проекта

```
reviews-parsers/
├── pyproject.toml          # Poetry конфигурация
├── docker-compose.yml      # PostgreSQL
├── test_list.txt           # Пример файла целей
│
├── yandex_reviews/         # Парсер Яндекс Карт
│   ├── __init__.py         # публичный API
│   ├── __main__.py         # точка входа python -m
│   ├── cli.py              # CLI + логирование
│   ├── config.py           # константы, селекторы
│   ├── database.py         # PostgreSQL (asyncpg)
│   ├── models.py           # Pydantic-модели
│   └── parser.py           # Playwright + BS4 парсер
│
├── google_reviews/         # Парсер Google Maps
│   ├── __init__.py         # публичный API
│   ├── __main__.py         # точка входа python -m
│   ├── cli.py              # CLI + логирование
│   ├── config.py           # константы, селекторы
│   ├── database.py         # PostgreSQL (asyncpg)
│   ├── models.py           # Pydantic-модели
│   └── parser.py           # Playwright + BS4 парсер
│
├── twogis_reviews/         # Парсер 2GIS (2ГИС)
│   ├── __init__.py         # публичный API
│   ├── __main__.py         # точка входа python -m
│   ├── cli.py              # CLI + логирование
│   ├── config.py           # константы, селекторы
│   ├── database.py         # PostgreSQL (asyncpg)
│   ├── models.py           # Pydantic-модели
│   └── parser.py           # Playwright парсер (API-перехват + DOM)
│
└── prodoctorov_reviews/    # Парсер ПроДокторов
    ├── __init__.py         # публичный API
    ├── __main__.py         # точка входа python -m
    ├── cli.py              # CLI + логирование
    ├── config.py           # константы, селекторы
    ├── database.py         # PostgreSQL (asyncpg)
    ├── models.py           # Pydantic-модели
    └── parser.py           # Playwright + BS4 парсер
```

## Ограничения

### Яндекс Карты
- Яндекс Карты отображают **максимум ~600 отзывов** на организацию
- При массовом парсинге возможна блокировка IP — используйте прокси
- CSS-селекторы Яндекс Карт могут измениться — при поломке обновите `config.py`

### Google Maps
- Google Maps загружает контент динамически (SPA) — парсинг медленнее
- CSS-классы Google обфусцированы и **могут измениться без предупреждения**
- Google активно блокирует ботов — возможны CAPTCHA и блокировки IP
- Рекомендуется использовать `--scroll-pause 0.5` и выше для стабильности

### 2GIS (2ГИС)
- 2GIS — SPA с полностью динамическим контентом, CSS-классы обфусцированы и часто меняются
- Парсер использует **двойную стратегию**: перехват API-ответов (надёжнее) + фоллбэк через DOM
- При массовом парсинге возможна блокировка IP — используйте прокси
- Отзывы загружаются через infinite scroll, без пагинации
- Рейтинг целый (1–5)

### ПроДокторов
- Сайт использует SSR — контент рендерится на сервере, пагинация постраничная
- При массовом парсинге возможна блокировка IP — используйте прокси и паузы
- Рейтинг на ПроДокторов дробный (1.0–5.0), при записи в БД округляется до целого
- Отзывы разделены на секции («Понравилось» / «Не понравилось») — в JSON сохраняются отдельными полями `pros`, `cons`
- CSS-селекторы и структура страниц могут измениться — при поломке обновите `config.py`
