import re
import logging
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page

from .config import (
    PLACE_URL_TEMPLATE,
    SCROLL_PAUSE_SEC,
    SCROLL_STEP_PX,
    PAGE_LOAD_TIMEOUT_SEC,
    INITIAL_LOAD_DELAY_MS,
    POST_EXPAND_DELAY_MS,
    EXPAND_CLICK_DELAY_MS,
    STABLE_SCROLL_THRESHOLD,
    USER_AGENT,
    SELECTORS,
)
from .models import Review, BusinessInfo, ParseResult

logger = logging.getLogger(__name__)


class GoogleReviewsParser:

    def __init__(self, headless: bool = True, **kwargs: Any):
        self.headless = headless
        self.scroll_pause_sec = kwargs.get("scroll_pause_sec", SCROLL_PAUSE_SEC)
        self.scroll_step_px = kwargs.get("scroll_step_px", SCROLL_STEP_PX)
        self.page_load_timeout_sec = kwargs.get("page_load_timeout_sec", PAGE_LOAD_TIMEOUT_SEC)
        self.initial_load_delay_ms = kwargs.get("initial_load_delay_ms", INITIAL_LOAD_DELAY_MS)
        self.post_expand_delay_ms = kwargs.get("post_expand_delay_ms", POST_EXPAND_DELAY_MS)
        self.expand_click_delay_ms = kwargs.get("expand_click_delay_ms", EXPAND_CLICK_DELAY_MS)
        self.stable_scroll_threshold = kwargs.get("stable_scroll_threshold", STABLE_SCROLL_THRESHOLD)

    async def parse_by_url(self, url: str) -> ParseResult:
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
            # Убираем navigator.webdriver для обхода бот-детекции
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

    async def parse_by_place_id(self, place_id: str) -> ParseResult:
        url = PLACE_URL_TEMPLATE.format(place_id=place_id)
        return await self.parse_by_url(url)

    async def _parse_page(self, page: Page, url: str) -> ParseResult:
        timeout_ms = self.page_load_timeout_sec * 1000

        await page.goto(url, wait_until="load", timeout=timeout_ms)

        # Обработка Google consent-диалога
        await self._handle_consent(page)

        # Ждём загрузки карточки заведения
        await self._wait_for_place_loaded(page)
        await page.wait_for_timeout(2000)

        # Извлекаем бизнес-инфо
        html_before = await page.content()
        soup_before = BeautifulSoup(html_before, "lxml")
        business_info = self._extract_business_info(soup_before)

        # Переход к отзывам — пробуем несколько стратегий
        await self._navigate_to_reviews(page)

        await page.wait_for_timeout(self.initial_load_delay_ms)

        # Ждём появления контейнера с отзывами
        await self._wait_for_reviews(page)

        # Скроллим до конца списка отзывов
        await self._scroll_reviews(page)

        # Раскрываем длинные отзывы
        await self._expand_elements(page, SELECTORS["review_expand_button"])
        await page.wait_for_timeout(self.post_expand_delay_ms)

        # Парсим HTML
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        reviews = self._extract_reviews(soup)

        return ParseResult(
            business_info=business_info,
            reviews=reviews,
            total_parsed=len(reviews),
            source_url=url,
        )

    async def _handle_consent(self, page: Page) -> None:
        """Обработка Google consent-диалога."""
        try:
            await page.wait_for_timeout(1000)
            for sel in [
                "button[aria-label*='Accept']",
                "button[aria-label*='Принять']",
                "form[action*='consent'] button",
            ]:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    logger.info("Consent-диалог принят")
                    await page.wait_for_timeout(2000)
                    return
        except Exception:
            pass

    async def _wait_for_place_loaded(self, page: Page) -> None:
        """Ожидание загрузки карточки заведения (SPA)."""
        try:
            await page.wait_for_function(
                "() => { const h1 = document.querySelector('h1.DUwDvf'); "
                "return h1 && h1.textContent.trim().length > 0; }",
                timeout=self.page_load_timeout_sec * 1000,
            )
            logger.debug("Карточка заведения загружена")
        except Exception:
            logger.warning("Карточка заведения не загрузилась, продолжаем")

    async def _navigate_to_reviews(self, page: Page) -> None:
        """Переход к отзывам. Google Maps может не рендерить role=tab в Playwright.
        
        Стратегии:
        1. Клик по вкладке 'Отзывы' (role=tab)
        2. Прокрутить сайдбар вниз + клик 'Ещё отзывы'
        3. Клик по рейтингу / количеству отзывов
        """
        # Стратегия 1: Клик по role=tab 'Отзывы'
        tab_clicked = await page.evaluate(
            """() => {
                const tabs = document.querySelectorAll("button[role='tab']");
                for (const tab of tabs) {
                    const text = tab.textContent.trim().toLowerCase();
                    const aria = (tab.getAttribute('aria-label') || '').toLowerCase();
                    if (text.includes('отзыв') || text.includes('review') || 
                        aria.includes('отзыв') || aria.includes('review')) {
                        tab.click();
                        return 'tab';
                    }
                }
                return null;
            }"""
        )
        if tab_clicked:
            logger.info("Переход к отзывам: клик по вкладке (role=tab)")
            await page.wait_for_timeout(3000)
            return

        logger.debug("Вкладки role=tab не найдены, прокручиваем сайдбар...")

        # Стратегия 2: Прокрутить сайдбар вниз и найти кнопку 'Ещё отзывы'
        # Google Maps без табов показывает отзывы внизу обзорной страницы
        for _ in range(10):
            await page.evaluate(
                """() => {
                    const sidebar = document.querySelector('div.m6QErb.DxyBCb');
                    if (sidebar) {
                        sidebar.scrollBy({top: 1000, behavior: 'smooth'});
                        return true;
                    }
                    // Фоллбэк — скроллим все m6QErb
                    const containers = document.querySelectorAll('div.m6QErb');
                    for (const c of containers) {
                        c.scrollBy({top: 1000, behavior: 'smooth'});
                    }
                    return false;
                }"""
            )
            await page.wait_for_timeout(500)

        # Ищем и кликаем 'Ещё отзывы'
        more_clicked = await page.evaluate(
            """() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const text = btn.textContent.trim().toLowerCase();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (text.includes('ещё отзыв') || text.includes('more review') ||
                        aria.includes('ещё отзыв') || aria.includes('more review')) {
                        btn.click();
                        return 'more_reviews';
                    }
                }
                return null;
            }"""
        )
        if more_clicked:
            logger.info("Переход к отзывам: клик по 'Ещё отзывы'")
            await page.wait_for_timeout(3000)
            return

        # Стратегия 3: Клик по рейтингу
        rating_clicked = await page.evaluate(
            """() => {
                // Клик по области рейтинга (кнопка со звёздами)
                const ratingBtn = document.querySelector('button.fontDisplayLarge');
                if (ratingBtn) {
                    ratingBtn.click();
                    return 'rating';
                }

                // Клик по числу отзывов
                const spans = document.querySelectorAll('span');
                for (const span of spans) {
                    const text = span.textContent.trim();
                    if (/^\(\d+\)$/.test(text) || /^\d+\s*(отзыв|review)/i.test(text)) {
                        span.click();
                        return 'review_count';
                    }
                }
                return null;
            }"""
        )
        if rating_clicked:
            logger.info("Переход к отзывам: клик по рейтингу/числу отзывов")
            await page.wait_for_timeout(3000)
            return

        logger.warning("Не удалось перейти к отзывам ни одним способом")

    async def _wait_for_reviews(self, page: Page) -> None:
        """Ожидание появления контейнера отзывов."""
        try:
            await page.wait_for_selector(
                SELECTORS["reviews_container"],
                timeout=10000,
            )
            count = await page.evaluate(
                f"() => document.querySelectorAll('{SELECTORS['reviews_container']}').length"
            )
            logger.debug("Контейнеры отзывов найдены: %d", count)
        except Exception:
            logger.warning("Контейнеры отзывов не появились (timeout 10s)")

    async def _scroll_reviews(self, page: Page) -> None:
        """Прокрутка списка отзывов до конца."""
        last_scroll = -1
        stable_count = 0
        pause_ms = int(self.scroll_pause_sec * 1000)

        reviews_sel = SELECTORS["reviews_container"]
        scroll_sel = SELECTORS["scroll_container"]

        scroll_js = f"""() => {{
            // Ищем контейнер m6QErb, который содержит отзывы
            const containers = document.querySelectorAll('{scroll_sel}');
            for (const el of containers) {{
                if (el.querySelectorAll('{reviews_sel}').length > 0) {{
                    el.scrollBy({{ top: {self.scroll_step_px}, behavior: 'smooth' }});
                    return [el.scrollTop, el.scrollHeight];
                }}
            }}
            // Фоллбэк: scrollable=true
            const scrollable = document.querySelector("div[scrollable='true']");
            if (scrollable) {{
                scrollable.scrollBy({{ top: {self.scroll_step_px}, behavior: 'smooth' }});
                return [scrollable.scrollTop, scrollable.scrollHeight];
            }}
            return [0, 0];
        }}"""

        while stable_count < self.stable_scroll_threshold:
            current_scroll, _ = await page.evaluate(scroll_js)

            if current_scroll == last_scroll:
                stable_count += 1
            else:
                stable_count = 0

            last_scroll = current_scroll
            await page.wait_for_timeout(pause_ms)

        logger.debug("Скроллинг завершён, финальная позиция: %d", last_scroll)

    async def _expand_elements(self, page: Page, selector: str) -> None:
        """Раскрытие свёрнутых отзывов (кнопка 'Ещё')."""
        try:
            buttons = await page.query_selector_all(selector)
            for btn in buttons:
                try:
                    await btn.click()
                    await page.wait_for_timeout(self.expand_click_delay_ms)
                except Exception:
                    continue
            if buttons:
                logger.debug("Раскрыто %d элементов [%s]", len(buttons), selector)
        except Exception as exc:
            logger.warning("Ошибка раскрытия элементов [%s]: %s", selector, exc)

    def _extract_business_info(self, soup: BeautifulSoup) -> BusinessInfo:
        """Извлечение информации об организации."""
        name = self._text(soup, SELECTORS["business_name"])
        address = self._text(soup, SELECTORS["business_address"])

        rating = None
        rating_el = soup.select_one(SELECTORS["business_rating"])
        if rating_el:
            try:
                raw = rating_el.get_text(strip=True).replace(",", ".")
                rating = float(raw)
            except ValueError:
                pass

        review_count = None
        count_el = soup.select_one(SELECTORS["business_review_count"])
        if count_el:
            text = count_el.get_text(strip=True)
            numbers = re.findall(r"[\d\s,.]+", text)
            if numbers:
                clean = numbers[-1].strip().replace(",", "").replace(".", "").replace(" ", "")
                if clean.isdigit():
                    review_count = int(clean)

        return BusinessInfo(
            name=name,
            address=address,
            overall_rating=rating,
            total_reviews_on_page=review_count,
        )

    def _extract_reviews(self, soup: BeautifulSoup) -> list[Review]:
        """Извлечение списка отзывов."""
        elements = soup.select(SELECTORS["reviews_container"])
        reviews = []

        for el in elements:
            review = self._parse_single_review(el)
            if review:
                reviews.append(review)

        logger.debug("Извлечено %d отзывов из %d контейнеров", len(reviews), len(elements))
        return reviews

    def _parse_single_review(self, el) -> Review | None:
        """Парсинг одного отзыва."""
        try:
            author = self._text(el, SELECTORS["review_author_name"])
            text = self._text(el, SELECTORS["review_text"])
            rating = self._extract_rating(el)
            date = self._text(el, SELECTORS["review_date"])

            response = None
            response_el = el.select_one(SELECTORS["review_response"])
            if response_el:
                response = response_el.get_text(strip=True)

            return Review(
                author=author,
                rating=rating,
                date=date,
                text=text,
                response=response,
            )
        except Exception as exc:
            logger.warning("Ошибка парсинга отзыва: %s", exc)
            return None

    def _extract_rating(self, el) -> int:
        """Извлечение рейтинга из aria-label звёзд."""
        stars_container = el.select_one(SELECTORS["review_stars_container"])
        if not stars_container:
            return 1

        aria = stars_container.get("aria-label", "")
        numbers = re.findall(r"\d+", aria)
        if numbers:
            val = int(numbers[0])
            return max(1, min(5, val))

        return 1

    @staticmethod
    def _text(parent, selector: str) -> str:
        el = parent.select_one(selector)
        return el.get_text(strip=True) if el else ""
