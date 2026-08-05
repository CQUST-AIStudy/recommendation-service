from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_cors_origins_are_environment_configurable():
    settings = Settings(
        _env_file=None,
        cors_origins="https://app.example.test, https://admin.example.test",
    )

    assert settings.cors_origin_list == [
        "https://app.example.test",
        "https://admin.example.test",
    ]


def test_cors_wildcard_is_rejected_with_credentials():
    with pytest.raises(ValidationError, match="cannot contain '\\*'"):
        Settings(_env_file=None, cors_origins="*", cors_allow_credentials=True)


def test_cors_wildcard_is_allowed_without_credentials():
    settings = Settings(
        _env_file=None,
        cors_origins="*",
        cors_allow_credentials=False,
    )

    assert settings.cors_origin_list == ["*"]
