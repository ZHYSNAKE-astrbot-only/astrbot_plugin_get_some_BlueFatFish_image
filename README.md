# 来点蓝色大肥鱼（AstrBot 插件）

`/来点蓝色大肥鱼 x` 从**图库仓库**随机发吊图,"x"表示来几张图；
`/鱼图投稿` 把图传到**待审仓库**,然后由管理员审核后加入到**图库仓库**。


## 配置（WebUI → 插件 → 来点蓝色大肥鱼）

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `gallery_repo` | `ZHYSNAKE/plugin_only` | 图库仓库 `owner/repo` |
| `staging_repo` | `ZHYSNAKE/plugin-only-would` | 待审仓库 `owner/repo` |
| `staging_token` | `******` | 待审仓库专用令牌 |

TODO：
把图分类，并且能够发送特定标签的图
ai审核
ai找图
