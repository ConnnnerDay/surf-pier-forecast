from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str

    model_config = {"from_attributes": True}
