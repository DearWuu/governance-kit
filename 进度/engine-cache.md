---
title: 引擎持久缓存
status: 已完成
owner: dev-governance-kit
updated: 2026-11-13
goal: /mnt/d 慢盘下重复读取进入百毫秒级，且 scan 保持现算保新鲜
evidence: ObsightServer 实测 show 10.7s→0.077s(≈130×)、docs 18.6s→0.08s(≈230×)、scan 8.8s→0.22s(≈40×)；14 用例含失效负控
doc: README.md
---
# 引擎持久缓存

## 目标

索引 TTL 20s、show 按文件 mtime+size 失效、聚合 TTL 20s、git_last 30s；`--no-cache` 旁路、`govern cache --clear` 清空；tmp+replace 原子写。

## 证据

- `engine/governkit/cache.py` + `tests/test_cache.py`（禁用失效函数的负控用例证明缓存真被校验）。
- 冷/热耗时对比表见 README「引擎持久缓存」。

## 完成方

AI自报（OpenCode 会话，待人工确认）
