"""Run after installing locally: python examples/customer_support.py."""
import json
from agentmeter import AgentMeter

meter = AgentMeter()

with meter.task("resolve_customer_dispute") as task:
    task.model_call("example-model", input_tokens=1200, output_tokens=300, cost="0.03")
    task.tool_call("customer_api", cost="0.002")
    task.success()

with meter.task("resolve_customer_dispute") as task:
    task.model_call("example-model", input_tokens=900, output_tokens=200, cost="0.02")
    task.retry("tool_timeout")
    task.tool_call("customer_api", cost="0.002")
    task.human_intervention("support_review", duration_seconds=120, cost="1.00")
    task.success()

with meter.task("resolve_customer_dispute") as task:
    task.model_call("example-model", input_tokens=900, output_tokens=200, cost="0.02")
    task.failure()

print(json.dumps(meter.metrics().to_dict(), indent=2))
