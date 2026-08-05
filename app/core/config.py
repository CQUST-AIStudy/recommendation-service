from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Database ──
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "ptadatabase"
    db_connect_timeout_seconds: int = Field(5, ge=1, le=60)
    db_read_timeout_seconds: int = Field(30, ge=1, le=300)
    db_write_timeout_seconds: int = Field(30, ge=1, le=300)

    # ── Service ──
    service_host: str = "0.0.0.0"
    service_port: int = 8003
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8080,http://127.0.0.1:8080"
    )
    cors_allow_credentials: bool = True
    recommendation_pending_timeout_seconds: int = Field(60, ge=10, le=3600)

    # ── Recommendation weights ──
    weight_need_match: float = Field(0.40, ge=0, le=1)
    weight_difficulty_fit: float = Field(0.20, ge=0, le=1)
    weight_success_prob: float = Field(0.15, ge=0, le=1)
    weight_novelty: float = Field(0.10, ge=0, le=1)
    weight_quality: float = Field(0.10, ge=0, le=1)
    weight_semantic: float = Field(0.10, ge=0, le=1)
    weight_repeat_penalty: float = Field(0.15, ge=0, le=1)
    weight_wrong_question: float = Field(0.10, ge=0, le=1)
    weight_pta_error: float = Field(0.12, ge=0, le=1)

    # ── BKT parameters (Corbett & Anderson 1995) ──
    bkt_p_transfer: float = 0.14
    bkt_p_guess: float = 0.20
    bkt_p_slip: float = 0.10
    bkt_p_initial: float = 0.30

    # ── Ebbinghaus parameters ──
    ebbinghaus_s_base: float = 5.0
    ebbinghaus_s_max: float = 60.0
    ebbinghaus_s_min: float = 1.0
    ebbinghaus_alpha: float = 0.12
    ebbinghaus_beta: float = 0.08
    ebbinghaus_lambda: float = 0.03
    ebbinghaus_delta: float = 0.05
    ebbinghaus_practice_reduction: float = 12.0

    # ── Wilson parameters ──
    wilson_z: float = 1.95

    # ── Recall ratios ──
    recall_weak_ratio: float = Field(0.60, ge=0, le=1)
    recall_difficulty_ratio: float = Field(0.25, ge=0, le=1)
    recall_exploration_ratio: float = Field(0.15, ge=0, le=1)
    recall_wrong_question_ratio: float = Field(0.20, ge=0, le=1)
    recall_semantic_ratio: float = Field(0.15, ge=0, le=1)

    # ── Offline embedding dataset used by online semantic recall ──
    embedding_enabled: bool = True
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    embedding_model_revision: str = "main"
    embedding_preprocessing_version: str = "v1"
    embedding_expected_dim: int = Field(512, ge=1)
    embedding_min_score: float = Field(0.30, ge=-1, le=1)
    embedding_weak_threshold: float = Field(60.0, ge=0, le=100)

    # ── Feedback score deltas ──
    feedback_delta_exposure: float = -0.01
    feedback_delta_click: float = 0.04
    feedback_delta_start: float = 0.06
    feedback_delta_complete: float = 0.10
    feedback_delta_skip: float = -0.12
    feedback_delta_dislike: float = -0.35
    feedback_recency_decay: float = 0.008
    feedback_recency_floor: float = 0.30
    feedback_max_history: int = 300

    # ── Diversity ──
    diversity_max_tag_ratio: float = Field(0.40, gt=0, le=1)
    diversity_min_candidate_multiplier: int = Field(3, ge=1)

    @model_validator(mode="after")
    def validate_recommendation_config(self) -> "Settings":
        if not self.cors_origin_list:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        if self.cors_allow_credentials and "*" in self.cors_origin_list:
            raise ValueError("CORS_ORIGINS cannot contain '*' when credentials are enabled")
        if self.recall_weak_ratio + self.recall_difficulty_ratio + self.recall_exploration_ratio <= 0:
            raise ValueError("at least one base recall ratio must be positive")
        positive_weights = (
            self.weight_need_match
            + self.weight_difficulty_fit
            + self.weight_success_prob
            + self.weight_novelty
            + self.weight_quality
            + self.weight_semantic
            + self.weight_wrong_question
            + self.weight_pta_error
        )
        if positive_weights <= 0:
            raise ValueError("at least one positive ranking weight must be configured")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
