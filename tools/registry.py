"""
Hermes-style 工具自注册表 v3.0
- 插件式 Provider 自动发现（PROVIDER_META 驱动）
- TeamManager 关键词动态加载
- 角色权限过滤
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("tools.registry")


@dataclass
class ToolEntry:
    name: str
    description: str
    input_schema: dict
    handler: Callable
    is_deterministic: bool = False
    allowed_roles: list[str] = field(default_factory=list)


class ToolRegistry:
    _instance: Optional["ToolRegistry"] = None

    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}
        self._provider_metas: list[dict] = []   # 已加载的 PROVIDER_META 列表

    @classmethod
    def get(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = ToolRegistry()
        return cls._instance

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
        is_deterministic: bool = False,
        allowed_roles: list[str] | None = None,
    ):
        """装饰器：工具自注册"""
        def decorator(fn: Callable) -> Callable:
            self._tools[name] = ToolEntry(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=fn,
                is_deterministic=is_deterministic,
                allowed_roles=allowed_roles or [],
            )
            logger.debug("注册工具：%s (deterministic=%s)", name, is_deterministic)
            return fn
        return decorator

    # ─── 插件式 Provider 加载 ────────────────────────────────────────────────

    def load_provider(self, module_path: str, config=None, **context) -> bool:
        """
        从模块路径加载工具 Provider。
        1. import 模块
        2. 读取 PROVIDER_META
        3. 检查 requires_config_key（如 game.enabled）
        4. 调用 register_fn(self, **matching_kwargs)
        5. 保存 PROVIDER_META 供 TeamManager 使用

        Returns True 若成功加载。
        """
        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            logger.warning("Provider 模块加载失败 [%s]: %s", module_path, e)
            return False

        meta = getattr(mod, "PROVIDER_META", None)
        if not meta:
            logger.warning("模块 %s 缺少 PROVIDER_META，跳过", module_path)
            return False

        # 检查配置开关
        required_key = meta.get("requires_config_key")
        if required_key and config is not None:
            if not _get_config_val(config, required_key):
                logger.info("Provider [%s] 未启用（%s=false）", meta["name"], required_key)
                return False

        # 找到 register 函数
        register_fn_name = meta.get("register_fn", f"register_{meta['name']}")
        register_fn = getattr(mod, register_fn_name, None)
        if not register_fn or not callable(register_fn):
            logger.warning("Provider [%s] 找不到 %s 函数", meta["name"], register_fn_name)
            return False

        # 按函数签名筛选 kwargs（自动兼容不同参数需求）
        sig = inspect.signature(register_fn)
        valid_kwargs: dict[str, Any] = {}
        for param_name in sig.parameters:
            if param_name == "registry":
                continue
            if param_name in context:
                valid_kwargs[param_name] = context[param_name]

        try:
            register_fn(self, **valid_kwargs)
        except Exception as e:
            logger.error("Provider [%s] register 失败: %s", meta["name"], e)
            return False

        self._provider_metas.append(meta)
        logger.info("Provider [%s] 已加载（%s）", meta["name"], meta.get("description", ""))
        return True

    def get_provider_metas(self) -> list[dict]:
        """返回所有已加载的 PROVIDER_META（供 TeamManager 动态构建路由）。"""
        return list(self._provider_metas)

    # ─── 工具定义 + 调度 ─────────────────────────────────────────────────────

    def get_definitions(self, role: str = "admin") -> list[dict]:
        tools = []
        for entry in self._tools.values():
            if entry.allowed_roles and role not in entry.allowed_roles:
                continue
            tools.append({
                "name": entry.name,
                "description": entry.description,
                "input_schema": entry.input_schema,
            })
        return tools

    def get_for_role(self, role: str) -> list[dict]:
        return self.get_definitions(role=role)

    async def dispatch(self, name: str, inputs: dict[str, Any]) -> Any:
        entry = self._tools.get(name)
        if entry is None:
            return {"error": f"工具 {name!r} 不存在"}
        try:
            if asyncio.iscoroutinefunction(entry.handler):
                return await entry.handler(**inputs)
            return entry.handler(**inputs)
        except Exception as exc:
            logger.exception("工具 %s 执行失败", name)
            return {"error": str(exc)}

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())


# ─── 配置值读取（支持 dot-notation）─────────────────────────────────────────

def _get_config_val(config, key: str) -> Any:
    """从 config 对象中按 dot-notation 读取值，如 'game.enabled'。"""
    parts = key.split(".")
    obj = config
    for part in parts:
        if hasattr(obj, part):
            obj = getattr(obj, part)
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


# 模块级单例
registry = ToolRegistry.get()
