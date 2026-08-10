from pydantic import BaseModel, EmailStr


class BetaRequestCreate(BaseModel):
    email: EmailStr
    note: str | None = None
