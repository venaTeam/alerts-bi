"""Environment-based configuration.

Configuration never lives in source. A ``.env`` file is read when present, but the real
process environment always wins, so CI and production can supply values without a file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MAX_BATCH_SIZE_CEILING",
    "AppConfig",
    "EsConfig",
    "LlmConfig",
    "SqlConfig",
    "load_config",
    "load_dotenv",
]

# Hard ceiling from design section 5.1. Configuration may lower it, never raise it.
MAX_BATCH_SIZE_CEILING = 200

_TRUTHY = {"1", "true", "yes", "on"}


def load_dotenv(path: str | Path = ".env") -> None:
    """Load ``KEY=value`` pairs from a dotenv file without overriding real environment."""
    file = Path(path)
    if not file.exists():
        return
    for raw_line in file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value


def _str(name: str, fallback: str = "") -> str:
    value = os.environ.get(name)
    return fallback if value is None or value == "" else value


def _int(name: str, fallback: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"env {name} must be an integer, got {value!r}") from exc


def _bool(name: str, fallback: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    return value.lower() in _TRUTHY


@dataclass(frozen=True, slots=True)
class EsConfig:
    url: str
    username: str
    password: str
    ca_cert: str
    request_timeout_ms: int
    page_size: int


@dataclass(frozen=True, slots=True)
class SqlConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    test_database: str
    encrypt: bool
    trust_server_certificate: bool
    request_timeout_ms: int


@dataclass(frozen=True, slots=True)
class LlmConfig:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    timeout_ms: int
    max_batch_size: int
    live_test: bool


@dataclass(frozen=True, slots=True)
class AppConfig:
    es: EsConfig
    sql: SqlConfig
    llm: LlmConfig


def load_config() -> AppConfig:
    """Build the application configuration from the environment."""
    load_dotenv()

    max_batch_size = _int("LLM_MAX_BATCH_SIZE", MAX_BATCH_SIZE_CEILING)
    if max_batch_size < 1:
        raise ValueError("LLM_MAX_BATCH_SIZE must be a positive integer")
    if max_batch_size > MAX_BATCH_SIZE_CEILING:
        # Section 5.1 fixes 200 as a hard count ceiling. Section 7.2 allows lowering it
        # after capacity measurement, never raising it.
        raise ValueError(
            f"LLM_MAX_BATCH_SIZE {max_batch_size} exceeds the design ceiling "
            f"of {MAX_BATCH_SIZE_CEILING}"
        )

    return AppConfig(
        es=EsConfig(
            url=_str("ES_URL", "http://localhost:9200").rstrip("/"),
            username=_str("ES_USERNAME"),
            password=_str("ES_PASSWORD"),
            ca_cert=_str("ES_CA_CERT"),
            request_timeout_ms=_int("ES_REQUEST_TIMEOUT_MS", 60000),
            page_size=_int("ES_PAGE_SIZE", 1000),
        ),
        sql=SqlConfig(
            host=_str("SQL_HOST", "localhost"),
            port=_int("SQL_PORT", 1433),
            user=_str("SQL_USER", "sa"),
            password=_str("SQL_PASSWORD"),
            database=_str("SQL_DATABASE", "alerts_bi_dev"),
            test_database=_str("SQL_TEST_DATABASE", "alerts_bi_test"),
            encrypt=_bool("SQL_ENCRYPT", False),
            trust_server_certificate=_bool("SQL_TRUST_SERVER_CERTIFICATE", True),
            request_timeout_ms=_int("SQL_REQUEST_TIMEOUT_MS", 60000),
        ),
        llm=LlmConfig(
            enabled=_bool("LLM_ENABLED", False),
            base_url=_str("LLM_BASE_URL"),
            api_key=_str("LLM_API_KEY"),
            model=_str("LLM_MODEL"),
            timeout_ms=_int("LLM_TIMEOUT_MS", 120000),
            max_batch_size=max_batch_size,
            live_test=_bool("LLM_LIVE_TEST", False),
        ),
    )
