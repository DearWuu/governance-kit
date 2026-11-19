"""仓库初始化：生成治理配置、AGENTS 模板、进度板骨架，可选复制技能。

上游：cli.py 的 init 子命令传入目标目录、--with-skill 与套件根。
下游：只写 target 下的初始文件；已存在的一律跳过不覆盖。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List

from .core import DEFAULT_CONFIG

KIT_SKILL_DIR = "skill"
SKILL_DEST = Path(".agents") / "skills" / "governance-kit"
SKILLS_ROOT = "skills"

CONFIG_EXTRA = {
    "_comment": "没有把握的检查项留空数组即关闭：tiers.ai_read/human_read、indexes.files/dirs。",
}

AGENTS_TEMPLATE = """# AGENTS.md

本文件是 AI 会话的仓库导航契约：只写规则、关键禁令与指针，不写叙事。

## 维护纪律

- 本文件不超过 60 行；超了拆到子树 AGENTS.md，根文件只留索引。
- 只写「规则 + 禁令 + 细节指针」，背景与枚举交给 `docs/`。
- 文档只陈述当前状态，不留修订痕迹，历史交给 git。
- 进度写 `进度/` 下一任务一文件，frontmatter 必填，格式见 `进度/README.md`。
- 任务状态只在 `进度板.md` 汇总，任务文件本身不重复维护列表。
- 人读文档正文不堆 文件:行号，代码实证放开发参考。
- 生成物由 `govern html` 刷新后提交，勿手改。
- 交付前跑 `govern scan --strict`，error 清零。

## 目录

- `docs/`：文档根。
- `进度板.md`：进度索引与用法。
- `进度/`：每任务一份 md。
- `.governance/config.json`：治理配置（分级、进度板、技能同步）。
"""

BOARD_TEMPLATE = """# 进度板

任务明细在 `进度/`，一任务一文件；本文件只做索引与说明，不重复任务正文。

## 怎么用

- 新建任务：复制 `进度/example-task.md`，改名为任务 slug（小写 kebab）。
- 必填 frontmatter：title / status / owner / updated / goal / evidence / doc。
- 状态枚举：待启动 / 进行中 / 待验收 / 已完成 / 挂起。
- 汇总视图用 `govern board`；校验用 `govern scan`；全景图用 `govern html`。

## 任务列表

任务列表由 `govern board` 从各任务文件的 frontmatter 汇总，不在此手写。
"""

TASKS_README = """---
title: 进度目录使用说明（非任务）
status: 挂起
owner: governance-kit
updated: 2026-01-01
goal: 说明每任务一文件的格式、命名与状态枚举
evidence:
doc: 进度/README.md
---
# 进度目录使用说明

每个任务一个 markdown 文件，放在本目录下，文件名用小写 kebab，例如 refactor-auth.md。

## frontmatter 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| title | 是 | 一行任务名，出现在进度板与全景图 |
| status | 是 | 待启动 / 进行中 / 待验收 / 已完成 / 挂起 |
| owner | 是 | 负责人或 agent 标识 |
| updated | 是 | 最近更新日期，YYYY-MM-DD |
| goal | 是 | 一句话完成标准 |
| evidence | 已完成必填 | commit / 测试输出 / 截图路径 |
| doc | 否 | 关联方案或设计文档路径 |

## 规则

- 进行中与待验收条目超过 14 天未更新会被判为停滞。
- 已完成但 evidence 为空会被判为自报完成，必须附证据。
- 任务正文写过程与结论，状态只改 frontmatter，不在正文堆修订记录。
"""

EXAMPLE_TASK = """---
title: 示例任务：替换为真实目标
status: 待启动
owner: 未指派
updated: 2026-01-01
goal: 一句话写清完成标准与验收方式
evidence:
doc:
---
# 示例任务：替换为真实目标

## 背景

为什么做、当前痛点是什么。

## 范围

- 做：列出交付物与验收标准。
- 不做：列出明确排除项。

## 完成标准

- [ ] 可复跑的验证命令与预期输出
- [ ] 文档与索引已同步

## 证据

完成后在 frontmatter 的 evidence 填 commit、测试输出或截图路径。
"""


def _dump_config() -> str:
    data: Dict[str, object] = dict(DEFAULT_CONFIG)
    data.update(CONFIG_EXTRA)
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _write(path: Path, content: str, actions: List[str], rel: str) -> None:
    if path.exists():
        actions.append(f"跳过 {rel}（已存在）")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    actions.append(f"创建 {rel}")


def _copy_skill(src: Path, dst: Path, dest_rel: Path, actions: List[str]) -> None:
    count = 0
    for item in sorted(src.rglob("*")):
        if not item.is_file():
            continue
        if "__pycache__" in item.parts or item.suffix == ".pyc":
            continue
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        count += 1
    dst_rel = str(dest_rel.as_posix())
    actions.append(f"复制 {src} -> {dst_rel}（{count} 文件）")


def _copy_tree_files(src: Path, dst: Path, actions: List[str]) -> None:
    for item in sorted(src.rglob("*")):
        if not item.is_file() or "__pycache__" in item.parts or item.suffix == ".pyc":
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if target.exists():
            actions.append(f"跳过 {rel.as_posix()}（已存在）")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.read_bytes())
        actions.append(f"创建 {rel.as_posix()}")


def init_repo(target: Path, with_skill: bool, kit_root: Path) -> List[str]:
    """在 target 下创建初始治理文件；返回创建/跳过清单。

    单一来源：优先复制套件内 templates/（文件化模板，随套件维护）；
    套件未带 templates/ 时回退到本模块内联骨架（离线 vendored 最小形态）。
    """
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    actions: List[str] = []

    templates = Path(kit_root) / "templates"
    if templates.is_dir():
        _copy_tree_files(templates, target, actions)
    else:
        _write(target / ".governance" / "config.json", _dump_config(), actions, ".governance/config.json")
        _write(target / "AGENTS.md", AGENTS_TEMPLATE, actions, "AGENTS.md")
        _write(target / "进度板.md", BOARD_TEMPLATE, actions, "进度板.md")
        _write(target / "进度" / "README.md", TASKS_README, actions, "进度/README.md")
        _write(target / "进度" / "example-task.md", EXAMPLE_TASK, actions,
               "进度/example-task.md")

    if with_skill:
        src = Path(kit_root) / KIT_SKILL_DIR
        if not src.is_dir():
            actions.append(f"跳过技能复制（套件缺少 {src}）")
        else:
            _copy_skill(src, target / SKILL_DEST, SKILL_DEST, actions)
        # skills/ 下每个带 SKILL.md 的子目录都是一个技能，全部安装（新增技能零改动）。
        skills_root = Path(kit_root) / SKILLS_ROOT
        if skills_root.is_dir():
            for sub in sorted(skills_root.iterdir()):
                if not sub.is_dir() or not (sub / "SKILL.md").is_file():
                    continue
                dest = Path(".agents") / "skills" / sub.name
                _copy_skill(sub, target / dest, dest, actions)
    return actions