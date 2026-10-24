# Zotero 导入与恢复

## 环境与预检

`scripts/zotero_connector_batch.py` 只访问 `127.0.0.1:23119` 的 Zotero 服务，不下载出版社文件、不读取账号；本地请求禁用代理且不跟随重定向。运行 Python 3.9+；PDF 保存需要 `pypdf`。在选定的 Python 环境中安装 skill 内的 `requirements.txt`；建议隔离虚拟环境，安装与运行必须使用同一个解释器。不要改变全局代理、关闭 TLS 校验或安装无关扩展。

本文的 `scripts/` 路径均相对于本 skill 的 `SKILL.md` 所在目录，而非本参考文件或用户工作区。以下命令从该目录运行；其他工作目录应改为已解析的绝对脚本路径。文件路径加引号，包含中文或空格也一样。

Zotero 服务未运行时请用户打开；本地 API 未启用时通过可用的 Zotero 工具/正常设置处理，并说明变更。不要扫描浏览器配置找凭据。

```sh
python "scripts/zotero_connector_batch.py" probe
python "scripts/zotero_connector_batch.py" find --doi "10.1234/example" --title "Paper title" --collection-key COLLECTION_KEY
```

`probe` 是只读诊断，返回 `connector_running`、`selected_target`，以及顶层 `collection_key`、`collection_path`（如 `["test", "nested"]`）、`collection_resolvable`、`target_warning`。根目录、群组、同路径歧义或 API 不可用时也尽量返回诊断 JSON，key 可能为空。不要把退出码 0 或 Connector 正在运行当成“可导入”的证明；检查警告和目标权限。导入 dry-run 还会验证个人文库、可写性、集合路径和唯一 key。`--expect-collection` 使用当前集合的精确显示名称，`--expect-collection-key` 使用解析得到的 key；同名集合有歧义时停止，不猜测。

## 有界查重，不把未命中当作不存在

`find` / `save` 先查规范化 DOI 与标题，未命中时补充核心词、目标集合和最近修改条目。它们不为每篇新论文遍历全库；等待新父项的轮询也只检查最近创建条目。结果中的 `duplicate_check` 报告覆盖范围、候选数、截断来源和警告。

- 精确 DOI 匹配优先于同名不同 DOI 的其他版本；观察到多个匹配父项仍阻断写入。只有同名 DOI 冲突、没有精确 DOI 时，`save` 返回 `needs_identity_review` 与候选 key，不自动创建新父项。
- 无共同 DOI 时还需第一作者与年份一致。支持 `2025年03期` 等中文日期；西文可兼容实际首字母缩写及中间名省略，但不能把 John Smith 与 James Smith 仅凭 J 视作同一人。双方都有中间名且冲突时也不能匹配，例如 John Alan Smith 与 John Brian Smith。
- 有界查询无结果不证明全库没有旧条目或未索引条目；已命中但查询截断，也可能漏掉其他匹配父项。无法充分排除这些不确定性时默认返回 `needs_duplicate_review`，不写入；不能为省事自动加 `--duplicate-search-reviewed`。
- 如需要继续，可先单独完成真实的全库/人工查重，并在本地 checkpoint 记录范围、时间及结论。批量任务可只读建立一次完整清单供整批比较，避免每篇及每次轮询重扫；不完整清单、过期清单或同时发生的文库修改仍需处理。确认该篇无重复后，才在 dry-run 和正式 save 中加入 `--duplicate-search-reviewed`。该参数是已完成额外核对的声明，不会替你执行核对，也不会抹掉覆盖警告。

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

### PDF 内容辅助检查

```sh
python "scripts/zotero_connector_batch.py" inspect-pdf --pdf "downloads/张三 - 2025 - 光电姿态检测.pdf" --title "来源中的真实题名"
```

已知 DOI 时增加 `--doi "已核实的 DOI"`。该只读命令严格解析 PDF，输出 `pdf_pages`、`pdf_size`、`sha256`、前两页文本的最多 500 字符预览 `first_page_snippet`、`has_extractable_text`、文本提取失败页数及 DOI/标题匹配结果。匹配使用前两页完整提取文本，不限于预览；单页文本提取异常被记录，不因此忽略结构解析失败。

`pdf_text_matched` 为辅助线索，不是论文身份或内容正确性的证明。必须将实际首页题名、DOI、作者与来源对应后才传 `--pdf-identity-confirmed`；无 DOI 的中文论文还需核对年份。文本不可提取或匹配失败时查看实际首页，不猜测，也不因扫描件没有文本而伪称不是 PDF。严格结构校验不通过则停止导入。正文 PDF 与 SI 分开，遵从用户排除条件。

若已诊断为重复且相同的根 `/MediaBox` 字段，可另行征得用户同意后按[有限副本修复](pdf-repair.md)生成新文件，再完整走上述核验；这不会使不合格原件获得导入资格。

### 稳定 URL 与无 URL 模式

默认仍要求稳定 source URL，拒绝凭据、会话或签名参数。若 CNKI 只有带 `v` 的临时详情链接，优先使用已核实 DOI 的链接，或页面/导出中实际提供的、不含签名参数的 `dbcode` + `dbname` + `filename` 记录链接。不要猜数据库名或编造稳定链接，也不要把签名 URL 放进 `extra`。

只有确实找不到稳定 URL 时，才将元数据 `url` 留空，并显式使用 `--allow-missing-url`；同时必须提供非空 `extra`，记录真实来源库、可见文献标识及刊名/学校等可追溯信息。此模式不接受另填 `--pdf-url`。不能通过清空一个已有稳定 URL 来规避正常流程，也不能用该参数放行带签名的 URL。

此时 Zotero 的 `item.url` 保持空白。固定 `http://127.0.0.1:23119/local-verified-import` 仅用作 Connector `uri` 和附件 `X-Metadata.url` 的本地协议占位值，不是论文地址，不写入条目 URL，不向它下载文件。无 URL 模式不放宽作者/日期、查重、PDF 身份、集合与 SHA-256 校验。

## 先 dry-run，再写入

以下命令从 skill 文件夹运行；集合名称和 key 使用现场核实的值：

```sh
python "scripts/zotero_connector_batch.py" save --metadata "runs/paper.json" --pdf "downloads/paper.pdf" --pdf-identity-confirmed --expect-collection "TARGET" --expect-collection-key COLLECTION_KEY --dry-run
python "scripts/zotero_connector_batch.py" save --metadata "runs/paper.json" --pdf "downloads/paper.pdf" --pdf-identity-confirmed --expect-collection "TARGET" --expect-collection-key COLLECTION_KEY
```

示例 `runs/` 应指向用户任务的本地 checkpoint，不向安装目录或公共仓库存文献数据。只有满足上述条件才按需添加 `--allow-missing-url` 或 `--duplicate-search-reviewed`，不作为默认模板参数。用户明确接受元数据-only 时，改用 `--metadata-only`，不能只省略 `--pdf`。可指定 `--expect-library-id` 加强校验；helper 不支持群组文库。已有 CLI 参数保留，`--allow-duplicate` 仍拒绝执行，防止重复父项误匹配；新选项不会解除现有安全检查。

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
| `needs_identity_review` | 同名条目存在 DOI 冲突且无精确 DOI 命中，或没有共同 DOI 时作者/年份缺失或不同。按候选 key 核对，不自动补附件或另建重复项。 |
| `needs_duplicate_review` | 有界查询未命中但未覆盖全库，或命中查询被截断，尚不能排除遗漏。未写入；先单独完成真实查重，不能盲加确认参数。 |
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
