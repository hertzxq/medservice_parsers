REVIEWS_URL_TEMPLATE = "https://prodoctorov.ru/{city}/lpu/{lpu_id}/otzivi/"
PAGE_URL_TEMPLATE = "https://prodoctorov.ru/{city}/lpu/{lpu_id}/otzivi/{page}/"

PAGE_LOAD_TIMEOUT_SEC = 60
INITIAL_LOAD_DELAY_MS = 3000
PAGE_NAVIGATE_DELAY_MS = 2000
MAX_PAGES = 200

SELECTORS = {
    # Контейнер всех отзывов
    "reviews_tab_content": "#tab-content",
    # Каждый отдельный отзыв — Schema.org разметка
    "review_item": "div[itemtype='https://schema.org/Review'], div[itemprop='review']",
    # Автор (внутри отзыва)
    "review_author": "[itemprop='author']",
    # Рейтинг (внутри отзыва)
    "review_rating": "[itemprop='ratingValue']",
    # Дата (внутри отзыва)
    "review_date": "[itemprop='datePublished']",
    # Тело отзыва
    "review_body": "[itemprop='reviewBody']",
    # Описание отзыва (дополнительное)
    "review_description": "[itemprop='description']",
    # Информация о клинике
    "business_name": "h1, [itemprop='name']",
    "business_address": "[data-qa='lpu_address'], [itemprop='address']",
    "business_rating": "[itemprop='ratingValue']",
    "business_review_count": "[itemprop='reviewCount']",
    # Пагинация
    "next_page_button": "a.b-button.b-button_blue",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
