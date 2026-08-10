from datetime import datetime
from typing import Any

from pydantic import BaseModel


class PasskeyRegisterVerifyRequest(BaseModel):
    credential: dict[str, Any]
    nickname: str | None = None


class PasskeyLoginVerifyRequest(BaseModel):
    credential: dict[str, Any]
    device_label: str | None = None


class PasskeyOut(BaseModel):
    id: str
    device_label: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
