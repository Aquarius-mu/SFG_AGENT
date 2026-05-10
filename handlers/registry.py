"""
Handler 分发表
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger("handlers.registry")

_handlers: dict[str, Callable] = {}


def register(name: str):
    def decorator(fn: Callable) -> Callable:
        _handlers[name] = fn
        return fn
    return decorator


def get(name: str) -> Callable | None:
    return _handlers.get(name)
