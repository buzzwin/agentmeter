"""Immutable, versioned records; monetary values are decimal currency units."""
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import math
from typing import Any

Money = Decimal | str | int | float


def money(value: Money) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("cost must be a finite nonnegative number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("cost must be a finite nonnegative number") from None
    if not result.is_finite() or result < 0:
        raise ValueError("cost must be a finite nonnegative number")
    return result


def count(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("token counts must be nonnegative integers")
    return value


def label(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name must be a nonempty string")
    return value


def seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("duration must be finite and nonnegative")
    if not math.isfinite(value) or value < 0:
        raise ValueError("duration must be finite and nonnegative")
    return float(value)


def to_dict(value: Any) -> Any:
    """JSON-safe conversion: decimals are strings to preserve precision."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: to_dict(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value


class Outcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class EscalationJudgment(str, Enum):
    CORRECT_ESCALATION = "correct_escalation"
    UNNECESSARY_ESCALATION = "unnecessary_escalation"
    MISSED_ESCALATION = "missed_escalation"
    CORRECT_AUTONOMY = "correct_autonomy"


class EventKind(str, Enum):
    MODEL = "model_call"
    TOOL = "tool_call"
    RETRY = "retry"
    HUMAN = "human_intervention"


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp_ns: int
    kind: EventKind
    name: str
    cost: Decimal
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float | None = None


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    name: str
    currency: str
    trace_id: str
    span_id: str
    started_at_ns: int
    ended_at_ns: int
    latency_seconds: float
    outcome: Outcome
    events: tuple[Event, ...]
    schema_version: int = 1

    @property
    def __post_init__(self):
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
                raise ValueError("confidence must be a finite number from 0 to 1")
            if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
                raise ValueError("confidence must be a finite number from 0 to 1")

    @property
    def total_cost(self) -> Decimal:
        return sum((event.cost for event in self.events), Decimal(0))

    @property
    def escalated(self) -> bool:
        return any(event.kind == EventKind.HUMAN for event in self.events)

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)
