import os
import re
import logging
from typing import Any
from datetime import datetime, timedelta

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
        # Реальный Chrome (channel="chrome") отдаёт полную карточку чаще, чем
        # bundled chromium, который Google нередко деградирует. Можно задать через
        # kwarg channel= или env GOOGLE_PARSER_CHANNEL=chrome.
        self.channel = kwargs.get("channel") or os.environ.get("GOOGLE_PARSER_CHANNEL") or None
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
            launch_kwargs: dict[str, Any] = {
                "headless": self.headless,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if self.channel:
                launch_kwargs["channel"] = self.channel
            browser = await pw.chromium.launch(**launch_kwargs)
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

        # domcontentloaded, а не load: Maps держит long-poll, "load" может зависнуть.
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        # Обработка Google consent-диалога / consent.google.com интерстишала
        await self._handle_consent(page)

        # Ждём загрузки карточки заведения (SPA)
        await self._wait_for_place_loaded(page)
        await page.wait_for_timeout(self.initial_load_delay_ms)

        # Извлекаем бизнес-инфо до перехода к отзывам
        soup_before = BeautifulSoup(await page.content(), "lxml")
        business_info = self._extract_business_info(soup_before)

        # Переход к отзывам + ожидание ленты, с одним retry через reload:
        # Google периодически отдаёт деградированную карточку (без вкладки
        # "Отзывы") при троттлинге — повторная загрузка часто помогает.
        have_reviews = False
        for attempt in range(2):
            await self._navigate_to_reviews(page)
            have_reviews = await self._wait_for_reviews(page)
            if have_reviews:
                break
            if attempt == 0:
                logger.info("Лента отзывов не появилась — перезагрузка и повторная попытка")
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception:
                    pass
                await self._handle_consent(page)
                await self._wait_for_place_loaded(page)
                await page.wait_for_timeout(self.initial_load_delay_ms)

        if have_reviews:
            # Сортировка "сначала новые" — детерминированный свежий набор
            await self._sort_by_newest(page)
            # Скроллим ленту до конца (lazy-load)
            await self._scroll_reviews(page)
            # Раскрываем длинные отзывы ("Ещё")
            await self._expand_elements(page, SELECTORS["review_expand_button"])
            await page.wait_for_timeout(self.post_expand_delay_ms)

        soup = BeautifulSoup(await page.content(), "lxml")
        reviews = self._extract_reviews(soup)

        return ParseResult(
            business_info=business_info,
            reviews=reviews,
            total_parsed=len(reviews),
            source_url=url,
        )

    async def _handle_consent(self, page: Page) -> None:
        """Принять Google consent: и отдельный хост consent.google.com, и inline-диалог."""
        accept_selectors = [
            "button[aria-label*='Принять все']",
            "button[aria-label*='Accept all']",
            "button[aria-label*='Принять']",
            "button[aria-label*='Accept']",
            "form[action*='consent'] button",
        ]
        try:
            on_consent = "consent." in (page.url or "")
            if on_consent:
                for sel in accept_selectors:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass
                        logger.info("consent.google.com принят")
                        await page.wait_for_timeout(1500)
                        return
                logger.warning("Обнаружена consent-страница, но кнопка согласия не найдена: %s", page.url)
                return

            # Inline cookie-баннер поверх карты
            await page.wait_for_timeout(800)
            for sel in accept_selectors:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    logger.info("Consent-диалог принят")
                    await page.wait_for_timeout(1200)
                    return
        except Exception as exc:
            logger.warning("Ошибка обработки consent: %s", exc)

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
        """Переход к отзывам.

        Вкладка "Отзывы" (button[role=tab]) инжектится через 1-2с после первой
        отрисовки, поэтому проверяем не один раз, а поллим до ~15с.
        """
        find_and_click = r"""() => {
            const tabs = document.querySelectorAll("button[role='tab']");
            for (const t of tabs) {
                const s = ((t.textContent || '') + ' ' + (t.getAttribute('aria-label') || '')).toLowerCase();
                if (s.includes('отзыв') || s.includes('review')) { t.click(); return 'tab'; }
            }
            // Фоллбэк: кнопка "Отзывов: N" / "N отзывов"
            const btns = document.querySelectorAll('button');
            for (const b of btns) {
                const s = (b.textContent || '').trim().toLowerCase();
                if ((/отзыв/.test(s) && /\d/.test(s)) || (/review/.test(s) && /\d/.test(s))) {
                    b.click(); return 'count_button';
                }
            }
            return null;
        }"""

        for _ in range(30):  # ~15с при шаге 500мс
            clicked = await page.evaluate(find_and_click)
            if clicked:
                logger.info("Переход к отзывам: %s", clicked)
                await page.wait_for_timeout(1500)
                return
            await page.wait_for_timeout(500)

        logger.warning("Вкладка/кнопка отзывов не найдена за ~15с")

    async def _wait_for_reviews(self, page: Page) -> bool:
        """Ожидание появления карточек отзывов. Возвращает True, если появились."""
        try:
            await page.wait_for_selector(SELECTORS["reviews_container"], timeout=15000)
            count = await page.evaluate(
                f"() => document.querySelectorAll('{SELECTORS['reviews_container']}').length"
            )
            logger.debug("Карточки отзывов найдены: %d", count)
            return count > 0
        except Exception:
            logger.warning("Карточки отзывов не появились (timeout 15s)")
            return False

    async def _sort_by_newest(self, page: Page) -> None:
        """Сортировка "Сначала новые" (best-effort — отсутствие не прерывает парсинг)."""
        try:
            opened = await page.evaluate(r"""() => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    const s = ((b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                    if (s.includes('по умолчанию') || s.includes('сортиров') || s.includes('sort')) {
                        b.click(); return true;
                    }
                }
                return false;
            }""")
            if not opened:
                return
            await page.wait_for_timeout(900)
            await page.evaluate(r"""() => {
                const items = document.querySelectorAll("[role='menuitemradio'], [role='menuitem']");
                for (const it of items) {
                    const s = (it.textContent || '').toLowerCase();
                    if (s.includes('сначала новые') || s.includes('новые') || s.includes('newest')) {
                        it.click(); return true;
                    }
                }
                return false;
            }""")
            await page.wait_for_timeout(1500)
            logger.info("Сортировка: сначала новые")
        except Exception as exc:
            logger.debug("Сортировка не применена: %s", exc)

    async def _scroll_reviews(self, page: Page) -> None:
        """Прокрутка ленты отзывов до конца.

        Контейнер прокрутки резолвится динамически — это .m6QErb, в поддереве
        которого реально есть карточки отзывов (фиксированный класс типа
        .DxyBCb/.WNBkOb меняется Google и часто не совпадает).

        Останов — по СТАБИЛЬНОМУ числу карточек, а не по scrollTop: Google
        лениво подгружает отзывы порциями, и плато scrollTop наступает раньше,
        чем подъезжает следующая порция (из-за этого терялся хвост ленты).
        """
        pause_ms = int(self.scroll_pause_sec * 1000)
        reviews_sel = SELECTORS["reviews_container"]
        hint_sel = SELECTORS["scroll_container_hint"]

        # Контейнер карточек вложен в скроллируемую панель: внутренний div с
        # карточками имеет scrollHeight==clientHeight (не скроллится), а реально
        # скроллится ВНЕШНИЙ .m6QErb (scrollHeight > clientHeight). Берём именно
        # его, иначе лента застревает на первой порции (~10 карточек).
        scroll_js = rf"""() => {{
            const cs = [...document.querySelectorAll('{hint_sel}')];
            let el = cs.find(c => c.querySelectorAll('{reviews_sel}').length > 0
                               && c.scrollHeight > c.clientHeight + 20)
                  || cs.find(c => c.querySelectorAll('{reviews_sel}').length > 0)
                  || document.querySelector("div[role='main']")
                  || document.querySelector("div[scrollable='true']");
            if (el) el.scrollTo(0, el.scrollHeight);  // instant, не smooth
            return document.querySelectorAll('{reviews_sel}').length;
        }}"""

        last_count = -1
        stable_count = 0
        for _ in range(120):  # верхний предел итераций (анти-зависание)
            count = await page.evaluate(scroll_js)
            if count == last_count:
                stable_count += 1
            else:
                stable_count = 0
            last_count = count
            if stable_count >= self.stable_scroll_threshold:
                break
            await page.wait_for_timeout(pause_ms)

        logger.debug("Скроллинг завершён, карточек загружено: %d", last_count)

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
        address = self._extract_address(soup)
        rating, review_count = self._extract_rating_and_count(soup)

        return BusinessInfo(
            name=name,
            address=address,
            overall_rating=rating,
            total_reviews_on_page=review_count,
        )

    def _extract_rating_and_count(self, soup: BeautifulSoup):
        """Рейтинг и число отзывов из div.F7nice (текст вида "4,5(174)")."""
        rating = None
        count = None

        el = soup.select_one(SELECTORS["business_rating"])
        if el:
            text = el.get_text(" ", strip=True)
            m_rating = re.search(r"(\d+[.,]\d+)", text)
            if m_rating:
                try:
                    rating = float(m_rating.group(1).replace(",", "."))
                except ValueError:
                    rating = None
            # Число отзывов: в скобках, либо рядом со словом "отзыв"/"review"
            m_paren = re.search(r"\(([\d\s .,]+)\)", text)
            m_word = re.search(r"([\d\s .,]+)\s*(?:отзыв|review)", text, re.IGNORECASE)
            chosen = m_paren.group(1) if m_paren else (m_word.group(1) if m_word else None)
            if chosen:
                digits = re.sub(r"\D", "", chosen)
                if digits:
                    count = int(digits)

        if count is None:
            # Фоллбэк: кнопка "Отзывов: N"
            btn = soup.select_one(SELECTORS["reviews_count_button"])
            if btn:
                digits = re.sub(r"\D", "", btn.get_text(" ", strip=True))
                if digits:
                    count = int(digits)

        return rating, count

    def _extract_address(self, soup: BeautifulSoup) -> str:
        """Чистый адрес из кнопки адреса (без вкраплённых GPS-координат)."""
        el = soup.select_one(SELECTORS["business_address"])
        if not el:
            return ""
        raw = el.get("aria-label", "") or el.get_text(" ", strip=True)
        if not raw:
            return ""
        # Убираем префикс "Адрес: "
        raw = re.sub(r"^\s*адрес:\s*", "", raw, flags=re.IGNORECASE)
        # Убираем координатный токен (напр. "59.925069")
        raw = re.sub(r"-?\d{1,3}\.\d{4,}", " ", raw)
        raw = re.sub(r"\s{2,}", " ", raw).strip(" ,")
        return raw

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
            date = self._extract_date(el)

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
        """Рейтинг из aria-label звёзд ("5 звёзд"), с фоллбэком."""
        stars = el.select_one(SELECTORS["review_stars_container"])
        if stars:
            aria = stars.get("aria-label", "")
            numbers = re.findall(r"\d+", aria)
            if numbers:
                return max(1, min(5, int(numbers[0])))

        # Фоллбэк: ищем aria-label со звёздами где-либо в карточке
        for node in el.select("[aria-label]"):
            aria = node.get("aria-label", "").lower()
            m = re.search(r"(\d+)\s*(?:звёзд|звезд|star)", aria)
            if m:
                return max(1, min(5, int(m.group(1))))

        # Крайне редкий случай: aria-label отсутствует. Не теряем отзыв,
        # ставим максимум (даёт recall; см. risk-notes по Google).
        logger.debug("Рейтинг отзыва не определён, ставлю 5 по умолчанию")
        return 5

    def _extract_date(self, el) -> str:
        """Дата отзыва. Google отдаёт относительные строки ("6 месяцев назад"),
        meta[itemprop=datePublished] нет — нормализуем в ISO (приблизительно)."""
        raw = self._text(el, SELECTORS["review_date"])
        if not raw:
            return ""
        iso = self._relative_to_iso(raw)
        return iso or raw

    @staticmethod
    def _relative_to_iso(raw: str) -> str | None:
        """Относительная дата (ru/en) → ISO YYYY-MM-DD. Месяц≈30д, год≈365д
        (точную дату публикации Google не отдаёт; годится для recency/сортировки)."""
        s = raw.strip().lower()
        now = datetime.now()

        if "сегодн" in s or "только что" in s or "today" in s:
            return now.date().isoformat()
        if "вчера" in s or "yesterday" in s:
            return (now - timedelta(days=1)).date().isoformat()

        m = re.search(
            r"(\d+)?\s*(секунд|минут|час|дн|день|недел|месяц|год|лет|"
            r"second|minute|hour|day|week|month|year)",
            s,
        )
        if not m:
            return None

        n = int(m.group(1)) if m.group(1) else 1
        unit = m.group(2)

        if unit.startswith(("секунд", "минут", "час", "second", "minute", "hour")):
            days = 0
        elif unit.startswith(("недел", "week")):
            days = n * 7
        elif unit.startswith(("месяц", "month")):
            days = n * 30
        elif unit.startswith(("год", "лет", "year")):
            days = n * 365
        else:  # дн / день / day
            days = n

        return (now - timedelta(days=days)).date().isoformat()

    @staticmethod
    def _text(parent, selector: str) -> str:
        el = parent.select_one(selector)
        return el.get_text(strip=True) if el else ""
