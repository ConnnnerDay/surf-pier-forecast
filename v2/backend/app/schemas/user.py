from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str
    totp_enabled: bool

    model_config = {"from_attributes": True}
