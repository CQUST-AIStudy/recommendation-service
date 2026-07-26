from __future__ import annotations

from pydantic import BaseModel, Field


# ──────────────────────────────────────────
# Recommendation generation
# ──────────────────────────────────────────

class GenerateRequest(BaseModel):
    student_id: int = Field(..., alias="studentId")
    limit: int = Field(20, alias="limit", ge=1, le=50)
    scene: str = Field("default", alias="scene")


class GenerateResponse(BaseModel):
    request_id: str = Field(..., alias="requestId")
    status: str = Field(..., alias="status")

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────
# Recommendation result
# ──────────────────────────────────────────

class RecommendProblemInfo(BaseModel):
    problem_id: int = Field(..., alias="problemId")
    title: str = Field("", alias="title")
    difficulty: str = Field("", alias="difficulty")
    source_url: str | None = Field(None, alias="sourceUrl")
    estimated_minutes: int = Field(30, alias="estimatedMinutes")
    tags: list[str] = Field(default_factory=list, alias="tags")

    model_config = {"populate_by_name": True}


class RecommendItemResponse(BaseModel):
    rank_no: int = Field(..., alias="rankNo")
    problem_id: int = Field(..., alias="problemId")
    score_total: float = Field(..., alias="scoreTotal")
    score_need_match: float = Field(..., alias="scoreNeedMatch")
    score_difficulty_fit: float = Field(..., alias="scoreDifficultyFit")
    score_success_prob: float = Field(..., alias="scoreSuccessProb")
    score_novelty: float = Field(..., alias="scoreNovelty")
    score_quality: float = Field(..., alias="scoreQuality")
    score_semantic: float = Field(0.0, alias="scoreSemantic")
    reason_text: str = Field(..., alias="reasonText")
    matched_tag: str | None = Field(None, alias="matchedTag")
    recall_source: str | None = Field(None, alias="recallSource")
    problem: RecommendProblemInfo | None = Field(None, alias="problem")
    forgetting_score: float | None = Field(None, alias="forgettingScore")
    last_practice_at: str | None = Field(None, alias="lastPracticeAt")

    model_config = {"populate_by_name": True}


class ResultResponse(BaseModel):
    request_id: str = Field(..., alias="requestId")
    status: str = Field(..., alias="status")
    student_id: int | None = Field(None, alias="studentId")
    scene: str | None = Field(None, alias="scene")
    request_limit: int | None = Field(None, alias="requestLimit")
    created_at: str | None = Field(None, alias="createdAt")
    finished_at: str | None = Field(None, alias="finishedAt")
    error_message: str | None = Field(None, alias="errorMessage")
    items: list[RecommendItemResponse] = Field(default_factory=list, alias="items")

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────
# Feedback
# ──────────────────────────────────────────

class ExposureRequest(BaseModel):
    request_id: str = Field(..., alias="requestId")
    problem_id: int = Field(..., alias="problemId")
    session_id: str | None = Field(None, alias="sessionId")


class FeedbackRequest(BaseModel):
    request_id: str = Field(..., alias="requestId")
    problem_id: int = Field(..., alias="problemId")
    action: str = Field(..., alias="action")  # click/start/complete/skip/dislike
    session_id: str | None = Field(None, alias="sessionId")


class FeedbackResponse(BaseModel):
    success: bool = Field(..., alias="success")

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────
# Sync recommendation
# ──────────────────────────────────────────

class SyncRequest(BaseModel):
    student_id: int = Field(..., alias="studentId")
    limit: int = Field(20, alias="limit", ge=1, le=50)
