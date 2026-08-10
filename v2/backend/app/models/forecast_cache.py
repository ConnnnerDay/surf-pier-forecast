from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ForecastCache(Base):
    """4-hour TTL cache for generate_forecast() results, keyed by v2's own
    SavedLocation id (already unique per user, unlike v1 which keyed on
    (user_id, location_id) since v1's location_id could be shared/anonymous).
    See docs/V2_PLAN.md phase 2 "Known follow-ups" and CLAUDE.md's v1
    "Data flow" section this mirrors."""

    __tablename__ = "forecast_cache"

    location_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("saved_locations.id"), primary_key=True
    )
    forecast_json: Mapped[dict] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
