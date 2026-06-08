from __future__ import annotations

from pydantic import BaseModel, Field


# ──────────────────────────────────────────
# Profile update schemas
# ──────────────────────────────────────────

class SkillUpdateRequest(BaseModel):
    student_id: int = Field(..., alias="studentId")
    tag_name: str = Field(..., alias="tagName")
    is_correct: bool = Field(..., alias="isCorrect")
    attempt_count: int = Field(1, alias="attemptCount", ge=1)


class BatchSkillUpdateRequest(BaseModel):
    student_id: int = Field(..., alias="studentId")
    updates: list[SkillUpdateItem] = Field(..., alias="updates")


class SkillUpdateItem(BaseModel):
    tag_name: str = Field(..., alias="tagName")
    is_correct: bool = Field(..., alias="isCorrect")
    attempt_count: int = Field(1, alias="attemptCount", ge=1)


class SkillStateResponse(BaseModel):
    student_id: int = Field(..., alias="studentId")
    tag_name: str = Field(..., alias="tagName")
    mastery_score: float = Field(..., alias="masteryScore")
    forgetting_score: float = Field(..., alias="forgettingScore")
    confidence_score: float = Field(..., alias="confidenceScore")
    attempt_count: int = Field(..., alias="attemptCount")
    success_count: int = Field(..., alias="successCount")
    last_practice_at: str | None = Field(None, alias="lastPracticeAt")

    model_config = {"populate_by_name": True}


class DecayRequest(BaseModel):
    days_threshold: int = Field(1, alias="daysThreshold", ge=1)


class DecayResponse(BaseModel):
    updated_count: int = Field(..., alias="updatedCount")

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────
# Profile initialization
# ──────────────────────────────────────────

class InitializeRequest(BaseModel):
    student_id: int = Field(..., alias="studentId")
    tag_names: list[str] = Field(..., alias="tagNames")


class InitializeResponse(BaseModel):
    student_id: int = Field(..., alias="studentId")
    initialized_count: int = Field(..., alias="initializedCount")

    model_config = {"populate_by_name": True}
