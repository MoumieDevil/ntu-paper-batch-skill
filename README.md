# NTU 批量下载论文 Skill

面向已通过 **NTU Library Databases A–Z / Data List** 打开数据库的用户，将小批量论文下载、核验并归档到 Zotero。不是 NTU 官方工具，也不提供账号、全文授权或绕过访问限制的能力。

仓库名：`ntu-paper-batch-skill`；调用名：`$ntu-paper-batch-download`。

当前版本包含外部审查后的定点修复；离线回归与三站实际测试结果分别记录在 [TESTING.md](TESTING.md)。本地代码测试通过不代表三站下载、PDF 校验和 Zotero 入库均已完成，也不是无人值守下载保证。

## 三条路径

| 来源 | 下载路径 |
|---|---|
| IEEE Xplore | 论文详情 → 官方 PDF → 查看器/实际 PDF frame 下载；或 Zotero Connector |
| Web of Science | Full Record → 出版社全文链接 → 出版社 PDF |
| 知网 | 精确结果行下载或详情页 Download → PDF（不选 CAJ）→ 显式中文元数据 |

已从 NTU 进入时直接开始正常检索/下载，不反复要求登录；某个出版社、文献或独立产品实际要求额外授权时，仍需停止并由用户处理。

## 使用前提

- 已打开并可用的机构浏览器会话，以及能接管该标签页的浏览器工具。
- Zotero Desktop 正在运行，本地 API 已启用；选中要保存的个人文库集合。
- Python 3.9+。本地导入助手需要 `pypdf` 验证 PDF；不需要 Zotero 云 API key。
- 该版本的脚本只支持个人文库，不自动修改群组文库或切换用户集合。

## 安装与调用

将仓库中的整个 `ntu-paper-batch-download/` 文件夹放到你的 skill 目录。安装目录取决于运行环境；不要把整个仓库当作单个 skill。目录结构遵循 [OpenAI skill 文档](https://developers.openai.com/plugins/build/skills)。

需要兼容旧调用名 `$zotero-connector-batch-save` 时，也安装仓库中同名文件夹，放在新版旁边。兼容入口转发到新版，不重复维护两套下载代码。更新已有安装前先备份。此次修复保留已有 CLI 参数并增加可选命令/参数；原有安全限制仍生效，覆盖不完整的查重结果也不会被当作可安全写入。

使用同一个独立 Python 环境安装依赖与运行命令。下列命令从已安装 `ntu-paper-batch-download` 的 `SKILL.md` 所在目录执行；脚本路径相对该目录，不相对用户工作区。其他位置运行时使用加引号的绝对路径：

```sh
python -m pip install -r "requirements.txt"
python "scripts/zotero_connector_batch.py" probe
python "scripts/zotero_connector_batch.py" inspect-pdf --pdf "downloads/论文 title.pdf" --title "已核实的题名"
```

示例请求：

> 使用 $ntu-paper-batch-download，在我从 NTU 打开的 IEEE、Web of Science、知网页面各找 5 篇近几年 camera-free posture detection 相关论文。优先 LED/光电探测器和 ToF，排除 SI，去重后保存正文 PDF 到 Zotero 的 test 集合。遇到真正的权限阻断就记录，不绕过。

不是脱离代理工具独立运行的全自动爬虫。浏览器工具与扩展的具体能力由当前环境提供，skill 不携带账号、浏览器配置或凭据。

## 审查后的改进与边界

- `probe` 直接返回 `collection_key`、层级 `collection_path`、`collection_resolvable` 和 `target_warning`。返回 JSON/退出码 0 不保证当前目标可写，仍需核对目标及 dry-run。
- 修复无 DOI 中文论文的 `2025年` / `2025年03期` 年份核对；兼容有证据支持的西文缩写及中间名省略，不把不同全名或冲突中间名当作同一作者。
- DOI 精确命中优先于同名不同 DOI 的版本；无法判定时返回 `needs_identity_review`。有界查重与最近条目观察替代每篇/每次轮询全库扫描；不完整覆盖返回 `needs_duplicate_review`，不能盲加 `--duplicate-search-reviewed`。只有另行完成真实查重后才使用该确认参数。
- `inspect-pdf` 输出页数、大小、SHA-256、前两页文本的 500 字符预览及 DOI/标题匹配结果；匹配使用完整提取文本，但只是辅助，仍须核实实际首页后声明 `--pdf-identity-confirmed`。
- 确实缺少稳定 URL 的论文可用 `--allow-missing-url` + 非空 `extra` 来源信息受控导入。条目 URL 留空，本地占位 URI 仅用于 Connector 协议；不放行签名链接，不编造 DOI/来源。
- 保留 loopback-only、禁用代理/重定向、严格 PDF 解析、目标集合和存储 SHA-256 核验。Zotero 保存仍是两阶段、非原子；部分失败保留进度，不盲重试写入，不自动删除或合并。
- 针对已实测的相同根 `/MediaBox` 重复定义，提供[有限副本修复](ntu-paper-batch-download/references/pdf-repair.md)：须另行取得用户同意，保留原件、仅改重复字段、严格复验，不是通用宽松解析。

参数、状态与恢复步骤见[导入与恢复](ntu-paper-batch-download/references/import-and-recovery.md)；页面上的正常下载分支、视觉验证码交互与真实阻断判断见[三站路径](ntu-paper-batch-download/references/site-workflows.md)。滑动拼图/识图点选可在工具支持、图像明确且满足当次确认要求时正常视觉尝试；不能完成则记录原因并交接。不把隐藏验证码模板、“暂无数据”或客户端拦截误报成 IP 封禁，不绕过验证机制、付费墙、反爬或工具权限。

## 验证与限制

从仓库根目录运行 `python -m unittest discover -s tests -v`。本次回归 85 项通过，保留原有 37 项并补充中文日期/路径、作者、查重覆盖、DOI 冲突、probe、无 URL 导入、PDF 文本与有限副本修复的反向测试。离线 API 测试均使用模拟请求，不向真实文库写入。三站各 1 篇真实论文已完成入库，知网采用用户授权的保留原件修复副本；日期与适用边界见 [TESTING.md](TESTING.md)，不代表所有论文或未来会话都可下载。

本仓库不存论文 PDF、账号、cookies、带会话签名的 URL 或个人 Zotero 数据。更新前请检查实际提交文件；`.gitignore` 不能代替隐私审查。
