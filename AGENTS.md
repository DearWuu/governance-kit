# AGENTS.md

`governance-kit`：通用 Agent 治理套件（skill 协议 + config 驱动 CLI + 宿主接入 + 可选外部适配器）。本文件给**维护本套件**的 agent 用，只留导航级信息。

## 目录职责

- `skill/SKILL.md`：技能入口——触发场景、命令清单、软约束/硬门控分层（≤80 行）。
- `skill/references/`：`protocol.md` 协作协议、`board-schema.md` 进度板、`doc-tiers.md` 文档分级、`adapters.md` 外部适配器接线与 TUI 插件接线；SKILL 只放指针。
- `engine/governkit/core.py`：`DEFAULT_CONFIG`（配置 schema 权威）、仓库索引、Finding 模型、`board_paths`（进度板位置自动发现）。
- `engine/governkit/cache.py`：持久缓存（索引/show/git/聚合四段，TTL 由 `GOVERN_*_TTL` 覆盖，tmp+replace 原子落盘）。
- `engine/governkit/rules.py`：十一条组合规则、`parse_board`（进度板解析权威，走 `core.board_paths`）、指针解析与适配器编排。
- `engine/governkit/cli.py` 等：子命令解析、进度板渲染（`board`）、文档视图（`docmap`）、只读四命令的数据层（`docinfo`：内容/元信息/指针）、全景图（`htmlgen`）、init 骨架（`initcmd`）、打钩写（`checkcmd`：只翻转 checklist 标记）。
- `进度板.md` 与 `进度/`：本仓进度板（仓库根，一任务一文件；打钩走 `govern check` 或插件进度板 tab）。
- `docs/近期功能实现.md`：近期功能实现文档规范（存储路径、条目字段与 `group`/`kind` 树状分组、条目 `sections` 与域级 `overview`/`crosscut` 深度标准、四态、机械层与叙事层分工、`LOGIC-BROKEN`/`LOGIC-STALE` 门控、git 共享）。
- `engine/govern`：可执行启动器；`reference-adapters/`：stylelint 配置、`visual_gate.py`、`contract_gate.py`，各带 README。
- `adapters/opencode-plugin/`：OpenCode TUI 插件（SolidJS + `@opentui`，入口 `src/index.tsx`）；`adapters/mcp/`：MCP 服务。
- `templates/`：文件化模板，与 `initcmd` 内置骨架同口径；`tests/`：unittest，`python3 tests/run.py`。

## 缓存

- 引擎持久缓存落在系统临时目录 `governance-kit/cache/<仓库哈希>.json`（`GOVERN_CACHE_DIR` 可改目录）：索引 TTL 20s（`GOVERN_INDEX_TTL`）、docs/refs/board 聚合 TTL 20s（`GOVERN_AGG_TTL`）、`show` 按 (rel,offset,limit) 且以源文件 mtime_ns+size 失效（`GOVERN_SHOW_TTL` 300s）、`git_last` 同策略（`GOVERN_GIT_TTL` 30s）；写入用 tmp + replace 原子替换。
- `--no-cache` 全局旁路，`govern cache --clear` 清本仓库、`--all --clear` 清整个缓存目录。
- 插件前端内存缓存：`buildSnapshot` 与 `show` 结果按 path+mtimeMs+size 缓存，watcher 或 `r` 失效，TTL 20s；MD 预览每页 100 行（`DOC_PAGE_LINES` 覆盖）。
- 实现见 `engine/governkit/cache.py`；细节见 `README.md` 与 `skill/references/adapters.md`。

## 自检命令

```bash
python3 engine/govern --repo . --strict scan     # 自扫，应零 findings
python3 tests/run.py                            # 引擎单测
python3 -c "import json;json.load(open('templates/.governance/config.json'))"
wc -l AGENTS.md skill/SKILL.md templates/AGENTS.md
```

- 不在此目录启动任何长驻服务；CLI 是短命进程。插件安装与 OpenCode 重载由开发人员执行。

## 约定

- 纯 stdlib、Python≥3.10；引擎不引入运行期第三方依赖（适配器命令不算）。
- 单文件 ≤2000 行硬上限，触线即拆；同一规则只写一遍，外部 linter 一律走 `config.adapters`。
- 配置 schema 只改 `DEFAULT_CONFIG`；`initcmd` 由它生成配置，所以只需同步 `templates/.governance/config.json`、README 规则表与 references。
- 改 `templates/` 或 `initcmd` 骨架 → 同步 references 的字段表、状态枚举与预算数字。
- 退出码 0/1/2 与 `--strict` 语义是 CI 依赖的契约，不要改。
- 适配器命令串必须让 `sarif` / `lychee` 关键字出现在命令行里，否则掉进 generic-exit。
- `tasks_dir` 下所有一级 md 都会建任务条目：说明文件须带合法 frontmatter 且 `status: 挂起`。
- TUI 插件登记在 `~/.config/opencode/cli.json` 的 `plugins` 数组，条目 `{ "package": "<插件目录>" }`；`~/.config/opencode/opencode.jsonc` 的 `plugins` 是服务端插件，不要混用。
- 插件须在自身目录 `npm install` peer 依赖（`solid-js` / `@opentui/core` / `@opentui/solid`），否则宿主静默跳过。
- 插件 `setup()` 内直接调 `context.keymap.layer` 与 `context.ui.slot`，禁止 `createRoot`（会抛 `Keymap.Provider is missing`）。
- 文档只陈述当前状态，不留修订痕迹；不入库密钥，适配器命令禁写死 token。
