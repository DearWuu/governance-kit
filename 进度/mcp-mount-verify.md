---
title: MCP 适配器在 Codex/Trae 实际挂载验证
status: 待启动
owner: 未指派
updated: 2026-11-13
goal: adapters/mcp/server.py 在 Codex 与 Trae 的 MCP 配置中实挂载，四只读工具可调用
evidence:
doc: adapters/mcp/README.md
---
# MCP 适配器在 Codex/Trae 实际挂载验证

## 目标

selftest 已过，但未在真实客户端挂载。需按各自 MCP 配置接入并验证 governance_scan/board/map/health 调用与 stdout 纪律。

## 下一步

在任一客户端写入挂载配置 → 会话内调用工具 → 记录兼容性问题。
