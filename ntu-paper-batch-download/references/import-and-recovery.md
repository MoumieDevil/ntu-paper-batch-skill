# Zotero 导入与恢复

## 环境与预检

`scripts/zotero_connector_batch.py` 只访问 `127.0.0.1:23119` 的 Zotero 服务，不下载出版社文件、不读取账号。运行 Python 3.9+；PDF 保存需要 `pypdf`。在选定的 Python 环境中安装 skill 内的 `requirements.txt`；建议隔离虚拟环境，安装与运行必须使用同一个解释器。不要改变全局代理、关闭 TLS 校验或安装无关扩展。

Zotero 服务未运行时请用户打开；本地 API 未启用时通过可用的 Zotero 工具/正常设置处理，并说明变更。不要扫描浏览器配置找凭据。

```sh
python scripts/zotero_connector_batch.py probe
python scripts/zotero_connector_batch.py find --doi 10.1234/example --title "Paper title"
```

`probe` 是只读连接检查；导入 dry-run 还会验证个人文库、可写性、集合路径和唯一 key。目标集合的显示名称必须精确匹配。嵌套集合通过层级路径对应到 key；同路径同名集合有歧义时停止，不猜测。

## 输入一条经过核实的元数据

以下是虚构格式示例，不能作为真实论文导入：

```json
{
  "itemType": "journalArticle",
  "title": "Example paper — replace with source title",
  "creators": [{"creatorType": "author", "firstName": "Example", "lastName": "Author"}],
  "date": "2025",
  "publicationTitle": "Example Journal",
  "url": "https://publisher.example/article/1",
  "libraryCatalog": "IEEE Xplore",
  "tags": ["user-requested-topic"]
}
```

作者、日期需来自原记录；字段缺失时补查来源或明确报告，不能填虚构值以通过校验。中文姓名可以用 `lastName` 存完整姓名并设 `fieldMode: 1`。会议用 `conferencePaper` / `proceedingsTitle`；学位用 `thesis` / `university` / `thesisType`。不向 helper 传入嵌套附件、笔记、关系或自定义 collections，以免触发额外下载或写错集合。

写入前核对 PDF 首页面内容，不能仅相信标题和文件名。`--pdf-identity-confirmed` 表示操作者已完成内容匹配，不会替你判断论文身份。对于无 DOI 的中文论文，核对题名、作者和年份。正文 PDF 与 SI 分开，遵从用户排除条件。

只保存可公开复用的稳定记录 URL。若 CNKI 只有带 `v` 的临时详情链接，优先使用来源提供的 DOI/稳定记录链接。当前 helper 要求有效 canonical source URL；找不到时不能用空 URL 调用它，应改用受支持的 Zotero UI 建立元数据并添加已验证附件，在 `extra` 保存可见文献标识及来源信息。无法操作 UI 则明确报告该限制。不要编造稳定链接，也不要把签名参数当书目信息保存。

## 先 dry-run，再写入

以下命令从 skill 文件夹运行；集合名称和 key 使用现场核实的值：

```sh
python scripts/zotero_connector_batch.py save --metadata paper.json --pdf paper.pdf --pdf-identity-confirmed --expect-collection "TARGET" --expect-collection-key COLLECTION_KEY --dry-run
python scripts/zotero_connector_batch.py save --metadata paper.json --pdf paper.pdf --pdf-identity-confirmed --expect-collection "TARGET" --expect-collection-key COLLECTION_KEY
```

用户明确接受元数据-only 时，改用 `--metadata-only`，不能只省略 `--pdf`。可指定 `--expect-library-id` 加强校验；helper 不支持群组文库。`--allow-duplicate` 已禁用，防止旧版重复父项误匹配。

每次只运行一个导入进程，导入期间不要手动切换 Zotero 集合。脚本写前/阶段间检查选择；没有跨进程事务或锁，无法保证 UI 同时切换时绝不发生部分写入。

脚本顺序为 `saveItems` → 观察父项 → `saveAttachment` → 核对集合及附件。HTTP 成功不等于全部完成。验证通过要求 Zotero 储存文件与预先校验的 PDF 的 SHA-256 相同；相同论文不同版本/重新压缩的文件可能 hash 不同，这会保守地要求人工确认，而不是自动覆盖。

## 状态与计数

| 状态 | 含义与下一步 |
|---|---|
| `dry_run` | 未写入，预检完成。 |
| `saved_complete` | 新父项位于正确集合，存储附件字节与输入 PDF 匹配。可计入新完成数。 |
| `saved_metadata` | 只按用户许可保存了元数据，不计入 PDF 下载数。 |
| `duplicate_complete` | 已有父项在目标集合，附件与输入匹配；不算新增。 |
| `duplicate_present` | 已有条目，但 PDF 版本/可读性未被充分验证；人工核对，不再创建父项。 |
| `needs_identity_review` | 题名相同但没有共同 DOI，作者/年份缺失或不同。不得自动补附件到候选父项，也不得自动另建重复项。 |
| `needs_collection` | 同一条目在别的集合。只将现有条目加入用户指定集合；不要复制/移出原集合。脚本不自动改集合。 |
| `needs_attachment` | 已有父项缺存储 PDF。通过受支持的 Zotero UI 将核实的 PDF 添加到该父项。 |
| `partial` | 已观察到父项，后续步骤未完成。保留 key，检查实际附件，再修复。 |
| `uncertain` | 写请求可能已生效但未观察到父项。重新只读查询/检查 Zotero，禁止盲重发。 |
| `blocked` | 预检或读取失败；查看原因。不能按成功计数。 |

退出码：完整成功/dry-run 为 0；需人工核对/修复或部分状态为 2；输入/预检阻断为 1。解析 JSON 的 `status`，不要只看退出码。`duplicate_complete` 也返回 0，但不是新增成功。

## 补附件与审计

- Connector 已保存元数据+PDF：只读核对，结束该条目。
- 已有父项缺 PDF：选中准确父项，用 Zotero 正常的添加存储附件操作，随后核对 parent key、PDF 子项及可读文件。当前 helper 不提供未知 session 的自动修复写入。
- 无桌面控制工具时，保留本地 PDF 与明确的父项 key，告知用户需要补挂；不以“已下载”冒充“已入库”。
- 索引延迟不影响已证实可读的储存 PDF；可报告“已入库，全文索引待完成”。
- 删除、替换、合并已有记录不是重试的一部分；没有明确授权不做。用户本来就授权了具体清理时遵照范围和工具确认规则，不反复索要同一权限。

本地 API 实现依据见 [api-notes.md](api-notes.md)。若 Zotero 升级使响应结构不符，应停止并按当前官方实现验证；不要直接写入 `zotero.sqlite`。
