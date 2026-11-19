---
title: Trae 字节副本与 SKILL-SYNC 落地
status: 待启动
owner: 未指派
updated: 2026-11-13
goal: 采用方仓库建立 .trae/skills/ 副本与 .trae/rules/ 桥，SKILL-SYNC 门控保持字节相等
evidence:
doc: skill/references/adapters.md
---
# Trae 字节副本与 SKILL-SYNC 落地

## 目标

canonical `.agents/skills/` 已就绪，Trae 副本链路未在任何仓库实际建立。落地动作：复制技能 → 建 rules 桥 → config.skills.copies 登记 → `govern sync` + 门控验证。

## 下一步

选首个采用仓执行并记录踩坑。
