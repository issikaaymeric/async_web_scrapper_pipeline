# Import some important libraries
import os 
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import func
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/database.db")

#Load the database

#engine = create_async_engine(DATABASE_URL, echo=True, pool_size=20, max_overflow=10, pool_timeout=30)
engine = create_async_engine(DATABASE_URL, echo=True)

# Define the base class for our models
class Base(DeclarativeBase):
    pass 


# Define the table
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(index=True)
    price: Mapped[float] = mapped_column()
    rating: Mapped[float | None] = mapped_column(nullable=True)
    image_url: Mapped[str | None] = mapped_column(nullable=True)
    url: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    
    
# Setting up the database connection
# DATABASE_URL = "sqlite+aiosqlite:///./data/database.db"
# engine = create_async_engine(DATABASE_URL, echo=True)

# Create a session maker
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


# Function to create the database tables
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)