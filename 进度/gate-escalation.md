---
title: 结构规则 warning→error 升级
status: 待启动
owner: 未指派
updated: 2026-11-13
goal: AGENTS-REF/INDEX-SYNC/SKILL-SYNC/GEN-DRIFT 四条在干净运行约两周后切 error（--strict 阻断），由人拍板
evidence:
doc: README.md
---
# 结构规则 warning→error 升级

## 目标

当前四条结构规则为 warning。触发条件：连续约两周日常扫描无误报困扰。动作：`.governance/config.json` 的 severity 表改一行，验收 `--strict` 对注入违规 exit 1。

## 下一步

到期限后由维护者确认误报情况，改 severity 并在两仓（采用方）各跑一轮 strict。
