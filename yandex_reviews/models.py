from datetime import datetime

from pydantic import BaseModel, Field


class Review(BaseModel):
    author: str = Field(default="")
    rating: int = Field(ge=1, le=5)
    date: str = Field(default="")
    text: str = Field(default="")
    response: str | None = Field(default=None)
    # Прямая ссылка на отзыв: ?reviews[publicId]={author.publicId} (см. parser).
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
