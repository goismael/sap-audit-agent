"""
SAP Audit Agent — Configuration Loader
Loads config.yaml and resolves environment variable references.
"""

import os
import yaml
import re
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv

load_dotenv()

_ENV_VAR_PATTERN = re.compile(r'^\$\{(.+)\}$')


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str):
        match = _ENV_VAR_PATTERN.match(value)
        if match:
            env_var = match.group(1)
            resolved = os.getenv(env_var)
            if resolved is None:
                raise ValueError(
                    f"Environment variable '{env_var}' referenced in config "
                    f"but not set. Add it to your .env file."
                )
            return resolved
        return value
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """
    Load and return the configuration dictionary.
    Resolves environment variable references automatically.

    Args:
        config_path: Path to config.yaml relative to project root

    Returns:
        Resolved configuration dictionary

    Raises:
        FileNotFoundError: If config.yaml doesn't exist
        ValueError: If a required environment variable is not set
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found at '{config_path}'. "
            f"Copy config/config.example.yaml to config/config.yaml "
            f"and fill in your values."
        )

    with open(path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    return _resolve_env_vars(raw_config)


# Singleton config instance
_config: Dict[str, Any] = {}


def get_config() -> Dict[str, Any]:
    """Return the loaded config, loading it if not yet loaded."""
    global _config
    if not _config:
        _config = load_config()
    return _config
