from pydantic import BaseModel, Field


class RegulationOut(BaseModel):
    species: str
    state: str
    status: str  # classify_legality() status: legal/catch_and_release/restricted/out_of_season/prohibited/unknown
    min_size: str | None = None
    slot: str | None = None
    bag_limit: str | None = None
    season: str | None = None
    gear: str | None = None
    notes: str | None = None
    official_source: str | None = None
    is_stale: bool = False


class LegalCatchRequest(BaseModel):
    species: str
    state: str
    length_in: float = Field(gt=0, le=300)


class LegalCatchResponse(BaseModel):
    verdict: str  # legal / too_small / too_large / cannot_target / unknown
    legal: bool | None
    reason: str
    min_size_in: float | None
    max_size_in: float | None
    regulation: RegulationOut
