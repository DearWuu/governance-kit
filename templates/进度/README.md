---
title: 进度目录使用说明（非任务）
status: 挂起
owner: governance-kit
updated: 2026-01-01
goal: 说明每任务一文件的格式、命名、状态枚举与双完成入口
evidence: ""
doc: 进度板.md
---

# 进度任务文件格式

本目录一个任务一个 `.md` 文件，文件名 kebab 命名（`<主题>-<动作>.md`），内容 = frontmatter + 正文。

本文件带合法 frontmatter 且 `status: 挂起`，因此作为说明条目存在而不触发 `BOARD-SCHEMA`——扫描会收录 `tasks_dir` 下所有一级 `.md`，说明文件同样要写成合法条目。

## 字段表

| 字段 | 必填 | 引擎校验 | 说明 |
| --- | --- | --- | --- |
| `title` | 是 | 否 | 缺失回退到正文首个标题，再回退到文件名 |
| `status` | 是 | 是 | 枚举之一，否则 `BOARD-SCHEMA` |
| `owner` | 是 | 是 | 开发者或 agent 标识，与 `dev-<个人>` 分支对应 |
| `updated` | 是 | 是 | `YYYY-MM-DD`，停滞计算基准 |
| `goal` | 是 | 是 | 一句话目标；含 `UI`/`视觉` 触发截图证据要求 |
| `evidence` | 是（可空串） | 完成时非空 | commit / 命令输出 / 截图路径 |
| `doc` | 是（可空串） | 否 | 关联的人读文档，没有留空串 |
| `confirmed_by` | 关单时必填 | 否 | 人工确认人与日期，值形如 `张三 2026-01-02` |

- `---` 必须在第 1 行；逐行 `key: value`，值两侧引号可选；不支持列表、嵌套、多行值。
- 引擎只读上表已知 key，额外字段被忽略且不报错。

## 状态枚举

`待启动` / `进行中` / `待验收` / `已完成` / `挂起`

```
待启动 ──> 进行中 ──> 待验收 ──> 已完成
              │           │
              └──> 挂起 <─┘
```

只有 `进行中` 与 `待验收` 参与停滞判断（默认 14 天）。`待验收` 表示等人工复核，不是等下一个 agent。

## 双完成入口

**AI 自报**：`status: 已完成` + 非空 `evidence` + 正文写完成方标识。

```yaml
status: 已完成
evidence: "commit 1a2b3c4；pytest 42 passed；截图 docs/assets/2026-01-02-check.png"
```

**人工确认**：`confirmed_by` 独立字段，人工复核后填，AI 不得自己填。

```yaml
status: 已完成
evidence: "commit 1a2b3c4；pytest 42 passed"
confirmed_by: "张三 2026-01-02"
```

关单判据 = `status: 已完成` 且 `evidence` 非空 且 `confirmed_by` 非空。引擎不解析 `confirmed_by`，这一项靠人工评审核对。

## 正文骨架

`## 目标` / `## 进展` / `## 下一步` / `## 证据` / `## 完成方`，空段可省略。