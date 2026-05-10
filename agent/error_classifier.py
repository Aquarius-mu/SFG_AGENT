"""
Hermes-style 结构化 API 错误分类
FailoverReason 枚举 + ClassifiedError 决策数据类
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FailoverReason(Enum):
    auth = "auth"
    billing = "billing"
    rate_limit = "rate_limit"
    overloaded = "overloaded"
    server_error = "server_error"
    timeout = "timeout"
    context_overflow = "context_overflow"
    payload_too_large = "payload_too_large"
    format_error = "format_error"
    unknown = "unknown"


@dataclass
class ClassifiedError:
    reason: FailoverReason
    retryable: bool
    should_compress: bool = False
    should_rotate_credential: bool = False
    retry_after_s: float = 0.0
    original: Exception | None = field(default=None, repr=False)


def classify_error(exc: Exception) -> ClassifiedError:
    """
    10 级分类优先级（Hermes 模式）：
    1. HTTP 状态码
    2. 错误消息关键词
    3. 上下文溢出推断（大响应 + 断连）
    """
    msg = str(exc).lower()
    status = _extract_status(msg)

    # 401/403 → 认证问题，不重试
    if status in (401, 403) or any(k in msg for k in ("unauthorized", "forbidden", "invalid api key")):
        return ClassifiedError(FailoverReason.auth, retryable=False,
                               should_rotate_credential=True, original=exc)

    # 402 → 账单问题
    if status == 402 or "billing" in msg or "payment" in msg:
        # 区分临时配额（可重试）和永久耗尽
        transient = any(k in msg for k in ("resets at", "try again", "quota"))
        return ClassifiedError(FailoverReason.billing, retryable=transient, original=exc)

    # 429 → 限速，可重试
    if status == 429 or "rate_limit" in msg or "rate limit" in msg or "too many requests" in msg:
        return ClassifiedError(FailoverReason.rate_limit, retryable=True,
                               retry_after_s=60.0, original=exc)

    # 529 / overloaded
    if status == 529 or "overloaded" in msg:
        return ClassifiedError(FailoverReason.overloaded, retryable=True,
                               retry_after_s=30.0, original=exc)

    # context overflow
    if any(k in msg for k in ("context", "too long", "max_tokens", "prompt is too long")):
        return ClassifiedError(FailoverReason.context_overflow, retryable=True,
                               should_compress=True, original=exc)

    # payload too large
    if status == 413 or "payload" in msg or "request too large" in msg:
        return ClassifiedError(FailoverReason.payload_too_large, retryable=False, original=exc)

    # 5xx 服务器错误，短暂重试
    if status and 500 <= status < 600:
        return ClassifiedError(FailoverReason.server_error, retryable=True,
                               retry_after_s=10.0, original=exc)

    # 超时
    if any(k in msg for k in ("timeout", "timed out", "connection reset", "read timeout")):
        return ClassifiedError(FailoverReason.timeout, retryable=True, original=exc)

    return ClassifiedError(FailoverReason.unknown, retryable=False, original=exc)


def _extract_status(msg: str) -> int | None:
    import re
    m = re.search(r"\b([2-5]\d{2})\b", msg)
    return int(m.group(1)) if m else None
