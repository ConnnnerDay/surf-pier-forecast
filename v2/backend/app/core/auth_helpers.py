import hashlib
from datetime import date

from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token
from app.models.user import RefreshToken, User
from app.schemas.auth import TokenPair


def age_years(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_tokens(db: Session, user: User, device_label: str | None) -> TokenPair:
    """Issue a fresh access+refresh token pair and record the refresh token
    (hashed) as a session row, shared by password login/signup, token
    refresh, and OAuth login/signup (app/api/routes/auth.py and
    app/api/routes/oauth.py) so there's one place that defines what "a
    session" is."""
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh),
            device_label=device_label,
        )
    )
    db.commit()
    return TokenPair(access_token=access, refresh_token=refresh)
