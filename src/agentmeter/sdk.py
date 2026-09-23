"""Explicit instrumentation independent of any model provider or agent framework."""
import asyncio
import logging
import re
import time
from uuid import uuid4
from .exporters import Exporter, InMemoryExporter
from .metrics import aggregate
from .model import Event, EventKind, Money, Outcome, TaskRecord, count, label, money, seconds

logger = logging.getLogger("agentmeter")


def _id(value: str | None, size: int) -> str:
    result = value if value is not None else uuid4().hex[:size]
    if not isinstance(result, str) or not re.fullmatch(f"[0-9a-f]{{{size}}}", result) or int(result, 16) == 0:
        raise ValueError(f"ID must be {size} lowercase hex characters and nonzero")
    return result


class AgentMeter:
    """Factory for independent tasks. Currency applies to every cost recorded."""
    def __init__(self, *, currency: str = "USD", exporter: Exporter | None = None,
                 strict_export: bool = False):
        if not isinstance(currency, str) or not re.fullmatch("[A-Z]{3}", currency):
            raise ValueError("currency must be a three-letter uppercase code")
        self.currency = currency
        self.exporter = exporter if exporter is not None else InMemoryExporter()
        self.strict_export = strict_export

    def task(self, name: str, *, trace_id: str | None = None,
             span_id: str | None = None) -> "Task":
        return Task(self, label(name), _id(trace_id, 32), _id(span_id, 16))

    def metrics(self, *, name: str | None = None):
        if not isinstance(self.exporter, InMemoryExporter):
            raise TypeError("metrics() requires InMemoryExporter; use aggregate(records)")
        rows = self.exporter.records
        return aggregate(r for r in rows if name is None or r.name == name)


class Task:
    """Single-use context manager. Use a separate instance per concurrent attempt."""
    def __init__(self, meter: AgentMeter, name: str, trace_id: str, span_id: str):
        self._meter = meter
        self._name = name
        self._trace_id = trace_id
        self._span_id = span_id
        self._events: list[Event] = []
        self._outcome = Outcome.UNKNOWN
        self._state = "new"
        self.record: TaskRecord | None = None
        self.export_error: Exception | None = None

    def __enter__(self):
        if self._state != "new":
            raise RuntimeError("task contexts are single-use")
        self._started = time.time_ns()
        self._clock = time.perf_counter()
        self._state = "active"
        return self

    async def __aenter__(self):
        return self.__enter__()

    def _active(self):
        if self._state != "active":
            raise RuntimeError("operation requires an active task context")

    def _event(self, kind, name, cost, *, input_tokens=0, output_tokens=0, duration_seconds=None):
        self._active()
        event = Event(uuid4().hex, time.time_ns(), kind, label(name), money(cost),
                      count(input_tokens), count(output_tokens),
                      None if duration_seconds is None else seconds(duration_seconds))
        self._events.append(event)
        return event

    def model_call(self, model: str, *, input_tokens: int = 0, output_tokens: int = 0,
                   cost: Money, duration_seconds: float | None = None) -> Event:
        """Record billed or estimated cost explicitly; no hidden price lookup."""
        return self._event(EventKind.MODEL, model, cost, input_tokens=input_tokens,
                           output_tokens=output_tokens, duration_seconds=duration_seconds)

    def tool_call(self, name: str, *, cost: Money, duration_seconds: float | None = None) -> Event:
        return self._event(EventKind.TOOL, name, cost, duration_seconds=duration_seconds)

    def retry(self, reason: str, *, overhead_cost: Money = 0) -> Event:
        """Count a retry; overhead excludes repeated model/tool charges."""
        return self._event(EventKind.RETRY, reason, overhead_cost)

    def human_intervention(self, name: str, *, cost: Money,
                           duration_seconds: float | None = None) -> Event:
        """Marks the attempt escalated even when the recorded cost is zero."""
        return self._event(EventKind.HUMAN, name, cost, duration_seconds=duration_seconds)

    def set_outcome(self, outcome: Outcome | str) -> None:
        self._active()
        self._outcome = Outcome(outcome)

    def success(self) -> None:
        self.set_outcome(Outcome.SUCCESS)

    def failure(self) -> None:
        self.set_outcome(Outcome.FAILURE)

    def cancel(self) -> None:
        self.set_outcome(Outcome.CANCELLED)

    def __exit__(self, exc_type, exc, traceback):
        self._active()
        if exc is not None:
            self._outcome = (Outcome.CANCELLED if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt))
                             else Outcome.FAILURE)
        self.record = TaskRecord(
            uuid4().hex, self._name, self._meter.currency, self._trace_id, self._span_id,
            self._started, time.time_ns(), max(0.0, time.perf_counter() - self._clock),
            self._outcome, tuple(self._events),
        )
        self._state = "closed"
        try:
            self._meter.exporter.export(self.record)
        except Exception as error:
            self.export_error = error
            if self._meter.strict_export and exc is None:
                raise
            # Avoid logging exception messages which may contain application secrets.
            logger.warning("AgentMeter export failed (%s); completed record remains on task.record",
                           type(error).__name__)
        return False

    async def __aexit__(self, exc_type, exc, traceback):
        return self.__exit__(exc_type, exc, traceback)
