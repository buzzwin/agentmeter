"""Independent contexts for concurrent agent runs, with no framework dependency."""
import asyncio
from agentmeter import AgentMeter

meter = AgentMeter()

async def run():
    async with meter.task("classify") as task:
        await asyncio.sleep(0.01)  # Replace with your agent invocation.
        task.model_call("example-model", input_tokens=100, output_tokens=5, cost="0.001")
        task.success()

async def main():
    await asyncio.gather(*(run() for _ in range(3)))
    print(meter.metrics().to_dict())

if __name__ == "__main__":
    asyncio.run(main())
