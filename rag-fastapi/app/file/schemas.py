from pydantic import BaseModel, Field, HttpUrl


class DeleteFileReq(BaseModel):
    doc_id: str = Field(alias="docId")


class UploadWebReq(BaseModel):
    url: str = Field(alias="url")