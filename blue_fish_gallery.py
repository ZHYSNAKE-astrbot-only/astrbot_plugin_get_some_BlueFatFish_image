# -*- coding: utf-8 -*-
"""蓝色大肥鱼「云端图库」核心。

设计要点：
1. 插件目录只保存「图片直链索引」（gallery.json / gallery_cache.json，几 KB），
   不把整个图库下载解压到本地；
2. 发图时只按需取用户要的那几张，在内存里转成图片消息组件发出；
3. 图库来自：内置清单（gallery.json）+ Gitee 图库仓库（gallery_repo）索引；
4. 别人投稿只写到待审仓库（staging_repo），绝不写图库仓库，审核在 Gitee 网页做。
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
UA = "Mozilla/5.0 (compatible; AstrBotBlueFatFish/2.5; +https://gitee.com)"


def is_image_name(name: str) -> bool:
    """只看扩展名（允许带查询串，Gitee/GitHub 直链有时会带参数）。"""
    path = urllib.parse.urlparse(str(name)).path
    return path.lower().endswith(IMAGE_EXTS)


def guess_ext(data: bytes, content_type: str = "") -> str:
    ct = (content_type or "").lower()
    if "png" in ct or data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if "gif" in ct or data[:3] == b"GIF":
        return ".gif"
    if "webp" in ct or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return ".webp"
    return ".jpg"


class CloudGallery:
    """只管理「链接」的图库，不缓存图片本体。"""

    def __init__(self, plugin_dir: str, config: dict | None, log: Any):
        self.dir = plugin_dir
        self.config = config or {}
        self.log = log
        self._lock = threading.RLock()

        self.builtin_path = os.path.join(plugin_dir, "gallery.json")
        self.cache_path = os.path.join(plugin_dir, "gallery_cache.json")
        self.tags_path = os.path.join(plugin_dir, "tags.json")
        self.staging_local_dir = os.path.join(plugin_dir, "staging_local")

        self._tags: dict[str, list[str]] = {}
        self._builtin: list[str] = self._load_builtin()
        self._remote: list[str] = []
        self._local: list[str] = []
        self._last_sync = 0.0
        self._load_cache()
        self._load_tags()

    # ------------------------------------------------------------------ 配置
    def cfg(self, key: str, default: Any) -> Any:
        value = self.config.get(key, default)
        return default if value is None else value

    # ------------------------------------------------------------------ 索引加载
    def _load_builtin(self) -> list[str]:
        try:
            with open(self.builtin_path, "r", encoding="utf-8") as f:
                urls, tags = self._parse_manifest_ex(f.read(), self.builtin_path)
            if tags:
                self._merge_tags(tags)
            return urls
        except Exception as e:
            self.log.warning(f"[鱼图] 内置清单读取失败：{e}")
            return []

    # ------------------------------------------------------------------ 标签
    def _merge_tags(self, incoming: dict, persist: bool = False) -> None:
        """把清单里声明的标签并进内存索引。"""
        with self._lock:
            for entry, tags in (incoming or {}).items():
                if not tags:
                    continue
                merged = self._tags.setdefault(str(entry), [])
                for tag in tags:
                    tag = str(tag).strip().lstrip("#")
                    if tag and tag not in merged:
                        merged.append(tag)
            if persist:
                self._save_tags()

    def _load_tags(self) -> None:
        """载入用户手动标注 / 审核时自动生成的标签。"""
        try:
            with open(self.tags_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._merge_tags(data.get("items", data))
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log.warning(f"[鱼图] 标签读取失败：{e}")

    def _save_tags(self) -> None:
        try:
            tmp = self.tags_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"items": self._tags}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.tags_path)
        except Exception as e:
            self.log.warning(f"[鱼图] 标签写入失败：{e}")

    def set_tags(self, entry: str, tags: list[str]) -> None:
        """给某张图打标签并持久化。"""
        if entry and tags:
            self._merge_tags({entry: tags}, persist=True)

    def tags_for(self, entry: str) -> list[str]:
        with self._lock:
            return list(self._tags.get(entry, []))

    def tag_stats(self) -> dict[str, int]:
        """统计当前可用图片里各标签的张数。"""
        counts: dict[str, int] = {}
        with self._lock:
            for url in self.urls:
                for tag in set(self._tags.get(url, [])):
                    counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def _extract_tags(self, item: Any) -> list[str]:
        if not isinstance(item, dict):
            return []
        raw = item.get("tags") or item.get("labels") or []
        if isinstance(raw, str):
            return [x.strip().lstrip("#") for x in re.split(r"[,，#\s]+", raw) if x.strip()]
        if isinstance(raw, (list, tuple)):
            return [str(x).strip().lstrip("#") for x in raw if str(x).strip()]
        return []

    # ------------------------------------------------------------------ 缓存
    def _load_cache(self) -> None:
        """上次成功同步到的远端索引，离线时照样有图可用。"""
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._remote = [str(x) for x in data.get("remote", []) if isinstance(x, str)]
            self._last_sync = float(data.get("updated", 0))
        except (FileNotFoundError, ValueError, TypeError):
            pass
        except Exception as e:
            self.log.warning(f"[鱼图] 索引缓存读取失败：{e}")

    def _save_cache(self) -> None:
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"updated": self._last_sync, "remote": self._remote},
                    f, ensure_ascii=False, indent=2,
                )
            os.replace(tmp, self.cache_path)
        except Exception as e:
            self.log.warning(f"[鱼图] 索引缓存写入失败：{e}")

    # ------------------------------------------------------------------ manifest 解析
    def _normalize(self, item: Any, base_url: str) -> str | None:
        if isinstance(item, dict):
            # 注意：Gitee contents API 的条目同时带 url（API 端点）和 download_url（图片直链），
            # 只认图片直链，别把 API 端点当成图片。
            picked = None
            for key in ("download_url", "src", "url", "path", "name", "file"):
                val = item.get(key)
                if not val:
                    continue
                sval = str(val)
                if "api/v5/repos" in sval or "api.github.com" in sval:
                    continue
                picked = val
                break
            if picked is None:
                return None
            item = picked
        text = str(item).strip()
        if not text or text.startswith("#"):
            return None
        if text.startswith(("http://", "https://")):
            return text if is_image_name(text) else None
        if os.path.isabs(text) and os.path.isfile(text):
            return text
        if not base_url or not is_image_name(text):
            return None
        return base_url.rstrip("/") + "/" + urllib.parse.quote(text.lstrip("/"))

    def _parse_manifest(self, text: str, source: str = "") -> list[str]:
        """兼容只取链接的调用。"""
        return self._parse_manifest_ex(text, source)[0]

    def _parse_manifest_ex(self, text: str, source: str = "") -> tuple[list[str], dict[str, list[str]]]:
        """支持 4 种清单，并顺带解析标签。

        标签可以写在条目里：{"url": "a.jpg", "tags": ["搞笑", "鱼"]}
        也可以写顶层映射：{"tags": {"a.jpg": ["搞笑"]}}
        或顶层列表：{"tags": ["蓝色大肥鱼"]}（给整份清单的图都打上）。
        """
        base_url = ""
        raw_items: Any = None
        tag_map: dict[str, list[str]] = {}
        global_tags: list[str] = []

        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None

        if isinstance(data, dict):
            # GitHub git tree API
            if isinstance(data.get("tree"), list):
                m = re.search(r"repos/([^/]+)/([^/]+)/git/trees/([^?/]+)", source or "")
                if m:
                    base_url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/"
                raw_items = [x.get("path", "") for x in data["tree"] if isinstance(x, dict)]
            else:
                base_url = str(data.get("base_url", ""))
                raw_items = data.get("images") or data.get("urls") or data.get("list") or []
                raw_tags = data.get("tags")
                if isinstance(raw_tags, dict):
                    tag_map = {
                        str(k): self._extract_tags({"tags": v})
                        for k, v in raw_tags.items()
                    }
                elif isinstance(raw_tags, (list, tuple)):
                    global_tags = self._extract_tags({"tags": raw_tags})
        elif isinstance(data, list):
            # Gitee contents API 返回的是带 download_url 的对象数组
            raw_items = data
        else:
            raw_items = [line.strip() for line in text.splitlines() if line.strip()]

        if not isinstance(raw_items, (list, tuple)):
            return [], {}

        out: list[str] = []
        tags_out: dict[str, list[str]] = {}
        for item in raw_items:
            norm = self._normalize(item, base_url)
            if not norm:
                continue
            if norm not in out:
                out.append(norm)
            item_tags = self._extract_tags(item)
            if item_tags:
                tags_out.setdefault(norm, []).extend(item_tags)

        if global_tags:
            for norm in out:
                tags_out.setdefault(norm, []).extend(global_tags)
        for key, tags in tag_map.items():
            if not tags:
                continue
            for norm in out:
                if (norm == key or norm.endswith("/" + key.lstrip("/"))
                        or os.path.basename(norm) == os.path.basename(key)):
                    tags_out.setdefault(norm, []).extend(tags)

        return out, {k: list(dict.fromkeys(v)) for k, v in tags_out.items() if v}

    # ------------------------------------------------------------------ 云端同步（只拉图库仓库）
    def _repo_sources(self) -> list[str]:
        """图库仓库（gallery_repo，只读）的 Gitee contents API 索引地址。"""
        repo = str(self.cfg("gallery_repo", "") or "").strip()
        if not repo or "/" not in repo:
            return []
        parts = repo.split("/")
        owner, name = parts[0], parts[1]
        branch = parts[2] if len(parts) > 2 else "main"
        return [
            f"https://gitee.com/api/v5/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(name)}/contents/?ref={urllib.parse.quote(branch)}"
        ]

    def _opener(self):
        return urllib.request.build_opener()

    def _read_url(self, url: str, limit: int, timeout: int | None = None) -> tuple[bytes, str]:
        timeout = timeout or 15
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with self._opener().open(req, timeout=timeout) as resp:
            return resp.read(limit), resp.headers.get("Content-Type", "")

    def refresh(self, force: bool = False) -> dict:
        """从云端拉「索引」，不拉图片本体。失败时保留内置清单与上次缓存。"""
        interval = 6 * 3600
        with self._lock:
            if not force and time.time() - self._last_sync < interval:
                return {"skipped": True, "count": len(self.urls)}

        sources = self._repo_sources()
        merged: list[str] = []
        remote_tags: dict[str, list[str]] = {}
        ok, failed = 0, []
        for src in sources:
            try:
                payload, _ = self._read_url(src, 2 * 1024 * 1024)
                found, tags = self._parse_manifest_ex(payload.decode("utf-8", "ignore"), src)
                merged.extend(found)
                for key, values in tags.items():
                    remote_tags.setdefault(key, []).extend(values)
                ok += 1
                self.log.info(f"[鱼图] 云端图库同步成功：{src} -> {len(found)} 张")
            except Exception as e:
                failed.append(f"{src} ({e})")
                self.log.warning(f"[鱼图] 云端图库同步失败：{src} -> {e}")

        if not sources:
            return {"skipped": True, "count": len(self.urls)}

        if ok:
            with self._lock:
                # 远端索引整体替换，避免源里删掉的图永远留在列表里；
                # 内置清单里已有的链接不重复存缓存。
                builtin = set(self._builtin)
                synced = [u for u in dict.fromkeys(merged) if u not in builtin]
                self._remote = list(dict.fromkeys(synced))
                self._last_sync = time.time()
                self._save_cache()
                self._merge_tags(remote_tags)
        return {"ok": ok, "failed": failed, "remote": len(self._remote), "count": len(self.urls)}

    # ------------------------------------------------------------------ 取图
    @property
    def urls(self) -> list[str]:
        with self._lock:
            return list(dict.fromkeys(self._builtin + self._remote + self._local))

    def pick(self, count: int, tag: str = "") -> list[str]:
        pool = self.urls
        needle = str(tag or "").strip().lstrip("#").lower()
        if needle:
            with self._lock:
                pool = [
                    u for u in pool
                    if any(needle == t.lower() or needle in t.lower()
                           for t in self._tags.get(u, []))
                ]
        if not pool:
            return []
        return random.sample(pool, min(max(1, count), len(pool)))

    def fetch_entry(self, entry: str) -> tuple[bytes, str]:
        """按条目取图片字节。本地文件直接读，网络图片走 HTTP（内存，不落盘）。"""
        if not entry.startswith(("http://", "https://")):
            with open(entry, "rb") as f:
                data = f.read()
            return data, ""
        limit = 8 * 1024 * 1024
        data, ctype = self._read_url(entry, limit + 1)
        if len(data) > limit:
            raise ValueError("图片超过 8MB 上限，已跳过")
        if not data:
            raise ValueError("图片内容为空")
        return data, ctype

    # ------------------------------------------------------------------ 投稿兜底（本机）
    def save_staging_local(self, data: bytes, filename: str) -> str:
        """没配好待审仓库时的本地兜底：只写 staging_local/，**不进图库索引**。"""
        os.makedirs(self.staging_local_dir, exist_ok=True)
        path = os.path.join(self.staging_local_dir, filename)
        with open(path, "wb") as f:
            f.write(data)
        return path

    # ------------------------------------------------------------------ Gitee API 辅助
    def _gitee_post(self, api: str, fields: dict, token: str, timeout: int = 20) -> dict:
        fields = dict(fields)
        fields["access_token"] = token
        payload = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            api, data=payload,
            headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"},
        )
        with self._opener().open(req, timeout=timeout) as resp:
            body = resp.read(256 * 1024).decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except (ValueError, TypeError):
            return {"_raw": body}

    def _gitee_put_file(self, owner: str, name: str, path: str, data: bytes,
                        branch: str, token: str, message: str) -> dict:
        """向指定仓库/分支提交一个文件（用于待审仓库投稿）。"""
        api = (
            f"https://gitee.com/api/v5/repos/{urllib.parse.quote(owner)}/"
            f"{urllib.parse.quote(name)}/contents/{urllib.parse.quote(path)}"
        )
        r = self._gitee_post(api, {
            "content": base64.b64encode(data).decode(),
            "message": message,
            "branch": branch,
        }, token)
        if isinstance(r, dict) and r.get("message") and not r.get("content"):
            raise RuntimeError(f"Gitee 提交文件失败：{r.get('message')}")
        return r

    def upload_to_staging(self, data: bytes, filename: str) -> dict:
        """把别人投稿的图片传到「待审仓库」，返回可点开的 Gitee 文件页面。

        安全性：只写 staging_repo，且**不把直链登记进图库索引** —— 待审图片
        永远不会被插件发出去，也污染不了图库仓库。批准由管理员在 Gitee 上手动完成。
        """
        repo = str(self.cfg("staging_repo", "") or "").strip()
        token = str(self.cfg("staging_token", "") or "").strip()
        branch = "main"
        prefix = "inbox"
        if not token or "/" not in repo:
            raise RuntimeError("未配置 staging_repo / staging_token")
        owner, name = repo.split("/", 1)
        key = f"{prefix}/{filename}" if prefix else filename
        result = self._gitee_put_file(
            owner, name, key, data, branch, token,
            message=f"投稿 {filename} (AstrBot 蓝色大肥鱼插件)",
        )
        content = result.get("content", {}) if isinstance(result, dict) else {}
        if not isinstance(content, dict):
            content = {}
        page = content.get("html_url") or f"https://gitee.com/{repo}/blob/{branch}/{key}"
        direct = content.get("download_url") or f"https://gitee.com/{repo}/raw/{branch}/{key}"
        return {"page": page, "url": direct, "key": key}

    # ------------------------------------------------------------------ 状态
    def stats(self) -> dict:
        with self._lock:
            return {
                "builtin": len(self._builtin),
                "remote": len(self._remote),
                "local": len(self._local),
                "total": len(self.urls),
                "last_sync": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(self._last_sync)
                ) if self._last_sync else "尚未同步",
            }
