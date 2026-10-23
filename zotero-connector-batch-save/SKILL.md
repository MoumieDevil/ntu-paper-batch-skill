---
name: zotero-connector-batch-save
description: Compatibility entry for the user's older Zotero batch workflow. When this name is invoked, use the installed sibling ntu-paper-batch-download skill for NTU institutional IEEE, Web of Science, and CNKI downloads and Zotero verification.
---

# Zotero Batch 旧名称兼容入口

该 skill 已升级为 **NTU 批量下载论文**，主实现是同级的 `ntu-paper-batch-download`。

在操作浏览器或 Zotero 之前，完整读取 [新版 SKILL.md](../ntu-paper-batch-download/SKILL.md)，然后按新版流程和其相关 references 执行。保留用户本次要求的主题、数量、年份、排除项和目标集合。

安装时必须同时放入两个文件夹；若新版目录不存在，应明确提示缺少依赖，不退回旧版不验证附件的写入逻辑。

本目录中的旧脚本路径只转发到新版。新版需明确选择 `--pdf` + `--pdf-identity-confirmed` 或经用户同意的 `--metadata-only`，并指定 `--expect-collection`；不能承诺旧的不安全参数行为仍然可用。
