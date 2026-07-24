from datetime import datetime, date

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="gerente")  # "admin" | "gerente"
    ativo = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)

    brands = relationship("UserBrand", back_populates="user", cascade="all, delete-orphan")


class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id = Column(Integer, primary_key=True)
    meta_account_id = Column(String, unique=True, nullable=False)  # ex: act_123456789
    nome = Column(String, nullable=False)

    brands = relationship("Brand", back_populates="ad_account")


class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True)
    nome = Column(String, unique=True, nullable=False)
    tier = Column(String, default="")
    ad_account_id = Column(Integer, ForeignKey("ad_accounts.id"), nullable=True)
    campaign_filter = Column(String, nullable=True)  # prefixo do nome da campanha, quando a conta é compartilhada
    diario_esperado_meta = Column(Float, default=0)

    ad_account = relationship("AdAccount", back_populates="brands")
    user_links = relationship("UserBrand", back_populates="brand", cascade="all, delete-orphan")


class UserBrand(Base):
    __tablename__ = "user_brands"
    __table_args__ = (UniqueConstraint("user_id", "brand_id", name="uq_user_brand"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)

    user = relationship("User", back_populates="brands")
    brand = relationship("Brand", back_populates="user_links")


class DailyMetric(Base):
    __tablename__ = "daily_metrics"
    __table_args__ = (UniqueConstraint("brand_id", "data", name="uq_brand_date"),)

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    data = Column(Date, nullable=False, default=date.today)
    gasto = Column(Float, default=0)
    leads_formulario = Column(Integer, default=0)
    leads_whatsapp = Column(Integer, default=0)
    fonte = Column(String, default="meta")  # "meta" | "manual"

    brand = relationship("Brand")


class SaldoCache(Base):
    __tablename__ = "saldo_cache"

    id = Column(Integer, primary_key=True)
    ad_account_id = Column(Integer, ForeignKey("ad_accounts.id"), unique=True, nullable=False)
    saldo = Column(Float, default=0)
    moeda = Column(String, default="BRL")
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    ad_account = relationship("AdAccount")


class TopCreativeCache(Base):
    __tablename__ = "top_creative_cache"

    id = Column(Integer, primary_key=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), unique=True, nullable=False)
    ad_id = Column(String, nullable=True)
    nome_anuncio = Column(String, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    metrica_label = Column(String, nullable=True)  # ex: "42 leads" ou "CPL R$ 8"
    atualizado_em = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand")
