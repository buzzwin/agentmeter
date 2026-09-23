# AgentMeter

**Measure AI by work completed, not tokens consumed.**

A small, framework-neutral Python SDK for agent economics. Track the whole task:
model calls, tools, retries, human intervention, outcome, and elapsed time.
Then answer the question token dashboards miss:

> How much do we spend for each task we actually complete successfully?

AgentMeter 0.1 is an early, local-first open-source foundation. It has **zero runtime
dependencies**, no service to deploy, no API key, and no network activity. It does
not intercept provider calls: you supply usage and costs explicitly.

## Quick start

Requires Python 3.10+. From this repository:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python examples/customer_support.py
```

The package is not yet published to PyPI; install from this checkout.

```python
from agentmeter import AgentMeter

meter = AgentMeter(currency="USD")

with meter.task("resolve_customer_dispute") as task:
    # Invoke your agent here, then record its actual usage.
    task.model_call(
        "your-model", input_tokens=1200, output_tokens=300, cost="0.03"
    )
    task.tool_call("customer_api", cost="0.002")
    task.success()  # Only after your application verifies the outcome.

metrics = meter.metrics()
print(metrics.cost_per_successful_task)  # Decimal('0.032')
print(metrics.success_rate)             # 1.0
```

Use decimal strings or `Decimal` for money. Floats are accepted through
`Decimal(str(value))`, but any precision already lost by the caller is unrecoverable.
All costs use the meter's currency. Currency codes must be three uppercase letters;
AgentMeter does not validate an ISO registry or perform currency conversion.

## Why task economics?

A cheaper model call can produce a more expensive workflow when retries and human
review increase. Failed work still consumes money. AgentMeter includes that cost
in the numerator, rather than reporting only the price of successful runs.

The included customer-support example produces these illustrative results:

| Metric | Value |
| --- | ---: |
| Attempted tasks | 3 |
| Successful tasks | 2 |
| Total cost | $1.074 |
| Cost per attempted task | $0.358 |
| Cost per successful task | $0.537 |
| Success rate | 66.7% |
| Autonomous completion rate | 33.3% |
| Human escalation rate | 33.3% |

These are synthetic costs, not provider pricing or benchmark claims.

## Metric definitions

An **attempt** is one finalized task context. Active contexts are excluded until
exit. Give each business task a stable name and compare similar task populations.
A new context is a new attempt; retries within that context are events.

Let `N` be finalized attempts, `S` successful attempts, `A` successful attempts with
no human-intervention events, `H` attempts with any human-intervention event, and
`C` the cost of **all** finalized attempts, including failure, cancellation, and unknown outcomes.

| Metric | Definition |
| --- | --- |
| Cost per attempted task | `C / N` |
| Cost per successful task | `C / S` |
| Success rate | `S / N` |
| Autonomous completion rate | `A / N` |
| Human escalation rate | `H / N` |
| Average model/tool calls | Respective event count / `N` |
| Average retries | Retry event count / `N` |
| Average latency | Sum of task elapsed seconds / `N` |

Rates are fractions from 0 to 1. A zero denominator produces `None` (`null` in JSON),
never a misleading zero cost per success. An empty aggregate has zero counts and
cost, no currency, and undefined ratios. Costs by category, token totals, and
counts by outcome are also included. Ratios use Python's Decimal context for
monetary division (default precision 28); recurring fractions are rounded.

`meter.metrics(name="resolve_customer_dispute")` filters the default in-memory
store by task name. `aggregate(records)` handles arbitrary record iterables,
deduplicates identical task IDs, rejects conflicting duplicates, and rejects mixed
currencies. Do not add overlapping parent and child task costs to the same population.
V1 does not infer business-request identity across separate attempts.

## Outcomes, retries, and human work

```python
with meter.task("reconcile_invoice") as task:
    task.model_call("your-model", input_tokens=500, output_tokens=50, cost="0.01")
    task.retry("invalid_output", overhead_cost="0.001")
    task.model_call("your-model", input_tokens=700, output_tokens=60, cost="0.015")
    task.human_intervention("review", duration_seconds=90, cost="0.75")
    task.success()
```

- `success()`, `failure()`, `cancel()`, or `set_outcome(...)` explicitly set the
  outcome. The last explicit value wins before exit.
- A clean exit without an outcome is **unknown**, not success.
- An escaping exception forces **failure**, even after `success()`, and propagates.
  `asyncio.CancelledError` and `KeyboardInterrupt` produce **cancelled** and propagate.
- Human intervention marks an attempt escalated, even at zero cost. Multiple human
  events count once for escalation rate. Successful human-assisted tasks are
  successful but not autonomous. V1 treats any human intervention as escalation.
- Retry overhead includes only additional costs not already reported on other
  events. Repeated model/tool calls must be logged separately and are charged once.
  Total cost is `model + tool + human + retry overhead`.
- Record costs for failed provider/tool calls too, when incurred. AgentMeter cannot
  infer unreported usage. Costs are required for model, tool, and human events; pass
  explicit zero for free work. Negative costs, nonfinite values, and fractional or
  negative token counts are rejected. Credits/refunds are outside V1.
- Optional event durations are supplied by you; task latency is measured with a
  monotonic clock. Event durations may overlap and are not summed into task latency.

No success judge, evaluation model, pricing table, or automatic token accounting is
included. Define completion criteria in the application and use billed charges or
your own documented estimates. Keep the context open through human completion if
that work should count; delayed outcome corrections are not supported yet.

## Export and event model

```python
from agentmeter import AgentMeter, JSONLExporter

meter = AgentMeter(exporter=JSONLExporter("tasks.jsonl"))
with meter.task("classify") as task:
    task.model_call("your-model", cost="0.001", input_tokens=100, output_tokens=5)
    task.success()

record = task.record  # Frozen TaskRecord, available after context exit.
print(record.to_dict())
```

Each JSONL line is a schema-versioned task record. `TaskRecord` contains task ID,
name, currency, trace/span IDs, Unix timestamps in nanoseconds, monotonic latency
in seconds, outcome, and an immutable tuple of events. Each `Event` contains its
own ID, timestamp, kind, name, decimal cost, token counts, and optional duration.
Kinds are `model_call`, `tool_call`, `retry`, and `human_intervention`. Money
serializes as strings to preserve precision; enums serialize as strings.

Record dataclasses are the trusted internal interchange model. Public recording
methods validate input; directly constructing dataclasses or deserializing
untrusted input requires caller validation. A JSONL importer is not included.

Exporters implement the tiny `Exporter` protocol: `export(record) -> None`.
The default `InMemoryExporter` offers snapshot `.records` and `.clear()`; it retains
all records, so use a different exporter for long-lived services. `JSONLExporter`
opens/appends/closes for each record; its parent directory must exist.

Export failures log a warning and remain available as `task.export_error`;
`task.record` remains available for recovery. `strict_export=True` raises delivery
errors on otherwise clean exit. An exporter error never replaces an application
exception. There is no retry queue, delivery guarantee, or background worker.

## OpenTelemetry-compatible concepts

The record model follows [OpenTelemetry's trace concepts](https://opentelemetry.io/docs/concepts/signals/traces/):
timed task operations, timestamped events, and trace/span correlation identifiers.
You can pass existing lowercase, nonzero 32-character trace IDs and 16-character
span IDs to `meter.task(..., trace_id=..., span_id=...)`. Otherwise IDs are generated.

This is **conceptual compatibility and correlation**, not an OpenTelemetry SDK,
OTLP exporter, or claim of official semantic-convention compliance. AgentMeter
does not create live OTel spans, read ambient context, propagate W3C headers, or
install a global tracer. An adapter can convert completed records to your telemetry
pipeline through the exporter protocol. Keep cost aggregation unsampled: sampled
traces alone will bias total spending and outcome rates.

## Framework integration boundary

The core imports no agent framework. For OpenAI Agents, LangGraph, CrewAI, AutoGen,
or custom agents, an adapter should:

1. Open one task context around a business-task attempt.
2. Translate observed model usage and tool charges to recording calls.
3. Record retries and human intervention explicitly.
4. Mark success only after the application's completion check.

Framework adapters are planned, not shipped in V1. Explicit task references avoid
global state and implicit attribution between concurrent agents.

Use `async with meter.task(...)` in async applications (see
`examples/async_agent.py`). Separate tasks may run concurrently; do not share a
single task instance across threads. The provided exporters synchronize writes
within one instance, not across processes. Exports are synchronous, including
under `async with`; use a custom queued exporter for slow destinations. Nested
contexts are independent attempts; there is no automatic parent-cost rollup.

## Privacy and operational limits

AgentMeter does not capture prompts, completions, tool arguments, exception
messages, or credentials. Names and retry reasons are user-supplied: use stable
operation labels rather than customer data. JSONL is plain local text; apply your
own file permissions and retention policy. No automatic telemetry is sent anywhere.
A process crash before context exit loses the active attempt. V1 is an accounting
instrumentation foundation, not an invoicing ledger or production storage backend.

## Development

```sh
python -m pip install -e '.[dev]'
python -m pytest
python examples/customer_support.py
python examples/async_agent.py
python -m build
```

Tests cover outcome semantics, failed-work accounting, retries, precision,
validation, zero denominators, duplicate IDs, currencies, monotonic timing,
concurrency, export failures, and JSONL serialization. CI runs Python 3.10–3.14.

```text
src/agentmeter/
  model.py       Immutable event/task schema and validation
  sdk.py         Task lifecycle and recording API
  metrics.py     Aggregation and business metrics
  exporters.py   Export protocol, in-memory and JSONL storage
examples/        Runnable, provider-free examples
tests/           Accounting and lifecycle tests
```

## Roadmap

- Optional OpenTelemetry adapter and framework integrations.
- Explicit estimated-versus-billed cost provenance and pricing adapters.
- Durable storage, delayed outcomes, and request-to-attempt correlation.
- Cohort comparisons and a small reporting interface.

V1 intentionally keeps the measurement contract small. Contributions should
preserve framework independence and make accounting assumptions explicit. See
[CONTRIBUTING.md](CONTRIBUTING.md). Licensed under [MIT](LICENSE).
