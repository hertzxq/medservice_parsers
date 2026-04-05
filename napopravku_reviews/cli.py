import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from .parser import NapopravkuParser
from .models import ParseResult

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="НаПоправку reviews parser")

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", type=str, help="Direct URL to clinic/reviews page")
    source.add_argument("--slug", type=str, help="Clinic slug (e.g., schastlivyy-vzglyad-...)")
    source.add_argument("--input-file", type=str, help="Path to text file with URLs or slugs (one per line)")

    parser.add_argument("--city", type=str, default="spb", help="City slug (default: spb)")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output JSON file path")
    parser.add_argument("--no-headless", action="store_true", help="Show browser window")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    parser.add_argument("--timeout", type=int, help="Page load timeout (seconds)")
    parser.add_argument("--initial-delay", type=int, help="Initial delay before parsing (ms)")
    parser.add_argument("--max-clicks", type=int, help="Maximum 'Show more' clicks")

    parser.add_argument("--save-db", action="store_true", help="Save results to PostgreSQL")
    parser.add_argument("--db-dsn", type=str, default=None, help="PostgreSQL DSN (default: from database.py)")

    return parser.parse_args()


def get_parser_kwargs(args: argparse.Namespace) -> dict:
    kwargs = {"headless": not args.no_headless}
    if args.timeout is not None:
        kwargs["page_load_timeout_sec"] = args.timeout
    if args.initial_delay is not None:
        kwargs["initial_load_delay_ms"] = args.initial_delay
    if args.max_clicks is not None:
        kwargs["max_load_more_clicks"] = args.max_clicks
    return kwargs


async def run_single(parser: NapopravkuParser, target: str, city: str) -> ParseResult:
    if target.startswith("http"):
        return await parser.parse_by_url(target)
    return await parser.parse_by_slug(city, target)


async def run_batch(parser: NapopravkuParser, input_file: str, city: str) -> list[ParseResult]:
    path = Path(input_file)
    if not path.is_file():
        logger.error("Файл не найден: %s", input_file)
        sys.exit(1)

    targets = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    logger.info("Найдено целей в файле: %d", len(targets))

    results = []
    for idx, target in enumerate(targets, 1):
        logger.info("Очередь [%d/%d] -> %s", idx, len(targets), target)
        try:
            res = await run_single(parser, target, city)
            results.append(res)
        except Exception as exc:
            logger.error("Ошибка при обработке %s: %s", target, exc)

    return results


async def save_to_db(results: list[ParseResult], dsn: str | None) -> None:
    from .database import Database

    db_kwargs = {"dsn": dsn} if dsn else {}
    db = Database(**db_kwargs)

    try:
        await db.connect()
        await db.create_tables()

        for result in results:
            await db.save_parse_result(result)
    finally:
        await db.disconnect()


def save_single_result(result: ParseResult, output_path: str | None) -> str:
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"napopravku_reviews_{timestamp}.json"

    data = result.model_dump(mode="json")
    data["parsed_at"] = result.parsed_at.isoformat()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return output_path


def save_batch_results(results: list[ParseResult], output_path: str | None) -> str:
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"napopravku_batch_{timestamp}.json"

    data = [
        {**res.model_dump(mode="json"), "parsed_at": res.parsed_at.isoformat()}
        for res in results
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return output_path


def main():
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    kwargs = get_parser_kwargs(args)
    parser = NapopravkuParser(**kwargs)

    try:
        if args.input_file:
            results = asyncio.run(run_batch(parser, args.input_file, args.city))
            if args.save_db:
                asyncio.run(save_to_db(results, args.db_dsn))
            output_path = save_batch_results(results, args.output)
            logger.info("Батч завершён. Успешно: %d. Файл: %s", len(results), output_path)
        else:
            target = args.url or args.slug
            result = asyncio.run(run_single(parser, target, args.city))
            if args.save_db:
                asyncio.run(save_to_db([result], args.db_dsn))
            output_path = save_single_result(result, args.output)
            logger.info(
                "Результат: организация=%s, отзывов=%d, файл=%s",
                result.business_info.name,
                result.total_parsed,
                output_path,
            )
    except KeyboardInterrupt:
        logger.warning("Парсинг прерван")
        sys.exit(1)
    except Exception as exc:
        logger.error("Сбой: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
