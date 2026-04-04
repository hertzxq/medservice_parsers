import re
import json
import logging
import asyncio
from typing import Any

from playwright.async_api import async_playwright, Page

from .config import (
    REVIEWS_URL_TEMPLATE,
    PAGE_LOAD_TIMEOUT_SEC,
    INITIAL_LOAD_DELAY_MS,
    SCROLL_PAUSE_SEC,
    SCROLL_STEP_PX,
    POST_EXPAND_DELAY_MS,
    EXPAND_CLICK_DELAY_MS,
    STABLE_SCROLL_THRESHOLD,
    MAX_API_RETRIES,
    API_RETRY_DELAY_MS,
    API_WAIT_TIMEOUT_SEC,
    DEFAULT_API_KEY,
    SELECTORS,
    USER_AGENT,
)
from .models import Review, BusinessInfo, ParseResult

logger = logging.getLogger(__name__)


class TwoGisParser:
    """Парсер отзывов с 2GIS (2ГИС).

    2GIS — SPA с динамически генерируемыми CSS-классами.
    Парсер использует два подхода:
      1. Перехват API-ответов через page.on("response") — наиболее надёжный способ.
      2. Фоллбэк: парсинг DOM-структуры через Playwright locators.
    """

    def __init__(self, headless: bool = True, **kwargs: Any):
        self.headless = headless
        self.scroll_pause_sec = kwargs.get("scroll_pause_sec", SCROLL_PAUSE_SEC)
        self.scroll_step_px = kwargs.get("scroll_step_px", SCROLL_STEP_PX)
        self.page_load_timeout_sec = kwargs.get("page_load_timeout_sec", PAGE_LOAD_TIMEOUT_SEC)
        self.initial_load_delay_ms = kwargs.get("initial_load_delay_ms", INITIAL_LOAD_DELAY_MS)
        self.post_expand_delay_ms = kwargs.get("post_expand_delay_ms", POST_EXPAND_DELAY_MS)
        self.expand_click_delay_ms = kwargs.get("expand_click_delay_ms", EXPAND_CLICK_DELAY_MS)
        self.stable_scroll_threshold = kwargs.get("stable_scroll_threshold", STABLE_SCROLL_THRESHOLD)
        self.max_api_retries = kwargs.get("max_api_retries", MAX_API_RETRIES)
        self.api_retry_delay_ms = kwargs.get("api_retry_delay_ms", API_RETRY_DELAY_MS)
        self.api_wait_timeout_sec = kwargs.get("api_wait_timeout_sec", API_WAIT_TIMEOUT_SEC)

    async def parse_by_url(self, url: str) -> ParseResult:
        """Парсинг по прямой ссылке на страницу организации или вкладку отзывов."""
        # Убедимся что URL указывает на вкладку отзывов
        if "/tab/reviews" not in url:
            url = url.rstrip("/") + "/tab/reviews"

        logger.info("Запуск парсинга: %s", url)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
                user_agent=USER_AGENT,
            )
            # Обход бот-детекции: убираем navigator.webdriver
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = await context.new_page()
            try:
                result = await self._parse_page(page, url)
            finally:
                await browser.close()

        logger.info("Парсинг завершён: %s | %d отзывов", url, result.total_parsed)
        return result

    async def parse_by_firm_id(self, firm_id: str, city: str = "moscow") -> ParseResult:
        """Парсинг по ID организации и городу."""
        url = REVIEWS_URL_TEMPLATE.format(city=city, firm_id=firm_id)
        return await self.parse_by_url(url)

    async def _parse_page(self, page: Page, url: str) -> ParseResult:
        timeout_ms = self.page_load_timeout_sec * 1000

        # Перехват API-ответов с отзывами
        api_reviews: list[dict] = []
        api_meta: dict = {}
        api_comments: dict[str, list[dict]] = {}  # review_id -> comments
        api_key_holder: list[str] = []  # для извлечения API-ключа из запросов
        api_url_template_holder: list[str] = []  # полный URL первого запроса (для пагинации)
        api_request_headers: dict[str, str] = {}  # заголовки первого API-запроса

        async def _intercept_response(response):
            try:
                resp_url = response.url
                if response.status != 200:
                    return
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type:
                    return

                # Обрабатываем только API отзывов 2GIS
                if "public-api.reviews.2gis.com" not in resp_url:
                    return

                # Извлекаем API-ключ из URL запроса
                key_match = re.search(r"[?&]key=([a-zA-Z0-9_.-]+)", resp_url)
                if key_match and not api_key_holder:
                    api_key_holder.append(key_match.group(1))
                    logger.debug("Извлечён API-ключ из ответа: %s", api_key_holder[0])

                try:
                    data = await response.json()
                except Exception:
                    return
                if not isinstance(data, dict):
                    return

                # Эндпоинт комментариев к конкретному отзыву
                # URL: .../reviews/{review_id}/comments
                if "/comments" in resp_url and "comments" in data:
                    m = re.search(r"/reviews/(\d+)/comments", resp_url)
                    if m and isinstance(data["comments"], list):
                        api_comments[m.group(1)] = data["comments"]
                    return

                # Главный эндпоинт: .../branches/{id}/reviews
                if "reviews" in data and isinstance(data["reviews"], list):
                    api_reviews.extend(data["reviews"])
                    if "meta" in data and isinstance(data["meta"], dict):
                        api_meta.update(data["meta"])
                    # Сохраняем URL-шаблон для пагинации
                    if not api_url_template_holder:
                        api_url_template_holder.append(resp_url)
                        logger.info("URL-шаблон для пагинации: %s", resp_url)
                    logger.debug(
                        "Перехвачено %d отзывов из API (всего %d)",
                        len(data["reviews"]),
                        len(api_reviews),
                    )

            except Exception:
                pass

        page.on("response", _intercept_response)

        def _intercept_request(request):
            """Перехватывает исходящие API-запросы для извлечения ключа и заголовков."""
            try:
                req_url = request.url
                if "public-api.reviews.2gis.com" not in req_url:
                    return
                # Извлекаем API-ключ из URL запроса (расширенный паттерн)
                key_match = re.search(r"[?&]key=([a-zA-Z0-9_.-]+)", req_url)
                if key_match and not api_key_holder:
                    api_key_holder.append(key_match.group(1))
                    logger.debug("Извлечён API-ключ из запроса: %s", api_key_holder[0][:16])
                # Сохраняем заголовки первого API-запроса
                if not api_request_headers:
                    try:
                        for k, v in request.headers.items():
                            api_request_headers[k] = v
                        logger.debug("Сохранены заголовки API-запроса: %s", list(api_request_headers.keys()))
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("request", _intercept_request)

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        # Закрываем модальные окна (cookie-баннер, уведомление о сортировке и т.п.)
        await self._dismiss_modals(page)

        await page.wait_for_timeout(self.initial_load_delay_ms)

        # Ждём первый API-ответ с таймаутом
        api_wait_deadline = asyncio.get_event_loop().time() + self.api_wait_timeout_sec
        while not api_reviews and asyncio.get_event_loop().time() < api_wait_deadline:
            await page.wait_for_timeout(500)
        if not api_reviews:
            logger.info(
                "API не ответил за %d сек, переключаемся на DOM-фоллбэк",
                self.api_wait_timeout_sec,
            )

        # Прокручиваем для подгрузки всех отзывов
        await self._scroll_to_bottom(page)

        # Раскрываем обрезанные тексты
        await self._expand_reviews(page)
        await page.wait_for_timeout(self.post_expand_delay_ms)

        # Если API перехватил первую страницу, дозагружаем все остальные
        if api_reviews:
            api_key = api_key_holder[0] if api_key_holder else DEFAULT_API_KEY
            await self._load_remaining_reviews(
                page, url, api_reviews, api_meta, api_comments, api_key,
                extra_headers=api_request_headers,
            )

            # Дозагружаем комментарии (ответы организации) для отзывов
            await self._load_comments_for_reviews(
                page, api_reviews, api_comments, api_key,
                extra_headers=api_request_headers,
            )

        # Извлекаем данные: предпочитаем API, затем DOM
        if api_reviews:
            logger.info("Используем данные из перехваченного API (%d записей)", len(api_reviews))
            reviews = self._parse_api_reviews(api_reviews, api_comments)
            business_info = self._parse_api_meta(api_meta)
            # Дополняем name/address из DOM (в meta API их нет)
            if not business_info.name or not business_info.address:
                dom_info = await self._extract_business_info_from_dom(page)
                if not business_info.name and dom_info.name:
                    business_info.name = dom_info.name
                if not business_info.address and dom_info.address:
                    business_info.address = dom_info.address
        else:
            logger.info("API не перехвачен, парсим DOM")
            reviews = await self._extract_reviews_from_dom(page)
            business_info = await self._extract_business_info_from_dom(page)

        # Логируем предупреждение если отзывы не найдены
        if not reviews:
            logger.warning(
                "Отзывы не найдены для URL: %s. "
                "Возможно, организация не имеет отзывов или страница не загрузилась.",
                url,
            )

        return ParseResult(
            business_info=business_info,
            reviews=reviews,
            total_parsed=len(reviews),
            source_url=url,
        )

    async def _dismiss_modals(self, page: Page) -> None:
        """Закрывает модальные окна и баннеры."""
        # Кнопка cookie-согласия
        for selector in [
            "button:has-text('Принять')",
            "button:has-text('Хорошо')",
            "button:has-text('Понятно')",
            "button:has-text('OK')",
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await page.wait_for_timeout(500)
            except Exception:
                continue

    async def _scroll_to_bottom(self, page: Page) -> None:
        """Прокручивает контейнер отзывов до конца."""
        last_scroll = -1
        stable_count = 0
        pause_ms = int(self.scroll_pause_sec * 1000)
        step = self.scroll_step_px

        # Ищем скроллируемый контейнер
        scroll_selectors = SELECTORS["scroll_container"].split(", ")
        scroll_selector = None
        for sel in scroll_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    scroll_selector = sel
                    break
            except Exception:
                continue

        if not scroll_selector:
            # Фоллбэк: скроллим весь документ
            logger.warning("Скролл-контейнер не найден, скроллим документ")
            while stable_count < self.stable_scroll_threshold:
                current_scroll = await page.evaluate(
                    """(step) => {
                        window.scrollBy({ top: step, behavior: 'smooth' });
                        return window.scrollY;
                    }""",
                    step,
                )

                if current_scroll == last_scroll:
                    stable_count += 1
                else:
                    stable_count = 0
                last_scroll = current_scroll
                await page.wait_for_timeout(pause_ms)
            return

        scroll_iteration = 0
        while stable_count < self.stable_scroll_threshold:
            current_scroll = await page.evaluate(
                """([selector, step]) => {
                    const el = document.querySelector(selector);
                    if (!el) return 0;
                    el.scrollBy({ top: step, behavior: 'smooth' });
                    return el.scrollTop;
                }""",
                [scroll_selector, step],
            )

            if current_scroll == last_scroll:
                stable_count += 1
            else:
                stable_count = 0

            last_scroll = current_scroll
            scroll_iteration += 1
            if scroll_iteration % 10 == 0:
                logger.info("Скроллинг: итерация %d, позиция %d px", scroll_iteration, last_scroll)
            await page.wait_for_timeout(pause_ms)

        logger.debug("Скроллинг завершён за %d итераций, финальная позиция: %d px", scroll_iteration, last_scroll)

    async def _expand_reviews(self, page: Page) -> None:
        """Раскрывает обрезанный текст отзывов (кнопка 'Читать целиком')."""
        try:
            # Используем evaluate для поиска и клика — не зависаем
            expanded = await page.evaluate("""() => {
                let count = 0;
                const elements = document.querySelectorAll('span, a, button, div');
                for (const el of elements) {
                    const text = (el.textContent || '').trim();
                    if (text === 'Читать целиком' || text === 'Показать полностью') {
                        try { el.click(); count++; } catch(e) {}
                    }
                }
                return count;
            }""")
            if expanded:
                logger.debug("Раскрыто %d текстов отзывов", expanded)
                await page.wait_for_timeout(500)
        except Exception as exc:
            logger.warning("Ошибка раскрытия текстов: %s", exc)

    async def _load_remaining_reviews(
        self,
        page: Page,
        url: str,
        api_reviews: list[dict],
        api_meta: dict,
        api_comments: dict[str, list[dict]],
        api_key: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """Дозагружает оставшиеся отзывы через Playwright APIRequestContext.

        2GIS API использует cursor-пагинацию через next_link (offset_date),
        а НЕ числовой offset. sort_by=date_created возвращает все отзывы,
        sort_by=trust — только избранные.
        """
        m = re.search(r"/firm/(\d+)", url)
        if not m:
            logger.warning("Не удалось извлечь firm_id из URL для пагинации")
            return
        firm_id = m.group(1)

        loaded = len(api_reviews)
        limit = 50

        logger.info(
            "Дозагрузка отзывов: %d уже перехвачено, запрашиваем через API sort_by=date_created",
            loaded,
        )

        # Заголовки для запросов — берём из перехваченных, дополняем обязательные
        request_headers = {
            "Referer": "https://2gis.ru/",
            "Origin": "https://2gis.ru",
            "Accept": "application/json",
        }
        if extra_headers:
            for k, v in extra_headers.items():
                if k.lower() not in ("host", "content-length", "content-type"):
                    request_headers[k] = v

        # Базовый URL: всегда sort_by=date_created для получения всех отзывов
        base_api_url = (
            f"https://public-api.reviews.2gis.com/3.0/branches/{firm_id}/reviews"
            f"?limit={limit}&sort_by=date_created&key={api_key}&locale=ru_RU"
            f"&fields=meta.providers,meta.branch_rating,meta.branch_reviews_count"
            f",meta.total_count,reviews.hiding_reason"
            f"&without_my_first_review=false&rated=false&is_advertiser=false"
        )

        # Cursor-пагинация: используем next_link из ответа
        next_url: str | None = base_api_url
        total_available: int | None = None
        page_num = 0

        while next_url:
            page_num += 1
            data = None
            for attempt in range(1, self.max_api_retries + 1):
                try:
                    data = await page.evaluate(
                        """async (apiUrl) => {
                            try {
                                const resp = await fetch(apiUrl);
                                if (resp.status === 429) return {__rate_limited: true};
                                if (!resp.ok) return {__error: resp.status};
                                return await resp.json();
                            } catch(e) {
                                return {__error: e.message};
                            }
                        }""",
                        next_url,
                    )

                    if isinstance(data, dict) and data.get("__rate_limited"):
                        delay = self.api_retry_delay_ms * attempt
                        logger.warning(
                            "Rate-limit (429) страница %d, попытка %d/%d, ждём %d мс",
                            page_num, attempt, self.max_api_retries, delay,
                        )
                        await page.wait_for_timeout(delay)
                        data = None
                        continue

                    if isinstance(data, dict) and "__error" in data:
                        logger.warning(
                            "API ошибка на странице %d: %s (попытка %d/%d)",
                            page_num, data["__error"], attempt, self.max_api_retries,
                        )
                        if attempt < self.max_api_retries:
                            await page.wait_for_timeout(self.api_retry_delay_ms)
                        data = None
                        continue

                    break

                except Exception as exc:
                    logger.warning(
                        "Ошибка на странице %d, попытка %d/%d: %s",
                        page_num, attempt, self.max_api_retries, exc,
                    )
                    if attempt < self.max_api_retries:
                        await page.wait_for_timeout(self.api_retry_delay_ms)
                    data = None

            if not data or not isinstance(data, dict):
                logger.warning("API вернул пустой ответ на странице %d, прерываем", page_num)
                break

            # Извлекаем total_count из meta (реальное число доступных отзывов)
            meta = data.get("meta", {})
            if total_available is None and isinstance(meta, dict):
                total_available = meta.get("total_count") or meta.get("branch_reviews_count", 0)
                # Обновляем api_meta для корректного подсчёта
                api_meta.update(meta)
                logger.info("Всего отзывов через API: %d", total_available)

            new_reviews = data.get("reviews", [])
            if not new_reviews:
                logger.debug("Нет новых отзывов на странице %d, завершаем", page_num)
                break

            api_reviews.extend(new_reviews)
            logger.info(
                "Страница %d: +%d отзывов (всего %d/%s)",
                page_num, len(new_reviews), len(api_reviews),
                total_available or "?",
            )

            # Cursor-пагинация: next_link из meta
            next_link = meta.get("next_link") if isinstance(meta, dict) else None
            if next_link:
                # next_link — относительный или абсолютный URL
                if next_link.startswith("http"):
                    next_url = next_link
                else:
                    next_url = f"https://public-api.reviews.2gis.com{next_link}"
            else:
                next_url = None

            # Проверяем, все ли загружены
            if total_available and len(api_reviews) >= total_available:
                break

            # Пауза чтобы не триггерить rate-limit
            await page.wait_for_timeout(300)

        logger.info("Дозагрузка завершена: %d отзывов", len(api_reviews))

    async def _load_comments_for_reviews(
        self,
        page: Page,
        api_reviews: list[dict],
        api_comments: dict[str, list[dict]],
        api_key: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """Дозагружает комментарии (ответы организации) для отзывов с comments_count > 0.

        Пропускает отзывы, у которых уже есть official_answer или перехваченные комментарии.
        """
        reviews_needing_comments = []
        for item in api_reviews:
            review_id = str(item.get("id", ""))
            if not review_id:
                continue
            if item.get("official_answer"):
                continue
            if review_id in api_comments:
                continue
            comments_count = item.get("comments_count", 0)
            if isinstance(comments_count, int) and comments_count > 0:
                reviews_needing_comments.append(review_id)

        if not reviews_needing_comments:
            return

        logger.info(
            "Дозагрузка комментариев для %d отзывов", len(reviews_needing_comments),
        )

        request_headers = {
            "Referer": "https://2gis.ru/",
            "Origin": "https://2gis.ru",
            "Accept": "application/json",
        }
        if extra_headers:
            for k, v in extra_headers.items():
                if k.lower() not in ("host", "content-length", "content-type"):
                    request_headers[k] = v

        for review_id in reviews_needing_comments:
            comments_url = (
                f"https://public-api.reviews.2gis.com/3.0/reviews/{review_id}/comments"
                f"?key={api_key}&locale=ru_RU"
            )
            try:
                resp = await page.context.request.get(
                    comments_url, headers=request_headers,
                )
                if resp.ok:
                    data = await resp.json()
                    if isinstance(data, dict) and "comments" in data:
                        api_comments[review_id] = data["comments"]
                        logger.debug(
                            "Загружено %d комментариев для отзыва %s",
                            len(data["comments"]), review_id,
                        )
                else:
                    logger.debug(
                        "Ошибка загрузки комментариев для %s: HTTP %d", review_id, resp.status,
                    )
                await resp.dispose()
            except Exception as exc:
                logger.warning("Ошибка загрузки комментариев для %s: %s", review_id, exc)

            await page.wait_for_timeout(200)

    # ── API-based extraction ──────────────────────────────────────────────────

    def _parse_api_reviews(
        self, items: list[dict], comments: dict[str, list[dict]] | None = None,
    ) -> list[Review]:
        """Парсит отзывы из перехваченных API-ответов 2GIS.

        Структура элемента (v3 API):
          id, rating, text, date_created, date_edited,
          user: { name, ... }, official_answer: { text, ... },
          comments_count, ...
        """
        reviews: list[Review] = []
        seen: set[str] = set()
        comments = comments or {}

        for item in items:
            try:
                review_id = str(item.get("id", ""))
                if review_id and review_id in seen:
                    continue
                if review_id:
                    seen.add(review_id)

                # Автор
                author = ""
                user_info = item.get("user")
                if isinstance(user_info, dict):
                    author = user_info.get("name", "")

                # Рейтинг (1-5)
                rating = item.get("rating", 5)
                if isinstance(rating, str):
                    try:
                        rating = int(rating)
                    except ValueError:
                        rating = 5
                rating = max(1, min(5, int(rating)))

                # Дата
                date = item.get("date_created", "")

                # Текст
                text = item.get("text", "")

                # Ответ организации — из поля official_answer или из перехваченных комментариев
                response = None
                official_answer = item.get("official_answer")
                if isinstance(official_answer, dict):
                    response = official_answer.get("text", "")
                elif review_id and review_id in comments:
                    # Ищем официальный ответ в комментариях
                    for comment in comments[review_id]:
                        if comment.get("is_official_answer"):
                            response = comment.get("text", "")
                            break

                reviews.append(Review(
                    author=author,
                    rating=rating,
                    date=date,
                    text=text,
                    response=response or None,
                ))
            except Exception as exc:
                logger.warning("Ошибка парсинга API-отзыва: %s", exc)

        logger.debug("Из API извлечено %d отзывов", len(reviews))
        return reviews

    def _parse_api_meta(self, meta: dict) -> BusinessInfo:
        """Извлекает бизнес-информацию из meta-блока API-ответа.

        Структура meta (v3 API):
          branch_rating: float, branch_reviews_count: int,
          providers: [...], total_count: int
        """
        rating = None
        rating_val = meta.get("branch_rating")
        if rating_val is not None:
            try:
                rating = float(rating_val)
            except (ValueError, TypeError):
                pass

        review_count = None
        count_val = meta.get("branch_reviews_count")
        if count_val is not None:
            try:
                review_count = int(count_val)
            except (ValueError, TypeError):
                pass

        # name и address из meta недоступны — будут заполнены из DOM при необходимости
        return BusinessInfo(
            name="",
            address="",
            overall_rating=rating,
            total_reviews_on_page=review_count,
        )

    # ── DOM-based extraction (фоллбэк) ───────────────────────────────────────

    async def _extract_business_info_from_dom(self, page: Page) -> BusinessInfo:
        """Извлекает информацию об организации из DOM через Playwright."""
        name = ""
        address = ""
        rating = None
        review_count = None

        try:
            # Название — обычно в заголовке карточки
            name_el = page.locator("h1, [class*='name'], [class*='header'] span").first
            try:
                name = await name_el.inner_text(timeout=2000)
                name = name.strip()
            except Exception:
                pass

            # Адрес — ищем элемент с адресом
            addr_el = page.locator("[class*='address'], [class*='Address']").first
            try:
                address = await addr_el.inner_text(timeout=2000)
                address = address.strip()
            except Exception:
                pass

            # Рейтинг — число рядом со звёздами
            try:
                rating_text = await page.evaluate("""() => {
                    // Ищем элемент с рейтингом (обычно число от 1.0 до 5.0)
                    const elements = document.querySelectorAll('span, div');
                    for (const el of elements) {
                        const text = el.textContent.trim();
                        if (/^[1-5]([.,]\d)?$/.test(text) && el.offsetWidth > 0) {
                            const parent = el.parentElement;
                            // Проверяем что рядом есть звёзды или слово "оценка"/"отзыв"
                            if (parent && /(star|rating|оценк|отзыв)/i.test(parent.innerHTML)) {
                                return text;
                            }
                        }
                    }
                    return '';
                }""")
                if rating_text:
                    rating = float(rating_text.replace(",", "."))
            except Exception:
                pass

            # Количество отзывов — ищем "N отзывов" или "N оценок"
            try:
                count_text = await page.evaluate("""() => {
                    const elements = document.querySelectorAll('span, div, a');
                    for (const el of elements) {
                        const text = el.textContent.trim();
                        const match = text.match(/(\d+)\s*(отзыв|оценк)/i);
                        if (match && el.offsetWidth > 0) {
                            return match[1];
                        }
                    }
                    return '';
                }""")
                if count_text:
                    review_count = int(count_text)
            except Exception:
                pass

        except Exception as exc:
            logger.warning("Ошибка извлечения бизнес-инфо из DOM: %s", exc)

        return BusinessInfo(
            name=name,
            address=address,
            overall_rating=rating,
            total_reviews_on_page=review_count,
        )

    async def _extract_reviews_from_dom(self, page: Page) -> list[Review]:
        """Извлекает отзывы из DOM через JavaScript.

        Использует структурный анализ: ищем блоки со звёздами, рядом с которыми
        расположены автор, дата и текст отзыва.
        """
        try:
            raw_reviews = await page.evaluate("""() => {
                const reviews = [];
                const MONTHS_RE = 'января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря';
                const DATE_RE = new RegExp('(\\d{1,2}\\s+(?:' + MONTHS_RE + ')\\s*\\d{0,4})', 'i');

                // Стратегия 1: ищем контейнеры по aria-label="Оценка"
                const allElements = document.querySelectorAll('*');
                const reviewContainers = new Set();

                for (const el of allElements) {
                    const ariaLabel = el.getAttribute('aria-label') || '';
                    if (/оценка|rating/i.test(ariaLabel)) {
                        let container = el;
                        for (let i = 0; i < 8; i++) {
                            if (!container.parentElement) break;
                            container = container.parentElement;
                            const text = container.innerText || '';
                            const hasDate = DATE_RE.test(text);
                            const hasText = text.length > 50;
                            if (hasDate && hasText) {
                                // Проверяем что контейнер не слишком большой (не весь список отзывов)
                                const childReviews = container.querySelectorAll('[aria-label*="ценка"], [aria-label*="rating"]');
                                if (childReviews.length <= 1) {
                                    reviewContainers.add(container);
                                }
                                break;
                            }
                        }
                    }
                }

                // Стратегия 2: если контейнеры не найдены, ищем по структуре "имя + дата + текст"
                if (reviewContainers.size === 0) {
                    const allDivs = document.querySelectorAll('div');
                    for (const div of allDivs) {
                        const text = div.innerText || '';
                        if (text.length > 30 && text.length < 5000) {
                            const hasDate = DATE_RE.test(text);
                            const hasStar = div.querySelector('svg') !== null || /★|☆|⭐/.test(text);
                            if (hasDate && hasStar) {
                                const children = div.querySelectorAll('div');
                                // Ищем блоки, которые выглядят как отдельные отзывы
                                if (children.length >= 3 && children.length < 50) {
                                    reviewContainers.add(div);
                                }
                            }
                        }
                    }
                }

                for (const container of reviewContainers) {
                    const fullText = container.innerText || '';
                    const lines = fullText.split('\n').map(l => l.trim()).filter(l => l);

                    // Автор — первая строка, если это имя (2-4 слова, без цифр, < 60 символов)
                    let author = '';
                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (trimmed.length > 60) continue;
                        if (/\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)/i.test(trimmed)) continue;
                        if (/^\d+\s*(отзыв|оценк)/i.test(trimmed)) continue;
                        if (/^(Читать целиком|Ответить|Полезный|Официальный)/i.test(trimmed)) continue;
                        // Имя — обычно 1-4 слова, может содержать буквы и пробелы
                        if (/^[A-ZА-ЯЁ][a-zа-яё]+( [A-ZА-ЯЁ][a-zа-яё]*){0,3}$/.test(trimmed)) {
                            author = trimmed;
                            break;
                        }
                        // Фоллбэк: первая короткая строка
                        if (!author && trimmed.length <= 40 && !/[\d:.]/.test(trimmed)) {
                            author = trimmed;
                        }
                        break;
                    }

                    // Рейтинг из aria-label
                    let rating = 5;
                    const ratingEl = container.querySelector('[aria-label*="ценка"], [aria-label*="rating"]');
                    if (ratingEl) {
                        const ratingLabel = ratingEl.getAttribute('aria-label') || '';
                        const rm = ratingLabel.match(/(\d)/);
                        if (rm) rating = parseInt(rm[1], 10);
                    }
                    // Фоллбэк: считаем закрашенные SVG
                    if (!ratingEl) {
                        const svgs = container.querySelectorAll('svg');
                        if (svgs.length === 5) {
                            let filled = 0;
                            for (const svg of svgs) {
                                const paths = svg.querySelectorAll('path');
                                let isFilled = false;
                                for (const p of paths) {
                                    const fill = p.getAttribute('fill') || '';
                                    if (fill && !/(gray|grey|transparent|none|#[cdef])/i.test(fill)) {
                                        isFilled = true;
                                    }
                                }
                                if (isFilled) filled++;
                            }
                            if (filled > 0) rating = filled;
                        }
                    }

                    // Дата
                    let date = '';
                    const fullDateMatch = fullText.match(new RegExp(
                        '(\\d{1,2}\\s+(?:' + MONTHS_RE + ')\\s*\\d{0,4})(?:,\\s*отредактирован)?', 'i'
                    ));
                    if (fullDateMatch) date = fullDateMatch[1].trim();

                    // Текст отзыва — собираем все строки, кроме служебных, затем берём самую длинную
                    const textCandidates = [];
                    for (const line of lines) {
                        if (line === author) continue;
                        if (line === date) continue;
                        if (/^\d+\s*(отзыв|оценк)/i.test(line)) continue;
                        if (/^(Читать целиком|Показать полностью|Ответить|Полезный|Официальный ответ|\d+ отзыв)/i.test(line)) continue;
                        if (/^Оценка \d из \d/i.test(line)) continue;
                        if (line.length < 3) continue;
                        textCandidates.push(line);
                    }
                    // Берём самую длинную, но собираем и соседние строки если они тоже длинные
                    let text = '';
                    if (textCandidates.length > 0) {
                        // Сортируем по длине, берём самую длинную
                        textCandidates.sort((a, b) => b.length - a.length);
                        text = textCandidates[0];
                        // Если есть несколько длинных строк (> 20 символов), объединяем
                        if (textCandidates.length > 1 && textCandidates[1].length > 20) {
                            text = textCandidates.filter(t => t.length > 15).join(' ');
                        }
                    }

                    // Ответ организации
                    let response = null;
                    const respMatch = fullText.match(/(?:официальный|ответ)\s+(?:ответ)?\s*(.+?)(?=Полезный|Ответить|$)/is);
                    if (respMatch) {
                        const respText = respMatch[1].trim();
                        if (respText.length > 5) response = respText;
                    }

                    if (text || author) {
                        reviews.push({
                            author: author,
                            rating: Math.max(1, Math.min(5, rating)),
                            date: date,
                            text: text,
                            response: response
                        });
                    }
                }

                return reviews;
            }""")

            reviews = []
            seen_signatures: set = set()
            for item in raw_reviews:
                try:
                    # Дедупликация DOM-отзывов по (автор, дата)
                    sig = (item.get("author", ""), item.get("date", ""))
                    if sig in seen_signatures and sig != ("", ""):
                        continue
                    seen_signatures.add(sig)

                    reviews.append(Review(
                        author=item.get("author", ""),
                        rating=max(1, min(5, item.get("rating", 5))),
                        date=item.get("date", ""),
                        text=item.get("text", ""),
                        response=item.get("response"),
                    ))
                except Exception as exc:
                    logger.warning("Ошибка создания Review из DOM: %s", exc)

            logger.debug("Из DOM извлечено %d отзывов (уникальных)", len(reviews))
            return reviews

        except Exception as exc:
            logger.error("Ошибка извлечения отзывов из DOM: %s", exc)
            return []
