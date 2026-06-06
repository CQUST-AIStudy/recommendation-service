from __future__ import annotations

from pydantic import BaseModel, Field


class SpiderImportWebhook(BaseModel):
    """spider-repo 爬取完成后触发的回调请求。"""
    class_id: int | None = Field(None, alias="classId")
    student_ids: list[int] = Field(default_factory=list, alias="studentIds")
    student_nos: list[str] = Field(default_factory=list, alias="studentNos")
    offering_ids: list[int] = Field(default_factory=list, alias="offeringIds")
    import_job_id: int | None = Field(None, alias="importJobId")

    model_config = {"populate_by_name": True}


class RefreshStudentRequest(BaseModel):
    """手动刷新单个学生的 PTA 技能画像。"""
    student_id: int | None = Field(None, alias="studentId")
    student_no: str | None = Field(None, alias="studentNo")

    model_config = {"populate_by_name": True}


class RefreshClassRequest(BaseModel):
    """手动刷新整个教学班的 PTA 技能画像。"""
    class_id: int = Field(..., alias="classId")

    model_config = {"populate_by_name": True}


class IngestionResponse(BaseModel):
    student_id: int | None = Field(None, alias="studentId")
    tags_updated: int = Field(0, alias="tagsUpdated")
    total_attempts_processed: int = Field(0, alias="totalAttemptsProcessed")
    source: str = Field("", alias="source")
    error: str | None = Field(None, alias="error")

    model_config = {"populate_by_name": True}


class ClassIngestionResponse(BaseModel):
    class_id: int = Field(..., alias="classId")
    students_processed: int = Field(0, alias="studentsProcessed")
    total_students: int = Field(0, alias="totalStudents")
    total_tags_updated: int = Field(0, alias="totalTagsUpdated")
    errors: int = Field(0, alias="errors")

    model_config = {"populate_by_name": True}
