"""Guardrail result types.

Severity separates "reject and repair" from "record but ship". Without it
every stylistic nit would trigger a repair round and triple latency.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str
    severity: str = "block"  # "block" | "warn"


@dataclass
class GuardResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(v.severity == "block" for v in self.violations)

    @classmethod
    def ok(cls) -> "GuardResult":
        return cls()

    def to_dict(self) -> dict:
        """Graph state must be JSON-serialisable for the checkpointer."""
        return {
            "passed": self.passed,
            "violations": [f"{v.rule}: {v.detail}" for v in self.violations],
        }
