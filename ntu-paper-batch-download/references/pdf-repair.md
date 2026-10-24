# 有限的 PDF 副本修复

仅当官方下载的 PDF 因根 `/Pages` 字典中重复且完全相同的 `/MediaBox` 被严格解析拒绝时考虑本路径。普通下载/导入授权不等于改写文件授权；先说明具体问题并取得用户对“保留原件，另建修复副本”的明确同意。不是自动 fallback，也不是通用 PDF 修复器。

## 支持边界

`scripts/repair_identical_mediabox.py`（相对于 SKILL.md 所在目录）仅支持传统 xref、简单且扁平的根 Pages 字典。它通过 xref 定位真实对象，仅将第二个连续、字面数字数组逐字节相同的 MediaBox 定义替换为等长空白；其他字节、文件长度和 xref 偏移不变。

- 不覆盖或删除原件；输出必须为新路径且不存在，独占创建，并检查前后原件 SHA-256。
- 不使用 `strict=False`；生成副本前严格解析全部已声明对象、叶页面和页面内容流，拒绝残留解析警告及对象跨度重叠，避免修改流内伪造对象。
- 物理对象间仅接受空白/完整注释，拒绝未声明流或对象；所有间接引用必须指向有效 xref 对象。已观察到的、固定字段且长度受限的 CNKI `WebFastLoad` 书目尾注可原样保留；不接受任意尾随数据。
- 拒绝不同数组、其他重复字段、复杂/增量/hybrid xref、压缩对象流、加密、签名或签名字段，以及不支持的结构。数值相等但字面不同也不自动处理。
- 输出仍可能含原 PDF 自带的内容和功能；此过程不是恶意内容清理或安全沙箱，不宣称所有 PDF 功能都经过验证。

## 明确授权后的命令

```sh
python "scripts/repair_identical_mediabox.py" --input "downloads/original.pdf" --output "downloads/original-mediabox-repaired.pdf" --approved
python "scripts/zotero_connector_batch.py" inspect-pdf --pdf "downloads/original-mediabox-repaired.pdf" --title "来源中的真实题名"
```

`--approved` 只是已经取得用户同意的声明，不能代替询问用户。若失败，不叠加更多通用修复、不覆盖同名输出、不删除可能已生成的副本；记录实际结果并重新评估。

记录脚本输出的原件/副本 SHA-256、修改对象号、偏移、长度和页数。再次核对正文身份及来源，才将**修复副本**传入正常 dry-run/save；Zotero 储存附件哈希应匹配副本，不是原件。元数据 `extra` 注明已获授权的格式副本及保留原件事实；本地 checkpoint 保存详细路径和哈希，公共仓库不放真实 PDF、个人路径或文库 key。

未进入支持边界或用户未同意时，保持 `blocked` 并交接，不能为了三站测试通过而降低主导入脚本的严格校验。
