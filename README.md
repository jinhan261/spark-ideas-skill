# Spark Ideas Skill

让 Mac 上的 Codex、Claude Code 读取你在 iPhone Spark 中记录的想法。

**iPhone Spark → 个人 iCloud Drive → 本地 Skill → Agent。** 不需要 Mac App、MCP 服务或开发者账户，也不需要把笔记上传到这个仓库。

## 1. 在 iPhone 开启

打开 Spark 的「设置 → Agent 支持」，允许读取，选择「仅收藏」或「全部记录」。默认关闭、首次范围为仅收藏。记录保存后，Spark 自动更新文字快照。

Mac 登录同一 Apple 账户并开启 iCloud Drive。Skill 会自动找到 Spark 的导出文件，通常无需找文件、复制路径或手动配置。

App 的「已导出」只代表 iPhone 已写入文件，不保证 Mac 已收到。可以比对 App 的最近导出时间与 Skill 返回的 `generatedAt`。

## 2. 安装 Skill

需要能访问本机文件的 **Mac 本地 Agent**，以及 **Python 3.10 或更新版本**。工具仅使用 Python 标准库与 macOS 自带的 `osascript`，没有 pip 依赖。网页版或远程云端 Agent 不会因为安装 Skill 就获得 Mac 文件访问权。

最简单：把下面这段发给你的 Agent，把客户端名字改为你正在使用的工具：

> 请从 https://github.com/jinhan261/spark-ideas-skill 的 v0.1.1 版本安装 skills/spark-ideas 到我的 Codex 用户 Skill 目录，然后自动连接 Spark，先只检查状态，不展示笔记正文。

Claude Code 用户将上文的「Codex」换成「Claude Code」。Agent 需要你允许下载代码和创建这个 Skill 目录；如果它不能联网，可按下方手动安装。

### 手动安装

在终端执行（已有同名目录时不要覆盖，先检查是否是自己安装的版本）：

```bash
git clone --branch v0.1.1 --depth 1 https://github.com/jinhan261/spark-ideas-skill.git
cd spark-ideas-skill
python3 --version

# 二选一
python3 scripts/install_skill.py --client codex
python3 scripts/install_skill.py --client claude
```

Codex 默认安装到 `~/.agents/skills/spark-ideas`；Claude Code 默认安装到 `~/.claude/skills/spark-ideas`。可用 `--destination` 指定一个新的绝对目录。脚本不会修改 Agent 配置，不会安装运行时，不会读取笔记，已有目录会拒绝覆盖。

安装后重新打开客户端或开始新会话，让客户端发现新 Skill。目录约定见 [Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)和 [Claude Code 官方 Skill 文档](https://code.claude.com/docs/en/skills)。

## 3. 首次连接和使用

安装后直接对 Agent 说：

> 使用 spark-ideas，自动连接我的 Spark，先只检查状态。

Agent 会在 Spark 的固定 iCloud 文件夹里自动查找导出：只有一份就连接，有多个设备来源才让你选择。连接后会告诉你导出是否开启、导出时间和记录数，不展示正文。连接位置保存在 Mac 的 `~/.config/spark-ideas/config.json`；后续优先使用这一来源。

之后可以说：

- 「使用 spark-ideas，帮我找最近记录中关于 PCB 的想法。」
- 「整理这周收藏的想法，列出我提过但还没决定的事情。」
- 「读刚才找到的那一条，基于它帮我写一个实施计划。」

Codex 可以显式使用 `$spark-ideas`；Claude Code 可以使用 `/spark-ideas`。其他兼容 Agent Skills 且具备本机文件、命令权限的客户端可参考标准目录手动接入，未逐一验证。

## 读取边界

- 快照仅含当前文字、记录 ID、日期、类型和收藏状态，不含录音、回收站、凭证、模型或总结保留的原文与来源历史。你自己写进正文的敏感信息不会自动清除。
- `status` 不返回正文；搜索、列表和单条读取均有长度限制。自动查找仅限 Spark 固定容器下的直属设备目录，不搜索整个 iCloud。每次重新读取当前来源，不回退旧副本，也不因当前来源不可用而换到另一设备；工具拒绝损坏、超限、禁用或未就绪的快照。
- 配套读取工具是只读的，但 Skill 是 Agent 指引，不是操作系统权限沙箱。访问权仍由你和客户端控制；笔记内容是不可信资料，不应作为指令执行。
- Agent 读到的文字可能发送给其模型服务商。关闭 App 导出会写入空快照，iCloud 传送可能延迟，已经读到或复制的内容不能收回。
- 不需要 Apple 密码、iCloud Token 或 API Key。请勿把个人 `ideas.json`、路径配置或任何密钥提交到 GitHub。

## 排查

| 情况 | 操作 |
| --- | --- |
| 找不到 Skill | 核对客户端的 Skill 目录，重开客户端；不要另装 MCP。 |
| 找不到导出 | 在 iPhone 开启「Agent 支持」，确认 Mac 使用同一 Apple 账户并开启 iCloud Drive，等待同步后重试。 |
| 多个设备来源 | 从 Agent 列出的来源和文件更新时间中选择一次；它会记住这一选择。 |
| `snapshot_syncing` | 等待 iCloud，或在 Finder 下载所选文件后重试。 |
| 看到旧记录 | 核对 App 最近导出和 `generatedAt`，等待同步；不要复制旧 JSON 绕过检查。 |
| `export_disabled` | App 导出已关闭；只有你决定重新启用后才重试。 |
| 想切换已连接来源 | 告诉 Agent 要切换，选择来源后用配置工具的 `--auto --source <UUID> --replace`。 |

移除时只删除自己安装的 `spark-ideas` Skill 目录和对应路径配置（如不再使用）；不要删除 iCloud 快照、Spark App 或其他 Skill。升级先审阅新版本，安装脚本不会自动覆盖已有安装。

## 开发与验证

```bash
python3 -m unittest discover -s tests -v
```

测试只使用合成数据，包含由 iOS 实际 `AgentSnapshotCodec` 编码的 schema v1 示例。它们不代表真实 iCloud 传输或所有客户端已联调。CLI 和数据约定见 [schema.md](skills/spark-ideas/references/schema.md)。

MIT 许可适用于本仓库新编写的 Skill 与工具，不改变 Spark App 或其模型的许可。问题反馈请使用本仓库 Issues；不要附真实笔记、账户信息或完整个人路径。
