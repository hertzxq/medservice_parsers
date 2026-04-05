# URL-шаблон для перехода к отзывам по firm_id
REVIEWS_URL_TEMPLATE = "https://2gis.ru/{city}/firm/{firm_id}/tab/reviews"

# Таймауты и задержки
PAGE_LOAD_TIMEOUT_SEC = 30
INITIAL_LOAD_DELAY_MS = 4000
SCROLL_PAUSE_SEC = 0.5
SCROLL_STEP_PX = 3000
POST_EXPAND_DELAY_MS = 1000
EXPAND_CLICK_DELAY_MS = 100
STABLE_SCROLL_THRESHOLD = 5

# Retry-параметры для дозагрузки через API
MAX_API_RETRIES = 3
API_RETRY_DELAY_MS = 2000

# Retry-параметры для загрузки комментариев (ответов организации)
COMMENT_RETRY_COUNT = 3
COMMENT_RETRY_DELAY_MS = 1000

# Таймаут ожидания первого API-ответа (секунды).
# Если API не ответит за это время, парсер переключится на DOM-фоллбэк.
API_WAIT_TIMEOUT_SEC = 10

# Максимальное количество отзывов (0 = без лимита)
MAX_REVIEWS = 0

# Публичный API-ключ 2GIS (фоллбэк, если не удалось извлечь из запросов)
DEFAULT_API_KEY = "6e7e1929-4ea9-4a5d-8c05-d601860389bd"

# 2GIS — SPA с динамическими CSS-классами.
# Селекторы строятся по стабильным атрибутам (role, aria-label, data-*),
# структурным паттернам и тексту. При обновлении вёрстки правьте сюда.
SELECTORS = {
    # Боковая панель с прокруткой (контейнер, который скроллится)
    "scroll_container": "div[class*='_15mwbsm'], div[scrollable='true'], div.scroll__container",
    # Кнопка «Читать целиком» для раскрытия обрезанного текста отзыва
    "review_expand_button": "span:has-text('Читать целиком')",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)
