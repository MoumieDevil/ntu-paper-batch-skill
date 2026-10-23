# 本地 API 假设与验证边界

检查日期：2026-10-23。这是客户端协议说明，不复制 Zotero 实现代码；升级 Zotero 后应重新核对响应和运行测试。

- [Zotero Connector 官方实现](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/server/server_connector.js)：`getSelectedCollection` 的集合 `id` 是内部数值，不是 Web API key；`targets` 提供库与集合层级。helper 只处理第一个个人文库根，并用层级对应本地 API 集合 key。结构不符则拒绝写入。
- `saveItems` 与 `saveAttachment` 是两个请求。后者使用当前保存 session 和 connector item ID；不能将任意已有条目 key 当作同一 session 的 parentItemID。helper 不提供未知 session 的自动补附件。
- Connector 的 HTTP 响应只能表示该阶段请求处理结果。本实现还检查父项集合、PDF 子项的 parentItem 和存储文件哈希。本次 live smoke 验证了分阶段保存和重复执行，不保证并发事务。
- [Zotero Local API 官方实现](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/server/server_localAPI.js)：本地个人文库读取前缀为 `/api/users/0`；分页使用 `start` / `limit`。`file/view/url` 返回普通文本文件 URL，不是 JSON 对象。
- helper 的 Local API 操作均为读取；写入走 Connector，不直接修改 SQLite，也不申请/保存云端 API key。若版本要求额外授权，遵从当前正常授权流程，不绕过。

完整状态、命令和手工恢复规则见 [import-and-recovery.md](import-and-recovery.md)。
