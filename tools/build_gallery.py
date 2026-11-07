# -*- coding: utf-8 -*-
"""把本地图片目录变成一份可分享的「云端图库清单」。

用途：你（或别人）把图片传到 Gitee / GitHub / 对象存储之后，用这个脚本生成
gallery 清单，把清单文件也放到同一个仓库，再把直链填进插件配置
gallery_sources，任何人的机器人就都能抽到这批图了 —— 图片本体始终在云上。

示例：
    python tools/build_gallery.py ./my_images \
        --base-url https://gitee.com/someone/fish_gallery/raw/main/ \
        -o my_gallery.json
    # 或者直接生成完整直链列表（不依赖 base_url）
    python tools/build_gallery.py ./my_images \
        --base-url https://example.com/img/ --full-url -o my_gallery.json
    # 给整份清单打共用标签，或用 JSON 文件逐图标注
    python tools/build_gallery.py ./my_images --base-url https://example.com/img/ \
        --tags 蓝色大肥鱼,搞笑 -o my_gallery.json
    python tools/build_gallery.py ./my_images --base-url https://example.com/img/ \
        --tag-map tags.json -o my_gallery.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
from urllib.parse import quote

EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _as_tags(value) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,，#\s]+", value)
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        return []
    return [str(x).strip().lstrip("#") for x in parts if str(x).strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="生成蓝色大肥鱼云端图库清单")
    ap.add_argument("folder", help="本地图片目录")
    ap.add_argument("--base-url", required=True, help="图片直链前缀，需以 / 结尾")
    ap.add_argument("-o", "--output", default="my_gallery.json", help="输出文件")
    ap.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    ap.add_argument("--full-url", action="store_true", help="把完整直链写进 images（而不是文件名）")
    ap.add_argument("--name", default="我的蓝色大肥鱼图库", help="图库名")
    ap.add_argument("--tags", default="", help="整份清单共用的标签，逗号分隔，如 搞笑,蓝色大肥鱼")
    ap.add_argument("--tag-map", default="", help='逐图标签 JSON 文件，形如 {"a.jpg": ["搞笑"]}')
    args = ap.parse_args()

    global_tags = _as_tags(args.tags)
    per_image: dict[str, list[str]] = {}
    if args.tag_map:
        try:
            with open(args.tag_map, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            raise SystemExit(f"读取 --tag-map 失败：{e}")
        if not isinstance(raw, dict):
            raise SystemExit("--tag-map 必须是 {文件名或相对路径: [标签]} 形式的 JSON 对象")
        for key, value in raw.items():
            tags = _as_tags(value)
            if tags:
                per_image[str(key).replace("\\", "/")] = tags

    base = args.base_url.rstrip("/") + "/"
    images: list = []
    for root, dirs, files in os.walk(args.folder):
        for name in sorted(files):
            if not name.lower().endswith(EXTS):
                continue
            rel = os.path.relpath(os.path.join(root, name), args.folder).replace("\\", "/")
            entry = base + quote(rel) if args.full_url else rel
            tags = per_image.get(rel) or per_image.get(name)
            images.append({"url": entry, "tags": tags} if tags else entry)
        if not args.recursive:
            dirs.clear()

    if not images:
        raise SystemExit("目录里没找到图片（支持 png/jpg/jpeg/gif/webp）")

    manifest = {
        "name": args.name,
        "version": 1,
        "source": args.base_url,
        "base_url": "" if args.full_url else base,
        "images": images,
    }
    if global_tags:
        manifest["tags"] = global_tags
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    tagged = sum(1 for x in images if isinstance(x, dict) and x.get("tags"))
    print(
        f"已生成 {args.output}：{len(images)} 张图"
        + (f"，其中 {tagged} 张带逐图标签" if tagged else "")
        + (f"，共用标签 {global_tags}" if global_tags else "")
        + "。清单只有索引，没有图片本体。"
    )
    print("把清单传到静态托管后，把地址填进插件配置的 gallery_sources 即可分享。")


if __name__ == "__main__":
    main()
