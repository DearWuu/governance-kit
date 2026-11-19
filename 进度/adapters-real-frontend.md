---
title: 参考适配器接入真实前端仓实测
status: 待启动
owner: 未指派
updated: 2026-11-13
goal: stylelint/visual 两类参考门控在装有真实依赖的前端仓库跑通并出基线三联图
evidence:
doc: skill/references/adapters.md
---
# 参考适配器接入真实前端仓实测

## 目标

本机未装 stylelint/浏览器，参考实现仅 selftest + 正则语义验证。需在真实前端仓（如 ObsightWork）安装依赖后接线：stylelint --max-warnings=0 进 CI；visual 以 CI 单一环境出基线。

## 下一步

选仓 → 装依赖 → 配 config.adapters → 跑一轮记录误报/漏报。
