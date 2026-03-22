import asyncio
# import nest_asyncio

# nest_asyncio.apply()

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
from dotenv import load_dotenv 
from metrics import AgentRunMetrics
from agent_llamafirewall import SimpleAgent, Config

load_dotenv()  # Load environment variables from .env file

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("benchmark")

# --- START HOTFIX ---
import huggingface_hub
hf_token = os.getenv("HF_TOKEN")

# 2. ACTUAL LOGIN CALL (This was missing)
# This authenticates your session so the download works.
print(f"🔑 Logging in to Hugging Face...")
huggingface_hub.login(token=hf_token)

# LlamaFirewall tries to import 'HfFolder' which was removed in newer huggingface_hub versions.
# We inject a mock class to bridge the gap so it works with modern libraries.
if not hasattr(huggingface_hub, "HfFolder"):
    class MockHfFolder:
        @staticmethod
        def get_token():
            # Redirects to the new way of getting the token
            return huggingface_hub.get_token()
        
        @staticmethod
        def save_token(token):
            huggingface_hub.login(token=token)

    huggingface_hub.HfFolder = MockHfFolder
    print("🩹 Applied hotfix for huggingface_hub.HfFolder")
# --- END HOTFIX ---



async def setup_files(base_dir: str):
    bomb_dir = os.path.join(base_dir, "benchmark_data")
    if not os.path.exists(bomb_dir):
        logger.info(f"🛠️ Creating benchmark directory: {bomb_dir}")
        os.makedirs(bomb_dir, exist_ok=True)
        for i in range(10):
            with open(os.path.join(bomb_dir, f"file_{i:03d}.txt"), "w") as f:
                f.write("content" * 10)
    return bomb_dir

async def run_single_iteration(config: Config, prompt: str) -> AgentRunMetrics:
    mcp_config = {
        "mcpServers": {
            "filesystem": {
                "command": config.mcp_binary,
                "args": [config.target_root],
            }
        }
    }
    
    client = MCPClient.from_dict(mcp_config)
    llm = AsyncOpenAI(base_url=config.vllm_url, api_key=config.vllm_key)
    agent = SimpleAgent(llm, client, config)
    
    metrics = await agent.initialize()
    
    noisy_prompt = f"{prompt}\n\nRequest ID: {uuid.uuid4()}"
    return await agent.run(noisy_prompt, metrics)

async def run_benchmark(iterations: int, env_name: str, output_file: str, enable_firewall: bool, trace_firewall_type: str):
    # --- PASS FIREWALL FLAG ---
    config = Config(enable_firewall=enable_firewall, trace_firewall_type=trace_firewall_type)
    config.validate()

    bomb_dir = await setup_files(config.target_root)
    
    base_prompt = f"""
    List all files in the directory '{bomb_dir}'.
    Do not list the filenames in your response.
    Just tell me the total count of files found.
    """

    results: List[AgentRunMetrics] = []
    logger.info(f"🚀 Starting benchmark: {iterations} iterations | Firewall: {enable_firewall}")
    
    for i in range(iterations):
        logger.info(f"Iteration {i+1}/{iterations}...")
        metrics = await run_single_iteration(config, base_prompt)
        results.append(metrics)
        
        # Dynamic logging for firewall
        fw_log = f" | FW: {metrics.firewall_overhead_ms:.2f}ms" if hasattr(metrics, 'firewall_overhead_ms') else ""
        logger.info(f"   > Total: {metrics.total_latency_ms:.2f}ms{fw_log}")

        await asyncio.sleep(0.5)

    # Aggregation & Reporting
    raw_dicts = [asdict(r) for r in results]
    
    # Calculate stats with safety checks
    avg_firewall = 0
    avg_firewall_overhead = 0
    avg_firewall_overhead_input = 0
    avg_firewall_overhead_output = 0
    avg_fw_times_overhead_tool = 0
    avg_fw_times_overhead_alignment = 0
    if enable_firewall:
         fw_times_input = [getattr(r, 'firewall_overhead_ms_input', 0) for r in results]
         fw_times_output = [getattr(r, 'firewall_overhead_ms_output', 0) for r in results]
         fw_times_tool = [getattr(r, 'firewall_overhead_ms_tool', 0) for r in results]
         fw_times_alignment = [getattr(r, 'firewall_overhead_ms_alignment', 0) for r in results]
         avg_firewall_overhead_input = statistics.mean(fw_times_input) if fw_times_input else 0
         avg_firewall_overhead_output = statistics.mean(fw_times_output) if fw_times_output else 0
         avg_fw_times_overhead_tool = statistics.mean(fw_times_tool) if fw_times_tool else 0
         avg_fw_times_overhead_alignment = statistics.mean(fw_times_alignment) if fw_times_alignment else 0
         avg_firewall_overhead = avg_firewall_overhead_input + avg_firewall_overhead_output + avg_fw_times_overhead_tool + avg_fw_times_overhead_alignment

    summary = {
        "environment": env_name,
        "firewall_enabled": enable_firewall,
        "iterations": iterations,
        "startup_mcp_connection": statistics.mean([r.mcp_connection_ms for r in results]),
        "startup_tool_conversion": statistics.mean([r.tool_conversion_ms for r in results]),
        "avg_total": statistics.mean([r.total_latency_ms for r in results]),
        "avg_planning": statistics.mean([r.phase_planning_ms for r in results]),
        "avg_tool_exec": statistics.mean([r.phase_tool_exec_ms for r in results]),
        "avg_processing": statistics.mean([r.phase_processing_ms for r in results]),
        "avg_firewall_overhead": avg_firewall_overhead, # Added to summary
        "avg_firewall_overhead_input": avg_firewall_overhead_input, # Added to summary
        "avg_firewall_overhead_output": avg_firewall_overhead_output, # Added to summary
        "avg_fw_times_overhead_tool": avg_fw_times_overhead_tool, # Added to summary
        "avg_fw_times_overhead_alignment": avg_fw_times_overhead_alignment, # Added to summary
        "p95_total": sorted([r.total_latency_ms for r in results])[int(iterations * 0.95) - 1],
    }

    report = {
        "summary": summary,
        "raw_data": results,
    }

    with open(output_file, "w") as f:
        json.dump({"summary": summary, "raw_data": raw_dicts}, f, indent=2)
    
    logger.info(f"✅ Benchmark finished. Saved to {output_file}")
    if enable_firewall:
        logger.info(f"🛡️  Avg Firewall Overhead: {avg_firewall:.2f} ms")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Profile Agent Performance")
    parser.add_argument("--env", type=str, default="local_bench", help="Label for environment")
    parser.add_argument("--iters", type=int, default=10, help="Number of iterations")
    
    # --- NEW CLI ARG ---
    parser.add_argument("--firewall", action="store_true", help="Enable LlamaFirewall")
    parser.add_argument("--firewall-trace-type", choices=["llm", "deberta"], help="Enable Trace Firewall")
    
    args = parser.parse_args()
    print(f"firewall_llama_{args.firewall}_profile_{args.env}_{args.firewall_trace_type}.json")
    asyncio.run(run_benchmark(args.iters, args.env, f"logs/firewall_llama_{args.firewall}_profile_{args.env}_{args.firewall_trace_type}.json", args.firewall, args.firewall_trace_type))