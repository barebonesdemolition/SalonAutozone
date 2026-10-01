from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    is_vendor = Column(Boolean, default=False, nullable=False)
    roles = Column(String, nullable=True)  # Comma-separated: "buyer,seller,vendor"
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    vehicles = relationship("VehicleListing", back_populates="seller", cascade="all, delete-orphan")
    businesses = relationship("Business", back_populates="owner", cascade="all, delete-orphan")
