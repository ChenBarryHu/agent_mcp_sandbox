# metrics.py
import time
from dataclasses import dataclass

def now_ms() -> float:
    """High-precision timestamp utility."""
    return time.perf_counter_ns() / 1_000_000

@dataclass
class StreamMetrics:
    """Metrics captured during a single LLM streaming response."""
    total_ms: float = 0.0
    ttft_ms: float = 0.0  # Time to First Token
    decode_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tps_prefill: float = 0.0
    tps_decode: float = 0.0

@dataclass
class AgentRunMetrics:
    """High-level metrics for a full agent execution cycle."""
    mcp_connection_ms: float = 0.0
    tool_conversion_ms: float = 0.0
    phase_planning_ms: float = 0.0
    phase_tool_exec_ms: float = 0.0
    phase_processing_ms: float = 0.0
    total_latency_ms: float = 0.0
    
    # Deep metrics from the final processing phase
    proc_ttft_ms: float = 0.0
    proc_prefill_tps: float = 0.0
    proc_decode_tps: float = 0.0

    # overhead metrics from the firewall
    # firewall_overhead_ms: float = 0.0
    firewall_overhead_ms_input: float = 0.0
    firewall_overhead_ms_output: float = 0.0
    firewall_overhead_ms_tool: float = 0.0
    firewall_overhead_ms_alignment: float = 0.0