from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Enum, BigInteger,
    UniqueConstraint, Index, Boolean, Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class ProductCategory(str, PyEnum):
    POKEMON = "pokemon"
    MAGIC = "magic"
    YUGIOH = "yugioh"
    VIDEOGAME = "videogame"
    OTHER = "other"


class SignalType(str, PyEnum):
    STRONG_BUY = "STRONG BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG SELL"


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(255), nullable=False)
    source = Column(String(50), nullable=False)  # pricecharting, cardmarket, ebay
    name = Column(String(500), nullable=False)
    category = Column(Enum(ProductCategory), nullable=False, default=ProductCategory.OTHER)
    set_name = Column(String(255), nullable=True)
    console_or_platform = Column(String(100), nullable=True)
    image_url = Column(String(1000), nullable=True)
    product_url = Column(String(1000), nullable=True)
    current_price = Column(Float, nullable=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    prices = relationship("PriceHistory", back_populates="product", cascade="all, delete-orphan")
    watchlist_entries = relationship("WatchlistEntry", back_populates="product", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="product", cascade="all, delete-orphan")
    portfolio_entries = relationship("PortfolioEntry", back_populates="product", cascade="all, delete-orphan")
    price_alerts = relationship("PriceAlert", back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("external_id", "source", name="uq_product_source"),
        Index("ix_product_name", "name"),
        Index("ix_product_category", "category"),
    )

    def __repr__(self):
        return f"<Product(id={self.id}, name='{self.name}', source='{self.source}')>"


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    date = Column(DateTime, nullable=False)
    price = Column(Float, nullable=False)
    volume = Column(Integer, nullable=True)
    source = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="prices")

    __table_args__ = (
        UniqueConstraint("product_id", "date", "source", name="uq_price_product_date_source"),
        Index("ix_price_product_date", "product_id", "date"),
    )


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="watchlist_entries")

    __table_args__ = (
        UniqueConstraint("telegram_user_id", "product_id", name="uq_watchlist_user_product"),
        Index("ix_watchlist_user", "telegram_user_id"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    signal_type = Column(Enum(SignalType), nullable=False)  # trigger when this signal fires
    is_active = Column(Boolean, default=True)
    last_triggered = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="alerts")

    __table_args__ = (
        Index("ix_alert_user", "telegram_user_id"),
        Index("ix_alert_active", "is_active"),
    )


class PriceAlert(Base):
    """Alert triggered when price crosses a threshold."""
    __tablename__ = "price_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    direction = Column(String(10), nullable=False)  # "above" or "below"
    target_price = Column(Float, nullable=False)
    is_active = Column(Boolean, default=True)
    last_triggered = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product", back_populates="price_alerts")

    __table_args__ = (
        Index("ix_price_alert_user", "telegram_user_id"),
        Index("ix_price_alert_active", "is_active"),
    )


class VintedWatch(Base):
    """Monitor Vinted for listings below a price threshold."""
    __tablename__ = "vinted_watches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, nullable=False)
    search_query = Column(String(255), nullable=False)
    max_price_eur = Column(Float, nullable=False)
    min_price_eur = Column(Float, default=0)  # Anti-fake: skip suspiciously cheap
    is_active = Column(Boolean, default=True)
    countries = Column(String(100), default="it")  # comma-separated: it,fr,de,es
    last_checked = Column(DateTime, nullable=True)
    seen_urls = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_vinted_watch_user", "telegram_user_id"),
        Index("ix_vinted_watch_active", "is_active"),
    )


class PortfolioEntry(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id = Column(BigInteger, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    buy_price = Column(Float, nullable=False)
    quantity = Column(Integer, default=1)
    buy_date = Column(DateTime, default=datetime.utcnow)
    sold = Column(Boolean, default=False)
    sell_price = Column(Float, nullable=True)
    sell_date = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    product = relationship("Product", back_populates="portfolio_entries")

    __table_args__ = (
        Index("ix_portfolio_user", "telegram_user_id"),
    )
