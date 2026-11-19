---
title: 引擎核心与统一 CLI
status: 已完成
owner: dev-governance-kit
updated: 2026-11-13
goal: stdlib/Python3.10 单引擎提供全部治理命令，统一 JSON 信封与退出码 0/1/2
evidence: python3 -m unittest discover → 103 tests OK；govern --repo . --strict scan 0 findings（2026-11-13）
doc: README.md
---
# 引擎核心与统一 CLI

## 目标

规则只写一份：仓库索引、8+ 组合规则、适配器编排、文档视图、init/pack/sync，全部经 `engine/govern` 暴露。

## 证据

- `tests/` 103 项 unittest 全绿（含引擎契约、缓存、打钩）。
- 真实仓只读扫描：ObsightServer 0 findings；ObsightWork 23（真超限+可疑引用）。

## 完成方

AI自报（OpenCode 会话，待人工确认）
