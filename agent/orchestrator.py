"""
SFG_AGENT 核心 Agent Loop v2.0
- 结构化错误分类 + Decorrelated Jitter 重试
- token 计数 → context_engine.update_from_response
- context_overflow 自动触发压缩再重试
- Preflight 安全预检（GM 指令注入检测）
- Skillify 异步触发（对话结束后）
- Curator 惰性触发检查
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Optional

import anthropic

from agent.context_engine import ContextEngine
from agent.error_classifier import ClassifiedError, FailoverReason, classify_error
from agent.memory_manager import MemoryManager
from agent.prompt_builder import MessageContext, PromptBuilder
from agent.retry_utils import jittered_backoff
from agent.team_manager import TeamManager
from agent.thinking import ThinkingManager
from handlers.send_policy import SendPolicy, preflight_check
from tools.registry import ToolRegistry

logger = logging.getLogger("agent.orchestrator")

_MAX_ITERATIONS = 20    # 单次对话最多工具调用轮次
_MAX_RETRIES = 3        # API 错误最大重试次数


class Orchestrator:
    def __init__(
        self,
        config,
        session_store,
        memory_manager: MemoryManager,
        prompt_builder: PromptBuilder,
        registry: ToolRegistry,
        thinking: ThinkingManager,
        context_engine: ContextEngine,
        send_policy: Optional[SendPolicy] = None,
        skillify=None,
        curator=None,
        team_manager=None,
        dashboard=None,
    ):
        self.config = config
        self.session_store = session_store
        self.memory_mgr = memory_manager
        self.prompt_builder = prompt_builder
        # 优先使用外部传入的 TeamManager（已加载 provider 关键词），否则创建默认实例
        self.team_manager = team_manager if team_manager is not None else TeamManager(registry=registry)
        self.registry = registry
        self.thinking = thinking
        self.context_engine = context_engine
        self.send_policy = send_policy
        self.skillify = skillify
        self.curator = curator
        self.dashboard = dashboard

        self.client = anthropic.AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_AUTH_TOKEN") or config.anthropic.api_key,
            base_url=os.getenv("ANTHROPIC_BASE_URL") or config.anthropic.base_url,
        )
        self.model: str = config.anthropic.model
        self.max_tokens: int = config.anthropic.max_tokens
        self._token_budget: int = getattr(config.anthropic, "token_budget", 200_000)
        self._last_activity_at: float = time.time()

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    async def run(self, user_input: str, ctx: MessageContext) -> str:
        t0 = time.monotonic()
        self._last_activity_at = time.time()

        # dashboard：请求开始
        if self.dashboard:
            self.dashboard.on_request(ctx.chat_id, ctx.group_name, ctx.sender_name, user_input)

        # ① 记忆预取（三层命名空间）
        memory_ctx = await self.memory_mgr.prefetch(
            user_input, group_id=ctx.chat_id, user_id=ctx.sender_id
        )
        self.memory_mgr.reset_scrubber()

        # ② TeamManager 快速路由（0 token，关键词匹配）
        routing = self.team_manager.route(user_input)

        # ③ 构建 system prompt（含聚焦提示 + prompt caching）
        system = self.prompt_builder.build(ctx, memory_ctx, focus_hint=routing.focus_hint)

        # ④ thinking level（adaptive thinking kwargs）
        thinking_kwargs = self.thinking.get_api_kwargs(ctx.chat_id)

        # ⑤ 加载对话历史
        history = await self.session_store.get_history(ctx.chat_id)
        history.append({"role": "user", "content": user_input})

        # ⑥ Agent loop（含重试）——按路由结果过滤工具集
        all_tools = self.registry.get_for_role(ctx.role)
        tools = self.team_manager.filter_tools(all_tools, routing.allowed_tool_prefixes)
        if len(tools) < len(all_tools):
            logger.debug(
                "TeamManager [%s] 工具集 %d→%d（confidence=%.2f）",
                routing.domain, len(all_tools), len(tools), routing.confidence,
            )
        final_text = ""

        try:
            final_text = await self._agent_loop(
                history, system, tools, thinking_kwargs, ctx, t0
            )
        except Exception as exc:
            err = classify_error(exc)
            logger.error("Agent loop 最终失败：%s reason=%s", exc, err.reason.value)
            if self.dashboard:
                self.dashboard.on_error(ctx.chat_id, str(exc)[:80])
            return f"抱歉，出现错误：{_friendly_error(err)}"

        # ⑥ 轨迹压缩（turn 结束后，超阈值则压缩）
        if self.context_engine.should_compress(history, self._token_budget):
            await self.memory_mgr.on_pre_compress(ctx.chat_id)
            history = await self.context_engine.compress(history, self.client, model=self.model)

        # ⑦ 持久化
        await self.session_store.save_history(ctx.chat_id, history)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        await self.session_store.record_stat(ctx.chat_id, elapsed_ms)

        # dashboard：请求完成
        if self.dashboard:
            tokens = getattr(self.context_engine, "_prompt_tokens", 0)
            self.dashboard.on_done(ctx.chat_id, final_text, tokens, elapsed_ms)

        # ⑧ 异步任务（不阻塞回复）
        asyncio.create_task(self._post_turn(user_input, final_text, ctx))

        logger.info(
            "[%s][%s] %d字 %.0fms",
            ctx.group_name, ctx.sender_name, len(final_text), elapsed_ms,
        )
        return final_text

    # ──────────────────────────────────────────────
    # Agent Loop（含 jitter 重试 + 错误分类）
    # ──────────────────────────────────────────────

    async def _agent_loop(
        self,
        history: list,
        system: str | list,
        tools: list,
        thinking_kwargs: dict,
        ctx: MessageContext,
        t0: float,
    ) -> str:
        final_text = ""
        iteration_count = 0
        error_count = 0
        all_tool_names: list[str] = []

        for iteration in range(_MAX_ITERATIONS):
            # 创新④：auto thinking 升级（多轮或多错时升 high）
            adjusted = self.thinking.auto_adjust(ctx.chat_id, iteration, error_count)
            if adjusted:
                thinking_kwargs = self.thinking.get_api_kwargs(ctx.chat_id)

            try:
                resp = await self._create_with_retry(history, system, tools, thinking_kwargs)
            except Exception:
                error_count += 1
                raise

            if hasattr(resp, "usage"):
                self.context_engine.update_from_response(resp.usage)

            history.append({"role": "assistant", "content": self._content_to_list(resp.content)})

            if resp.stop_reason == "end_turn" or resp.stop_reason != "tool_use":
                final_text = self._extract_text(resp.content)
                break

            # 记录工具调用（创新⑤热力图）
            turn_tools = [b.name for b in resp.content if hasattr(b, "type") and b.type == "tool_use"]
            all_tool_names.extend(turn_tools)

            tool_results = await self._execute_tools(resp.content, ctx.role, ctx.chat_id)
            history.append({"role": "user", "content": tool_results})
            iteration_count = iteration + 1

        # 存储本轮工具组合（供 Skillify 热力图判断）
        if self.skillify and all_tool_names:
            ctx._tool_names_this_turn = all_tool_names

        return final_text

    async def _create_with_retry(
        self,
        history: list,
        system: str | list,
        tools: list,
        thinking_kwargs: dict,
    ):
        """带 Jitter 重试的 API 调用，context_overflow 自动触发压缩。"""
        for attempt in range(_MAX_RETRIES):
            try:
                kwargs: dict[str, Any] = dict(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=history,
                )
                if tools:
                    kwargs["tools"] = tools
                if thinking_kwargs:
                    kwargs.update(thinking_kwargs)  # thinking + output_config

                return await self.client.messages.create(**kwargs)

            except Exception as exc:
                err = classify_error(exc)
                is_last = attempt == _MAX_RETRIES - 1

                logger.warning(
                    "API 错误 attempt=%d reason=%s retryable=%s: %s",
                    attempt, err.reason.value, err.retryable, exc,
                )

                if not err.retryable or is_last:
                    raise

                # context_overflow → 先压缩历史再重试
                if err.should_compress:
                    logger.info("context overflow，触发紧急压缩")
                    compressed = await self.context_engine.compress(history, self.client, model=self.model)
                    history.clear()
                    history.extend(compressed)

                # Jitter 退避
                delay = max(err.retry_after_s, jittered_backoff(attempt))
                logger.info("等待 %.1fs 后重试...", delay)
                await asyncio.sleep(delay)

        raise RuntimeError("超过最大重试次数")

    # ──────────────────────────────────────────────
    # 工具执行（Preflight + 并发）
    # ──────────────────────────────────────────────

    async def _execute_tools(self, content: list, role: str,
                              chat_id: str = "") -> list[dict]:
        tasks = []
        tool_use_ids = []

        for block in content:
            if hasattr(block, "type") and block.type == "tool_use":
                # Preflight 安全检查
                safe, reason = preflight_check(block.name, block.input)
                if not safe:
                    logger.warning("Preflight 拦截 %s: %s", block.name, reason)
                    tool_use_ids.append(block.id)
                    _r = reason
                    tasks.append(_blocked_tool(_r))
                    continue
                tool_use_ids.append(block.id)
                tasks.append(self._call_tool(block.name, block.input, role, chat_id))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_results = []
        for tid, result in zip(tool_use_ids, results):
            if isinstance(result, Exception):
                content_str = json.dumps({"error": str(result)}, ensure_ascii=False)
            else:
                content_str = (
                    json.dumps(result, ensure_ascii=False)
                    if not isinstance(result, str)
                    else result
                )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tid,
                "content": content_str,
            })

        return tool_results

    async def _call_tool(self, name: str, inputs: dict, role: str,
                         chat_id: str = "") -> Any:
        entry = self.registry._tools.get(name)
        if entry is None:
            return {"error": f"工具 {name!r} 未找到"}
        if entry.allowed_roles and role not in entry.allowed_roles:
            return {"error": f"权限不足：{role} 无法使用工具 {name}"}

        logger.debug("调用工具：%s %s", name, json.dumps(inputs, ensure_ascii=False)[:80])
        t0 = time.monotonic()
        result = await self.registry.dispatch(name, inputs)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if self.dashboard and chat_id:
            self.dashboard.on_tool(chat_id, name, elapsed_ms)
        return result

    # ──────────────────────────────────────────────
    # 对话后异步任务
    # ──────────────────────────────────────────────

    async def _post_turn(self, user_msg: str, assistant_msg: str, ctx: MessageContext) -> None:
        """记忆同步 + Skillify热力图 + Curator（不阻塞回复）"""
        try:
            await self.memory_mgr.sync_turn(
                user_msg, assistant_msg,
                group_id=ctx.chat_id, user_id=ctx.sender_id,
            )
        except Exception as e:
            logger.debug("记忆同步失败：%s", e)

        if self.skillify:
            try:
                tool_names = getattr(ctx, "_tool_names_this_turn", [])
                # 写入跨群热力图（GroupHeatmap）
                if tool_names:
                    await self.skillify.record_cross_group(tool_names, group_id=ctx.chat_id)
                # 单群热力图检查：达阈值触发 Skillify
                should_learn = self.skillify.record_tool_combo(tool_names, group_id=ctx.chat_id)
                if should_learn:
                    await self.skillify.maybe_skillify(
                        user_msg, assistant_msg, source_label=ctx.group_name
                    )
            except Exception as e:
                logger.debug("Skillify 失败：%s", e)

        if self.curator:
            try:
                await self.curator.tick(self._last_activity_at)
            except Exception as e:
                logger.debug("Curator tick 失败：%s", e)

    # ──────────────────────────────────────────────
    # 工具函数
    # ──────────────────────────────────────────────

    @staticmethod
    def _extract_text(content) -> str:
        if isinstance(content, str):
            return content
        texts = []
        for block in content:
            if hasattr(block, "type"):
                if block.type == "text":
                    texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts).strip()

    @staticmethod
    def _content_to_list(content) -> list:
        if isinstance(content, list):
            result = []
            for block in content:
                if hasattr(block, "model_dump"):
                    result.append(block.model_dump())
                elif isinstance(block, dict):
                    result.append(block)
            return result
        return []


async def _blocked_tool(reason: str) -> dict:
    return {"error": f"安全检查未通过：{reason}"}


def _friendly_error(err: ClassifiedError) -> str:
    messages = {
        FailoverReason.rate_limit: "请求频率超限，请稍后再试",
        FailoverReason.overloaded: "服务繁忙，请稍后再试",
        FailoverReason.context_overflow: "对话历史过长，请使用 /compact 压缩",
        FailoverReason.auth: "API 认证失败，请联系管理员",
        FailoverReason.billing: "API 额度不足，请联系管理员",
        FailoverReason.timeout: "请求超时，请重试",
    }
    return messages.get(err.reason, f"内部错误（{err.reason.value}）")
