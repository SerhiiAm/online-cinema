from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.movies import DbId, NamedItem


class CartItemAdd(BaseModel):
    movie_id: DbId


class CartMovie(BaseModel):
    """What the cart shows about a movie (TASK.md: title, price, genres, year)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    year: int
    price: Decimal
    genres: list[NamedItem]


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    movie: CartMovie
    added_at: datetime


class CartResponse(BaseModel):
    items: list[CartItemResponse]
    total: Decimal
