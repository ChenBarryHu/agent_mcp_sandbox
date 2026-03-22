# benchmark.py
import asyncio
import argparse
import os
import logging
import statistics
import json
import uuid
from typing import List
from dataclasses import asdict

from openai import AsyncOpenAI
from mcp_use import MCPClient

# Import from our new local modules
from metrics import AgentRunMetrics
from agent import SimpleAgent, Config

# Setup Logging for the CLI
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("benchmark")

async def setup_files(base_dir: str):
    """Ensures deterministic test files exist."""
    bomb_dir = os.path.join(base_dir, "benchmark_data")
    if not os.path.exists(bomb_dir):
        logger.info(f"🛠️ Creating benchmark directory: {bomb_dir}")
        os.makedirs(bomb_dir, exist_ok=True)
        for i in range(10):
            with open(os.path.join(bomb_dir, f"file_{i:03d}.txt"), "w") as f:
                f.write("content" * 10)
    return bomb_dir

async def run_single_iteration(config: Config, prompt: str) -> AgentRunMetrics:
    """Runs a complete lifecycle: Init Client -> Init Agent -> Execute Prompt."""
    
    # Define MCP config relative to our target directory
    mcp_config = {
        "mcpServers": {
            "filesystem": {
                "command": config.mcp_binary,
                "args": [config.target_root],
            }
        }
    }
    
    # Initialize components
    client = MCPClient.from_dict(mcp_config)
    llm = AsyncOpenAI(base_url=config.vllm_url, api_key=config.vllm_key)
    agent = SimpleAgent(llm, client, config)
    
    # 1. Initialization (capture metrics)
    metrics = await agent.initialize()
    
    # 2. Add noise to prompt (prevents caching)
    noisy_prompt = f"{prompt}\n\nRequest ID: {uuid.uuid4()}"
    
    # 3. Execution (capture metrics)
    return await agent.run(noisy_prompt, metrics)

async def run_benchmark(iterations: int, env_name: str, output_file: str):
    config = Config()
    config.validate()

    bomb_dir = await setup_files(config.target_root)
    
    base_prompt = f"""
    List all files in the directory '{bomb_dir}'.
    Do not list the filenames in your response.
    Just tell me the total count of files found.
    """

    # logger.info("🔥 Warming up (1 iteration)...")
    # await run_single_iteration(config, base_prompt)
    
    results: List[AgentRunMetrics] = []
    logger.info(f"🚀 Starting benchmark: {iterations} iterations")
    
    for i in range(iterations):
        logger.info(f"Iteration {i+1}/{iterations}...")
        metrics, need_retry = await run_single_iteration(config, base_prompt)
        while need_retry:
            logger.info(f"Retry Iteration {i+1}/{iterations}...")
            metrics, need_retry = await run_single_iteration(config, base_prompt)
        results.append(metrics)
        logger.info(f"   > Total: {metrics.total_latency_ms:.2f}ms | Plan: {metrics.phase_planning_ms:.2f}ms | Proc: {metrics.phase_processing_ms:.2f}ms")
        await asyncio.sleep(0.5)

    # Aggregation & Reporting
    raw_dicts = [asdict(r) for r in results]
    summary = {
        "environment": env_name,
        "iterations": iterations,
        # Startup Metrics
        "startup_mcp_connection": statistics.mean([r.mcp_connection_ms for r in results]),
        "startup_tool_conversion": statistics.mean([r.tool_conversion_ms for r in results]),
        # Execution Metrics
        "avg_total": statistics.mean([r.total_latency_ms for r in results]),
        "avg_planning": statistics.mean([r.phase_planning_ms for r in results]),
        "avg_tool_exec": statistics.mean([r.phase_tool_exec_ms for r in results]),
        "avg_processing": statistics.mean([r.phase_processing_ms for r in results]),
        "p95_total": sorted([r.total_latency_ms for r in results])[int(iterations * 0.95) - 1],
        "avg_prefill_tps": statistics.mean([r.proc_prefill_tps for r in results]),
        "avg_decode_tps": statistics.mean([r.proc_decode_tps for r in results]),
        "avg_ttft": statistics.mean([r.proc_ttft_ms for r in results]),
    }

    report = {
        "summary": summary,
        "raw_data": results,
    }

    with open(output_file, "w") as f:
        json.dump({"summary": summary, "raw_data": raw_dicts}, f, indent=2)
    
    logger.info(f"✅ Benchmark finished. Saved to {output_file}")
    logger.info(f"📊 Avg Planning (TTFT): {summary['avg_planning']:.2f} ms")
    logger.info(f"📊 Avg Processing (Prefill): {summary['avg_processing']:.2f} ms")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Profile Agent Performance")
    parser.add_argument("--env", type=str, default="local_bench", help="Label for environment")
    parser.add_argument("--iters", type=int, default=10, help="Number of iterations")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.iters, args.env, f"profile_{args.env}.json"))