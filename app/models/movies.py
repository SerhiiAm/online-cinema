from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Link tables: deleting a movie removes its links (CASCADE), deleting a
# genre/star/director that is still linked to a movie fails (RESTRICT).
movie_genres = Table(
    "movie_genres",
    Base.metadata,
    Column(
        "movie_id",
        ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "genre_id",
        ForeignKey("genres.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)

movie_stars = Table(
    "movie_stars",
    Base.metadata,
    Column(
        "movie_id",
        ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "star_id",
        ForeignKey("stars.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)

movie_directors = Table(
    "movie_directors",
    Base.metadata,
    Column(
        "movie_id",
        ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "director_id",
        ForeignKey("directors.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


class Star(Base):
    __tablename__ = "stars"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


class Director(Base):
    __tablename__ = "directors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


# Names are unique ignoring case: "Drama" and "drama" can't both exist,
# otherwise the genre filter would split one genre into two.
for _model in (Genre, Star, Director, Certification):
    Index(
        f"uq_{_model.__tablename__}_name_lower",
        func.lower(_model.name),
        unique=True,
    )


class Movie(Base):
    __tablename__ = "movies"
    __table_args__ = (
        UniqueConstraint("name", "year", "time", name="uq_movies_name_year_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    year: Mapped[int]
    time: Mapped[int]
    imdb: Mapped[float]
    votes: Mapped[int]
    meta_score: Mapped[float | None]
    gross: Mapped[float | None]
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # RESTRICT: a certification used by a movie can't be deleted.
    certification_id: Mapped[int] = mapped_column(
        ForeignKey("certifications.id", ondelete="RESTRICT")
    )

    certification: Mapped[Certification] = relationship()
    genres: Mapped[list[Genre]] = relationship(secondary=movie_genres)
    stars: Mapped[list[Star]] = relationship(secondary=movie_stars)
    directors: Mapped[list[Director]] = relationship(secondary=movie_directors)
