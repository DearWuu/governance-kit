---
title: skill 协议层与 init 单源模板
status: 已完成
owner: dev-governance-kit
updated: 2026-11-13
goal: 协作协议/板 schema/分级/接线文档成套；init 以 templates/ 为单一来源且幂等
evidence: govern init 二次执行 11 项「跳过（已存在）」；init 产物 strict scan 0 findings；SKILL 80 行内
doc: skill/SKILL.md
---
# skill 协议层与 init 单源模板

## 目标

`.agents/skills/` 可发现的协议层（四份 references）；`init` 优先复制 templates/、无 templates 回退内联，二次执行不覆盖。

## 证据

- `/tmp` 夹具 init→scan--strict 0 findings；幂等抓验（跳过计数）。
- AGENTS 47/50、SKILL 80/80 行预算达标。

## 完成方

AI自报（OpenCode 会话，待人工确认）
