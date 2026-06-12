# hl/gl форсируют русскую локаль UI — под неё заточены селекторы и парсер дат.
PLACE_URL_TEMPLATE = "https://www.google.com/maps/place/?q=place_id:{place_id}&hl=ru&gl=ru"

SCROLL_PAUSE_SEC = 0.6
SCROLL_STEP_PX = 3000
PAGE_LOAD_TIMEOUT_SEC = 30
INITIAL_LOAD_DELAY_MS = 2000
POST_EXPAND_DELAY_MS = 1500
EXPAND_CLICK_DELAY_MS = 80
STABLE_SCROLL_THRESHOLD = 5

# Классы Google Maps — обфусцированы и периодически меняются. Где возможно,
# парсер опирается на стабильные якоря (role=tab, data-item-id, data-review-id,
# aria-label звёзд) и резолвит контейнер прокрутки динамически (см. parser.py).
SELECTORS = {
    # Лента отзывов: НЕ фиксированный класс — это .m6QErb, в поддереве которого
    # есть карточки отзывов. Используется как подсказка, реальный контейнер
    # выбирается динамически в _scroll_reviews().
    "scroll_container_hint": "div.m6QErb",
    "reviews_container": "div.jftiEf",          # карточка отзыва (несёт data-review-id)
    "review_author_name": "div.d4r55",
    "review_text": "span.wiI7pd",
    "review_stars_container": "span.kvMYJc",     # aria-label вида "5 звёзд"
    "review_date": "span.rsqaWe",
    "review_response": "div.CDe7pd",             # ответ владельца
    "review_expand_button": "button.w8nwRe.kyuRq",  # "Ещё"
    "business_name": "h1.DUwDvf",
    # F7nice держит и рейтинг, и счётчик: текст вида "4,5(174)".
    # (div.fontDisplayLarge на текущем макете пуст.)
    "business_rating": "div.F7nice",
    "business_review_count": "div.F7nice",
    "business_address": "button[data-item-id='address']",
    "reviews_tab": "button[role='tab']",
    "reviews_count_button": "button.GQjSyb",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
