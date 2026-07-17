from pydantic import BaseModel, Field
from typing import Optional, List, Literal


class UploadFileDto(BaseModel):
    file_name: str = Field(alias="fileName")
    file_size: str = Field(alias="fileSize")
    file_type: str = Field(alias="fileType")
    doc_id: str = Field(alias="docId")


class SendMessageReq(BaseModel):
    content: str
    upload_file_list: Optional[List[UploadFileDto]] = Field(default=None, alias="uploadFileList")
    session_id: str = Field(alias="sessionId")
    is_knowledge_based: Optional[bool] = Field(default=None, alias="isKnowledgeBased")


class SingleChatDataReq(BaseModel):
    session_id: str = Field(alias="sessionId")


class DeleteChatReq(BaseModel):
    session_id: str = Field(alias="sessionId")