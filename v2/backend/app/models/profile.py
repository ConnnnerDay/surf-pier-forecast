import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


def _uuid() -> str:
    return str(uuid.uuid4())


class Profile(Base):
    """Personal fishing profile / preferences — the personalization scope
    locked in docs/V2_PLAN.md §3 (beyond v1's wind/surf-only thresholds)."""

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True)

    max_wind_mph: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_surf_ft: Mapped[float | None] = mapped_column(Float, nullable=True)

    fishing_styles: Mapped[list[str]] = mapped_column(JSON, default=list)
    gear_limitations: Mapped[list[str]] = mapped_column(JSON, default=list)
    accessibility_needs: Mapped[list[str]] = mapped_column(JSON, default=list)
    experience_level: Mapped[str] = mapped_column(String(20), default="beginner")
    target_species: Mapped[list[str]] = mapped_column(JSON, default=list)

    units: Mapped[str] = mapped_column(String(10), default="imperial")
    theme: Mapped[str] = mapped_column(String(10), default="system")

    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="profile")
