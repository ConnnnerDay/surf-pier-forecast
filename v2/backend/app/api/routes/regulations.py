from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.core.catch_calculator import evaluate_catch
from app.models.user import User
from app.schemas.regulations import LegalCatchRequest, LegalCatchResponse, RegulationOut
from regulations import classify_legality, lookup_regulation
from storage.species_loader import SPECIES_DB

router = APIRouter(prefix="/regulations", tags=["regulations"])


def _to_regulation_out(species: str, state: str, reg: dict | None, status: str) -> RegulationOut:
    reg = reg or {}
    return RegulationOut(
        species=species,
        state=state.upper(),
        status=status,
        min_size=reg.get("min_size") or None,
        slot=reg.get("slot") or None,
        bag_limit=reg.get("bag_limit") or None,
        season=reg.get("season") or None,
        gear=reg.get("gear") or None,
        notes=reg.get("notes") or None,
        official_source=reg.get("official_source") or None,
        is_stale=bool(reg.get("is_stale")),
    )


@router.get("/species", response_model=list[str])
def list_species(user: Annotated[User, Depends(get_current_user)]) -> list[str]:
    """Every species name known to the app, for the regulations lookup's
    autocomplete — not filtered by current conditions like the forecast's
    ranked list is, since this is a general reference lookup."""
    return sorted(sp["name"] for sp in SPECIES_DB)


@router.get("/lookup", response_model=RegulationOut)
def lookup(
    user: Annotated[User, Depends(get_current_user)],
    species: Annotated[str, Query(min_length=1)],
    state: Annotated[str, Query(min_length=2, max_length=2)],
) -> RegulationOut:
    reg = lookup_regulation(species, state)
    status = classify_legality(reg, month=datetime.now(UTC).month)
    return _to_regulation_out(species, state, reg, status)


@router.post("/legal-catch", response_model=LegalCatchResponse)
def legal_catch(
    payload: LegalCatchRequest, user: Annotated[User, Depends(get_current_user)]
) -> LegalCatchResponse:
    reg = lookup_regulation(payload.species, payload.state)
    status = classify_legality(reg, month=datetime.now(UTC).month)
    result = evaluate_catch(reg, status, payload.length_in)
    return LegalCatchResponse(
        **result,
        regulation=_to_regulation_out(payload.species, payload.state, reg, status),
    )
