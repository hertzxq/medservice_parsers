REVIEWS_URL_TEMPLATE = "https://yandex.ru/maps/org/{org_id}/reviews/"

SCROLL_PAUSE_SEC = 0.3
SCROLL_STEP_PX = 5000
PAGE_LOAD_TIMEOUT_SEC = 15
INITIAL_LOAD_DELAY_MS = 3000
POST_EXPAND_DELAY_MS = 1000
EXPAND_CLICK_DELAY_MS = 50
STABLE_SCROLL_THRESHOLD = 5

SELECTORS = {
    "reviews_container": "div.business-review-view",
    "review_author_name": "div.business-review-view__author-name span[itemprop='name']",
    "review_text": "span.spoiler-view__text-container",
    "review_stars_container": "div.business-rating-badge-view__stars",
    "review_star_full": "span.business-rating-badge-view__star._full",
    "review_date": "span.business-review-view__date",
    "review_date_meta": "meta[itemprop='datePublished']",
    "review_response": "div.business-review-view__comment",
    "review_expand_button": "span.business-review-view__expand",
    "review_comment_expand": "div.business-review-view__comment-expand",
    "business_name": "h1.orgpage-header-view__header",
    "business_address": "div.business-contacts-view__address",
    "business_rating": "div.business-rating-amount-view__rating",
    "business_review_count": "div.business-rating-amount-view__reviews",
    "scroll_container": "div.scroll__container",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
