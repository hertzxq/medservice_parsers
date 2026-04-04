PLACE_URL_TEMPLATE = "https://www.google.com/maps/place/?q=place_id:{place_id}"

SCROLL_PAUSE_SEC = 0.5
SCROLL_STEP_PX = 3000
PAGE_LOAD_TIMEOUT_SEC = 30
INITIAL_LOAD_DELAY_MS = 5000
POST_EXPAND_DELAY_MS = 1500
EXPAND_CLICK_DELAY_MS = 80
STABLE_SCROLL_THRESHOLD = 5

SELECTORS = {
    "scroll_container": "div.m6QErb.DxyBCb",
    "reviews_container": "div.jftiEf",
    "review_author_name": "div.d4r55",
    "review_text": "span.wiI7pd",
    "review_stars_container": "span.kvMYJc",
    "review_date": "span.rsqaWe",
    "review_response": "div.CDe7pd",
    "review_expand_button": "button.w8nwRe.kyuRq",
    "business_name": "h1.DUwDvf",
    "business_rating": "div.fontDisplayLarge",
    "business_review_count": "div.F7nice",
    "business_address": "button[data-item-id='address'] div.Io6YTe",
    "reviews_tab": "button[role='tab']",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
