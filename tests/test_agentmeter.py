import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import json
from unittest.mock import patch

import pytest
from agentmeter import AgentMeter, InMemoryExporter, JSONLExporter, Outcome, aggregate


def test_economics_includes_failed_work_and_no_retry_double_count():
    meter = AgentMeter()
    with meter.task("resolve") as t:
        t.model_call("model", input_tokens=100, output_tokens=10, cost="0.10")
        t.retry("invalid_response", overhead_cost="0.01")
        t.model_call("model", input_tokens=50, output_tokens=5, cost="0.20")
        t.tool_call("lookup", cost="0.04")
        t.success()
    with meter.task("resolve") as t:
        t.human_intervention("review", cost="0.65")
        t.success()
    with meter.task("resolve") as t:
        t.model_call("model", cost="1.00")
        t.failure()
    m = meter.metrics()
    assert m.total_cost == Decimal("2.00")
    assert m.cost_per_successful_task == Decimal("1.00")
    assert m.cost_per_attempted_task == Decimal(2) / 3
    assert m.success_rate == 2 / 3
    assert m.autonomous_completion_rate == 1 / 3
    assert m.human_escalation_rate == 1 / 3
    assert m.average_model_calls == 1
    assert m.average_tool_calls == 1 / 3
    assert m.average_retries == 1 / 3
    assert (m.input_tokens, m.output_tokens) == (150, 15)
    assert m.model_cost + m.tool_cost + m.human_cost + m.retry_overhead_cost == m.total_cost


def test_empty_and_zero_success():
    m = aggregate([])
    assert m.total_cost == 0
    assert m.cost_per_successful_task is None
    assert m.cost_per_attempted_task is None
    assert m.success_rate is None
    assert m.average_latency_seconds is None
    meter = AgentMeter()
    with meter.task("unverified"):
        pass
    assert meter.metrics().unknown_tasks == 1
    assert meter.metrics().success_rate == 0
    assert meter.metrics().cost_per_successful_task is None


def test_exception_overrides_success_and_propagates():
    meter = AgentMeter()
    with pytest.raises(ValueError, match="application"):
        with meter.task("resolve") as t:
            t.success()
            t.tool_call("lookup", cost="0.2")
            raise ValueError("application")
    assert t.record.outcome == Outcome.FAILURE
    assert t.record.total_cost == Decimal("0.2")


def test_zero_cost_human_counts_once_and_is_not_autonomous():
    meter = AgentMeter()
    with meter.task("review") as t:
        t.human_intervention("approval", cost=0)
        t.human_intervention("review", cost=0)
        t.success()
    assert meter.metrics().human_escalation_rate == 1
    assert meter.metrics().autonomous_completion_rate == 0


@pytest.mark.parametrize("cost", [-1, "NaN", "Infinity", float("nan"), True, "bad"])
def test_invalid_cost_rejected_without_recording(cost):
    with AgentMeter().task("test") as t:
        with pytest.raises(ValueError):
            t.tool_call("tool", cost=cost)
    assert t.record.events == ()


@pytest.mark.parametrize("tokens", [-1, 1.5, True, "2"])
def test_invalid_token_counts(tokens):
    with AgentMeter().task("test") as t:
        with pytest.raises(ValueError):
            t.model_call("model", input_tokens=tokens, cost=0)


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan"), True])
def test_invalid_durations(duration):
    with AgentMeter().task("test") as t:
        with pytest.raises(ValueError):
            t.tool_call("tool", cost=0, duration_seconds=duration)


def test_context_lifecycle_and_immutable_snapshot():
    t = AgentMeter().task("test")
    with pytest.raises(RuntimeError):
        t.success()
    with t:
        t.success()
    with pytest.raises(RuntimeError):
        t.tool_call("late", cost=0)
    with pytest.raises(RuntimeError):
        with t:
            pass
    with pytest.raises(FrozenInstanceError):
        t.record.name = "changed"


def test_monotonic_latency_despite_wall_clock_change():
    with patch("agentmeter.sdk.time.time_ns", side_effect=[200, 100]), \
         patch("agentmeter.sdk.time.perf_counter", side_effect=[10, 12.5]):
        with AgentMeter().task("test") as t:
            t.success()
    assert t.record.latency_seconds == 2.5
    assert aggregate([t.record]).average_latency_seconds == 2.5


def test_deduplication_conflicts_and_currency():
    with AgentMeter().task("test") as t:
        t.success()
    assert aggregate([t.record, t.record]).attempted_tasks == 1
    with pytest.raises(ValueError, match="conflicting"):
        aggregate([t.record, replace(t.record, outcome=Outcome.FAILURE)])
    with pytest.raises(ValueError, match="currency"):
        aggregate([t.record, replace(t.record, task_id="other", currency="EUR")])


def test_jsonl_serialization(tmp_path):
    path = tmp_path / "events.jsonl"
    meter = AgentMeter(exporter=JSONLExporter(path))
    for _ in range(2):
        with meter.task("test") as t:
            t.model_call("model", cost="0.000000123", input_tokens=4)
            t.success()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["events"][0]["cost"] == "0.000000123"
    assert rows[0]["outcome"] == "success"
    assert rows[0]["schema_version"] == 1
    assert len(rows[0]["trace_id"]) == 32
    assert len(rows[0]["span_id"]) == 16
    assert rows[0]["task_id"] != rows[1]["task_id"]
    with pytest.raises(TypeError):
        meter.metrics()


class BrokenExporter:
    def export(self, record):
        raise OSError("private error contents")


def test_export_failure_is_observable_and_does_not_mask_application(caplog):
    meter = AgentMeter(exporter=BrokenExporter())
    with meter.task("test") as t:
        t.success()
    assert isinstance(t.export_error, OSError)
    assert t.record.outcome == Outcome.SUCCESS
    assert "private error contents" not in caplog.text
    strict = AgentMeter(exporter=BrokenExporter(), strict_export=True)
    with pytest.raises(OSError):
        with strict.task("test"):
            pass
    with pytest.raises(ValueError, match="original"):
        with strict.task("test"):
            raise ValueError("original")


def test_async_isolation_and_cancellation():
    meter = AgentMeter()
    async def run(i):
        async with meter.task(str(i)) as t:
            await asyncio.sleep(0)
            t.model_call("model", cost=i)
            t.success()
    async def main():
        await asyncio.gather(*(run(i) for i in range(10)))
        with pytest.raises(asyncio.CancelledError):
            async with meter.task("cancelled"):
                raise asyncio.CancelledError()
    asyncio.run(main())
    assert meter.metrics().total_cost == 45
    assert meter.metrics().attempted_tasks == 11
    assert meter.metrics().cancelled_tasks == 1
    assert meter.metrics().successful_tasks == 10


def test_threaded_tasks_and_name_filter():
    meter = AgentMeter()
    def run(i):
        with meter.task("even" if i % 2 == 0 else "odd") as t:
            t.tool_call("api", cost="0.1")
            t.success()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, range(100)))
    assert meter.metrics().attempted_tasks == 100
    assert meter.metrics().total_cost == Decimal("10")
    assert meter.metrics(name="even").attempted_tasks == 50
    meter.exporter.clear()
    assert meter.metrics().attempted_tasks == 0


def test_validation_and_supplied_trace_context():
    with pytest.raises(ValueError):
        AgentMeter(currency="usd")
    with pytest.raises(ValueError):
        AgentMeter().task("")
    for invalid in ["0" * 32, "X" * 32, "short"]:
        with pytest.raises(ValueError):
            AgentMeter().task("test", trace_id=invalid)
    with AgentMeter().task("test", trace_id="a" * 32, span_id="b" * 16) as t:
        t.cancel()
    assert t.record.trace_id == "a" * 32
    assert t.record.span_id == "b" * 16
    assert t.record.outcome == Outcome.CANCELLED
