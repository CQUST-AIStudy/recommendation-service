from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Database ──
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "ptadatabase"

    # ── Service ──
    service_host: str = "0.0.0.0"
    service_port: int = 8003

    # ── Recommendation weights ──
    weight_need_match: float = 0.40
    weight_difficulty_fit: float = 0.20
    weight_success_prob: float = 0.15
    weight_novelty: float = 0.10
    weight_quality: float = 0.10
    weight_repeat_penalty: float = 0.15
    weight_wrong_question: float = 0.10

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
    recall_weak_ratio: float = 0.60
    recall_difficulty_ratio: float = 0.25
    recall_exploration_ratio: float = 0.15

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
    diversity_max_tag_ratio: float = 0.40
    diversity_min_candidate_multiplier: int = 3

    @property
    def cors_origins(self) -> list[str]:
        return ["*"]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
