import re
import logging
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from playwright.async_api import async_playwright, Page

from .config import (
    REVIEWS_URL_TEMPLATE,
    PAGE_LOAD_TIMEOUT_SEC,
    INITIAL_LOAD_DELAY_MS,
    LOAD_MORE_CLICK_DELAY_MS,
    EXPAND_CLICK_DELAY_MS,
    MAX_LOAD_MORE_CLICKS,
    USER_AGENT,
    SELECTORS,
)
from .models import Review, BusinessInfo, ParseResult

logger = logging.getLogger(__name__)


class NapopravkuParser:

    def __init__(self, headless: bool = True, **kwargs: Any):
        self.headless = headless
        self.page_load_timeout_sec = kwargs.get("page_load_timeout_sec", PAGE_LOAD_TIMEOUT_SEC)
        self.initial_load_delay_ms = kwargs.get("initial_load_delay_ms", INITIAL_LOAD_DELAY_MS)
        self.load_more_click_delay_ms = kwargs.get("load_more_click_delay_ms", LOAD_MORE_CLICK_DELAY_MS)
        self.expand_click_delay_ms = kwargs.get("expand_click_delay_ms", EXPAND_CLICK_DELAY_MS)
        self.max_load_more_clicks = kwargs.get("max_load_more_clicks", MAX_LOAD_MORE_CLICKS)

    async def parse_by_url(self, url: str) -> ParseResult:
        """Парсинг по прямой ссылке на страницу клиники или отзывов."""
        if "/otzyvy" not in url:
            url = url.rstrip("/") + "/otzyvy/"
        # Убираем хэш-фрагмент
        url = url.split("#")[0]
        if not url.endswith("/"):
            url += "/"

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
            # Обход бот-детекции
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

    async def parse_by_slug(self, city: str, clinic_slug: str) -> ParseResult:
        """Парсинг по городу и слагу клиники (например: city='spb', slug='schastlivyy-vzglyad-...')."""
        url = REVIEWS_URL_TEMPLATE.format(city=city, clinic_slug=clinic_slug)
        return await self.parse_by_url(url)

    async def _parse_page(self, page: Page, url: str) -> ParseResult:
        """Основной цикл: загрузка страницы → подгрузка всех отзывов → извлечение данных."""
        timeout_ms = self.page_load_timeout_sec * 1000

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(self.initial_load_delay_ms)

        # Закрываем баннеры (cookie и т.п.)
        await self._dismiss_modals(page)

        # Подгружаем все отзывы кнопкой «Показать ещё»
        await self._load_all_reviews(page)

        # Раскрываем ответы клиники (спойлеры)
        await self._expand_responses(page)

        # Получаем HTML и парсим
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        business_info = self._extract_business_info(soup)
        reviews = self._extract_reviews(soup)

        return ParseResult(
            business_info=business_info,
            reviews=reviews,
            total_parsed=len(reviews),
            source_url=url,
        )

    async def _dismiss_modals(self, page: Page) -> None:
        """Закрывает cookie-баннеры и модальные окна."""
        for selector in [
            "button:has-text('Принять')",
            "button:has-text('Хорошо')",
            "button:has-text('Понятно')",
            "button:has-text('OK')",
            "button:has-text('Согласен')",
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await page.wait_for_timeout(500)
            except Exception:
                continue

    async def _load_all_reviews(self, page: Page) -> None:
        """Нажимает кнопку «Показать ещё» пока она доступна, подгружая все отзывы."""
        click_count = 0
        selector = SELECTORS["load_more_button"]

        while click_count < self.max_load_more_clicks:
            try:
                btn = page.locator(selector).first
                if not await btn.is_visible(timeout=2000):
                    logger.debug("Кнопка «Показать ещё» не найдена, все отзывы загружены")
                    break

                await btn.scroll_into_view_if_needed()
                await btn.click()
                click_count += 1

                if click_count % 5 == 0:
                    logger.info("Нажатий «Показать ещё»: %d", click_count)

                await page.wait_for_timeout(self.load_more_click_delay_ms)

            except Exception as exc:
                logger.debug("Кнопка «Показать ещё» недоступна: %s", exc)
                break

        if click_count > 0:
            logger.info("Загрузка завершена, нажатий «Показать ещё»: %d", click_count)

    async def _expand_responses(self, page: Page) -> None:
        """Раскрывает все ответы клиники (спойлеры)."""
        try:
            # Ищем все кнопки «Ответ клиники» и кликаем
            expanded = await page.evaluate("""() => {
                let count = 0;
                const buttons = document.querySelectorAll(
                    '.review-comment-collapse-button, ' +
                    'button[class*="comment-collapse"], ' +
                    'button[class*="response"], ' +
                    'a[class*="comment-collapse"]'
                );
                for (const btn of buttons) {
                    try { btn.click(); count++; } catch(e) {}
                }
                // Фоллбэк: ищем по тексту
                if (count === 0) {
                    const elements = document.querySelectorAll('button, a, span');
                    for (const el of elements) {
                        const text = (el.textContent || '').trim();
                        if (text === 'Ответ клиники' || text === 'Показать ответ') {
                            try { el.click(); count++; } catch(e) {}
                        }
                    }
                }
                return count;
            }""")
            if expanded:
                logger.debug("Раскрыто %d ответов клиники", expanded)
                await page.wait_for_timeout(self.expand_click_delay_ms)
        except Exception as exc:
            logger.warning("Ошибка раскрытия ответов: %s", exc)

    # ── Извлечение информации о клинике ──────────────────────────────────────

    def _extract_business_info(self, soup: BeautifulSoup) -> BusinessInfo:
        """Извлекает информацию о клинике."""
        # Название
        name = ""
        name_el = soup.select_one(SELECTORS["business_name"])
        if name_el:
            name = name_el.get_text(strip=True)
            # Убираем префикс «Отзывы о клинике» / «N отзывов о клинике»
            name = re.sub(r"^\d+\s+отзыв\w*\s+о\s+клинике\s+", "", name, flags=re.IGNORECASE)
            name = re.sub(r"^Отзывы\s+о\s+клинике\s+", "", name, flags=re.IGNORECASE)

        # Адрес
        address = ""
        addr_el = soup.select_one(SELECTORS["business_address"])
        if addr_el:
            address = addr_el.get_text(strip=True)
        # Фоллбэк: ищем в метаданных или в блоке контактов
        if not address:
            addr_meta = soup.select_one("[itemprop='address']")
            if addr_meta:
                address = addr_meta.get("content", "") or addr_meta.get_text(strip=True)

        # Рейтинг
        rating = None
        rating_el = soup.select_one(SELECTORS["business_rating"])
        if rating_el:
            try:
                rating = float(rating_el.get_text(strip=True).replace(",", "."))
            except (ValueError, TypeError):
                pass
        # Фоллбэк: Schema.org
        if rating is None:
            rating_schema = soup.select_one("[itemprop='ratingValue']")
            if rating_schema:
                try:
                    val = rating_schema.get("content", "") or rating_schema.get_text(strip=True)
                    rating = float(str(val).replace(",", "."))
                except (ValueError, TypeError):
                    pass

        # Количество отзывов
        review_count = None
        count_el = soup.select_one(SELECTORS["business_review_count"])
        if count_el:
            nums = re.findall(r"\d+", count_el.get_text(strip=True))
            if nums:
                review_count = int(nums[0])
        # Фоллбэк: из заголовка h1 ("72 отзыва о клинике ...")
        if review_count is None and name_el:
            h1_text = name_el.get_text(strip=True)
            h1_match = re.match(r"(\d+)\s+отзыв", h1_text)
            if h1_match:
                review_count = int(h1_match.group(1))
        # Фоллбэк: Schema.org
        if review_count is None:
            count_schema = soup.select_one("[itemprop='reviewCount']")
            if count_schema:
                val = count_schema.get("content", "") or count_schema.get_text(strip=True)
                nums = re.findall(r"\d+", str(val))
                if nums:
                    review_count = int(nums[0])

        return BusinessInfo(
            name=name,
            address=address,
            overall_rating=rating,
            total_reviews_on_page=review_count,
        )

    # ── Извлечение отзывов ───────────────────────────────────────────────────

    def _extract_reviews(self, soup: BeautifulSoup) -> list[Review]:
        """Извлекает все отзывы со страницы.

        Стратегия:
          1. Schema.org разметка (если доступна)
          2. CSS-классы .review-card
        """
        reviews: list[Review] = []

        # Способ 1: Schema.org
        schema_reviews = soup.select(SELECTORS["schema_review"])
        if schema_reviews:
            for el in schema_reviews:
                review = self._parse_review_schema(el)
                if review:
                    reviews.append(review)
            if reviews:
                logger.debug("Извлечено %d отзывов через Schema.org", len(reviews))
                return reviews

        # Способ 2: CSS-классы
        review_cards = soup.select(SELECTORS["review_item"])
        for card in review_cards:
            review = self._parse_review_card(card)
            if review:
                reviews.append(review)

        logger.debug("Извлечено %d отзывов через CSS-селекторы", len(reviews))
        return reviews

    def _parse_review_schema(self, el: Tag) -> Review | None:
        """Парсит отзыв с Schema.org разметкой."""
        try:
            # Автор
            author = ""
            author_el = el.select_one(SELECTORS["schema_author"])
            if author_el:
                author = author_el.get("content", "") or author_el.get_text(strip=True)
                author = self._clean_author(author)

            # Рейтинг
            rating = 5.0
            rating_el = el.select_one(SELECTORS["schema_rating"])
            if rating_el:
                try:
                    val = rating_el.get("content", "") or rating_el.get_text(strip=True)
                    rating = float(str(val).replace(",", "."))
                    rating = max(1.0, min(5.0, rating))
                except (ValueError, TypeError):
                    pass

            # Дата
            date = ""
            date_el = el.select_one(SELECTORS["schema_date"])
            if date_el:
                date = date_el.get("content", "") or date_el.get_text(strip=True)

            # Текст отзыва
            text = ""
            for body_sel in SELECTORS["schema_body"].split(", "):
                body_el = el.select_one(body_sel)
                if body_el:
                    text = body_el.get_text(" ", strip=True)
                    if text:
                        break

            # Врач
            doctor = self._extract_doctor(el)

            # Плюсы/минусы
            pros, cons = self._extract_pros_cons(el)

            # Ответ клиники
            response = self._extract_response(el)

            # Если текст пустой, собираем весь контент
            if not text:
                text = self._collect_text(el)

            return Review(
                author=author,
                rating=rating,
                date=date,
                text=text,
                doctor=doctor,
                pros=pros,
                cons=cons,
                response=response,
            )
        except Exception as exc:
            logger.warning("Ошибка парсинга отзыва (Schema): %s", exc)
            return None

    def _parse_review_card(self, card: Tag) -> Review | None:
        """Парсит отзыв по CSS-классам (.review-card)."""
        try:
            # Автор
            author = self._clean_author(self._text(card, SELECTORS["review_author"]))

            # Рейтинг (количество активных звёзд)
            stars = card.select(SELECTORS["review_stars_active"])
            rating = float(len(stars)) if stars else 5.0
            rating = max(1.0, min(5.0, rating))

            # Дата
            date = self._text(card, SELECTORS["review_date"])

            # Текст отзыва
            text = self._text(card, SELECTORS["review_body"])

            # Врач
            doctor = self._extract_doctor(card)

            # Плюсы/минусы
            pros, cons = self._extract_pros_cons(card)

            # Ответ клиники
            response = self._extract_response(card)

            return Review(
                author=author,
                rating=rating,
                date=date,
                text=text,
                doctor=doctor,
                pros=pros,
                cons=cons,
                response=response,
            )
        except Exception as exc:
            logger.warning("Ошибка парсинга отзыва (card): %s", exc)
            return None

    # ── Вспомогательные методы ────────────────────────────────────────────────

    def _extract_doctor(self, el: Tag) -> str | None:
        """Извлекает имя врача из отзыва."""
        for selector in SELECTORS["review_doctor"].split(", "):
            doctor_el = el.select_one(selector)
            if doctor_el:
                doctor = doctor_el.get_text(strip=True)
                if doctor:
                    return doctor
        return None

    def _extract_pros_cons(self, el: Tag) -> tuple[str | None, str | None]:
        """Извлекает секции «Что понравилось» и «Что не понравилось»."""
        full_text = el.get_text(" ", strip=True)
        pros = self._extract_section(full_text, "Что понравилось")
        if not pros:
            pros = self._extract_section(full_text, "Понравилось")
        cons = self._extract_section(full_text, "Что не понравилось")
        if not cons:
            cons = self._extract_section(full_text, "Не понравилось")
        return pros, cons

    @staticmethod
    def _extract_section(full_text: str, header: str) -> str | None:
        """Извлекает текст секции по заголовку."""
        # Ищем текст между текущим заголовком и следующим (или концом)
        pattern = re.compile(
            rf"{re.escape(header)}[:\s]+(.*?)(?=(?:Что понравилось|Понравилось|Что не понравилось|Не понравилось|Ответ клиники|$))",
            re.DOTALL,
        )
        match = pattern.search(full_text)
        if match:
            text = match.group(1).strip()
            if text and text not in ("-", "—") and text.lower() != "нет":
                return text
        return None

    def _extract_response(self, el: Tag) -> str | None:
        """Извлекает ответ клиники."""
        # Ищем по селектору тела ответа
        for selector in SELECTORS["review_response_body"].split(", "):
            resp_el = el.select_one(selector)
            if resp_el:
                text = resp_el.get_text(" ", strip=True)
                if text:
                    return text

        # Фоллбэк: ищем по тексту «Ответ клиники» → следующий блок
        full_text = el.get_text(" ", strip=True)
        response_match = re.search(
            r"Ответ клиники\s*(.*?)$",
            full_text,
            re.DOTALL,
        )
        if response_match:
            text = response_match.group(1).strip()
            if text:
                return text

        return None

    def _collect_text(self, el: Tag) -> str:
        """Собирает весь текст, исключая служебные элементы."""
        parts = []
        for child in el.find_all(string=True, recursive=True):
            parent_tag = child.parent
            if parent_tag and parent_tag.name in ("script", "style", "noscript"):
                continue
            stripped = child.strip()
            if stripped:
                parts.append(stripped)
        return " ".join(parts)

    @staticmethod
    def _clean_author(raw: str) -> str:
        """Очищает имя автора от мусора типа '3 отзываДо 10 записей через НаПоправку'."""
        raw = re.sub(r"\s+", " ", raw).strip()
        # Убираем суффиксы: "N отзыв(ов/а)", "До N записей через НаПоправку" и т.п.
        raw = re.sub(r"\d+\s*отзыв\w*.*$", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"До\s+\d+\s+записей.*$", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"Более\s+\d+\s+записей.*$", "", raw, flags=re.IGNORECASE).strip()
        return raw

    @staticmethod
    def _text(parent, selector: str) -> str:
        el = parent.select_one(selector)
        return el.get_text(strip=True) if el else ""
