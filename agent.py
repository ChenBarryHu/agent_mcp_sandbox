# agent.py
import json
import os
import logging
from dataclasses import dataclass
from typing import List, Tuple, Any

from dotenv import load_dotenv
from openai import AsyncOpenAI
from mcp_use import MCPClient
from mcp_use.agents.adapters.langchain_adapter import LangChainAdapter

from metrics import StreamMetrics, AgentRunMetrics, now_ms

# Load env vars once here, or let the main app handle it
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
    system_prompt: str = """You are a precise filesystem agent. 
    Rules:
    1. If asked to list files, use the tool immediately. 
    2. Do not explain your plan.
    3. Your final answer must be BRIEF."""

    def validate(self):
        if not all([self.target_root, self.mcp_binary, self.vllm_url]):
            raise ValueError("❌ Missing .env configuration. Please check TARGET_DIRECTORY, MCP_FILESYSTEM_BINARY, and VLLM_BASE_URL.")

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

    async def run(self, prompt: str, metrics: AgentRunMetrics) -> AgentRunMetrics:
        start_time = now_ms()
        messages = [
            {"role": "system", "content": self.config.system_prompt}, 
            {"role": "user", "content": prompt}
        ]
        need_retry = False

        # Phase 1: Planning
        content, tool_calls, plan_stats = await self._stream_request(messages)
        metrics.phase_planning_ms = plan_stats.total_ms
        
        messages.append(self._create_assistant_msg(content, tool_calls))

        if tool_calls:
            # Phase 2: Execution
            t_exec_start = now_ms()
            need_retry = await self._execute_tools(tool_calls, messages)
            metrics.phase_tool_exec_ms = now_ms() - t_exec_start

            # Phase 3: Processing
            _, _, proc_stats = await self._stream_request(messages)
            
            metrics.phase_processing_ms = proc_stats.total_ms
            metrics.proc_ttft_ms = proc_stats.ttft_ms
            metrics.proc_prefill_tps = proc_stats.tps_prefill
            metrics.proc_decode_tps = proc_stats.tps_decode
            metrics.proc_tokens_in = proc_stats.tokens_in
            metrics.proc_tokens_out = proc_stats.tokens_out

        metrics.total_latency_ms = now_ms() - start_time
        return metrics, need_retry

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

    async def _execute_tools(self, tool_calls, messages):
        needs_retry = False
        for call in tool_calls:
            tool = next((t for t in self.all_tools if t.name == call.function.name), None)
            try:
                args = json.loads(call.function.arguments)
                result = await tool.ainvoke(args)
                if 'error' in result:
                    logger.warning(f"Caught null parameter error for {call.function.name} with arguments {args}. Triggering retry.")
                    needs_retry = True

            except Exception as e:
                result = str(e)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(result)})
        return needs_retry

    def _create_assistant_msg(self, content, tool_calls):
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} 
                for tc in tool_calls
            ]
        return msg