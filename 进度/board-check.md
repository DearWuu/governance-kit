---
title: 进度板打钩白名单写
status: 已完成
owner: dev-governance-kit
updated: 2026-11-13
goal: 唯一写操作=翻转 checklist 标记，其余文本逐字节不动，写后缓存即时失效
evidence: check 命令 12 用例全绿；真机打钩文件实况 26:- [x] 与 updated=2026-11-13（/tmp/opencode/gkV-checked.txt）
doc: skill/references/board-schema.md
---
# 进度板打钩白名单写

## 目标

`govern check <rel> <index> --toggle|--on|--off`：只替换标记字符，自动刷新 frontmatter updated，原子写，越界/非任务/legacy 板 exit 2；围栏代码块内示例行不可打钩。

## 证据

- `tests/test_governkit.py` CheckCommandTests 12 项（往返/缩进中文保留/幂等/缓存即时反映）。
- 插件进度板 tab 点击打钩→文件真实翻转→取消还原（tmux 抓屏）。

## 完成方

AI自报（OpenCode 会话，待人工确认）
