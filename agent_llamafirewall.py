import json
import os
import logging
import asyncio
# import nest_asyncio

# nest_asyncio.apply()

from dataclasses import dataclass
from typing import List, Tuple, Any, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI
from mcp_use import MCPClient
from mcp_use.agents.adapters.langchain_adapter import LangChainAdapter

# --- UPDATED IMPORTS FOR FIREWALL ---
try:
    from llamafirewall import LlamaFirewall, ScannerType, Role, UserMessage, AssistantMessage, ToolMessage, Trace
    from llamafirewall.scanners.experimental.alignmentcheck_scanner import AlignmentCheckScanner
except ImportError:
    # Fallback/Mock for development if package isn't installed yet
    class LlamaFirewall:
        def __init__(self, scanners): pass
        def scan(self, msg): 
            # Mock allowing everything by default
            return type('obj', (object,), {'decision': 'ALLOW', 'policy_violations': []})()
    
    class ScannerType: 
        PROMPT_GUARD = 'prompt_guard'
        CODE_SHIELD = 'code_shield'
    
    class Role: 
        USER = 'user'
        ASSISTANT = 'assistant'
        TOOL = 'tool' 
        
    class UserMessage:
        def __init__(self, content): self.content = content
    class AssistantMessage:
        def __init__(self, content): self.content = content
    class ToolMessage:
        def __init__(self, content): self.content = content

from metrics import StreamMetrics, AgentRunMetrics, now_ms

load_dotenv()
logger = logging.getLogger("agent_profiler")

@dataclass
class Config:
    """Centralized configuration management."""
    target_root: str = os.getenv("TARGET_DIRECTORY", "./test_data")
    mcp_binary: str = os.getenv("MCP_FILESYSTEM_BINARY", "")
    vllm_url: str = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
    vllm_key: str = os.getenv("VLLM_API_KEY", "EMPTY")
    model_name: str = "Qwen/Qwen2.5-0.5B-Instruct"
    
    enable_firewall: bool = False 
    trace_firewall_type: str = "llm"
    
    system_prompt: str = """You are a precise filesystem agent. 
    Rules:
    1. If asked to list files, use the tool immediately. 
    2. Do not explain your plan.
    3. Your final answer must be BRIEF."""

    def validate(self):
        if not all([self.target_root, self.mcp_binary, self.vllm_url]):
            raise ValueError("❌ Missing .env configuration.")

class MockToolCall:
    def __init__(self, d):
        self.id = d['id']
        self.type = 'function'
        self.function = type('obj', (object,), {'name': d['function']['name'], 'arguments': d['function']['arguments']})

class StreamProcessor:
    """Handles the complexity of parsing OpenAI chunks and calculating throughput."""
    @staticmethod
    async def process(response_stream) -> Tuple[str, List[Any], StreamMetrics]:
        t_start = now_ms()
        t_first = None
        collected_content = []
        tool_calls_buffer = []
        usage = None

        async for chunk in response_stream:
            if t_first is None:
                t_first = now_ms()

            if hasattr(chunk, 'usage') and chunk.usage:
                usage = chunk.usage

            delta = chunk.choices[0].delta if chunk.choices else None
            if delta:
                if delta.content:
                    collected_content.append(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        StreamProcessor._update_tool_buffer(tool_calls_buffer, tc)

        t_end = now_ms()
        if t_first is None: t_first = t_end

        metrics = StreamProcessor._calculate_metrics(t_start, t_first, t_end, usage)
        
        full_content = "".join(collected_content)
        full_tools = [StreamProcessor._mock_tool_call(tc) for tc in tool_calls_buffer]

        return full_content, full_tools, metrics

    @staticmethod
    def _update_tool_buffer(buffer, tc):
        if len(buffer) <= tc.index:
            buffer.append({"id": "", "function": {"name": "", "arguments": ""}, "type": "function"})
        
        if tc.id: buffer[tc.index]["id"] += tc.id
        if tc.function.name: buffer[tc.index]["function"]["name"] += tc.function.name
        if tc.function.arguments: buffer[tc.index]["function"]["arguments"] += tc.function.arguments

    @staticmethod
    def _mock_tool_call(data):
        return MockToolCall(data)

    @staticmethod
    def _calculate_metrics(t_start, t_first, t_end, usage) -> StreamMetrics:
        m = StreamMetrics()
        m.total_ms = t_end - t_start
        m.ttft_ms = t_first - t_start
        m.decode_ms = t_end - t_first
        
        if usage:
            m.tokens_in = usage.prompt_tokens
            m.tokens_out = usage.completion_tokens
            if m.ttft_ms > 0:
                m.tps_prefill = usage.prompt_tokens / (m.ttft_ms / 1000)
            if m.decode_ms > 0 and usage.completion_tokens > 0:
                m.tps_decode = usage.completion_tokens / (m.decode_ms / 1000)
        return m

class SimpleAgent:
    def __init__(self, llm: AsyncOpenAI, client: MCPClient, config: Config):
        self.llm = llm
        self.client = client
        self.config = config
        self.adapter = LangChainAdapter()
        self.adapter._record_telemetry = False
        self.all_tools = []
        self.openai_tools = []
        
        # --- INITIALIZE FIREWALL ---
        self.firewall = None
        if self.config.enable_firewall:
            logger.info("🛡️ Initializing LlamaFirewall (Full Spectrum)...")


            # 3. BUILD THE SCANNER INSTANCE
            # We inject our local client into the scanner

            self.firewall = LlamaFirewall(
                scanners={
                    # 1. Scan User Prompt (Jailbreaks)
                    Role.USER: [ScannerType.PROMPT_GUARD],
                    
                    # # 2. Scan Agent Output (Hallucinations/Safe Content)
                    Role.ASSISTANT: [ScannerType.PROMPT_GUARD],
                    
                    # 3. Scan Tool Outputs (Code Injection/Data Leakage)
                    Role.TOOL: [ScannerType.PROMPT_GUARD] 
                }
            )
            if self.config.trace_firewall_type == "llm":
                logger.info("🛡️ Initializing LlamaFirewall AGENT_ALIGNMENT(LLM) for Trace guardrail...")
                self.firewall_trace = LlamaFirewall(
                    scanners={
                        Role.ASSISTANT: [ScannerType.AGENT_ALIGNMENT], # AGENT_ALIGNMENT, PROMPT_GUARD
                    }
                )
            elif self.config.trace_firewall_type == "deberta":
                logger.info("🛡️ Initializing LlamaFirewall PROMPT_GUARD for Trace guardrail...")
                self.firewall_trace = LlamaFirewall(
                    scanners={
                        Role.ASSISTANT: [ScannerType.PROMPT_GUARD], # AGENT_ALIGNMENT, PROMPT_GUARD
                    }
                )
            else:
                # through an exception if an invalid option is provided
                raise ValueError(f"Invalid trace_firewall_type: {self.config.trace_firewall_type}. Must be 'llm' or 'deberta'.")

    async def initialize(self) -> AgentRunMetrics:
        metrics = AgentRunMetrics()
        t0 = now_ms()
        
        self.client._record_telemetry = False
        if not self.client.get_all_active_sessions():
            await self.client.create_all_sessions()
        
        await self.adapter.create_all(self.client)
        self.all_tools = self.adapter.tools
        t1 = now_ms()
        
        self.openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.args_schema.model_json_schema(),
                }
            } 
            for tool in self.all_tools
        ]
        t2 = now_ms()
        
        metrics.mcp_connection_ms = t1 - t0
        metrics.tool_conversion_ms = t2 - t1
        return metrics

    async def _execute_scan_core(self, scanner_func, payload, metric_attr: str, metrics: AgentRunMetrics, log_obj) -> bool:
        """
        Shared core logic for executing a security scan in a thread, calculating overhead, 
        and handling results/failsafes.
        """
        t_scan = now_ms()
        try:
            # Execute the specific scanner function passed in
            result = await asyncio.to_thread(scanner_func, payload)
        except Exception as e:
            logger.error(f"Firewall scan failed: {e}")
            return True # Fail safe: Block if firewall fails

        # Dynamically accumulate overhead on the specified metric attribute
        overhead = now_ms() - t_scan
        current_val = getattr(metrics, metric_attr, 0)
        setattr(metrics, metric_attr, current_val + overhead)

        if result.decision == "BLOCK":
            logger.warning(f"⛔ Firewall Blocked {type(log_obj).__name__}: {result.policy_violations}")
            return True
            
        return False

    # ----------------------------------------------------------------------
    # Refactored Wrappers
    # ----------------------------------------------------------------------

    async def _run_security_scan_input(self, message_obj, metrics: AgentRunMetrics) -> bool:
        if not self.config.enable_firewall or not self.firewall: return False
        return await self._execute_scan_core(
            self.firewall.scan, message_obj, 'firewall_overhead_ms_input', metrics, message_obj
        )

    async def _run_security_scan_output(self, message_obj, metrics: AgentRunMetrics) -> bool:
        if not self.config.enable_firewall or not self.firewall: return False
        return await self._execute_scan_core(
            self.firewall.scan, message_obj, 'firewall_overhead_ms_output', metrics, message_obj
        )

    async def _run_security_scan_tool(self, message_obj, metrics: AgentRunMetrics) -> bool:
        if not self.config.enable_firewall or not self.firewall: return False
        return await self._execute_scan_core(
            self.firewall.scan, message_obj, 'firewall_overhead_ms_tool', metrics, message_obj
        )
    
    async def _run_security_scan_trace(self, message_obj, metrics: AgentRunMetrics, message_list: list) -> bool:
        # Note: Added a fallback to self.firewall just in case firewall_trace isn't initialized
        if not self.config.enable_firewall or not getattr(self, 'firewall_trace', self.firewall): 
            return False

        # Prepare the clean trace payload
        clean_trace = []
        for m in message_list:
            if hasattr(m, 'role'): 
                clean_trace.append(m)
                continue
            
            role, content = m.get("role"), m.get("content", "")
            if role == "user":
                clean_trace.append(UserMessage(content=content))
            elif role == "assistant":
                clean_trace.append(AssistantMessage(content=content))

        return await self._execute_scan_core(
            self.firewall_trace.scan_replay, clean_trace, 'firewall_overhead_ms_alignment', metrics, message_obj
        )

    async def run(self, prompt: str, metrics: AgentRunMetrics) -> AgentRunMetrics:
        start_time = now_ms()
        
        try:
            # --- 1. SCAN USER INPUT ---
            if await self._run_security_scan_input(UserMessage(content=prompt), metrics):
                return metrics # Stops execution; finally block handles the latency

            messages = [
                {"role": "system", "content": self.config.system_prompt}, 
                {"role": "user", "content": prompt}
            ]

            # Phase 1: Planning
            content, tool_calls, plan_stats = await self._stream_request(messages)
            
            # Record Planning Metrics
            metrics.phase_planning_ms = plan_stats.total_ms
            # TODO: Maybe also record plan_stats.tokens_in, tokens_out, and ttft here?
            
            messages.append(self._create_assistant_msg(content, tool_calls))
            
            # --- 2. SCAN AGENT OUTPUT (PLANNING TRACE) ---
            if await self._run_security_scan_trace(AssistantMessage(content=content), metrics, message_list=messages):
                 logger.error("Agent output blocked by firewall during planning.")
                 return metrics

            if tool_calls:
                # Phase 2: Execution
                t_exec_start = now_ms()
                tool_execution_allowed = await self._execute_tools(tool_calls, messages, metrics)
                metrics.phase_tool_exec_ms = now_ms() - t_exec_start
                
                if not tool_execution_allowed:
                    return metrics

                # Phase 3: Processing
                final_content, _, proc_stats = await self._stream_request(messages)
                
                # --- 3. SCAN AGENT OUTPUT (FINAL RESPONSE) ---
                if await self._run_security_scan_output(AssistantMessage(content=final_content), metrics):
                     logger.error("Final agent output blocked by firewall.")
                     return metrics
                
                # Record Processing Metrics
                metrics.phase_processing_ms = proc_stats.total_ms
                metrics.proc_ttft_ms = proc_stats.ttft_ms
                metrics.proc_prefill_tps = proc_stats.tps_prefill
                metrics.proc_decode_tps = proc_stats.tps_decode
                metrics.proc_tokens_in = proc_stats.tokens_in
                metrics.proc_tokens_out = proc_stats.tokens_out

        finally:
            # This executes no matter how the function returns!
            # It ensures total latency is ALWAYS accurately captured.
            metrics.total_latency_ms = now_ms() - start_time
            
        return metrics

    async def _stream_request(self, messages) -> Tuple[str, List, StreamMetrics]:
        response_stream = await self.llm.chat.completions.create(
            model=self.config.model_name,
            messages=messages,
            tools=self.openai_tools,
            temperature=0.0,
            stream=True,
            stream_options={"include_usage": True}
        )
        return await StreamProcessor.process(response_stream)

    async def _execute_tools(self, tool_calls, messages, metrics: AgentRunMetrics) -> bool:
        """
        Executes tools and scans their output.
        Returns: False if a tool output was BLOCKED, True otherwise.
        """
        for call in tool_calls:
            tool = next((t for t in self.all_tools if t.name == call.function.name), None)
            try:
                args = json.loads(call.function.arguments)
                if not args.get('path'): # Safety check to prevent listing root directory
                    logger.warning("Tool call with empty path argument detected. mannually setting to target root/benchmark_data for safety.")
                    args['path'] = os.path.join(self.config.target_root, "benchmark_data")
                result = await tool.ainvoke(args)
                
            except Exception as e:
                result = str(e)
            
            result_str = str(result)

            # --- 3. SCAN TOOL OUTPUT ---
            if await self._run_security_scan_tool(ToolMessage(content=result_str), metrics):
                # Option: Block entire flow
                return False 
            # ---------------------------

            messages.append({"role": "tool", "tool_call_id": call.id, "content": result_str})
        
        return True

    def _create_assistant_msg(self, content, tool_calls):
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id, 
                    "type": "function", 
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                } 
                for tc in tool_calls
            ]
        return msg