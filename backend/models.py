from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sites: Mapped[List["Site"]] = relationship(
        "Site", back_populates="customer", cascade="all, delete-orphan"
    )


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    customer: Mapped["Customer"] = relationship("Customer", back_populates="sites")
    devices: Mapped[List["Device"]] = relationship(
        "Device", back_populates="site", cascade="all, delete-orphan"
    )


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_type: Mapped[str] = mapped_column(String(128), nullable=False)
    device_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    site: Mapped["Site"] = relationship("Site", back_populates="devices")


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    condition: Mapped[str] = mapped_column(String(8), nullable=False)  # "gt" | "lt"
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    notify_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    logs: Mapped[List["AlertLog"]] = relationship(
        "AlertLog", back_populates="rule", cascade="all, delete-orphan"
    )


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    rule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized for fast device-centric queries without joins
    device_uid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    rule: Mapped["AlertRule"] = relationship("AlertRule", back_populates="logs")


# ---------------------------------------------------------------------------
# Pydantic v2 schemas
# ---------------------------------------------------------------------------

from pydantic import BaseModel, ConfigDict


class DeviceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    site_id: int
    device_type: str
    device_uid: str
    name: str
    is_online: bool
    last_seen: Optional[datetime]
    created_at: datetime


class SiteSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    name: str
    location: str
    created_at: datetime
    devices: List[DeviceSchema] = []


class SensorReading(BaseModel):
    device_uid: str
    device_type: str
    value: float
    ts: int  # Unix timestamp
