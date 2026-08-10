from pydantic import BaseModel


class SavedLocationCreate(BaseModel):
    label: str
    lat: float
    lng: float
    curated_location_id: str | None = None
    is_default: bool = False


class SavedLocationOut(BaseModel):
    id: str
    label: str
    lat: float
    lng: float
    curated_location_id: str | None
    is_default: bool

    model_config = {"from_attributes": True}
