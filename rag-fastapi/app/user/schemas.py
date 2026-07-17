from pydantic import BaseModel, Field


class RegisterReq(BaseModel):
    phone_number: str = Field(alias="phoneNumber")
    password: str
    confirm_password: str = Field(alias="confirmPassword")


class LoginReq(BaseModel):
    phone_number: str = Field(alias="phoneNumber")
    password: str


class LoginResult(BaseModel):
    token: str
    phone_number: str = Field(alias="phoneNumber")
    avatar: str