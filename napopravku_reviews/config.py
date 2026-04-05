# URL-шаблон: {city} = "spb", "msk" и т.д.; {clinic_slug} = слаг клиники
REVIEWS_URL_TEMPLATE = "https://{city}.napopravku.ru/clinics/{clinic_slug}/otzyvy/"

PAGE_LOAD_TIMEOUT_SEC = 60
INITIAL_LOAD_DELAY_MS = 3000
LOAD_MORE_CLICK_DELAY_MS = 2000
EXPAND_CLICK_DELAY_MS = 300
MAX_LOAD_MORE_CLICKS = 200

SELECTORS = {
    # Информация о клинике
    "business_name": "h1",
    "business_rating": ".rating-info__value",
    "business_review_count": ".rating-info__review, .n-rating__reviews-count",
    "business_address": ".clinic-address",

    # Контейнер отзывов
    "review_item": "div.review-card",

    # Поля отзыва
    "review_author": ".review-card__author-name",
    "review_date": ".review-card__date",
    "review_body": ".review-card__body",
    "review_stars_active": "label.n-rating__star--active",
    "review_doctor": ".review-card__doctor a, a.photo-block__link",

    # Ответ клиники (спойлер)
    "review_response_button": ".review-comment-collapse-button",
    "review_response_body": ".review-comment__body, .review-card__comment-body",

    # Schema.org разметка (основной способ)
    "schema_review": "div[itemtype*='schema.org/Review'], div[itemprop='review']",
    "schema_author": "[itemprop='author']",
    "schema_rating": "[itemprop='ratingValue']",
    "schema_date": "[itemprop='datePublished']",
    "schema_body": "[itemprop='reviewBody'], [itemprop='description']",

    # Пагинация
    "load_more_button": "button.clinic-section__loading-btn",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
