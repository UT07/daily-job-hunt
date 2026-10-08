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
        """Graph state must be JSON-serialisable for the checkpointer.

        `blocking` is separate because this flattening used to DISCARD
        severity: every violation became one "rule: detail" string, so past
        this boundary nothing could tell a block from a warn. `passed` is
        computed before the flattening and so survived, but the REASONS did
        not -- and the reasons are what a repair prompt, a log line and a
        stored verdict all need.

        Measured 2026-10-07: over a 212-résumé batch, 87 runs (41%) logged
        "Repair budget exhausted — finalizing best-effort" and shipped with a
        block-severity violation still present. Which violation, in every one
        of those 87 cases, was not recorded anywhere. CLAUDE.md #13 -- a
        guard's severity is part of its implementation -- applied to the
        boundary that was throwing it away.

        `violations` keeps its exact previous shape and contents; `blocking`
        is a subset of it, so existing readers are unaffected.
        """
        return {
            "passed": self.passed,
            "violations": [f"{v.rule}: {v.detail}" for v in self.violations],
            "blocking": [f"{v.rule}: {v.detail}" for v in self.violations
                         if v.severity == "block"],
        }
