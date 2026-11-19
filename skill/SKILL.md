---
name: governance-kit
description: 搭建或审计 Agent 治理体系时使用——AGENTS.md 分层与行数预算、文档分级（人机分读）、进度板与跨开发者 agent 协作、近期功能实现文档（叙事复核与同步）、文档漂移与索引失同步、前端样式 token 与视觉/契约验收门控、OpenCode TUI 内实时查看治理结果，以及把同一套协议复制到其他仓库。
---

# governance-kit

## 何时用

- 给仓库搭/改 Agent 治理体系：AGENTS.md 分层 + 文档分级 + 进度板 + 近期功能实现文档。
- 审计既有仓库：AGENTS.md 行数与引用、索引失同步、人读文档混入 `文件:行号`、进度板字段或状态非法、逻辑条目 stale/broken。
- 多开发者 + 多 agent 协作：认领、交接、停滞升级、共享抽象归属；前端质量门控：样式 token、视觉回归、契约 breaking、生成物漂移；在 OpenCode TUI 里实时看治理结果并把协议复制到另一个仓库（`init` / `pack` / `sync`）。

## 引擎怎么调

`engine/govern <cmd>`（等价 `PYTHONPATH=engine python3 -m governkit <cmd>`），纯 stdlib、Python≥3.10。在仓库任意子目录执行，向上找 `.git`（缺失则找 `.governance` / `AGENTS.md`）定位根。

| 命令 | 作用 |
| --- | --- |
| `scan` / `health` | 内置规则 + 适配器全跑，逐条打印 findings 与 summary / 同一扫描只出计数 |
| `board` | 从任务文件 frontmatter 汇总进度板，`--status <枚举>` 过滤 |
| `check <rel> <index>` | 进度板打钩：翻转任务文件 checklist 项（`--on`/`--off` 定向，`--toggle` 或缺省即翻转） |
| `logic init <domain>` / `logic add <domain> <id> --file F [--symbol S] [--group 路径] [--kind 分类]` | 建业务域骨架 / 自动定位代码并起草条目，可指定分组与分类 |
| `logic list` / `logic sync [domain]` | 查看条目与状态 / 重定位指针行号并回写 `source_hash`，报 `moved` / `changed` / `broken` / `unchanged` |
| `logic accept <domain> <id>` / `logic check [--strict]` | 叙事复核后盖戳（`ack_hash` 盖成当前内容 hash，刷新域级 `updated`）/ 单独检查近期功能实现文档 |
| `map <rel>` / `outline <rel>` / `view <rel>` | 上下文地图 / 结构大纲 / 带行号查看（`--lines A:B`） |
| `tree` / `tests <rel>` | 目录树（`--mode all\|docs\|contracts`）/ 镜像测试候选路径 |
| `symbols <query>` / `xref <symbol>` | 按名检索符号定义 / 谁引用了该符号 |
| `show <rel>` / `refs` / `docs` | 只读查看单文件 / 只读聚合指针及状态 / 只读文档清单 |
| `html` | 生成自包含全景图（含进度板清单 ☑/☐ 与近期功能实现区块）并写入 `govern-scan-hash`，`--out` 改输出路径；不用 OpenCode 的查看方式见 `skills/govern-view/SKILL.md` |
| `sync` / `init <dir>` / `pack --out <zip>` | 刷技能副本 / 铺骨架（`--with-skill`）/ 打包交付 |
| `cache` / `upgrade` / `version` | 引擎持久缓存（`--clear` 清本仓、`--all --clear` 清整目录）/ 升级提示 / 版本号 |

通用 flag：`--repo PATH`（缺省当前目录）、`--config PATH`、`--json`（信封 `{v,cmd,repo,data,summary,truncated}`）、`--strict`、`--no-adapters`、`--no-cache`。退出码：`0` 正常；`--strict` 且存在 error → `1`；用法/IO 错误 → `2`。不加 `--strict` 时 findings 只报告不阻断，CI 用 `scan --strict`。

- 配置 `.governance/config.json`（也认 `.jsonc` / `.toml`），与 `DEFAULT_CONFIG` 深合并；`severity` 覆盖规则级别。
- 进度板默认在仓库根：`进度板.md`（索引）+ `进度/`（每任务一文件）；`board.path` / `board.tasks_dir` 留空即自动发现。
- 引擎持久缓存落在系统临时目录 `governance-kit/cache/<仓库哈希>.json`，TTL 由 `GOVERN_*_TTL` 覆盖，`--no-cache` 全局旁路。

## 近期功能实现文档工作流

- 存储：仓库内 `.governance/logic/<domain>.json`，一业务域一文件，随 git 入库共享。
- 条目：`id`/`title`/`group`/`kind`/`summary`/`narrative`/`flow`/`sections`（`title`+`body`）/`pointers`（`file`+`symbol`+`line`+`end`+`role`）/`source_hash`/`ack_hash`；域级 `overview`（字符串）与 `crosscut`（`title`+`body`）；状态 `fresh`/`stale`/`broken`/`draft`。
- 深度：条目 `sections` 至少 3 个小节，近期功能域（一功能一域，日常最常查，每域配「配额与配置总表」条目）优先用 功能逻辑/配置与限额/边界与异常/使用与验证，基座域用 处理流程/核心机制/数据与契约/并发与事务/配置与限额/失败与降级/观测与测试/部署与资源，每小节至少 3 句且含机制名、分支条件、配置项与默认值、表名/队列名、超时与重试等可核查细节；`pointers` 2–5 个且 role 区分 入口/核心/下游/配置/测试；域 `overview` 5–8 句写边界与组件总览、数据流向、进程分工，`crosscut` 写并发与锁/超时与预算/开关清单/降级链/观测与门控；`summary`/`narrative` 只作概述，细节一律进 `sections`。
- 分组：`group` 为斜杠分组路径（默认空串挂域根），第一层固定用中文分类名 `功能模块`/`服务与进程`/`服务器资源`/`接口契约`/`数据与存储`/`任务与调度`；`kind` 取 `module`/`service`/`resource`/`api`/`data`/`task`；条目超过 5 个必须分组，插件按 domain → group → 条目 树状展示。
- 改完代码先跑 `govern logic sync`：机械层（指针行号、`source_hash`）自动刷新；叙事层（`summary`/`narrative`/`flow`/`sections` 与域级 `overview`/`crosscut`）由 AI 或人维护，`sync` 绝不改它；对报 `changed` 的条目复核叙事后 `govern logic accept <domain> <id>` 盖戳，只报 `moved` 的条目无需复核。状态按实时源码内容判定：`source_hash` 只含内容不含行号，状态现算并与 `ack_hash` 比较，不跑 `sync` 也会被 `scan`/`logic check` 检出 `LOGIC-STALE`，只移动行号不误报。
- 门控：`LOGIC-BROKEN` 默认 error（指针失效）、`LOGIC-STALE` 默认 warning（代码改过未复核），随 `scan`/`health`/`--strict` 生效，可在 `.governance/config` 的 `severity` 覆盖。
- 细则（字段、深度标准、状态、git 共享、示例）：`docs/近期功能实现.md`；写法细则见 `skills/recent-features/SKILL.md`。

## 只读四命令 + 打钩写

- `board` / `show <rel>`：进度板汇总（每项带 `checklist`）/ 单文件内容（`--offset`/`--limit` 分页）+ 元信息 + 文件内指针 + 关联任务。
- `refs` / `docs`：指针聚合（逐条给来源与状态）/ 文档清单（tier / lines / refs / dead）；指针状态四态：`ok`、`qualified`、`under`、`dead`。
- `check <rel> <index>`：只翻转 checklist 行的 `- [ ]` / `- [x]` 标记（缩进与正文逐字保留），并把 frontmatter `updated` 刷成当天；只接受 task-file 模式的任务文件，围栏代码块内的示例行不算 checklist，越界或非任务文件退出码 2。

## 软约束 vs 硬门控

- **软约束**（默认 warning，不阻断）：AGENTS.md 超目标预算、悬空/欠限定引用、索引未收录、人读文档混入行号或修订痕迹、进度板字段缺失或停滞、技能副本不一致、报告漂移、适配器失败、`LOGIC-STALE`。
- **硬门控**（`--strict` 下 error 即退出 1）：AGENTS.md 超 `line_hard`（100 行）；工具侧 error 级（stylelint error、视觉 FAIL、契约 breaking）；`LOGIC-BROKEN`。
- 升级路径：在 `config.severity` 把某规则改成 `error`；CI 统一跑 `scan --strict`。首次接入先全 warning，消掉真问题后再逐条升级。三态门控（视觉）：`PASS` 放行、`FAIL` 阻断、`UNABLE` 不阻断但必须记原因并挂人工复核。

## OpenCode TUI 插件

- 插件在 `~/.config/opencode/cli.json` 的 `plugins` 数组登记 `{ "package": "<插件目录>" }`；`~/.config/opencode/opencode.jsonc` 的 `plugins` 是服务端插件，不要混用；插件目录须 `npm install` peer 依赖（`solid-js` / `@opentui/core` / `@opentui/solid`），否则宿主静默跳过。
- 呈现：全屏 `/doc-tree`（五 tab：契约层 / docs 分级 / 进度板 / 告警 / 近期功能实现，`1`–`5` 切换）与侧边栏 `sidebar.content` 紧凑组件；逻辑 tab 按 domain → group → 条目 树状展示、按状态着色、指针点击跳代码，顶栏与侧边栏显示计数。
- 键鼠：`j`/`k`、`↑`/`↓` 选择，`Tab`、`1`–`5` 切 tab，`[`/`]` 翻页，`o` 选指针后 `Enter` 跳转，`b` 返回，`/` 过滤，`r` 重扫，`c` 折叠，`Esc` 返回或关闭；动作条按钮与 tab、指针、findings、树行均可点击（清单项单击即打钩/取消），且有键盘等价。
- 接线细节与 `show`/`refs`/`docs` 契约见 `references/adapters.md`。

## 协作协议要点

- 单一事实源在 canonical 仓库，靠 git 分发；副本用 `sync` 刷，禁手工双写。
- 认领 = 进度板 `owner` 字段 + 对齐 `dev-<个人>` 分支；markdown 无 CAS，独立提交**先合入者赢**。
- 交接包三件套：进展 / 证据 / 下一步，落在条目正文，不靠聊天记录；共享抽象专人制，禁并行各改一版。
- 开工 pull、收工 push；`进行中`/`待验收` 超 14 天未更新即黄牌；agent 只出 brief 提案，人拍板。
- 状态词汇对齐 A2A（submitted/working/input-required/completed/canceled），`completed` 不等于关单，仍需 `confirmed_by` 人工确认。

## 指针

- `references/protocol.md`（协作协议）、`references/board-schema.md`（进度板）、`references/doc-tiers.md`（文档分级）、`references/adapters.md`（适配器与 TUI 接线）。
- `docs/近期功能实现.md`：近期功能实现文档的存储、条目字段（含 `sections`）、域级 `overview`/`crosscut`、`group`/`kind` 树状分组、内容深度标准、状态、同步复核与 git 共享；`reference-adapters/` 与 `templates/`：可执行参考门控与骨架模板，总览见 `README.md`。
