---
title: 鼠标点击路径真实终端复核
status: 挂起
owner: 未指派
updated: 2026-11-13
goal: 插件全部可点元素（tab/动作条/树行/指针/findings/checklist/侧边栏）在真实终端逐一实测
evidence:
doc: adapters/opencode-plugin/README.md
---
# 鼠标点击路径真实终端复核

## 目标

tmux 注入环境鼠标事件到不了插件槽位（宿主自带按钮同样不触发），onMouseUp 仅代码级验证、键盘等价已实测。需在真实终端逐个点击并记录失效项。

## 下一步

有真实终端环境时执行；若宿主槽位不支持鼠标透传，则记录为平台限制并仅保留键盘路径。
