import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


def _uuid() -> str:
    return str(uuid.uuid4())


class SavedLocation(Base):
    """A user's saved fishing spot. MVP caps this at ~5 per user (enforced in
    the API layer, not the schema) per docs/V2_PLAN.md."""

    __tablename__ = "saved_locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    label: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)

    # Curated-location reference id when this came from the curated list
    # (see v1's locations.py), null for a dynamic any-point selection.
    curated_location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    user: Mapped["User"] = relationship(back_populates="locations")
