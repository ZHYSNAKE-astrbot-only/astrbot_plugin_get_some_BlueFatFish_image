# -*- coding: utf-8 -*-
"""AstrBot 插件「来点蓝色大肥鱼」—— 云端图库版。

- 图库仓库（`gallery_repo`）：插件**只读**，从中按需取图发消息。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import sys
import threading

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from blue_fish_gallery import CloudGallery  # noqa: E402


@register(
    "来点蓝色大肥鱼",
    "ZHYSNAKE",
    "云端图库版：从 Gitee 图库仓库按需取图发送，插件对仓库只读",
    "2.5.8",
)
class BlueFatFishPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.gallery = CloudGallery(_PLUGIN_DIR, self.config, logger)
        logger.info(
            f"[鱼图] 云端图库就绪：索引 {self.gallery.stats()['total']} 张，"
            "图片本体不落盘（原 image/ 整包下载已移除）"
        )
        # 内置清单已可用，同步放后台，网络不通也不影响插件启动
        threading.Thread(target=self._startup_sync, daemon=True).start()

    # ------------------------------------------------------------ 小工具
    def cfg(self, key: str, default=None):
        value = self.config.get(key, default)
        return default if value is None else value

    def _startup_sync(self):
        try:
            result = self.gallery.refresh()
            if not result.get("skipped"):
                logger.info(f"[鱼图] 云端同步完成：{result}")
        except Exception as e:
            logger.warning(f"[鱼图] 后台同步失败（不影响使用内置清单）：{e}")

    def _parse_count(self, raw) -> int:
        default = 1
        limit = 10
        text = str(raw).strip() if raw is not None else ""
        try:
            n = int(text) if text else default
        except ValueError:
            n = default
        return max(1, min(n, limit))

    def _parse_image_args(self, raw) -> tuple[int, str]:
        """解析「/来点蓝色大肥鱼 3 #搞笑」这类参数，返回 (张数, 标签)。"""
        text = str(raw or "").strip()
        tags = re.findall(r"#([^\s#]+)", text)
        rest = re.sub(r"#[^\s#]+", " ", text)
        nums = re.findall(r"\d+", rest)
        default = 1
        limit = 10
        n = int(nums[0]) if nums else default
        return max(1, min(n, limit)), (tags[0] if tags else "")

    # ------------------------------------------------------------ 发图（只读发图仓库）
    async def _build_components(self, entries: list[str]) -> list:
        """服务端按需取图 -> 内存 -> 图片组件（base64），协议端不需要能访问图床。"""
        results = await asyncio.gather(
            *(asyncio.to_thread(self.gallery.fetch_entry, e) for e in entries),
            return_exceptions=True,
        )
        comps = []
        for entry, result in zip(entries, results):
            if isinstance(result, BaseException):
                logger.warning(f"[鱼图] 取图失败：{entry} -> {result}")
                continue
            data, _ = result
            try:
                comps.append(Comp.Image.fromBytes(data))
            except AttributeError:  # 兼容没有 fromBytes 的旧版 AstrBot
                comps.append(Comp.Image.fromBase64(base64.b64encode(data).decode()))
        return comps

    async def _send_images(self, event: AstrMessageEvent, count, tag: str = "") -> int:
        n = self._parse_count(count)
        entries = self.gallery.pick(n, tag)
        if not entries:
            return 0
        comps = await self._build_components(entries)
        if not comps:
            return 0
        chain = MessageChain(chain=comps)
        try:
            # 官方推荐的主动消息通道
            await self.context.send_message(event.unified_msg_origin, chain)
        except Exception as e:
            logger.warning(f"[鱼图] send_message 不可用，回退 event.send：{e}")
            await event.send(chain)
        return len(comps)

    # ------------------------------------------------------------ 指令
    @filter.command("来点蓝色大肥鱼")
    async def get_some_image(self, event: AstrMessageEvent, args: GreedyStr):
        """从发图仓库随机发图。用法：/来点蓝色大肥鱼 [张数] [#标签]"""
        n, tag = self._parse_image_args(args)
        entries = self.gallery.pick(n, tag)
        if not entries:
            if tag:
                yield event.plain_result(
                    f"没有带 #{tag} 标签的图，换个标签，或直接发 /来点蓝色大肥鱼～"
                )
            else:
                yield event.plain_result(
                    "发图图库暂时是空的～ 先在插件配置里填好图库仓库地址（gallery_repo，格式 owner/repo），重载插件后再试"
                )
            return
        comps = await self._build_components(entries)
        if not comps:
            yield event.plain_result(
                "图片没取到（可能是网络问题或图库地址填错）。检查一下 gallery_repo 配置～"
            )
            return
        yield event.chain_result(comps)

    # ------------------------------------------------------------ LLM 工具（AI 自主发图）
    @filter.llm_tool(name="send_blue_fat_fish_image")
    async def tool_send_image(self, event: AstrMessageEvent, count: int = 1, tag: str = ""):
        """发送蓝色大肥鱼吊图。当用户想看吊图、表情包、想被逗一下、或要求发几张鱼图时调用，会直接把图片发给用户。

        Args:
            count(number): 要发送的图片张数，默认 1，最多 5
            tag(string): 可选，只发带这个标签的图片，如「搞笑」「蓝色大肥鱼」；留空表示不限标签
        """
        sent = await self._send_images(event, count, tag)
        if not sent:
            return (
                "图库里没有带这个标签的图片，没能发出图片，请如实告诉用户。"
                if tag else "图库暂时是空的或取图失败，没能发出图片，请如实告诉用户。"
            )
        return f"已向用户发送 {sent} 张蓝色大肥鱼吊图，请简短地接话，不要重复描述图片。"
