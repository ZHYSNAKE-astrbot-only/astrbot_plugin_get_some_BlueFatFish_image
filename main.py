# -*- coding: utf-8 -*-
"""AstrBot 插件「来点蓝色大肥鱼」—— 云端双仓库版。

- 图库仓库（`gallery_repo`）：插件**只读**，从中按需取图发消息。
- 待审仓库（`staging_repo`）：别人投稿时插件**只写这里**，绝不写图库仓库。
- 审核由管理员在 Gitee 网页上完成：看图 -> 把合格的图加入图库仓库。
  聊天里没有任何批准入口，所以即使别人是 AstrBot 管理员也无法污染图库。
- 对外只有两个指令：`/来点蓝色大肥鱼`、`/鱼图投稿`。
"""

from __future__ import annotations

import asyncio
import base64
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from blue_fish_gallery import CloudGallery, guess_ext  # noqa: E402


@register(
    "来点蓝色大肥鱼",
    "ZHYSNAKE",
    "云端双仓库版：图库仓库只读，别人投稿只进待审 Gitee 仓库（务必设为私有，防恶意投稿违规图导致账号被封），由管理员在 Gitee 上挑选",
    "2.5.6",
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

    # ------------------------------------------------------------ 指令（只有两个）
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

    @filter.command("鱼图投稿")
    async def submit_image(self, event: AstrMessageEvent):
        """把图片和指令一起发，图片直接传到待审 Gitee 仓库（绝不进图库仓库）。"""
        images = self._images_in_event(event)
        if not images:
            yield event.plain_result("把图片和 /鱼图投稿 一起发过来就行～")
            return

        total = min(len(images), 5)
        staging = str(self.cfg("staging_repo", "") or "").strip()
        token = str(self.cfg("staging_token", "") or "").strip()
        if not staging or not token:
            # 没配好也不丢图：只存本机投稿池，绝不写图库仓库
            yield event.plain_result(
                f"收到 {total} 张投稿。⚠️ 待审仓库地址（staging_repo）或令牌（staging_token）还没配好，"
                "这几张先临时存在本机 staging_local/，不会进入发图图库。"
            )
            for comp in images[:5]:
                try:
                    data, ctype = await self._component_bytes(comp)
                    filename = (
                        f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
                        f"{guess_ext(data, ctype)}"
                    )
                    await asyncio.to_thread(
                        self.gallery.save_staging_local, data, filename
                    )
                except Exception as e:
                    logger.warning(f"[鱼图] 本地暂存失败：{e}")
                    yield event.plain_result(f"这张投稿暂存失败：{e}")
            return

        yield event.plain_result(f"收到 {total} 张投稿，正在上传到待审仓库…")
        for comp in images[:5]:
            try:
                data, ctype = await self._component_bytes(comp)
                filename = (
                    f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
                    f"{guess_ext(data, ctype)}"
                )
                info = await asyncio.to_thread(
                    self.gallery.upload_to_staging, data, filename
                )
                yield event.plain_result(
                    f"已投稿到待审仓库：{info['page']}\n"
                    "管理员会在 Gitee 上挑选，合格的才加入发图仓库。"
                )
            except Exception as e:
                logger.warning(f"[鱼图] 投稿到待审仓库失败：{e}")
                yield event.plain_result(f"这张投稿失败：{e}")

    # ------------------------------------------------------------ 图片读取（投稿用）
    @staticmethod
    def _images_in_event(event: AstrMessageEvent) -> list:
        """从消息链里挑出图片组件，兼容不同 AstrBot 版本的组件类型表示。"""
        chain = getattr(getattr(event, "message_obj", None), "message", None) or []
        found = []
        for comp in chain:
            ctype = getattr(comp, "type", None)
            type_name = str(
                getattr(ctype, "value", None) or getattr(ctype, "name", None) or ctype or ""
            )
            if (
                type(comp).__name__ == "Image"
                or type_name == "Image"
                or (hasattr(comp, "convert_to_base64") and type_name in ("", "Image"))
            ):
                found.append(comp)
        return found

    async def _component_bytes(self, comp) -> tuple[bytes, str]:
        """把消息里的图片组件读成字节，兼容新旧版本 AstrBot。"""
        try:
            b64 = await comp.convert_to_base64()
            if b64:
                return base64.b64decode(b64), ""
        except Exception:
            pass

        for attr in ("file", "url", "path"):
            ref = str(getattr(comp, attr, "") or "").strip()
            if not ref:
                continue
            if ref.startswith("base64://"):
                return base64.b64decode(ref[9:]), ""
            if ref.startswith("data:") and ";base64," in ref:
                return base64.b64decode(ref.split(";base64,", 1)[1]), ""
            if ref.startswith("file://"):
                path = urllib.request.url2pathname(urllib.parse.urlparse(ref).path)
                if os.path.isfile(path):
                    with open(path, "rb") as f:
                        return f.read(), ""
            elif ref.startswith(("http://", "https://")):
                return await asyncio.to_thread(self.gallery.fetch_entry, ref)
            elif os.path.isfile(ref):
                with open(ref, "rb") as f:
                    return f.read(), ""
        raise ValueError("无法从图片消息中读出数据")

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
