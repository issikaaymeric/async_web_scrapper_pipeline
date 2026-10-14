import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import ForeignKey, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, func, text

load_dotenv()

# Anchor the default database to the project (<root>/data/database.db), not the current
# working directory. dashboard.py reads the same file, so both sides always agree
# no matter where the scraper is launched from.
DATA_DIR = Path(__file__).resolve().parents[1]  # <root>/data
DEFAULT_DB_PATH = DATA_DIR / "database.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DEFAULT_DB_PATH.as_posix()}")

if DATABASE_URL.startswith("sqlite"):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQL logging is opt-in (SQL_ECHO=1). echo=True prints every statement and duplicates
# lines once logging.basicConfig is configured.
engine = create_async_engine(DATABASE_URL, echo=os.getenv("SQL_ECHO", "0") == "1")


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(index=True)
    price: Mapped[float] = mapped_column()
    currency: Mapped[str] = mapped_column(default="USD", server_default="USD")
    rating: Mapped[float | None] = mapped_column(nullable=True)
    image_url: Mapped[str | None] = mapped_column(nullable=True)
    url: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    price_history: Mapped[list["ProductPriceHistory"]] = relationship(back_populates="product")


class ProductPriceHistory(Base):
    __tablename__ = "product_price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    price: Mapped[float] = mapped_column()
    currency: Mapped[str] = mapped_column(default="USD", server_default="USD")
    captured_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    product: Mapped[Product] = relationship(back_populates="price_history")


AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


async def init_db() -> None:
    """Create the tables if they do not exist yet."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
               
async def _ensure_currency_columns(conn) -> None:
    for table in ("products", "product_price_history"):
        info = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        if info and "currency" not in {row[1] for row in info}:
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'USD'")
            )


async def init_db() -> None:
    """Create the tables if they do not exist yet, and add missing columns."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if DATABASE_URL.startswith("sqlite"):
            await _ensure_currency_columns(conn)