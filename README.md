# 来点蓝色大肥鱼（AstrBot 插件）

`/来点蓝色大肥鱼 x` 从**图库仓库**随机发吊图，"x"表示来几张图，不加x就是一张。


## 配置（WebUI → 插件 → 来点蓝色大肥鱼）

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `gallery_repo` | `ZHYSNAKE/plugin_only` | 图库仓库 `owner/repo` |

## 数据目录

随包只读清单 `gallery.json` 保留在插件目录；运行期持久化数据写在 AstrBot 规定的插件数据目录，便于迁移与审计：

```
data/plugin_data/astrbot_plugin_get_some_BlueFatFish_image/
├── gallery_cache.json   # 云端图库索引缓存
└── tags.json            # 图片标签
```

TODO：
把图分类，并且能够发送特定标签的图
ai找图
