from pydantic import BaseModel, Field

VALID_EXPERIENCE_LEVELS = {"beginner", "intermediate", "advanced"}
VALID_UNITS = {"imperial", "metric"}
VALID_THEMES = {"system", "light", "dark"}


class ProfileOut(BaseModel):
    max_wind_mph: float | None
    max_surf_ft: float | None
    fishing_styles: list[str]
    gear_limitations: list[str]
    accessibility_needs: list[str]
    experience_level: str
    target_species: list[str]
    units: str
    theme: str
    onboarding_completed: bool

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    """All fields optional — PATCH semantics, only provided fields change."""

    max_wind_mph: float | None = Field(default=None, ge=0)
    max_surf_ft: float | None = Field(default=None, ge=0)
    fishing_styles: list[str] | None = None
    gear_limitations: list[str] | None = None
    accessibility_needs: list[str] | None = None
    experience_level: str | None = None
    target_species: list[str] | None = None
    units: str | None = None
    theme: str | None = None
    onboarding_completed: bool | None = None
