# Yandex Maps Reviews Parser

Парсер отзывов с Яндекс Карт. Собирает все доступные отзывы организации и сохраняет в JSON.

## Стек

- **Playwright** — headless Chromium для загрузки страниц
- **BeautifulSoup + lxml** — парсинг HTML
- **Pydantic** — валидация и сериализация данных

## Установка

```bash
poetry install
playwright install chromium
```

## Использование

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



## PostgreSQL БД

Парсер может автоматически сохранять данные в PostgreSQL (создаёт таблицы `organizations` и `reviews`, делает upsert и дедупликацию).

**1. Запуск БД через Docker:**
```bash
docker compose up -d
```

**2. Парсинг с сохранением в БД:**
Добавьте флаг `--save-db` при запуске CLI:
```bash
python -m yandex_reviews --org-id 1543198007 --save-db
```

**3. Как посмотреть данные в БД:**
Можно подключиться к контейнеру через встроенную утилиту `psql`:
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

## Структура

```
yandex_reviews/
├── __init__.py     # публичный API
├── __main__.py     # точка входа python -m
├── cli.py          # CLI + логирование
├── config.py       # константы, селекторы
├── models.py       # Pydantic-модели
└── parser.py       # Playwright + BS4 парсер
```

## Ограничения

- Яндекс Карты отображают **максимум ~600 отзывов** на организацию
- При массовом парсинге возможна блокировка IP — используйте прокси
- CSS-селекторы Яндекс Карт могут измениться — при поломке обновите `config.py`
