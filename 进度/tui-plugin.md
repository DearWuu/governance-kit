---
title: OpenCode TUI 插件（全屏 + 侧边栏 + 嵌套树 + 打钩）
status: 已完成
owner: dev-governance-kit
updated: 2026-11-13
goal: 双呈现（/doc-tree 全屏 + sidebar.content 卡片），富详情四段与全部键盘等价交互真机可用
evidence: tsc --noEmit rc=0；tmux 真机抓屏 /tmp/opencode/gkV-board.txt、gkV-checked.txt、gkW-contract.txt（2026-11-13）
doc: adapters/opencode-plugin/README.md
---
# OpenCode TUI 插件

## 目标

契约层/docs 分级/进度板三 tab；右侧元信息+MD 内容预览(100行/页)+指针跳转+findings；嵌套目录树可折叠；动作条按钮与键盘等价；进度板可视化打钩（只许打钩）。

## 证据

- 隔离实例真机：动作条/嵌套树/翻页/指针跳转/打钩文件翻转/ANSI 蓝(橙=0)/空分组隐藏均有抓屏。
- 侧边栏与 Superagent、Qoder Credits 共存（gk-verify-sidebar.txt）。

## 完成方

AI自报（OpenCode 会话，待人工确认）
