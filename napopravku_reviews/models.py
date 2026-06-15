from datetime import datetime

from pydantic import BaseModel, Field


class Review(BaseModel):
    author: str = Field(default="")
    rating: float = Field(ge=1.0, le=5.0)
    date: str = Field(default="")
    text: str = Field(default="")
    doctor: str | None = Field(default=None)
    pros: str | None = Field(default=None)
    cons: str | None = Field(default=None)
    response: str | None = Field(default=None)
    # Прямая ссылка на отзыв: {страница отзывов}#{id} (карточка имеет id="response{n}")
    url: str | None = Field(default=None)


class BusinessInfo(BaseModel):
    name: str = Field(default="")
    address: str = Field(default="")
    overall_rating: float | None = Field(default=None)
    total_reviews_on_page: int | None = Field(default=None)


class ParseResult(BaseModel):
    business_info: BusinessInfo
    reviews: list[Review]
    parsed_at: datetime = Field(default_factory=datetime.now)
    total_parsed: int = Field(default=0)
    source_url: str = Field(default="")
