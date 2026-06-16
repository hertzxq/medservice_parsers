import re
import json
import asyncio
import logging
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page

from .config import (
    REVIEWS_URL_TEMPLATE,
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


class YandexReviewsParser:

    def __init__(self, headless: bool = True, **kwargs: Any):
        self.headless = headless
        # Настройки скроллинга из kwargs или дефолт из config
        self.scroll_pause_sec = kwargs.get("scroll_pause_sec", SCROLL_PAUSE_SEC)
        self.scroll_step_px = kwargs.get("scroll_step_px", SCROLL_STEP_PX)
        self.page_load_timeout_sec = kwargs.get("page_load_timeout_sec", PAGE_LOAD_TIMEOUT_SEC)
        self.initial_load_delay_ms = kwargs.get("initial_load_delay_ms", INITIAL_LOAD_DELAY_MS)
        self.post_expand_delay_ms = kwargs.get("post_expand_delay_ms", POST_EXPAND_DELAY_MS)
        self.expand_click_delay_ms = kwargs.get("expand_click_delay_ms", EXPAND_CLICK_DELAY_MS)
        self.stable_scroll_threshold = kwargs.get("stable_scroll_threshold", STABLE_SCROLL_THRESHOLD)

    async def parse_by_url(self, url: str) -> ParseResult:
        if "/reviews" not in url:
            url = url.rstrip("/") + "/reviews/"

        logger.info("Запуск парсинга: %s", url)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
                user_agent=USER_AGENT,
            )
            page = await context.new_page()
            try:
                result = await self._parse_page(page, url)
            finally:
                await browser.close()

        logger.info("Парсинг завершён: %s | %d отзывов", url, result.total_parsed)
        return result

    async def parse_by_org_id(self, org_id: int | str) -> ParseResult:
        url = REVIEWS_URL_TEMPLATE.format(org_id=org_id)
        return await self.parse_by_url(url)

    async def _parse_page(self, page: Page, url: str) -> ParseResult:
        timeout_ms = self.page_load_timeout_sec * 1000

        # Прямую ссылку на отзыв строим из author.publicId. Его НЕТ в DOM-карточке,
        # но он есть в данных: первая страница — в SSR-скрипте, остальные —
        # в ответах XHR /maps/api/business/fetchReviews. Перехватываем ответы.
        self._org_id = self._extract_org_id(url)
        self._pid_map: dict[str, str] = {}
        self._pending: list[asyncio.Future] = []

        # Яндекс грузит страницы 2+ из веб-воркера, поэтому перехват fetch на
        # главном потоке их не видит — ловим на сетевом уровне Playwright.
        page.on("response", self._on_response)

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        try:
            await page.wait_for_selector(SELECTORS["scroll_container"], timeout=timeout_ms)
        except Exception:
            logger.warning("Контейнер скролла не найден, продолжаем")

        await page.wait_for_timeout(self.initial_load_delay_ms)

        await self._scroll_to_bottom(page)
        await self._expand_elements(page, SELECTORS["review_expand_button"])
        await self._expand_elements(page, SELECTORS["review_comment_expand"])
        await page.wait_for_timeout(self.post_expand_delay_ms)

        # Хвост: Яндекс догружает отзывы и ПОСЛЕ «стабильного» скролла, поэтому
        # подскролливаем и ждём, пока перестанут приходить ответы fetchReviews
        # (иначе ~последняя пачка publicId не успевает перехватиться).
        scroll_selector = SELECTORS["scroll_container"]
        for _ in range(12):
            before = len(self._pending)
            try:
                await page.evaluate(
                    f"() => {{ const el=document.querySelector('{scroll_selector}'); if(el) el.scrollTo(0, el.scrollHeight); }}"
                )
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            if self._pending:
                await asyncio.gather(*self._pending, return_exceptions=True)
            if len(self._pending) == before:
                break

        # Снимаем слушатель, чтобы поздние ответы не плодили «осиротевшие» задачи
        # (Target closed после закрытия браузера), затем дожидаемся собранных.
        try:
            page.remove_listener("response", self._on_response)
        except Exception:
            pass
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        # Первая страница отзывов — в SSR-скрипте (чистый JSON).
        self._index_ssr(soup)

        business_info = self._extract_business_info(soup)
        reviews = self._extract_reviews(soup)

        logger.debug("publicId собрано для %d отзывов", len(self._pid_map))

        return ParseResult(
            business_info=business_info,
            reviews=reviews,
            total_parsed=len(reviews),
            source_url=url,
        )

    # ── Сбор author.publicId для прямых ссылок на отзыв ──────────────────────

    @staticmethod
    def _extract_org_id(url: str) -> str:
        m = re.search(r"/org/(?:[\w-]+/)?(\d+)", url)
        return m.group(1) if m else ""

    @staticmethod
    def _review_key(author: str, text: str) -> str:
        # Ключ устойчив к различиям пробелов/пунктуации между DOM get_text() и
        # JSON-текстом: оставляем только буквы/цифры (кириллица в т.ч.), ё→е.
        norm = lambda s: re.sub(r"[^0-9a-zа-я]+", "", (s or "").lower().replace("ё", "е"))
        t = norm(text)
        if len(t) >= 8:
            return t[:120]
        # Короткий/пустой текст — добавляем автора, чтобы снизить коллизии.
        return f"{norm(author)}|{t}"

    def _on_response(self, response) -> None:
        if "fetchReviews" in (getattr(response, "url", "") or ""):
            self._pending.append(asyncio.ensure_future(self._consume_response(response)))

    async def _consume_response(self, response) -> None:
        try:
            text = await response.text()
            self._index_reviews_json(json.loads(text))
        except Exception:
            return

    def _index_ssr(self, soup: BeautifulSoup) -> None:
        for sc in soup.find_all("script"):
            txt = sc.string or sc.get_text() or ""
            if '"reviews"' in txt and "publicId" in txt:
                try:
                    data = json.loads(txt)
                except Exception:
                    continue
                self._index_reviews_json(data)
                break

    def _index_reviews_json(self, data: Any) -> None:
        arr = self._find_reviews_array(data)
        if not arr:
            return
        for r in arr:
            try:
                author = r.get("author") or {}
                pid = author.get("publicId")
                if pid:
                    self._pid_map[self._review_key(author.get("name", ""), r.get("text", ""))] = pid
            except Exception:
                continue

    def _find_reviews_array(self, o: Any, depth: int = 0):
        if o is None or depth > 8:
            return None
        if isinstance(o, dict):
            rv = o.get("reviews")
            if isinstance(rv, list) and rv and isinstance(rv[0], dict) and "author" in rv[0] and "text" in rv[0]:
                return rv
            for v in o.values():
                r = self._find_reviews_array(v, depth + 1)
                if r:
                    return r
        elif isinstance(o, list):
            if o and isinstance(o[0], dict) and "author" in o[0] and "text" in o[0]:
                return o
            for it in o:
                r = self._find_reviews_array(it, depth + 1)
                if r:
                    return r
        return None

    async def _scroll_to_bottom(self, page: Page) -> None:
        last_scroll = -1
        stable_count = 0
        scroll_selector = SELECTORS["scroll_container"]
        pause_ms = int(self.scroll_pause_sec * 1000)

        while stable_count < self.stable_scroll_threshold:
            current_scroll, _ = await page.evaluate(
                f"""() => {{
                    const el = document.querySelector('{scroll_selector}');
                    if (!el) return [0, 0];
                    el.scrollBy({{ top: {self.scroll_step_px}, behavior: 'smooth' }});
                    return [el.scrollTop, el.scrollHeight];
                }}"""
            )

            if current_scroll == last_scroll:
                stable_count += 1
            else:
                stable_count = 0

            last_scroll = current_scroll
            await page.wait_for_timeout(pause_ms)

        logger.debug("Скроллинг завершён, финальная позиция: %d", last_scroll)

    async def _expand_elements(self, page: Page, selector: str) -> None:
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
        name = self._text(soup, SELECTORS["business_name"])
        address = self._text(soup, SELECTORS["business_address"])

        rating = None
        rating_el = soup.select_one(SELECTORS["business_rating"])
        if rating_el:
            try:
                rating = float(rating_el.get_text(strip=True).replace(",", "."))
            except ValueError:
                pass

        review_count = None
        count_el = soup.select_one(SELECTORS["business_review_count"])
        if count_el:
            numbers = re.findall(r"\d+", count_el.get_text(strip=True))
            if numbers:
                review_count = int(numbers[0])

        return BusinessInfo(
            name=name,
            address=address,
            overall_rating=rating,
            total_reviews_on_page=review_count,
        )

    def _extract_reviews(self, soup: BeautifulSoup) -> list[Review]:
        elements = soup.select(SELECTORS["reviews_container"])
        reviews = []

        for el in elements:
            review = self._parse_single_review(el)
            if review:
                reviews.append(review)

        logger.debug("Извлечено %d отзывов из %d контейнеров", len(reviews), len(elements))
        return reviews

    def _parse_single_review(self, el) -> Review | None:
        try:
            author = self._text(el, SELECTORS["review_author_name"])
            text = self._text(el, SELECTORS["review_text"])
            rating = self._extract_rating(el)
            date = self._extract_date(el)

            response = None
            response_el = el.select_one(SELECTORS["review_response"])
            if response_el:
                response = response_el.get_text(strip=True)

            # Прямая ссылка на отзыв по author.publicId, собранному из SSR/XHR.
            review_url = None
            pid = self._pid_map.get(self._review_key(author, text))
            if pid and self._org_id:
                review_url = (
                    f"https://yandex.ru/maps/org/{self._org_id}"
                    f"/reviews?reviews[publicId]={pid}"
                )

            return Review(
                author=author,
                rating=rating,
                date=date,
                text=text,
                response=response,
                url=review_url,
            )
        except Exception as exc:
            logger.warning("Ошибка парсинга отзыва: %s", exc)
            return None

    def _extract_rating(self, el) -> int:
        stars_container = el.select_one(SELECTORS["review_stars_container"])
        if not stars_container:
            return 1

        aria = stars_container.get("aria-label", "")
        numbers = re.findall(r"\d+", aria)
        if numbers:
            return int(numbers[0])

        full_stars = stars_container.select(SELECTORS["review_star_full"])
        return len(full_stars) or 1

    def _extract_date(self, el) -> str:
        date_meta = el.select_one(SELECTORS["review_date_meta"])
        if date_meta:
            content = date_meta.get("content", "")
            if content:
                return content

        return self._text(el, SELECTORS["review_date"])

    @staticmethod
    def _text(parent, selector: str) -> str:
        el = parent.select_one(selector)
        return el.get_text(strip=True) if el else ""
