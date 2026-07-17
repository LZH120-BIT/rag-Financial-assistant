from beanie import Document
from pydantic import Field, BaseModel
from typing import Dict, List, Literal, Optional
from bson import ObjectId
from datetime import datetime


class UploadFileItem(BaseModel):
    file_name: str = Field(alias="fileName")
    file_size: str = Field(alias="fileSize")
    file_type: str = Field(alias="fileType")
    doc_id: str = Field(alias="docId")


class ReadFileData(BaseModel):
    type: Literal["readDocument", "queryKB"]
    status_info: Literal["inProgress", "completed"] = Field(alias="statusInfo")
    prompt_info: str = Field(alias="promptInfo")
    file_list: List[str] = Field(default=[], alias="fileList")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    display_content: Optional[str] = Field(default=None, alias="displayContent")
    upload_file_list: Optional[List[UploadFileItem]] = Field(default=None, alias="uploadFileList")
    read_file_data: Optional[ReadFileData] = Field(default=None, alias="readFileData")


class ChatData(Document):
    user_id: str = Field(alias="userId")
    create_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"), alias="createDate")
    create_time: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000), alias="createTime")
    chat_list: List[Dict] = Field(default=[], alias="chatList")

    class Settings:
        name = "chatdatas"