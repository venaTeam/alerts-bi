"""The pipeline's configuration: the three backing services it talks to."""

from __future__ import annotations

from dataclasses import dataclass

from alerts_bi.config.elasticsearch import EsConfig, load_es_config
from alerts_bi.config.env import load_dotenv
from alerts_bi.config.llm import LlmConfig, load_llm_config
from alerts_bi.config.sql import SqlConfig, load_sql_config

__all__ = ["AppConfig", "load_config"]


@dataclass(frozen=True, slots=True)
class AppConfig:
    es: EsConfig
    sql: SqlConfig
    llm: LlmConfig


def load_config() -> AppConfig:
    """Build the application configuration from the environment."""
    load_dotenv()
    return AppConfig(es=load_es_config(), sql=load_sql_config(), llm=load_llm_config())
