from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .models import RunSpec, RunState
from .store import RunStore


@dataclass(frozen=True)
class BudgetConfig:
    monthly_budget_usd: float = 0.0
    per_run_budget_usd: float = 0.0
    estimated_cost_per_run_usd: float = 0.0
    estimated_cost_per_prompt_usd: float = 0.0
    warning_ratio: float = 0.8

    @classmethod
    def from_env(cls) -> "BudgetConfig":
        return cls(
            monthly_budget_usd=env_float("RUN_MANAGER_COST_MONTHLY_BUDGET_USD", 0.0),
            per_run_budget_usd=env_float("RUN_MANAGER_COST_PER_RUN_BUDGET_USD", 0.0),
            estimated_cost_per_run_usd=env_float(
                "RUN_MANAGER_COST_ESTIMATE_PER_RUN_USD",
                0.0,
            ),
            estimated_cost_per_prompt_usd=env_float(
                "RUN_MANAGER_COST_ESTIMATE_PER_PROMPT_USD",
                0.0,
            ),
            warning_ratio=clamp(
                env_float("RUN_MANAGER_COST_WARNING_RATIO", 0.8),
                0.0,
                1.0,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CostManager:
    def __init__(self, store: RunStore, config: BudgetConfig | None = None):
        self.store = store
        self.config = config or BudgetConfig.from_env()

    def quote(self, spec: RunSpec) -> dict[str, Any]:
        estimate = self.estimate_spec(spec)
        projected_monthly = self.monthly_estimated_spend() + estimate
        allowed = True
        reasons: list[str] = []
        if self.config.per_run_budget_usd > 0 and estimate > self.config.per_run_budget_usd:
            allowed = False
            reasons.append("per-run budget exceeded")
        if (
            self.config.monthly_budget_usd > 0
            and projected_monthly > self.config.monthly_budget_usd
        ):
            allowed = False
            reasons.append("monthly budget exceeded")
        return {
            "estimated_cost_usd": round(estimate, 6),
            "projected_monthly_cost_usd": round(projected_monthly, 6),
            "allowed": allowed,
            "reasons": reasons,
            "config": self.config.to_dict(),
        }

    def require_allowed(self, spec: RunSpec) -> dict[str, Any]:
        quote = self.quote(spec)
        if not quote["allowed"]:
            raise ValueError("; ".join(quote["reasons"]) or "cost budget exceeded")
        return quote

    def status(self) -> dict[str, Any]:
        current_month = month_prefix()
        runs = self.store.list_runs()
        entries = [self.run_cost_entry(run) for run in runs]
        month_entries = [
            entry for entry in entries if str(entry["created_at"]).startswith(current_month)
        ]
        total = round(sum(float(entry["estimated_cost_usd"]) for entry in month_entries), 6)
        budget = self.config.monthly_budget_usd
        warning_threshold = round(budget * self.config.warning_ratio, 6) if budget > 0 else None
        status = "unconfigured"
        if budget > 0:
            if total > budget:
                status = "over_budget"
            elif warning_threshold and total >= warning_threshold:
                status = "warn"
            else:
                status = "ok"
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "status": status,
            "config": self.config.to_dict(),
            "month": current_month,
            "monthly_estimated_cost_usd": total,
            "monthly_budget_usd": budget,
            "warning_threshold_usd": warning_threshold,
            "runs": entries,
        }

    def summary(self) -> dict[str, Any]:
        status = self.status()
        runs = status.pop("runs", [])
        status["run_count"] = len(runs)
        return status

    def monthly_estimated_spend(self) -> float:
        current_month = month_prefix()
        total = 0.0
        for run in self.store.list_runs():
            if run.created_at.startswith(current_month):
                total += float(self.run_cost_entry(run)["estimated_cost_usd"])
        return total

    def estimate_spec(self, spec: RunSpec) -> float:
        explicit = numeric_metadata(spec.metadata.get("estimated_cost_usd"))
        if explicit is not None:
            return explicit
        return self.config.estimated_cost_per_run_usd

    def run_cost_entry(self, run: RunState) -> dict[str, Any]:
        quote = run.spec.metadata.get("cost_quote")
        if isinstance(quote, dict):
            estimated = numeric_metadata(quote.get("estimated_cost_usd"))
        else:
            estimated = None
        if estimated is None:
            estimated = self.estimate_spec(run.spec)
        prompt_cost = max(0, run.prompt_count) * self.config.estimated_cost_per_prompt_usd
        return {
            "run_id": run.run_id,
            "status": run.status,
            "adapter": run.spec.adapter,
            "created_at": run.created_at,
            "estimated_cost_usd": round(estimated + prompt_cost, 6),
            "prompt_count": run.prompt_count,
        }


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(0.0, parsed)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def numeric_metadata(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if isinstance(value, str):
        try:
            return max(0.0, float(value))
        except ValueError:
            return None
    return None


def month_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")
