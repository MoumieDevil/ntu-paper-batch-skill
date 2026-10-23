# NTU 批量下载论文 Skill

面向已通过 **NTU Library Databases A–Z / Data List** 打开数据库的用户，将小批量论文下载、核验并归档到 Zotero。不是 NTU 官方工具，也不提供账号、全文授权或绕过访问限制的能力。

仓库名：`ntu-paper-batch-skill`；调用名：`$ntu-paper-batch-download`。

当前为首个经过本地导入验证的版本；数据库端的实际限制详见测试记录，不是三站无人值守下载保证。

## 三条路径

| 来源 | 下载路径 |
|---|---|
| IEEE Xplore | 论文详情 → 官方 PDF / Zotero Connector |
| Web of Science | Full Record → 出版社全文链接 → 出版社 PDF |
| 知网 | 精确结果行右侧下载 → PDF → 显式中文元数据 |

已从 NTU 进入时直接开始正常检索/下载，不反复要求登录；某个出版社、文献或独立产品实际要求额外授权时，仍需停止并由用户处理。

## 使用前提

- 已打开并可用的机构浏览器会话，以及能接管该标签页的浏览器工具。
- Zotero Desktop 正在运行，本地 API 已启用；选中要保存的个人文库集合。
- Python 3.9+。本地导入助手需要 `pypdf` 验证 PDF；不需要 Zotero 云 API key。
- 该版本的脚本只支持个人文库，不自动修改群组文库或切换用户集合。

## 安装与调用

将仓库中的整个 `ntu-paper-batch-download/` 文件夹放到你的 skill 目录。安装目录取决于运行环境；不要把整个仓库当作单个 skill。目录结构遵循 [OpenAI skill 文档](https://developers.openai.com/plugins/build/skills)。

需要兼容旧调用名 `$zotero-connector-batch-save` 时，也安装仓库中同名文件夹，放在新版旁边。兼容入口转发到新版，不重复维护两套下载代码。更新已有安装前先备份；新版导入参数更严格，不保证旧命令不改即可执行。

在独立 Python 环境安装依赖：

```sh
python -m pip install -r requirements.txt
python ntu-paper-batch-download/scripts/zotero_connector_batch.py probe
```

示例请求：

> 使用 $ntu-paper-batch-download，在我从 NTU 打开的 IEEE、Web of Science、知网页面各找 5 篇近几年 camera-free posture detection 相关论文。优先 LED/光电探测器和 ToF，排除 SI，去重后保存正文 PDF 到 Zotero 的 test 集合。遇到真正的权限阻断就记录，不绕过。

不是脱离代理工具独立运行的全自动爬虫。浏览器工具与扩展的具体能力由当前环境提供，skill 不携带账号、浏览器配置或凭据。

## 相比旧版的改进

- 不把隐藏验证码模板、“暂无数据”或客户端拦截误报成 IP 封禁。
- 正确处理新标签、结果行下载和 HTML 假 PDF。
- 明确区分 Zotero 父条目创建、附件上传与验证，删除“原子导入”的错误保证。
- DOI/Unicode 标题去重；已有条目缺 PDF 时提示修复，不重复建父项。
- 指定集合、UTF-8、附件与部分失败检查；不把请求被接受当成最终成功。
- 小批量串行、失败保留进度；不绕过验证码、付费墙、反爬或工具权限。

## 验证与限制

运行 `python -m unittest discover -s tests -v`。离线测试使用模拟本地 API，不向真实文库写入。实测边界见 [TESTING.md](TESTING.md)，不要把单元测试通过理解为所有数据库实时下载可用。

本仓库不存论文 PDF、账号、cookies、带会话签名的 URL 或个人 Zotero 数据。更新前请检查实际提交文件；`.gitignore` 不能代替隐私审查。
