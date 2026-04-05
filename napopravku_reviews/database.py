import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

DEFAULT_DSN = "postgresql://parsers:parsers_secret@localhost:5432/parsers_db"

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS organizations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(500) NOT NULL DEFAULT '',
    address VARCHAR(500) NOT NULL DEFAULT '',
    overall_rating REAL,
    total_reviews_on_page INTEGER,
    source_url TEXT NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'napopravku',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_url, source)
);

CREATE TABLE IF NOT EXISTS reviews (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    author VARCHAR(300) NOT NULL DEFAULT '',
    rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    date VARCHAR(100) NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    doctor VARCHAR(300),
    response TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, author, date)
);

CREATE INDEX IF NOT EXISTS idx_reviews_org_id ON reviews(organization_id);
CREATE INDEX IF NOT EXISTS idx_reviews_rating ON reviews(rating);
"""


class Database:

    def __init__(self, dsn: str = DEFAULT_DSN):
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        logger.info("Подключение к БД: %s", self.dsn.split("@")[-1])

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("Отключение от БД")

    async def create_tables(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TABLES_SQL)
        logger.info("Таблицы созданы/проверены")

    async def upsert_organization(
        self,
        name: str,
        address: str,
        overall_rating: float | None,
        total_reviews_on_page: int | None,
        source_url: str,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO organizations (name, address, overall_rating, total_reviews_on_page, source_url, source)
                VALUES ($1, $2, $3, $4, $5, 'napopravku')
                ON CONFLICT (source_url, source) DO UPDATE SET
                    name = EXCLUDED.name,
                    address = EXCLUDED.address,
                    overall_rating = EXCLUDED.overall_rating,
                    total_reviews_on_page = EXCLUDED.total_reviews_on_page,
                    updated_at = NOW()
                RETURNING id
                """,
                name, address, overall_rating, total_reviews_on_page, source_url,
            )
        org_id = row["id"]
        logger.debug("Организация upsert: id=%d, name=%s", org_id, name)
        return org_id

    async def insert_reviews(self, organization_id: int, reviews: list[dict[str, Any]]) -> int:
        if not reviews:
            return 0

        inserted = 0
        async with self._pool.acquire() as conn:
            for review in reviews:
                try:
                    # Рейтинг: округляем float до int для совместимости со схемой
                    raw_rating = review.get("rating", 1)
                    db_rating = max(1, min(5, round(float(raw_rating))))

                    # Текст: объединяем основной текст + pros + cons
                    text_parts = [review.get("text", "")]
                    if review.get("pros"):
                        text_parts.append(f"Понравилось: {review['pros']}")
                    if review.get("cons"):
                        text_parts.append(f"Не понравилось: {review['cons']}")
                    full_text = "\n".join(p for p in text_parts if p)

                    await conn.execute(
                        """
                        INSERT INTO reviews (organization_id, author, rating, date, text, doctor, response)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (organization_id, author, date) DO NOTHING
                        """,
                        organization_id,
                        review.get("author", ""),
                        db_rating,
                        review.get("date", ""),
                        full_text,
                        review.get("doctor"),
                        review.get("response"),
                    )
                    inserted += 1
                except Exception as exc:
                    logger.warning("Пропуск отзыва: %s", exc)

        logger.info("Записано %d/%d отзывов для org_id=%d", inserted, len(reviews), organization_id)
        return inserted

    async def save_parse_result(self, result: Any) -> int:
        bi = result.business_info
        org_id = await self.upsert_organization(
            name=bi.name,
            address=bi.address,
            overall_rating=bi.overall_rating,
            total_reviews_on_page=bi.total_reviews_on_page,
            source_url=result.source_url,
        )

        reviews_data = [r.model_dump() for r in result.reviews]
        await self.insert_reviews(org_id, reviews_data)
        return org_id
