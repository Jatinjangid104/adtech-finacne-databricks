import os
import re
import yaml
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from functools import lru_cache
from copy import deepcopy

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    pass


class DotDict:

    def __init__(self, data: Dict):
        self._data = data

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_"):
            return super().__getattribute__(key)
        try:
            val = self._data[key]
            return DotDict(val) if isinstance(val, dict) else val
        except KeyError:
            raise AttributeError(f"Config key '{key}' not found")

    def __getitem__(self, key: str) -> Any:
        val = self._data[key]
        return DotDict(val) if isinstance(val, dict) else val

    def get(self, key: str, default: Any = None) -> Any:
        val = self._data.get(key, default)
        return DotDict(val) if isinstance(val, dict) else val

    def to_dict(self) -> Dict:
        return deepcopy(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        return f"DotDict({self._data})"


class ConfigLoader:

    ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

    def __init__(self, config_path: str, overrides: Optional[Dict] = None):
        self._path = config_path
        self._overrides = overrides or {}
        self._raw: Dict = {}
        self._config: Optional[DotDict] = None

    def load(self) -> "ConfigLoader":
        try:
            with open(self._path, "r") as f:
                raw = yaml.safe_load(f.read())
        except FileNotFoundError:
            raise ConfigurationError(f"Config file not found: {self._path}")
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML in {self._path}: {e}")

        raw = self._interpolate_env_vars(raw)
        raw = self._deep_merge(raw, self._overrides)
        self._raw = raw
        self._config = DotDict(raw)
        logger.info(f"Configuration loaded from {self._path}")
        return self

    def _interpolate_env_vars(self, obj: Any) -> Any:
        if isinstance(obj, str):
            def replace(match):
                var_name = match.group(1)
                value = os.environ.get(var_name)
                if value is None:
                    logger.warning(f"Environment variable ${{{var_name}}} not set")
                return value or match.group(0)
            return self.ENV_VAR_PATTERN.sub(replace, obj)
        elif isinstance(obj, dict):
            return {k: self._interpolate_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._interpolate_env_vars(i) for i in obj]
        return obj

    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        result = deepcopy(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @property
    def config(self) -> DotDict:
        if self._config is None:
            raise ConfigurationError("Config not loaded. Call .load() first.")
        return self._config

    def section(self, key: str) -> DotDict:
        return self.config[key]
