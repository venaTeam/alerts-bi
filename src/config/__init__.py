"""Configuration for the service.

Split by what each block configures rather than kept in one file, so a setting is added
next to the thing that needs it:

* :mod:`~src.config.env` - reading the environment and a ``.env`` file
* :mod:`~src.config.elasticsearch`, :mod:`~src.config.sql`,
  :mod:`~src.config.llm` - the three backing services, each validating its own values
* :mod:`~src.config.app` - the pipeline's configuration, composing those three
* :mod:`~src.config.api` - the HTTP surface: where it listens, what it writes

Everything is re-exported here, so ``from src.config import load_config`` is still the
one import a caller needs.
"""

from __future__ import annotations

from src.config.api import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ApiSettings,
    load_api_settings,
)
from src.config.app import AppConfig, load_config
from src.config.elasticsearch import EsConfig, load_es_config
from src.config.env import load_dotenv, read_bool, read_int, read_str
from src.config.llm import MAX_BATCH_SIZE_CEILING, LlmConfig, load_llm_config
from src.config.portal import PortalSettings, load_portal_settings
from src.config.sql import SqlConfig, load_sql_config

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_BATCH_SIZE_CEILING",
    "ApiSettings",
    "AppConfig",
    "EsConfig",
    "LlmConfig",
    "PortalSettings",
    "SqlConfig",
    "load_api_settings",
    "load_config",
    "load_dotenv",
    "load_es_config",
    "load_llm_config",
    "load_portal_settings",
    "load_sql_config",
    "read_bool",
    "read_int",
    "read_str",
]
