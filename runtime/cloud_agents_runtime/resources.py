from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from .models import RunSpec


@dataclass(frozen=True)
class ResourceLimitConfig:
    default_cpus: float = 1.0
    max_cpus: float = 1.0
    default_memory_mb: int = 1024
    max_memory_mb: int = 1024
    default_pids: int = 512
    max_pids: int = 512
    default_timeout_seconds: int = 3600
    max_timeout_seconds: int = 3600

    @classmethod
    def from_env(cls) -> "ResourceLimitConfig":
        default_cpus = env_float("RUN_MANAGER_DEFAULT_CPUS", cls.default_cpus)
        default_memory_mb = env_int("RUN_MANAGER_DEFAULT_MEMORY_MB", cls.default_memory_mb)
        default_pids = env_int("RUN_MANAGER_DEFAULT_PIDS", cls.default_pids)
        default_timeout_seconds = env_int(
            "RUN_MANAGER_DEFAULT_TIMEOUT_SECONDS",
            cls.default_timeout_seconds,
        )
        return cls(
            default_cpus=default_cpus,
            max_cpus=env_float("RUN_MANAGER_MAX_CPUS", default_cpus),
            default_memory_mb=default_memory_mb,
            max_memory_mb=env_int("RUN_MANAGER_MAX_MEMORY_MB", default_memory_mb),
            default_pids=default_pids,
            max_pids=env_int("RUN_MANAGER_MAX_PIDS", default_pids),
            default_timeout_seconds=default_timeout_seconds,
            max_timeout_seconds=env_int(
                "RUN_MANAGER_MAX_TIMEOUT_SECONDS",
                default_timeout_seconds,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourcePolicy:
    cpus: float
    memory_mb: int
    pids: int
    timeout_seconds: int
    requested: dict[str, Any]
    sources: dict[str, str]
    enforcement: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def limits_dict(self) -> dict[str, Any]:
        return {
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "pids": self.pids,
            "timeout_seconds": self.timeout_seconds,
        }


class ResourcePolicyResolver:
    def __init__(self, config: ResourceLimitConfig | None = None):
        self.config = config or ResourceLimitConfig.from_env()

    def resolve(self, spec: RunSpec) -> ResourcePolicy:
        sandbox = ensure_dict(spec.sandbox, "sandbox")
        requested = ensure_dict(
            sandbox.get("resources") or sandbox.get("limits") or {},
            "sandbox.resources",
        )

        cpus, cpu_source = resolve_float(
            requested,
            ("cpus", "cpu", "cpu_count"),
            self.config.default_cpus,
            "default",
        )
        memory_mb, memory_source = resolve_memory_mb(
            requested,
            ("memory_mb", "mem_mb", "memory", "memory_limit"),
            self.config.default_memory_mb,
            "default",
        )
        pids, pids_source = resolve_int(
            requested,
            ("pids", "pids_limit"),
            self.config.default_pids,
            "default",
        )
        timeout_seconds, timeout_source = resolve_timeout_seconds(
            requested,
            spec.timeout_seconds,
            self.config.default_timeout_seconds,
        )

        validate_range("cpus", cpus, self.config.max_cpus)
        validate_range("memory_mb", memory_mb, self.config.max_memory_mb)
        validate_range("pids", pids, self.config.max_pids)
        validate_range(
            "timeout_seconds",
            timeout_seconds,
            self.config.max_timeout_seconds,
        )

        policy = ResourcePolicy(
            cpus=cpus,
            memory_mb=memory_mb,
            pids=pids,
            timeout_seconds=timeout_seconds,
            requested=dict(requested),
            sources={
                "cpus": cpu_source,
                "memory_mb": memory_source,
                "pids": pids_source,
                "timeout_seconds": timeout_source,
            },
            enforcement={
                "timeout_seconds": "run_manager_watchdog",
                "cpu_memory_pids": "service_cgroup_or_container_runtime",
            },
        )
        apply_policy(spec, policy)
        return policy


def apply_policy(spec: RunSpec, policy: ResourcePolicy) -> None:
    sandbox = dict(spec.sandbox)
    sandbox["resources"] = policy.limits_dict()
    spec.sandbox = sandbox
    spec.timeout_seconds = policy.timeout_seconds
    metadata = dict(spec.metadata)
    metadata["resource_policy"] = policy.to_dict()
    spec.metadata = metadata


def ensure_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a json object")
    return value


def resolve_float(
    data: dict[str, Any],
    keys: tuple[str, ...],
    default: float,
    default_source: str,
) -> tuple[float, str]:
    key, value = first_present(data, keys)
    if key is None:
        return default, default_source
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a positive number") from None
    return parsed, f"requested.{key}"


def resolve_int(
    data: dict[str, Any],
    keys: tuple[str, ...],
    default: int,
    default_source: str,
) -> tuple[int, str]:
    key, value = first_present(data, keys)
    if key is None:
        return default, default_source
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a positive integer") from None
    return parsed, f"requested.{key}"


def resolve_memory_mb(
    data: dict[str, Any],
    keys: tuple[str, ...],
    default: int,
    default_source: str,
) -> tuple[int, str]:
    key, value = first_present(data, keys)
    if key is None:
        return default, default_source
    return parse_memory_mb(value, key), f"requested.{key}"


def resolve_timeout_seconds(
    data: dict[str, Any],
    top_level_timeout: Any,
    default: int,
) -> tuple[int, str]:
    key, value = first_present(data, ("timeout_seconds", "timeout"))
    if key is not None:
        return parse_positive_int(value, key), f"requested.{key}"
    if top_level_timeout is not None:
        return parse_positive_int(top_level_timeout, "timeout_seconds"), "run_spec.timeout_seconds"
    return default, "default"


def first_present(data: dict[str, Any], keys: tuple[str, ...]) -> tuple[str | None, Any]:
    for key in keys:
        if key in data and data[key] is not None:
            return key, data[key]
    return None, None


def parse_memory_mb(value: Any, name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be memory in MB or a string like 512m/1g")
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?|[kmgt])?\s*", value.lower())
    if not match:
        raise ValueError(f"{name} must be memory in MB or a string like 512m/1g")
    amount = float(match.group(1))
    unit = (match.group(2) or "mb").lower()
    multipliers = {
        "": 1,
        "b": 1 / (1024 * 1024),
        "k": 1 / 1024,
        "ki": 1 / 1024,
        "kb": 1 / 1024,
        "kib": 1 / 1024,
        "m": 1,
        "mi": 1,
        "mb": 1,
        "mib": 1,
        "g": 1024,
        "gi": 1024,
        "gb": 1024,
        "gib": 1024,
        "t": 1024 * 1024,
        "ti": 1024 * 1024,
        "tb": 1024 * 1024,
        "tib": 1024 * 1024,
    }
    return max(1, int(amount * multipliers[unit]))


def parse_positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive integer") from None
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def validate_range(name: str, value: int | float, maximum: int | float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    if value > maximum:
        raise ValueError(f"{name} exceeds worker maximum {maximum}")


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return parse_positive_int(value, name)


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise ValueError(f"{name} must be a positive number") from None
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed
