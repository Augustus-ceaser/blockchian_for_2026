from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
from typing import Awaitable, Callable


class BuiltInExecutionError(ValueError):
    pass


@dataclass(frozen=True)
class ResourceLimitPolicy:
    max_cpu_cores: int = 2
    max_memory_mb: int = 1024
    max_timeout_seconds: int = 600

    def validate(self, *, cpu_cores: int, memory_mb: int, timeout_seconds: int) -> None:
        if not (0 < cpu_cores <= self.max_cpu_cores):
            raise BuiltInExecutionError("CPU request exceeds local policy")
        if not (0 < memory_mb <= self.max_memory_mb):
            raise BuiltInExecutionError("memory request exceeds local policy")
        if not (0 < timeout_seconds <= self.max_timeout_seconds):
            raise BuiltInExecutionError("timeout request exceeds local policy")


async def synthetic_statistics(values: tuple[float, ...]) -> dict[str, float | int]:
    if not values or len(values) > 10_000 or any(not math.isfinite(value) for value in values):
        raise BuiltInExecutionError("synthetic values are invalid")
    count = len(values)
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    return {"count": count, "mean": mean, "standard_deviation": math.sqrt(variance)}


class BuiltInFunctionRunner:
    """In-process allowlist runner; it never accepts paths, shell, or user code."""

    def __init__(self, resource_policy: ResourceLimitPolicy | None = None) -> None:
        self._policy = resource_policy or ResourceLimitPolicy()
        self._entrypoints: dict[
            str, Callable[[tuple[float, ...]], Awaitable[dict[str, float | int]]]
        ] = {"builtin.synthetic_statistics.v1": synthetic_statistics}

    async def run(
        self,
        *,
        entrypoint_id: str,
        values: tuple[float, ...],
        cpu_cores: int,
        memory_mb: int,
        timeout_seconds: int,
    ) -> dict[str, float | int]:
        self._policy.validate(
            cpu_cores=cpu_cores, memory_mb=memory_mb, timeout_seconds=timeout_seconds
        )
        function = self._entrypoints.get(entrypoint_id)
        if function is None:
            raise BuiltInExecutionError("entrypoint is not allowlisted")
        return await asyncio.wait_for(function(values), timeout=timeout_seconds)
