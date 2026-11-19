---
title: 示例任务：接入治理自检
status: 待启动
owner: dev-example
updated: 2026-01-01
goal: 在 CI 中接入 govern scan 作为门控
evidence: ""
doc: docs/方案/治理接入.md
confirmed_by: ""
---

## 目标

把治理自检接进 CI，PR 上自动跑 `govern scan`，error 级阻断合并。

## 进展

- 已确认 config 位置与规则级别，尚未改 CI 配置。

## 下一步

- 在 CI 增加一步 `govern scan`，先只跑不阻断，收齐一轮 findings 再升 error。

## 完成标准

- [ ] 可复跑的验证命令与预期输出
- [ ] 文档与索引已同步

## 证据

（完成后补：commit、CI 运行链接、命令输出结论）

## 完成方

（AI 自报完成时写 agent 标识；人工确认写 `confirmed_by`）