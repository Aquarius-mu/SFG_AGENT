"""
飞书卡片构建器（Feishu Card Builder）
替代原 lark_sweet_bot.sh 中的 jq bash 函数，生成 schema 2.0 卡片 JSON
"""
from __future__ import annotations


def _truncate(s: str, max_len: int = 80) -> str:
    return s[:max_len] + "…" if len(s) > max_len else s


class CardBuilder:

    @staticmethod
    def thinking(sender_name: str, question: str) -> dict:
        return {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": f"💬 {sender_name} 问"},
                "template": "grey",
            },
            "config": {"streaming_mode": True},
            "body": {
                "elements": [
                    {"tag": "markdown", "content": f"> {_truncate(question)}"},
                    {"tag": "hr"},
                    {"tag": "markdown", "content": "⏳ **思考中…**"},
                ]
            },
        }

    @staticmethod
    def reply(sender_name: str, question: str, answer: str, duration: str) -> dict:
        question_short = _truncate(question, 120)

        if len(answer) > 1500:
            answer_elem = {
                "tag": "collapsible_panel",
                "expanded": True,
                "background_color": "grey-100",
                "header": {
                    "title": {"tag": "plain_text", "content": "📄 查看完整回答"},
                    "background_color": "blue-100",
                },
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": answer}}],
            }
        else:
            answer_elem = {"tag": "div", "text": {"tag": "lark_md", "content": answer}}

        return {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": f"💬 {sender_name} 问"},
                "template": "wathet",
            },
            "config": {"streaming_mode": False},
            "body": {
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"> {question_short}"}},
                    {"tag": "hr"},
                    answer_elem,
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"<font color=grey>:DONE: 耗时 **{duration}**</font>",
                        },
                    },
                ]
            },
        }

    @staticmethod
    def error(msg: str = "抱歉，我暂时无法回答，请稍后再试。", hint: str = "") -> dict:
        content = msg
        if hint:
            content += f"\n\n💡 _{hint}_"
        return {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "❌ 出错了"},
                "template": "red",
            },
            "config": {"streaming_mode": False},
            "body": {"elements": [{"tag": "markdown", "content": content}]},
        }

    @staticmethod
    def success(title: str, content: str) -> dict:
        return {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "green",
            },
            "config": {"streaming_mode": False},
            "body": {"elements": [{"tag": "markdown", "content": content}]},
        }

    @staticmethod
    def no_permission() -> dict:
        return {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "⛔ 权限不足"},
                "template": "red",
            },
            "config": {"streaming_mode": False},
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "抱歉，你没有使用该 Bot 的权限。\n请联系管理员申请。"}
                ]
            },
        }

    @staticmethod
    def help() -> dict:
        return {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "📖 使用帮助"},
                "template": "blue",
            },
            "config": {"streaming_mode": False},
            "body": {
                "elements": [
                    {"tag": "markdown", "content": "**可用指令**"},
                    {
                        "tag": "markdown",
                        "content": (
                            "| 指令 | 说明 |\n"
                            "|------|------|\n"
                            "| `/clear` | 清除会话历史，开启新对话 |\n"
                            "| `/think high` | 开启深度推理（复杂分析） |\n"
                            "| `/think low` | 关闭深度推理（普通对话） |\n"
                            "| `/compact` | 压缩过长的对话历史 |\n"
                            "| `/stats` | 查看本群对话统计 |\n"
                            "| `/help` | 显示本帮助 |"
                        ),
                    },
                    {"tag": "hr"},
                    {"tag": "markdown", "content": "直接发消息并 **@Bot** 即可对话。"},
                ]
            },
        }

    @staticmethod
    def stats(chat_id: str, total: int, today: int, avg_ms: int) -> dict:
        return {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "📊 对话统计"},
                "template": "blue",
            },
            "config": {"streaming_mode": False},
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            f"**本群统计**\n\n"
                            f"- 历史对话：**{total}** 条\n"
                            f"- 今日对话：**{today}** 条\n"
                            f"- 平均耗时：**{avg_ms}ms**"
                        ),
                    }
                ]
            },
        }
