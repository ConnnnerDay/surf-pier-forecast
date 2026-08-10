from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.beta_request import BetaRequest
from app.schemas.beta import BetaRequestCreate

router = APIRouter(prefix="/beta-requests", tags=["beta"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_beta_request(
    payload: BetaRequestCreate, db: Annotated[Session, Depends(get_db)]
) -> dict[str, str]:
    db.add(BetaRequest(email=payload.email, note=payload.note))
    db.commit()
    return {"status": "received"}
