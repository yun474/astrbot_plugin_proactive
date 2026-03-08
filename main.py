from __future__ import annotations

import random
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig


PROACTIVE_SYSTEM_INJECT = """【系统提示】
你现在是主动插入一段群聊对话，并非被用户直接@或呼叫。
你可以选择：
1. 正常回复（直接输出你想说的话）
2. 用表情回应而不发文字（调用 proactive_react_emoji 工具，传入一个合适的 QQ 表情 ID）
3. 完全保持沉默（调用 proactive_react_emoji 工具，emoji_id 传入 -1 表示不作任何回应）

请根据当前聊天内容的语境自然判断。如果话题与你无关、氛围不合适、或者你觉得插嘴会显得突兀，请选择表情或沉默。
不要强行凑话题，保持真实自然的群友感。"""


# 常用 QQ 表情 ID 参考（部分）：
# 14=微笑 21=可爱 76=委屈 178=666 182=好的 212=打脸 277=裂开
# 完整列表：https://bot.q.qq.com/wiki/develop/api-v2/openapi/emoji/model.html


@register(
    "proactive",
    "hwl",
    "概率触发主动插入群聊，AI 可自主决定是否发言或贴表情",
    "1.0.0",
)
class ProactivePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    # ------------------------------------------------------------------ #
    # LLM 工具：让 AI 用表情回应或保持沉默
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="proactive_react_emoji")
    async def proactive_react_emoji(
        self,
        event: AstrMessageEvent,
        emoji_id: int,
    ):
        """在主动插入群聊时，选择用表情回应而不发文字，或选择完全沉默。

        Args:
            emoji_id(number): QQ 表情 ID（整数）。传入 -1 表示完全沉默不作任何回应。
                常用参考：14=微笑, 21=可爱, 76=委屈, 178=666, 182=好的, 212=打脸, 277=裂开
        """
        if emoji_id == -1:
            logger.info("[proactive] AI 选择沉默，不回复也不贴表情")
            event.stop_event()
            return "已选择沉默。"

        # 用 aiocqhttp 直接调 set_msg_emoji_like
        try:
            if event.get_platform_name() == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                assert isinstance(event, AiocqhttpMessageEvent)
                client = event.bot
                await client.api.call_action(
                    "set_msg_emoji_like",
                    message_id=event.message_obj.message_id,
                    emoji_id=str(emoji_id),
                )
                logger.info(f"[proactive] AI 选择贴表情 emoji_id={emoji_id}")
            else:
                logger.info(
                    f"[proactive] 非 aiocqhttp 平台，无法贴表情，emoji_id={emoji_id}"
                )
        except Exception as e:
            logger.warning(f"[proactive] 贴表情失败: {e}")

        # 贴完表情后不需要再发文字，停止后续流程
        event.stop_event()
        return f"已贴表情 emoji_id={emoji_id}。"

    # ------------------------------------------------------------------ #
    # 监听所有群消息，按概率触发
    # ------------------------------------------------------------------ #
    def _check_group_allowed(self, group_id: str) -> bool:
        """检查群是否在白/黑名单范围内。"""
        whitelist = [str(g).strip() for g in (self.config.get("group_whitelist") or []) if str(g).strip()]
        blacklist = [str(g).strip() for g in (self.config.get("group_blacklist") or []) if str(g).strip()]

        if whitelist:
            return group_id in whitelist
        if blacklist:
            return group_id not in blacklist
        return True

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，按概率决定是否主动插入回复。"""
        group_id = str(event.message_obj.group_id or "")
        if not self._check_group_allowed(group_id):
            return

        prob: float = float(self.config.get("reply_probability", 0.1))
        if prob <= 0:
            return

        if random.random() > prob:
            return

        ai_judge: bool = bool(self.config.get("ai_judge_enabled", True))

        provider = self.context.get_using_provider()
        if provider is None:
            logger.debug("[proactive] 没有可用的 LLM provider，跳过主动回复")
            return

        logger.info(
            f"[proactive] 触发主动回复 (概率={prob}, ai_judge={ai_judge})"
        )

        if ai_judge:
            # 注入系统提示 + 开放表情/沉默工具，让 AI 自主决定
            func_tools_mgr = self.context.get_llm_tool_manager()
            yield event.request_llm(
                prompt=event.message_str,
                func_tool_manager=func_tools_mgr,
                system_prompt=PROACTIVE_SYSTEM_INJECT,
                image_urls=[],
            )
        else:
            # 纯概率触发，直接让 AI 回复，注入简短提示让语气自然
            simple_inject = (
                "你现在主动插入一段群聊，请根据语境自然地回应，不要强行凑话题。"
            )
            yield event.request_llm(
                prompt=event.message_str,
                system_prompt=simple_inject,
                image_urls=[],
            )

    async def terminate(self):
        pass
