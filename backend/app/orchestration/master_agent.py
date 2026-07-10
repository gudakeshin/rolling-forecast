"""Master Agent -- ReACT agent loop powered by Claude via LangGraph."""

import json
import logging
from typing import Any, AsyncIterator

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent

from sqlalchemy.orm import Session
from app.config import settings
from app.orchestration.context_manager import ContextManager
from app.domain.registry import get_registry
from app.domain.base_skill import SkillContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Rolling Forecast Assistant, an AI-powered FP&A agent that helps finance teams generate, manage, and analyze rolling forecasts.

## Your Capabilities
You have access to specialized skills (tools) for:
- **Forecast Planning:** Analyze data quality and run model comparisons (ARIMA, Prophet, ETS, Linear) BEFORE generating. Shows MAPE scores so the user can choose.
- **Forecast Generation:** Generate statistical baseline forecasts. Supports auto-selection (best model per line item via MAPE comparison) or user-chosen models. Confidence scoring is automatic.
- **Confidence Scoring:** Re-score confidence with custom thresholds (only needed if the user wants to change thresholds — generation includes default scoring).
- **Version Management:** Create, list, compare, and manage immutable forecast versions
- **Overrides:** Apply human overrides to forecast values with dependency recalculation
- **Review:** AI-powered triage to identify items needing attention
- **Comparison:** Compare forecasts with variance analysis and bridge charts
- **Querying:** Answer questions about forecast data, accuracy, and trends

## CRITICAL WORKFLOW: Forecast Generation

When the user asks to generate, run, or create a forecast, ALWAYS follow this two-step process:

### Step 1: Plan (use `plan_forecast` tool)
- Analyze the uploaded data
- Run ALL 4 algorithms (ARIMA, Prophet, ETS, Linear Trend) on a sample of line items
- Show the MAPE (Mean Absolute Percentage Error) comparison table
- Present the AI recommendation
- Ask the user which approach they prefer

### Step 2: Generate (use `generate_baseline` tool)
- Only after the user confirms their preference
- Use model_type="auto" by default (picks best model PER LINE ITEM)
- NEVER default to model_type="linear" — always use "auto" unless user explicitly asks for a specific model
- Pass models_to_test if the user wants to limit which algorithms are tested

**IMPORTANT:** Do NOT skip Step 1. Do NOT default model_type to "linear". The whole point is that each line item gets the BEST model based on MAPE testing.

**Exception:** If the user explicitly says "use Prophet" or "run ARIMA", skip planning and go straight to generate_baseline with that model_type.

## Current User Context
{user_context}

## Guidelines
1. Always explain what you're doing before invoking a skill
2. Present results conversationally with key highlights first, then offer detailed views
3. When showing numbers, use proper formatting (e.g., $12.3M, +3.2%, 87/100 confidence)
4. Flag items that need attention -- low confidence lines, material variances, missing data
5. For complex requests, break them into steps and execute sequentially
6. If a skill fails, explain what went wrong and suggest alternatives
7. When showing forecast data, always mention the version name and date
8. Respect the user's role -- don't allow actions they don't have permission for
9. After generating forecasts, highlight the confidence distribution, model comparison results, and any critical/warning items with specific remediation actions.
10. Use tables for structured data and mention when detailed views are available in the side panel
11. When critical items need action (override, manual input), proactively suggest specific next steps.
12. When showing model comparison results, explain what MAPE means and which models performed best and why.
13. When answering questions, check if relevant context from uploaded documents is available. Use the search_context tool to find information in the user's document library.
14. Use web_search for current market data, news, or external context. Use financial_lookup for stock prices, economic indicators, and company financials.
15. When a user shares a URL, use fetch_url to read and optionally index the content.
"""


class MasterAgent:
    """
    The central ReACT agent that orchestrates forecast operations.

    Uses LangGraph's prebuilt react agent with Claude as the reasoning engine.
    Skills from the registry are exposed as callable tools.
    """

    def __init__(self, context_manager: ContextManager, db: Session):
        self.context_manager = context_manager
        self.db = db
        self._agent = None
        self._pending_query: str = ""

    def _build_skill_context(self) -> SkillContext:
        """Build a SkillContext from the current state."""
        return SkillContext(
            db=self.db,
            context_manager=self.context_manager,
            user_id=self.context_manager.user_id,
            user_role=self.context_manager.user_role,
            conversation_id=self.context_manager.conversation_id,
            working_memory=dict(self.context_manager._working_memory),
            user=self.context_manager.user,
        )

    def _get_agent(self):
        """Build or return the LangGraph react agent."""
        if self._agent is not None:
            return self._agent

        # Initialize Claude LLM (omit temperature — deprecated on Claude Sonnet 5+)
        llm = ChatAnthropic(
            model=settings.anthropic_model,
            anthropic_api_key=settings.anthropic_api_key,
            max_tokens=4096,
            timeout=120.0,
            max_retries=2,
        )

        # Get tools from skills registry
        registry = get_registry()
        skill_context = self._build_skill_context()
        tools = registry.as_langchain_tools(skill_context)

        # Build the system prompt with optional document context
        system_message = SYSTEM_PROMPT.format(
            user_context=self.context_manager.get_system_context()
        )
        doc_context = self.context_manager.get_relevant_context(
            self._pending_query or "", top_k=3
        )
        if doc_context:
            system_message += (
                "\n\n## Relevant Context from Uploaded Documents\n"
                + doc_context
            )

        # Create the react agent using LangGraph
        self._agent = create_react_agent(
            model=llm,
            tools=tools,
            prompt=system_message,
        )

        return self._agent

    async def astream(self, user_message: str) -> AsyncIterator[dict[str, Any]]:
        """
        Process a user message and stream the response as events.

        Yields SSE events:
        - message_start: {conversation_id}
        - token: {text} for intermediate LLM tokens
        - content_block: {type, data} for each content block
        - tool_start: {tool_name, tool_input} when a skill is invoked
        - tool_end: {tool_name, tool_output} when a skill completes
        - thinking: {text} when the agent is reasoning (intermediate steps)
        - message_end: {content, content_blocks, tool_calls, panel_payload}
        - error: {error}
        """
        self._pending_query = user_message
        self._agent = None  # rebuild to inject fresh document context
        agent = self._get_agent()

        # Build chat history from context
        chat_history = self.context_manager.get_chat_history()
        messages = []
        for msg in chat_history[:-1]:  # Exclude the current message
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))

        # Add current message
        messages.append(HumanMessage(content=user_message))

        try:
            all_content_blocks = []
            all_tool_calls = []
            final_panel_payload = None
            final_text = ""
            streamed_tokens = []

            # Track pending tool calls from agent messages to capture inputs
            # Use tool_call_id as key for reliable matching
            pending_tool_inputs: dict[str, dict] = {}  # tool_call_id -> {name, args}
            pending_tool_inputs_by_name: dict[str, dict] = {}  # fallback by name

            # Track which tool_start events we've already emitted
            emitted_tool_starts: set[str] = set()

            # Stream the agent execution with recursion + wall-clock budget
            import asyncio

            from app.services.observability import get_langfuse_callback

            stream_budget_s = float(getattr(settings, "max_forecast_generation_minutes", 30)) * 60
            stream_budget_s = min(stream_budget_s, 180.0)  # chat turn cap

            run_config: dict[str, Any] = {"recursion_limit": 25}
            langfuse_cb = get_langfuse_callback()
            if langfuse_cb is not None:
                run_config["callbacks"] = [langfuse_cb]

            async def _consume():
                async for event in agent.astream(
                    {"messages": messages},
                    config=run_config,
                    stream_mode="updates",
                ):
                    yield event

            agen = _consume()
            deadline = asyncio.get_event_loop().time() + stream_budget_s

            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    yield {
                        "event": "error",
                        "data": {"error": "Agent streaming budget exceeded"},
                    }
                    break
                try:
                    event = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    yield {
                        "event": "error",
                        "data": {"error": "Agent streaming budget exceeded"},
                    }
                    break

                for node_name, node_output in event.items():
                    if node_name == "tools":
                        # Tool execution result events
                        tool_messages = node_output.get("messages", [])
                        for tool_msg in tool_messages:
                            tool_name = getattr(tool_msg, "name", "unknown")
                            tool_call_id = getattr(tool_msg, "tool_call_id", None)

                            # Retrieve the captured tool input — try by tool_call_id first, then by name
                            tool_input = {}
                            if tool_call_id and tool_call_id in pending_tool_inputs:
                                info = pending_tool_inputs.pop(tool_call_id)
                                tool_input = info.get("args", {})
                            elif tool_name in pending_tool_inputs_by_name:
                                tool_input = pending_tool_inputs_by_name.pop(tool_name, {})

                            # Emit tool_start if not already emitted
                            start_key = f"{tool_name}:{tool_call_id or ''}"
                            if start_key not in emitted_tool_starts:
                                yield {
                                    "event": "tool_start",
                                    "data": {
                                        "tool_name": tool_name,
                                        "tool_input": tool_input,
                                    },
                                }
                                emitted_tool_starts.add(start_key)

                            # Parse tool output
                            output_summary = ""
                            try:
                                content = tool_msg.content if hasattr(tool_msg, "content") else str(tool_msg)
                                obs_data = json.loads(content) if isinstance(content, str) else content
                                if isinstance(obs_data, dict):
                                    if obs_data.get("content_blocks"):
                                        for cb in obs_data["content_blocks"]:
                                            all_content_blocks.append(cb)
                                            yield {
                                                "event": "content_block",
                                                "data": cb,
                                            }
                                    if obs_data.get("panel_payload"):
                                        final_panel_payload = obs_data["panel_payload"]
                                    output_summary = obs_data.get("message", "")
                                    all_tool_calls.append({
                                        "tool": tool_name,
                                        "input": tool_input,
                                        "output_summary": output_summary,
                                    })
                                else:
                                    output_summary = str(obs_data)[:300]
                                    all_tool_calls.append({
                                        "tool": tool_name,
                                        "input": tool_input,
                                        "output_summary": output_summary,
                                    })
                            except (json.JSONDecodeError, TypeError):
                                raw = ""
                                try:
                                    raw = str(content)[:300]
                                except Exception:
                                    raw = "Parse error"
                                all_tool_calls.append({
                                    "tool": tool_name,
                                    "input": tool_input,
                                    "output_summary": raw,
                                })
                                output_summary = raw

                            yield {
                                "event": "tool_end",
                                "data": {
                                    "tool_name": tool_name,
                                    "success": True,
                                    "output_summary": output_summary,
                                },
                            }

                    elif node_name == "agent":
                        # Agent response -- may contain text and/or tool_calls
                        agent_messages = node_output.get("messages", [])
                        for agent_msg in agent_messages:
                            # Capture tool call inputs from the agent's decision
                            if hasattr(agent_msg, "tool_calls") and agent_msg.tool_calls:
                                for tc in agent_msg.tool_calls:
                                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                                    tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                    tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")

                                    # Store by both ID and name for reliable retrieval
                                    if tc_id:
                                        pending_tool_inputs[tc_id] = {"name": tc_name, "args": tc_args}
                                    pending_tool_inputs_by_name[tc_name] = tc_args

                                    # Emit tool_start early when the agent decides to call a tool
                                    start_key = f"{tc_name}:{tc_id}"
                                    if start_key not in emitted_tool_starts:
                                        yield {
                                            "event": "tool_start",
                                            "data": {
                                                "tool_name": tc_name,
                                                "tool_input": tc_args,
                                            },
                                        }
                                        emitted_tool_starts.add(start_key)

                            # Capture text content
                            if hasattr(agent_msg, "content"):
                                content = agent_msg.content
                                if isinstance(content, str) and content.strip():
                                    # Stream intermediate text tokens
                                    new_text = content
                                    if new_text != final_text:
                                        delta = new_text[len(final_text):] if new_text.startswith(final_text) else new_text
                                        if delta:
                                            streamed_tokens.append(delta)
                                            yield {
                                                "event": "token",
                                                "data": {"text": delta},
                                            }
                                        final_text = new_text
                                elif isinstance(content, list):
                                    # Handle mixed content (text blocks + tool use blocks)
                                    for block in content:
                                        if isinstance(block, dict) and block.get("type") == "text":
                                            text = block.get("text", "")
                                            if text.strip():
                                                streamed_tokens.append(text)
                                                yield {
                                                    "event": "token",
                                                    "data": {"text": text},
                                                }
                                                final_text += text
                                        elif isinstance(block, dict) and block.get("type") == "tool_use":
                                            # Capture tool use from mixed content blocks
                                            tc_name = block.get("name", "")
                                            tc_args = block.get("input", {})
                                            tc_id = block.get("id", "")
                                            if tc_id:
                                                pending_tool_inputs[tc_id] = {"name": tc_name, "args": tc_args}
                                            pending_tool_inputs_by_name[tc_name] = tc_args

                    else:
                        # Handle other node types (custom nodes, checkpoints, etc.)
                        logger.debug(f"Unhandled node in stream: {node_name}")

            # Add final text as content block
            if final_text:
                text_block = {"type": "text", "data": {"text": final_text}}
                all_content_blocks.insert(0, text_block)

                yield {
                    "event": "content_block",
                    "data": text_block,
                }

            # Send final message_end
            yield {
                "event": "message_end",
                "data": {
                    "content": final_text,
                    "content_blocks": all_content_blocks,
                    "tool_calls": all_tool_calls if all_tool_calls else None,
                    "panel_payload": final_panel_payload,
                },
            }

        except Exception as e:
            logger.error(f"Agent execution error: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": {"error": str(e)},
            }

    async def invoke(self, user_message: str) -> dict[str, Any]:
        """Non-streaming version -- runs the agent and returns the full response."""
        final_result = {}
        async for event in self.astream(user_message):
            if event["event"] == "message_end":
                final_result = event["data"]
            elif event["event"] == "error":
                final_result = {
                    "content": f"Error: {event['data']['error']}",
                    "content_blocks": [],
                }
        return final_result
