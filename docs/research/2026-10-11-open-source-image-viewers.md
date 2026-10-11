# 开源「看图 / 图片库」服务调研报告

日期：2026-10-11 ｜ 快照口径：GitHub API star/推送时间均为 **2026-10-11 当日**

## TL;DR（一句话结论）

**不存在**单一开源项目能覆盖需求组合（Web+TUI+CLI 三端、规则化播放系统、用户组、评分/tag/艺术家、评论/笔记、加密、识图预留）。
最接近的是三类项目的拼图：**booru 系**（标签/评分/权限最强，但只有 Web、无播放规则、无加密）、**Hydrus**（桌面标签管理圣经，有幻灯片与 mpv 集成，但单机单用户、无服务化三端）、**Komga/Kavita**（漫画系列元数据最全，但无评分、无 booru 式标签播放）。
→ **自建 MVP（Pic Shelf）合理**，吸收上述项目的成熟概念（详见文末「借鉴清单」）。项目出发点——替代「mpv 组合命令」式临时流程——未被任何现成产品整体覆盖。

---

## 一、需求清单（调研对照基线）

| # | 需求 |
|---|---|
| R1 | 从目录导入，对图片/图片组编码（哈希、缩略图）以便快速检索 |
| R2 | 图片组（专辑 / pool / 系列） |
| R3 | 评分系统 |
| R4 | Tag 系统 |
| R5 | 作者 / 艺术家系统 |
| R6 | 漫画 / 系列类别 |
| R7 | 播放系统：定时、定量、轮播、随机、跨 album、按艺术家、默认尺寸、无障碍视觉 |
| R8 | Web 前端 + TUI 端 + CLI 端（后期 Desktop） |
| R9 | 用户组功能 |
| R10 | 可加密 |
| R11 | 评论 / 笔记、多用户共享库 |
| R12 | 本地模型识图（后期） |

## 二、候选总表（★ = star 数，快照 2026-10-11）

| 项目 | 类别 | ★ | 最近推送 | 许可 | 一句话定位 |
|---|---|---|---|---|---|
| [Immich](https://github.com/immich-app/immich) | 相册/备份 | 116.0k | 2026-10-11 | AGPL-3.0 | 最像自托管 Google Photos：手机备份、多用户、CLIP 语义搜索 |
| [PhotoPrism](https://github.com/photoprism/photoprism) | 相册/归档 | 40.3k | 2026-10-10 | 自定义(AGPL 声明) | 归档索引强、AI 标签、单用户为主 |
| [Ente](https://github.com/ente-io/ente) | 相册/E2EE | 29.3k | 2026-10-09 | AGPL-3.0 | 唯一端到端加密主流相册（加密设计参考） |
| [Stash](https://github.com/stashapp/stash) | 媒体管理器 | 13.1k | 2026-10-07 | AGPL-3.0 | 标签/演员/画廊/评分/插件齐全（成人向定位，模型通用） |
| [Kavita](https://github.com/Kareadita/Kavita) | 漫画/阅读 | 11.8k | 2026-10-10 | GPL-3.0 | 漫画+电子书统一服务器、多用户、AniList 元数据 |
| [LibrePhotos](https://github.com/LibrePhotos/librephotos) | 相册 | 8.1k | 2026-10-10 | MIT | 自托管 + 可配置 ML（人脸/标签） |
| [Komga](https://github.com/gotson/komga) | 漫画服务器 | 6.7k | 2026-10-08 | MIT | 漫画/杂志/电子书服务器：系列、收藏、多用户、API |
| [Photoview](https://github.com/photoview/photoview) | 相册 | 6.6k | 2026-10-08 | AGPL-3.0 | 面向 NAS 的简单相册（人脸识别） |
| [Lychee](https://github.com/LycheeOrg/Lychee) | 相册 | 4.3k | 2026-10-09 | MIT | 漂亮的照片管理系统（多用户、分享） |
| [Piwigo](https://github.com/Piwigo/Piwigo) | 相册 | 3.9k | 2026-10-09 | GPL-2.0 | 老牌相册：**用户组/权限体系最成熟**、内置星级评分、幻灯片插件 |
| [Hydrus Network](https://github.com/hydrusnetwork/hydrus) | 桌面管理器 | 3.2k | 2026-10-10 | 自定义(宽松) | 桌面标签管理器"圣经"：命名空间标签、重复检测、幻灯片、mpv 集成 |
| [LANraragi](https://github.com/Difegue/LANraragi) | 漫画/归档 | 3.1k | 2026-10-03 | MIT | 压缩包漫画库（tag/类别/归档管理） |
| [Danbooru](https://github.com/danbooru/danbooru) | booru | 2.8k | 2026-10-10 | 自定义 | 旗舰 booru 引擎：**图上区域注释(note)**、艺术家实体、pool、幻灯片 |
| [PiGallery2](https://github.com/bpatrik/pigallery2) | 相册 | 2.3k | 2026-08-25 | MIT | 文件夹优先的轻量相册 |
| [Damselfly](https://github.com/Webreaper/Damselfly) | 相册 | 1.8k | 2026-09-29 | GPL-3.0 | 大库索引管理（大量照片场景） |
| [HomeGallery](https://github.com/xemle/home-gallery) | 相册 | 1.2k | 2026-06-25 | MIT | 本地索引 + 人脸/对象识别 |
| [szurubooru](https://github.com/rr-/szurubooru) | booru | 1.0k | 2026-07-27 | GPL-3.0 | Danbooru 风 booru：tag 分类/别名/蕴含、pool、评分、**多边形注释** |
| [e621ng](https://github.com/e621ng/e621ng) | booru | 0.6k | 2026-10-09 | 自定义 | e621 同款成品引擎（pools / notes / 角色标签） |
| [Shimmie2](https://github.com/shish/shimmie2) | booru | 0.6k | 2026-10-08 | GPL-2.0 | 开箱即用社区图库（PHP，主题插件多） |
| [Philomena](https://github.com/derpibooru/philomena) | booru | 0.3k | 2026-10-06 | AGPL-3.0 | Derpibooru 同款（画廊幻灯片、筛选器、评分），部署较重(Elixir) |
| [Blombooru](https://github.com/mrblomblo/blombooru) | booru(新) | 0.08k | 2026-10-10 | MIT | 2026 新项目：个人向 booru，Docker 简单，单用户 |
| [PTUI](https://www.linuxlinks.com/ptui-terminal-based-image-viewer/) | TUI 看图 | - | - | 开源 | 终端图片浏览器：chafa/jp2a 渲染 + 星级评分(XMP sidecar) |
| [vimiv-qt](https://github.com/karlch/vimiv-qt) | 桌面看图 | 0.2k | 2024-08(半停) | GPL-3.0 | vim 键位桌面看图器 |

## 三、按需求逐类分析

### 3.1 booru 系（szurubooru / Shimmie2 / Philomena / Danbooru / e621ng / Blombooru）

**最强的部分**：R2 图片组（pool）、R3 评分（booru 三档 rating + 收藏，szurubooru 支持打分与收藏）、R4 Tag（命名空间、分类、别名、蕴含——这正是"作者:"式可检索体系的成熟形态）、R11 评论（全系支持）+ **图上区域注释**（szurubooru/Danbooru/e621 的 note，即在图上框选区域挂文字，正是"note"需求的原生形态）、权限（szurubooru 有细化 privilege 系统）。
**缺口**：R1 只处理上传到自带存储的文件，不"索引现有目录"（szurubooru/Shimmie 需导入；Blombooru 也以导入为主）；**R7 播放**基本没有规则化队列（Philomena 画廊幻灯片最接近，但无"按艺术家/跨 album/定量"参数化）；R8 无 TUI/CLI；R9 是"社区角色"而非"用户组+库授权"；R10 无加密；R5 艺术家多退化为标签而非实体；R12 无（Danbooru/Philomena 生态有第三方 AI tagger 接入）。
**判语**：把它们当"看图服务"用，最贴近 R2-R4/R11；但当"私人库播放器"差得远，且都要求改变文件存放方式（先导入）而本项目要的是「对应目录导入」。

### 3.2 桌面类（Hydrus / vimiv-qt / PTUI）

- **Hydrus**：标签体系（命名空间/别名/重复检测/自动打标脚本）是全场最强，媒体查看器支持幻灯片与 mpv 内嵌播放；但它是**单机单用户 Qt 桌面程序**——没有 Web/TUI/CLI 服务化形态（社区有第三方 [Hydrui](https://hydrui.dev/en/docs) 网页壳连本地客户端），没有多用户/共享库/组权限（局部有 Client API 共享，但非用户体系），无加密。**如果只是「单人桌面收藏」，Hydrus 就是答案；一旦要「多端+多人」，它出局。**
- **PTUI**：终端里做到"文件浏览 + chafa 渲染 + 星级评分（XMP sidecar）"，但无中央库/标签/多用户/服务端——可作 TUI 端交互参考。
- **vimiv-qt**：vim 键位桌面看图器，2024 年后基本停更，无库管理。

### 3.3 相册/备份类（Immich / PhotoPrism / LibrePhotos / Piwigo / Lychee / Photoview / PiGallery2 / Damselfly / HomeGallery / Ente）

**覆盖**：R1（索引/缩略图，PhotoPrism/Immich 最强）、R9（Immich、Piwigo 的用户/组与相册级权限成熟）、R11 部分（Immich 共享相册 + 评论；Piwigo 评论）、AI 标签（Immich CLIP、PhotoPrism/LibrePhotos 分类）、R10（只有 Ente 是端到端加密）。
**缺口**：R3 评分（**仅 Piwigo 内置星级评分**，其余产品普遍"收藏"代替评分）；R4 Tag 多有但偏"自动分类标签"，无 booru 式精确检索语法；R5 艺术家实体无（Immich 有人物/人脸≠艺术家）；R6 漫画类别无；R7 幻灯片**存在但不可参数化**（PhotoPrism 幻灯片可设时长/循环，但无"定量/按艺术家/跨专辑"规则）；R8 无 TUI/CLI；R2 图片组=相册/专辑（够用）。
**判语**：这类解决"照片备份与回忆"，不解决"图片作品收藏/检索/播放"。其中 **Piwigo 的组权限模型 + 内置评分** 是 R3/R9 的最佳参考；**Ente 的 E2EE 设计**是 R10 参考。

### 3.4 漫画/阅读类（Komga / Kavita / LANraragi）

**覆盖**：R6（系列/卷/压缩包、ComicInfo.xml 元数据、阅读进度）、R9（Komga/Kavita 多用户与库级权限）、R11 部分。
**缺口**：R3 评分——**Komga 没有评分功能**（用户社区长期抱怨，只能用 emoji 标签土法替代，见 Diverse Tech Geek 评测）；Kavita 有 10 星评分但绑定阅读场景；R4 是系列元数据标签而非自由 tag；R7 无幻灯片播放（阅读器≠播放器）；R8 无 TUI/CLI；R10 无。
**判语**：漫画"读"得很专业，但要"看图+playlist 播放+自由标签"是错位的。

### 3.5 播放器/渲染器（基建角色，非整方案）

- **mpv**：现状基线工具。可靠结论：图片播放/幻灯片参数（`--image-display-duration`、`--loop-playlist`、`--shuffle`）成熟，是"播放执行层"的最佳搭档——缺的只是**喂给它的"库感知队列"**（跨专辑/按艺术家/按评分/tag 过滤）。
- **chafa / img2txt(libcaca)**：终端内图片渲染（TUI 预览、CLI --player terminal 可用）。

## 四、需求 × 候选矩阵

图例：● 完整 ｜ ◐ 部分/间接 ｜ ○ 无

| 需求 | booru 系 | Hydrus | Immich 等相册 | Piwigo | Komga/Kavita | PTUI | 组合拳可行性 |
|---|---|---|---|---|---|---|---|
| R1 目录导入+编码 | ◐（需导入进库） | ● | ● | ● | ● | ◐ | 已有 |
| R2 图片组 | ● pool | ◐ | ● 专辑 | ● 相册 | ● 系列 | ○ | 已有 |
| R3 评分 | ● | ● | ○(收藏) | ● | ○(仅Kavita) | ●(单机) | 已有 |
| R4 Tag 系统 | ● | ●● | ◐ | ◐ | ◐ | ○ | 已有 |
| R5 艺术家实体 | ◐(标签化) | ◐(标签化) | ○ | ○ | ◐(元数据) | ○ | **无现成** |
| R6 漫画类别 | ○ | ◐ | ○ | ○ | ● | ○ | 已有(Komga) |
| R7 播放规则化 | ○/◐ | ◐(本机) | ◐(固定) | ◐(插件) | ○ | ○ | **无现成** |
| R8 Web+TUI+CLI | ○(仅Web) | ◐(仅桌面) | ○(仅Web) | ○ | ○ | ◐(仅TUI) | **无现成** |
| R9 用户组 | ◐(社区角色) | ○ | ●/◐ | ● | ● | ○ | 已有 |
| R10 加密 | ○ | ○ | ○(除Ente) | ○ | ○ | ○ | **基本空缺** |
| R11 评论/笔记/共享库 | ●评论●注释 | ◐ | ◐ | ◐ | ◐ | ○ | 已有(boo ru注释最强) |
| R12 本地识图 | ◐(第三方) | ◐ | ● | ● | ○ | ○ | 已有 |

**结论**：任何单项目在表中至少空缺 4 项，且空缺处恰好是本项目瞄准的差异化需求（R7/R8/R10 全表最弱列）。**自建 MVP + 借鉴**是正确路线。

## 五、借鉴清单（Pic Shelf MVP 采用）

| 来源 | 借用 |
|---|---|
| Hydrus | 标签命名空间/别名/蕴含的数据方向；重复检测（SHA-256 精确 + pHash 相似） |
| booru（szurubooru/Danbooru/e621 系） | pool/专辑模型；per-user 评分与收藏；图上区域注释（Roadmap）；权限细化 |
| Piwigo | 组-能力-库授权三级权限模型；内置评分的语义（星=可更新） |
| Komga/Kavita | 系列/ComicInfo.xml 元数据方向（Roadmap CBZ）；多用户库授权现状 |
| Ente | E2EE 设计参考（Roadmap 加密） |
| PTUI/Hydrus | TUI 交互（vim 键位、评分快捷键、连播）；终端渲染走 chafa/img2txt |
| mpv | 播放执行层：Pic Shelf 只负责生成"库感知队列"，渲染交给 mpv |
| Immich | CLIP 语义搜索方向（Roadmap R12） |

## 六、主要来源

- GitHub 仓库与 API（star/推送/许可快照 2026-10-11）：见总表链接
- [szurubooru README（功能清单）](https://github.com/rr-/szurubooru)
- [Piwigo 官方文档：评论与评分](https://doc.piwigo.org/comments-and-ratings/managing-ratings-votes)、[组权限坛帖](https://piwigo.org/forum/viewtopic.php?id=34520)
- [Hydrus 官方文档（媒体浏览/幻灯片语境）](https://hydrusnetwork.github.io/hydrus/getting_started_files.html)
- [Hydrui 文档（Hydrus 远程网页壳背景）](https://hydrui.dev/en/docs)
- [Immich vs PhotoPrism vs LibrePhotos 对比（2026）](https://thebackroomtech.com/immich-vs-photoprism-vs-librephotos)、[Immich vs PhotoPrism vs Ente（2026-07 快照）](https://contabo.com/blog/immich-vs-photoprism-vs-ente)
- [Kavita vs Komga 2026 对比](https://jellywatch.app/blog/jellyfin-comics-manga-kavita-komga-self-hosted-reader-2026)、[Komga 评测（评分缺失抱怨）](https://www.diversetechgeek.com/media-server-review-komga)
- [PTUI 介绍（LinuxLinks）](https://www.linuxlinks.com/ptui-terminal-based-image-viewer/)
