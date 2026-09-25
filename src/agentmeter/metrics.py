"""Aggregate finalized task attempts, without hiding unsuccessful work."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable
from .model import EscalationJudgment, EventKind, Outcome, TaskRecord, to_dict


@dataclass(frozen=True)
class Metrics:
    currency: str | None
    attempted_tasks: int
    successful_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    unknown_tasks: int
    autonomous_completions: int
    escalated_tasks: int
    total_cost: Decimal
    model_cost: Decimal
    tool_cost: Decimal
    retry_overhead_cost: Decimal
    human_cost: Decimal
    cost_per_attempted_task: Decimal | None
    cost_per_successful_task: Decimal | None
    success_rate: float | None
    autonomous_completion_rate: float | None
    human_escalation_rate: float | None
    average_model_calls: float | None
    average_tool_calls: float | None
    average_retries: float | None
    average_latency_seconds: float | None
    input_tokens: int
    output_tokens: int

    def to_dict(self):
        return to_dict(self)


def aggregate(records: Iterable[TaskRecord]) -> Metrics:
    """Deduplicate identical task IDs; reject conflicts and mixed currencies."""
    unique: dict[str, TaskRecord] = {}
    for record in records:
        if record.task_id in unique and unique[record.task_id] != record:
            raise ValueError(f"conflicting records for task_id {record.task_id}")
        unique[record.task_id] = record
    rows = tuple(unique.values())
    currencies = {row.currency for row in rows}
    if len(currencies) > 1:
        raise ValueError("aggregate one currency at a time; no implicit FX conversion")
    n = len(rows)
    successes = sum(row.outcome == Outcome.SUCCESS for row in rows)
    autonomous = sum(row.outcome == Outcome.SUCCESS and not row.escalated for row in rows)
    escalated = sum(row.escalated for row in rows)
    events = [event for row in rows for event in row.events]
    costs = {kind: sum((e.cost for e in events if e.kind == kind), Decimal(0))
             for kind in EventKind}
    total = sum(costs.values(), Decimal(0))
    confidence_rows = [r for r in rows if r.confidence is not None and r.outcome in (Outcome.SUCCESS, Outcome.FAILURE)]
    judged = [r for r in rows if r.escalation_judgment is not None]
    cj = sum(r.escalation_judgment == EscalationJudgment.CORRECT_ESCALATION for r in judged)
    uj = sum(r.escalation_judgment == EscalationJudgment.UNNECESSARY_ESCALATION for r in judged)
    mj = sum(r.escalation_judgment == EscalationJudgment.MISSED_ESCALATION for r in judged)
    ca = sum(r.escalation_judgment == EscalationJudgment.CORRECT_AUTONOMY for r in judged)
    def rate(value):
        return value / n if n else None
    def calls(kind):
        return rate(sum(e.kind == kind for e in events))
    return Metrics(
        currency=next(iter(currencies), None), attempted_tasks=n,
        successful_tasks=successes,
        failed_tasks=sum(r.outcome == Outcome.FAILURE for r in rows),
        cancelled_tasks=sum(r.outcome == Outcome.CANCELLED for r in rows),
        unknown_tasks=sum(r.outcome == Outcome.UNKNOWN for r in rows),
        autonomous_completions=autonomous, escalated_tasks=escalated,
        total_cost=total, model_cost=costs[EventKind.MODEL],
        tool_cost=costs[EventKind.TOOL], retry_overhead_cost=costs[EventKind.RETRY],
        human_cost=costs[EventKind.HUMAN],
        cost_per_attempted_task=total / n if n else None,
        cost_per_successful_task=total / successes if successes else None,
        success_rate=rate(successes), autonomous_completion_rate=rate(autonomous),
        human_escalation_rate=rate(escalated),
        average_model_calls=calls(EventKind.MODEL), average_tool_calls=calls(EventKind.TOOL),
        average_retries=calls(EventKind.RETRY),
        average_latency_seconds=rate(sum(r.latency_seconds for r in rows)),
        input_tokens=sum(e.input_tokens for e in events),
        output_tokens=sum(e.output_tokens for e in events),
    )
