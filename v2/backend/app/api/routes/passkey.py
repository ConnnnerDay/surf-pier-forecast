from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import webauthn
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, parse_client_data_json
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.api.deps import get_current_user
from app.core.auth_helpers import issue_tokens
from app.core.config import get_settings
from app.db.session import get_db
from app.models.passkey import PasskeyCredential, WebAuthnChallenge
from app.models.user import User
from app.schemas.auth import TokenPair
from app.schemas.passkey import PasskeyLoginVerifyRequest, PasskeyOut, PasskeyRegisterVerifyRequest

router = APIRouter(prefix="/auth/passkey", tags=["passkey"])

_CHALLENGE_TTL = timedelta(minutes=5)


def _store_challenge(db: Session, challenge: bytes, purpose: str, user_id: str | None) -> None:
    db.add(
        WebAuthnChallenge(
            challenge=bytes_to_base64url(challenge),
            purpose=purpose,
            user_id=user_id,
            expires_at=datetime.now(UTC) + _CHALLENGE_TTL,
        )
    )
    db.commit()


def _consume_challenge(db: Session, credential: dict[str, Any], purpose: str) -> WebAuthnChallenge:
    """Look up (and delete) the challenge this response is answering, by
    decoding it straight out of the credential's clientDataJSON — the
    frontend never sends us a separate "which challenge is this" id."""
    try:
        client_data_raw = base64url_to_bytes(credential["response"]["clientDataJSON"])
        challenge_bytes = parse_client_data_json(client_data_raw).challenge
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed passkey response"
        ) from exc

    row = (
        db.query(WebAuthnChallenge)
        .filter_by(challenge=bytes_to_base64url(challenge_bytes), purpose=purpose)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passkey challenge not found or already used",
        )
    db.delete(row)
    db.commit()

    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        # SQLite doesn't reliably round-trip tzinfo through DateTime(timezone=True).
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Passkey challenge expired"
        )
    return row


@router.post("/register/options")
def register_options(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    settings = get_settings()
    existing = db.query(PasskeyCredential).filter_by(user_id=user.id).all()

    options = webauthn.generate_registration_options(
        rp_id=settings.passkey_rp_id,
        rp_name=settings.passkey_rp_name,
        user_id=user.id.encode(),
        user_name=user.email,
        user_display_name=user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id)) for c in existing
        ],
    )
    _store_challenge(db, options.challenge, purpose="register", user_id=user.id)
    return webauthn.helpers.options_to_json_dict(options)


@router.post("/register/verify", response_model=PasskeyOut, status_code=status.HTTP_201_CREATED)
def register_verify(
    payload: PasskeyRegisterVerifyRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PasskeyCredential:
    settings = get_settings()
    challenge_row = _consume_challenge(db, payload.credential, purpose="register")
    if challenge_row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Challenge mismatch")

    try:
        verified = webauthn.verify_registration_response(
            credential=payload.credential,
            expected_challenge=base64url_to_bytes(challenge_row.challenge),
            expected_rp_id=settings.passkey_rp_id,
            expected_origin=settings.passkey_rp_origin,
        )
    except InvalidRegistrationResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Could not verify passkey"
        ) from exc

    passkey = PasskeyCredential(
        user_id=user.id,
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        device_label=payload.nickname,
    )
    db.add(passkey)
    db.commit()
    db.refresh(passkey)
    return passkey


@router.post("/login/options")
def login_options(db: Annotated[Session, Depends(get_db)]) -> dict:
    settings = get_settings()
    # No allow_credentials — a "discoverable"/usernameless request lets the
    # browser show any resident passkey registered for this RP, regardless
    # of which account it belongs to (registration requires resident_key
    # above, so every credential we store is eligible here).
    options = webauthn.generate_authentication_options(
        rp_id=settings.passkey_rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _store_challenge(db, options.challenge, purpose="authenticate", user_id=None)
    return webauthn.helpers.options_to_json_dict(options)


@router.post("/login/verify", response_model=TokenPair)
def login_verify(
    payload: PasskeyLoginVerifyRequest, db: Annotated[Session, Depends(get_db)]
) -> TokenPair:
    settings = get_settings()
    challenge_row = _consume_challenge(db, payload.credential, purpose="authenticate")

    raw_id = payload.credential.get("rawId") or payload.credential.get("id")
    if not raw_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed passkey response"
        )
    # rawId as sent by the browser is already base64url, matching how we
    # stored it at registration time.
    passkey = db.query(PasskeyCredential).filter_by(credential_id=raw_id).one_or_none()
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown passkey")

    try:
        verified = webauthn.verify_authentication_response(
            credential=payload.credential,
            expected_challenge=base64url_to_bytes(challenge_row.challenge),
            expected_rp_id=settings.passkey_rp_id,
            expected_origin=settings.passkey_rp_origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
        )
    except InvalidAuthenticationResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not verify passkey"
        ) from exc

    passkey.sign_count = verified.new_sign_count
    db.commit()

    user = db.get(User, passkey.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")

    return issue_tokens(db, user, device_label=payload.device_label or "Passkey sign-in")


@router.get("/list", response_model=list[PasskeyOut])
def list_passkeys(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[PasskeyCredential]:
    return db.query(PasskeyCredential).filter_by(user_id=user.id).all()


@router.delete("/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_passkey(
    passkey_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    passkey = db.query(PasskeyCredential).filter_by(id=passkey_id, user_id=user.id).one_or_none()
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
    db.delete(passkey)
    db.commit()
