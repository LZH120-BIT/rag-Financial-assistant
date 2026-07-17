from beanie import Document
from pydantic import Field


class User(Document):
    avatar: str = Field(default="默认头像url")
    phone_number: str = Field(alias="phoneNumber")
    password: str

    class Settings:
        name = "userinfo"