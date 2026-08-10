from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.location import SavedLocation
from app.models.user import User
from app.schemas.location import SavedLocationCreate, SavedLocationOut

router = APIRouter(prefix="/locations", tags=["locations"])

MAX_SAVED_LOCATIONS = 5


@router.get("", response_model=list[SavedLocationOut])
def list_locations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[SavedLocation]:
    return db.query(SavedLocation).filter_by(user_id=user.id).all()


@router.post("", response_model=SavedLocationOut, status_code=status.HTTP_201_CREATED)
def create_location(
    payload: SavedLocationCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SavedLocation:
    existing_count = db.query(SavedLocation).filter_by(user_id=user.id).count()
    if existing_count >= MAX_SAVED_LOCATIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can save at most {MAX_SAVED_LOCATIONS} locations",
        )
    location = SavedLocation(user_id=user.id, **payload.model_dump())
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(
    location_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    location = db.query(SavedLocation).filter_by(id=location_id, user_id=user.id).one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    db.delete(location)
    db.commit()
