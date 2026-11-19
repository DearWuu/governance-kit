---
name: govern-view
description: 当需要在不使用 OpenCode 的环境查看治理可视化（进度板、告警、近期功能实现、文档全景图）、生成或刷新 HTML 全景图、用 CLI 查看治理数据或打钩清单项时使用；不用于写功能文档（走 recent-features）、不用于搭建治理体系（走 governance-kit）
---

# 治理可视化查看（不依赖 OpenCode）

治理数据都在仓库里（`.governance/`、`进度/`、`docs/`），TUI 面板只是消费端之一。
HTML 全景图与 CLI 在任何环境可用：浏览器、CI、纯终端、其它 AI 工具。

命令约定：下文 `govern` 指 `python3 <governance-kit 根>/engine/govern --repo <仓库根>`；
套件装在 OpenCode 插件目录时根为 `plugins/governance-kit`，也可用 `govern pack` 出的 zip 解压使用。

## 1. HTML 全景图（首选）

```bash
govern html                          # 写 docs/文档全景图.html（配置 html.path 可改）
govern html --out /tmp/preview.html  # 临时预览，不落仓库
```

浏览器直接打开产物：单文件、内联 CSS/JS、无外部资源、离线可用。区块：

- 总览：文件 / 任务 / 功能条目 / 待复核 / error / warning / info 计数
- 契约树：AGENTS.md 层级，超行数预算标红
- 文档树：AI读 / 人读 / 未分级
- 进度板：按状态分组，负责人 / 更新日期 / 证据，清单项 ☑/☐ 与 done/total
- 近期功能实现：域概览与横切设计 → 条目（状态点、机制小节、流程、指针 file:line），可折叠
- Findings：按严重度 / 规则 / 关键词过滤

刷新纪律：代码或文档改动后跑 `govern html` 重生成，与改动同批提交。
HTML 内 `govern-scan-hash` 由 GEN-DRIFT 规则校验，忘刷新会被 `govern scan` 抓出。
HTML 是只读快照：页面内不能打钩（无后端），打钩走第 3 节 CLI 或 TUI。

## 2. CLI 速览

| 命令 | 看什么 |
|---|---|
| `govern scan [--strict]` | 全部告警；strict 下 error 退出码 1 |
| `govern health` | 只看汇总计数 |
| `govern board [--json]` | 进度板任务与 checklist |
| `govern logic list` | 近期功能实现全部域与条目状态（fresh/stale/broken/draft） |
| `govern logic check [--strict]` | 功能文档门控：broken/stale |
| `govern show <file>` | 单文件内容 + 元信息 + 指针 + 关联任务 |
| `govern view / tree / symbols / xref` | 带行号查看 / 目录树 / 符号检索 / 交叉引用 |

各命令加 `--json` 输出结构化 envelope，可脚本化二次渲染。

## 3. 无 TUI 打钩

```bash
govern board                        # 先看清单项 index（0 起）
govern check 进度/<任务>.md <index> --toggle
govern check 进度/<任务>.md 0 --on  # 或 --off 定向
```

这是任务文件唯一允许的写操作；打钩后重跑 `govern html` 同步全景图。

## 4. 验收

- HTML 含 `<meta name="govern-scan-hash">`，与重扫 scan-hash 一致（无 GEN-DRIFT）。
- 无外部资源引用，离线可打开。
- 近期功能实现区块与 `govern logic list` 的条目数、状态一致。
- 进度板区块的 ☑/☐ 与任务文件实际勾选一致。