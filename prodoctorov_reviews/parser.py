import re
import logging
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from playwright.async_api import async_playwright, Page

from .config import (
    REVIEWS_URL_TEMPLATE,
    PAGE_URL_TEMPLATE,
    PAGE_LOAD_TIMEOUT_SEC,
    INITIAL_LOAD_DELAY_MS,
    PAGE_NAVIGATE_DELAY_MS,
    MAX_PAGES,
    USER_AGENT,
    SELECTORS,
)
from .models import Review, BusinessInfo, ParseResult

logger = logging.getLogger(__name__)


class ProdoctorovParser:

    def __init__(self, headless: bool = True, **kwargs: Any):
        self.headless = headless
        self.page_load_timeout_sec = kwargs.get("page_load_timeout_sec", PAGE_LOAD_TIMEOUT_SEC)
        self.initial_load_delay_ms = kwargs.get("initial_load_delay_ms", INITIAL_LOAD_DELAY_MS)
        self.page_navigate_delay_ms = kwargs.get("page_navigate_delay_ms", PAGE_NAVIGATE_DELAY_MS)
        self.max_pages = kwargs.get("max_pages", MAX_PAGES)

    async def parse_by_url(self, url: str) -> ParseResult:
        """Парсинг по прямой ссылке на страницу клиники или отзывов."""
        if "/otzivi" not in url:
            url = url.rstrip("/") + "/otzivi/"
        if not url.endswith("/"):
            url += "/"

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
                result = await self._parse_all_pages(page, url)
            finally:
                await browser.close()

        logger.info("Парсинг завершён: %s | %d отзывов", url, result.total_parsed)
        return result

    async def parse_by_lpu_id(self, city: str, lpu_id: str) -> ParseResult:
        """Парсинг по городу и ID клиники (например: city='moskva', lpu_id='69674-medpraym')."""
        url = REVIEWS_URL_TEMPLATE.format(city=city, lpu_id=lpu_id)
        return await self.parse_by_url(url)

    async def _parse_all_pages(self, page: Page, base_url: str) -> ParseResult:
        """Проход по всем страницам отзывов с пагинацией."""
        timeout_ms = self.page_load_timeout_sec * 1000

        # Загружаем первую страницу
        await page.goto(base_url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(self.initial_load_delay_ms)

        # Извлекаем информацию о клинике с первой страницы
        first_html = await page.content()
        first_soup = BeautifulSoup(first_html, "lxml")
        business_info = self._extract_business_info(first_soup)

        all_reviews: list[Review] = []
        current_page = 1

        # Определяем базовый URL для пагинации
        page_base = self._get_page_base_url(base_url)

        # URL текущей страницы — нужен для прямых ссылок на отзыв (#rate-{id}).
        current_url = base_url

        while current_page <= self.max_pages:
            logger.info("Обработка страницы %d ...", current_page)

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            self._reviews_page_url = current_url
            page_reviews = self._extract_reviews(soup)
            if not page_reviews:
                logger.info("Страница %d: отзывов не найдено, завершаем", current_page)
                break

            all_reviews.extend(page_reviews)
            logger.info("Страница %d: +%d отзывов (всего %d)", current_page, len(page_reviews), len(all_reviews))

            # Лимит страниц достигнут?
            if current_page >= self.max_pages:
                logger.info("Достигнут лимит страниц (%d), завершаем", self.max_pages)
                break

            # Проверяем, есть ли кнопка «Далее»
            has_next = await self._has_next_page(page)
            if not has_next:
                logger.info("Кнопка «Далее» не найдена, завершаем")
                break

            # Переходим на следующую страницу
            current_page += 1
            next_url = f"{page_base}{current_page}/"
            logger.debug("Переход на: %s", next_url)

            await page.goto(next_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(self.page_navigate_delay_ms)
            current_url = next_url

        return ParseResult(
            business_info=business_info,
            reviews=all_reviews,
            total_parsed=len(all_reviews),
            source_url=base_url,
        )

    async def _has_next_page(self, page: Page) -> bool:
        """Проверяет наличие кнопки «Далее» на странице."""
        try:
            next_links = await page.query_selector_all("a")
            for link in next_links:
                text = await link.inner_text()
                if "Далее" in text.strip():
                    return True
        except Exception:
            pass
        return False

    def _get_page_base_url(self, url: str) -> str:
        """Извлекает базовый URL для формирования ссылок пагинации.

        Пример: https://prodoctorov.ru/moskva/lpu/69674-medpraym/otzivi/
             -> https://prodoctorov.ru/moskva/lpu/69674-medpraym/otzivi/
        """
        # Убираем номер страницы, если есть (например /otzivi/3/)
        url = re.sub(r"/otzivi/\d+/?$", "/otzivi/", url)
        if not url.endswith("/"):
            url += "/"
        return url

    @staticmethod
    def _normalize_rating(raw_value: float) -> float:
        """Нормализует рейтинг с процентной шкалы (0-100) в шкалу 1-5."""
        if raw_value > 5.0:
            # ПроДокторов хранит рейтинг как процент: 100 = 5.0, 80 = 4.0 и т.д.
            return round(raw_value / 20.0, 1)
        return raw_value

    def _extract_business_info(self, soup: BeautifulSoup) -> BusinessInfo:
        """Извлекает информацию о клинике."""
        # Название клиники — из data-qa атрибута
        name = ""
        name_el = soup.select_one("[data-qa='lpu_card_heading_lpu_name']")
        if name_el:
            # Собираем только прямые текстовые узлы (без иконок и дочерних span)
            direct_texts = [t.strip() for t in name_el.find_all(string=True, recursive=False) if t.strip()]
            name = " ".join(direct_texts) if direct_texts else name_el.get_text(strip=True)
        if not name:
            h1 = soup.select_one("h1")
            if h1:
                direct_texts = [t.strip() for t in h1.find_all(string=True, recursive=False) if t.strip()]
                name = " ".join(direct_texts) if direct_texts else h1.get_text(strip=True)

        # Адрес — из блока контактов (пропускаем часы работы и телефон)
        address = ""
        for addr_el in soup.select(".b-contacts-list__text"):
            addr_text = addr_el.get_text(strip=True)
            # Пропускаем часы работы ("Закрыто", "Открыто", "Круглосуточно") и телефоны
            if not addr_text:
                continue
            if re.match(r"^(Закрыто|Открыто|Круглосуточно|\+7|\d{1}\s)", addr_text):
                continue
            address = addr_text
            break
        # Фоллбэк: адрес из карточки отзыва
        if not address:
            addr_el = soup.select_one(".b-review-card__address")
            if addr_el:
                address = addr_el.get_text(strip=True)

        # Рейтинг (приходит как процент 0-100, нормализуем в 1-5)
        rating = None
        rating_el = soup.select_one("[itemprop='ratingValue']")
        if rating_el:
            try:
                val = rating_el.get("content", "") or rating_el.get_text(strip=True)
                raw_rating = float(str(val).replace(",", "."))
                rating = self._normalize_rating(raw_rating)
            except (ValueError, TypeError):
                pass

        # Количество отзывов — из TOC-элемента «Отзывы»
        review_count = None
        for toc_item in soup.select(".b-doctor-details__toc-item, .b-doctor-details__toc-item_active"):
            toc_text = toc_item.get_text(" ", strip=True)
            if "Отзывы" in toc_text:
                nums = re.findall(r"\d+", toc_text)
                if nums:
                    review_count = int(nums[0])
                break

        # Фоллбэк: Schema.org
        if review_count is None:
            count_el = soup.select_one("[itemprop='reviewCount']")
            if count_el:
                val = count_el.get("content", "") or count_el.get_text(strip=True)
                nums = re.findall(r"\d+", str(val))
                if nums:
                    review_count = int(nums[0])

        return BusinessInfo(
            name=name,
            address=address,
            overall_rating=rating,
            total_reviews_on_page=review_count,
        )

    def _extract_reviews(self, soup: BeautifulSoup) -> list[Review]:
        """Извлекает все отзывы со страницы."""
        reviews: list[Review] = []

        # Способ 1: ищем по Schema.org разметке
        review_elements = soup.select("div[itemtype*='schema.org/Review']")
        if not review_elements:
            review_elements = soup.select("div[itemprop='review']")

        if review_elements:
            for el in review_elements:
                review = self._parse_review_schema(el)
                if review:
                    reviews.append(review)
            logger.debug("Извлечено %d отзывов через Schema.org", len(reviews))
            return reviews

        # Способ 2: парсинг по визуальной структуре страницы
        reviews = self._extract_reviews_visual(soup)
        logger.debug("Извлечено %d отзывов через визуальный парсинг", len(reviews))
        return reviews

    def _parse_review_schema(self, el: Tag) -> Review | None:
        """Парсит отзыв с Schema.org разметкой."""
        try:
            # Автор (нормализуем пробелы)
            author = ""
            author_el = el.select_one("[itemprop='author']")
            if author_el:
                raw = author_el.get("content", "") or author_el.get_text(" ", strip=True)
                author = re.sub(r"\s+", " ", raw).strip()

            # Рейтинг (процент 0-100 → шкала 1-5)
            rating = 5.0
            rating_el = el.select_one("[itemprop='ratingValue']")
            if rating_el:
                try:
                    val = rating_el.get("content", "") or rating_el.get_text(strip=True)
                    raw_rating = float(str(val).replace(",", "."))
                    rating = self._normalize_rating(raw_rating)
                except (ValueError, TypeError):
                    pass

            # Дата
            date = ""
            date_el = el.select_one("[itemprop='datePublished']")
            if date_el:
                date = date_el.get("content", "") or date_el.get_text(strip=True)

            # Тело отзыва
            text = ""
            body_el = el.select_one("[itemprop='reviewBody']")
            if body_el:
                text = body_el.get_text(" ", strip=True)

            if not text:
                desc_el = el.select_one("[itemprop='description']")
                if desc_el:
                    text = desc_el.get_text(" ", strip=True)

            # Убираем заголовки секций из начала текста
            text = self._clean_review_text(text)

            # Секции «Понравилось» / «Не понравилось» из текста
            pros, cons = self._extract_pros_cons(el)

            # Если text пустой, собираем весь текстовый контент
            if not text:
                text = self._collect_review_text(el)

            # Ответ клиники
            response = self._extract_response(el)

            # Прямая ссылка на отзыв: у карточки есть data-review-id, а внутри —
            # именованный якорь <a name="rate-{id}">, на который ведёт #rate-{id}.
            review_url = None
            rid = el.get("data-review-id")
            base = getattr(self, "_reviews_page_url", "")
            if rid and base:
                review_url = f"{base.split('#')[0]}#rate-{rid}"

            return Review(
                author=author,
                rating=rating,
                date=date,
                text=text,
                pros=pros,
                cons=cons,
                response=response,
                url=review_url,
            )
        except Exception as exc:
            logger.warning("Ошибка парсинга отзыва (Schema): %s", exc)
            return None

    def _extract_reviews_visual(self, soup: BeautifulSoup) -> list[Review]:
        """Фоллбэк: парсинг отзывов по визуальной структуре (без Schema.org)."""
        reviews: list[Review] = []

        # Ищем блоки с рейтингом — характерный маркер начала отзыва
        # Паттерн: «Пациент +7 ...» + дата + рейтинг + текст
        tab_content = soup.select_one("#tab-content")
        if not tab_content:
            return reviews

        # Ищем все элементы, содержащие текст "Пациент" (авторов)
        patient_blocks = []
        for el in tab_content.find_all(string=re.compile(r"Пациент\s*\+7")):
            # Поднимаемся к ближайшему контейнеру-отзыву
            parent = el.find_parent("div")
            if parent:
                # Ищем ближайший общий контейнер
                container = self._find_review_container(parent)
                if container and container not in patient_blocks:
                    patient_blocks.append(container)

        for container in patient_blocks:
            review = self._parse_review_visual(container)
            if review:
                reviews.append(review)

        return reviews

    def _find_review_container(self, el: Tag) -> Tag | None:
        """Находит контейнер отдельного отзыва, поднимаясь по дереву."""
        current = el
        for _ in range(10):
            parent = current.parent
            if not parent or parent.name == "body":
                return current
            # Если у родителя есть несколько дочерних блоков с текстом "Пациент",
            # значит мы поднялись слишком высоко
            patient_count = len(parent.find_all(string=re.compile(r"Пациент\s*\+7"), recursive=True))
            if patient_count > 1:
                return current
            current = parent
        return current

    def _parse_review_visual(self, container: Tag) -> Review | None:
        """Парсит один отзыв по визуальной структуре."""
        try:
            full_text = container.get_text(" ", strip=True)

            # Автор
            author_match = re.search(r"(Пациент\s*\+7\s*[\d\sXx]+)", full_text)
            author = author_match.group(1).strip() if author_match else ""

            # Рейтинг
            rating = 5.0
            rating_match = re.search(r"(\d[.,]\d)\s*(?:Отлично|Хорошо|Нормально|Плохо|Ужасно)", full_text)
            if rating_match:
                try:
                    rating = float(rating_match.group(1).replace(",", "."))
                except ValueError:
                    pass

            # Дата
            date = ""
            date_match = re.search(
                r"(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
                r"(?:\s+\d{4})?\s*(?:в\s+\d{1,2}:\d{2})?)",
                full_text,
            )
            if date_match:
                date = date_match.group(1).strip()

            # Секции
            text = self._extract_section(full_text, "История пациента")
            pros = self._extract_section(full_text, "Понравилось")
            cons = self._extract_section(full_text, "Не понравилось")

            if not text:
                # Убираем служебную информацию, оставляем содержательный текст
                text = full_text

            return Review(
                author=author,
                rating=rating,
                date=date,
                text=text,
                pros=pros if pros else None,
                cons=cons if cons else None,
                response=None,
            )
        except Exception as exc:
            logger.warning("Ошибка визуального парсинга: %s", exc)
            return None

    @staticmethod
    def _clean_review_text(text: str) -> str:
        """Убирает заголовки секций из текста отзыва."""
        # Удаляем заголовки секций в начале текста
        section_headers = ["История пациента", "Понравилось", "Не понравилось"]
        for header in section_headers:
            if text.startswith(header):
                text = text[len(header):].strip()
        # Нормализуем пробелы
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _extract_pros_cons(self, el: Tag) -> tuple[str | None, str | None]:
        """Извлекает секции «Понравилось» и «Не понравилось»."""
        full_text = el.get_text(" ", strip=True)
        pros = self._extract_section(full_text, "Понравилось")
        cons = self._extract_section(full_text, "Не понравилось")
        return pros, cons

    @staticmethod
    def _extract_section(full_text: str, header: str) -> str | None:
        """Извлекает текст секции по заголовку."""
        pattern = re.compile(
            rf"{re.escape(header)}\s+(.*?)(?=(?:История пациента|Понравилось|Не понравилось|Приём был|$))",
            re.DOTALL,
        )
        match = pattern.search(full_text)
        if match:
            text = match.group(1).strip()
            if text and text != "-" and text != "—" and text.lower() != "нет":
                return text
        return None

    def _extract_response(self, el: Tag) -> str | None:
        """Извлекает ответ клиники на отзыв."""
        # Ищем блок ответа — обычно подблок с названием клиники
        full_text = el.get_text(" ", strip=True)
        # Ответ обычно идёт после основного отзыва, начинается с названия клиники
        # Ищем паттерн: «Клиника «...» ДАТА ТЕКСТ»
        response_match = re.search(
            r"(Клиника\s*[«\"].*?[»\"])\s*(\d{1,2}\s+\w+\s+\d{4}\s+в\s+\d{1,2}:\d{2})\s*(.*?)$",
            full_text,
            re.DOTALL,
        )
        if response_match:
            return response_match.group(3).strip() or None
        return None

    def _collect_review_text(self, el: Tag) -> str:
        """Собирает весь текст отзыва, исключая служебные элементы."""
        text_parts = []
        for child in el.find_all(string=True, recursive=True):
            parent_tag = child.parent
            if parent_tag and parent_tag.name in ("script", "style", "noscript"):
                continue
            stripped = child.strip()
            if stripped:
                text_parts.append(stripped)
        return " ".join(text_parts)
