from beanie import Document
from pydantic import Field
from typing import Literal


class FileDocument(Document):
    user_id: str = Field(alias="userId")
    file_name: str = Field(alias="fileName")
    file_path: str = Field(alias="filePath")
    file_type: str = Field(alias="fileType")
    file_size: str = Field(alias="fileSize")
    file_text: str = Field(alias="fileText")
    upload_type: Literal["UD", "UB"] = Field(alias="uploadType")

    class Settings:
        name = "fileanagements"