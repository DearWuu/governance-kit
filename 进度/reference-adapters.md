---
title: 参考适配器与 MCP
status: 已完成
owner: dev-governance-kit
updated: 2026-11-13
goal: 样式/视觉/契约三类硬门控参考实现 + 只读 MCP 适配器，全部 stdlib 可自检
evidence: visual_gate --selftest 11 项、contract_gate --selftest 9 项、mcp/server.py --selftest 6 项全过（exit 0）
doc: skill/references/adapters.md
---
# 参考适配器与 MCP

## 目标

stylelint token 强制配置、纯 Python PNG 三态视觉门控（PASS/FAIL/UNABLE）、OpenAPI breaking 检测（oasdiff 优先）、MCP stdio 四只读工具。

## 证据

- 三个 `--selftest` 全绿；stylelint 正则语义经 Node 编译验证。
- MCP initialize/tools/list/tools/call 经真实引擎往返。

## 完成方

AI自报（OpenCode 会话，待人工确认）
