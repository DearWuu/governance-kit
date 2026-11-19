# governance-kit

通用 Agent 治理套件：给「多人 + 多 agent + 多仓库」的工程装一层可执行的治理协议——文档结构、AGENTS.md 预算、进度板协作、近期功能实现文档、样式与视觉/契约门控，全部由配置驱动。引擎纯 stdlib、无网络依赖，可由 OpenCode TUI 插件在终端里实时呈现。

## 分层结构

| 层 | 位置 | 职责 |
| --- | --- | --- |
| skill 协议层 | `skill/SKILL.md` + `skill/references/` | 告诉 agent 何时用、怎么协作；写清软约束与硬门控的边界 |
| 引擎层 | `engine/governkit/` | 读配置、跑内置组合规则、编排适配器，产出统一 findings |
| 宿主接入层 | `adapters/opencode-plugin/`、`adapters/mcp/` | 把引擎结果接到 OpenCode TUI 面板与 MCP 服务 |
| 外部适配器（可选） | `config.adapters` + `reference-adapters/` | stylelint / 视觉 / 契约 / 死链；未安装则优雅跳过 |

## 复用与自建边界

- **复用成熟工具**：CSS 规则、视觉 diff、OpenAPI breaking、死链。它们有活跃维护和确定的判定口径，重写只会更差。
- **自建组合规则**：跨文件的 AGENTS.md 行数与引用、索引双射、文档分级、进度板 schema 与停滞、代码逻辑指针与叙事复核、技能副本字节一致、生成物漂移。这类规则没有现成 linter 能表达，需要仓库级视角。
- **边界**：引擎不内嵌、不 fork 任何 linter，只以 shell 命令调用并按 SARIF / lychee JSON / exit code 归一化。规则只写一遍；适配器失败是 finding，不是引擎 bug。

## 目录

```
governance-kit/
├── AGENTS.md                  # 维护本套件的 agent 导航
├── README.md                  # 本文件：总览与快速开始
├── engine/
│   ├── govern                 # 可执行启动器（转交 cli.main）
│   └── governkit/
│       ├── core.py            # DEFAULT_CONFIG（配置权威）、仓库索引、Finding 模型、board_paths
│       ├── cache.py           # 持久缓存：索引/show/git/聚合四段，TTL 与原子落盘
│       ├── rules.py           # 十一条组合规则、parse_board（进度板解析权威）、适配器编排
│       ├── cli.py             # 子命令解析、JSON 信封、退出码
│       ├── board.py           # 进度板文本/JSON 渲染
│       ├── docmap.py          # map / outline / view / tree / tests / symbols / xref
│       ├── docinfo.py         # show / refs / docs：内容、元信息与指针（纯函数）
│       ├── htmlgen.py         # 自包含 HTML 全景图
│       └── initcmd.py         # init 骨架（配置、AGENTS.md、进度板）
├── adapters/
│   ├── opencode-plugin/       # OpenCode TUI 插件（SolidJS + @opentui，源码由宿主加载）
│   └── mcp/                   # MCP 服务（server.py + README）
├── skill/
│   ├── SKILL.md               # 技能入口：触发场景、命令清单、门控分层
│   └── references/
│       ├── protocol.md        # 跨开发者 agent 协作协议
│       ├── board-schema.md    # 进度板字段/状态/双完成入口
│       ├── doc-tiers.md       # 人机分读、索引同步、AGENTS.md 预算
│       └── adapters.md        # 适配器接线与归一化、OpenCode TUI 插件接线
├── skills/                    # 写作与查看技能（init --with-skill 全部安装）
│   ├── recent-features/       # 近期功能实现文档写法
│   └── govern-view/           # 不依赖 OpenCode 的治理可视化（HTML 全景图 + CLI）
├── reference-adapters/
│   ├── stylelint/             # .stylelintrc.json（token/!important/类名）+ 说明
│   ├── visual/                # visual_gate.py（PASS/FAIL/UNABLE）+ 说明
│   └── contract/              # contract_gate.py（breaking/regen）+ 说明
├── templates/                 # 文件化模板，与 init 同口径
│   ├── AGENTS.md
│   ├── .governance/config.json
│   ├── 进度板.md               # 进度板索引模板
│   ├── 进度/                   # 任务文件与目录说明模板
│   └── docs/                  # README.md、五个人读目录
├── 进度板.md                  # 进度板索引（仓库根）
├── 进度/                      # 每任务一文件（仓库根）
├── docs/                      # 近期功能实现文档规范、全景图（html 子命令产物）
│   └── 近期功能实现.md             # 存储、条目字段、树状分组、状态、深度标准、同步复核与 git 共享
└── tests/                     # test_governkit.py + fixtures.py + run.py
```

## 安装

前置：OpenCode V2（TUI 插件依赖宿主的 `cli.json` 插件注册）；Python 3.10+（引擎纯 stdlib，无第三方依赖）；Node/npm（仅装插件 peer 依赖用，插件源码由宿主直接加载，不需要打包）。

### 方式一：只装技能（不需要 TUI 面板）

只要治理协议、功能文档写作与查看能力、不需要终端面板时用这一种。技能按目录被发现，复制到 OpenCode 全局技能目录即可：

macOS / Linux：

```bash
cp -r skill ~/.config/opencode/skills/governance-kit
cp -r skills/recent-features ~/.config/opencode/skills/recent-features
cp -r skills/govern-view ~/.config/opencode/skills/govern-view
```

Windows（PowerShell）：

```powershell
Copy-Item -Recurse skill "$env:USERPROFILE\.config\opencode\skills\governance-kit"
Copy-Item -Recurse skills\recent-features "$env:USERPROFILE\.config\opencode\skills\recent-features"
Copy-Item -Recurse skills\govern-view "$env:USERPROFILE\.config\opencode\skills\govern-view"
```

验证：重启 OpenCode，技能列表出现 `governance-kit`、`recent-features`、`govern-view` 即成功。引擎不需要安装，按需直接调用：`python3 <本仓库>/engine/govern --repo <目标仓库> scan`。

### 方式二：完整插件（引擎 + TUI 面板 + 技能）

1. 把整仓复制到 OpenCode 插件目录，目录名保持 `governance-kit`：

macOS / Linux：

```bash
cp -r <本仓库> ~/.config/opencode/plugins/governance-kit
```

Windows（PowerShell）：

```powershell
Copy-Item -Recurse <本仓库> "$env:USERPROFILE\.config\opencode\plugins\governance-kit"
```

2. 安装插件 peer 依赖（宿主解析不到依赖会静默跳过插件）：

```bash
cd ~/.config/opencode/plugins/governance-kit/adapters/opencode-plugin
npm install --no-save solid-js@^1.9.0 @opentui/core@">=0.8.1" @opentui/solid@">=0.8.1"
```

3. 在 `~/.config/opencode/cli.json` 的 `plugins`（复数，**对象数组**）里登记插件包目录——指向 `adapters/opencode-plugin`，不是仓库根：

```json
{
  "plugins": [
    { "package": "/root/.config/opencode/plugins/governance-kit/adapters/opencode-plugin" }
  ]
}
```

Windows 用户 `package` 写 Windows 路径，例如 `C:/Users/<你>/.config/opencode/plugins/governance-kit/adapters/opencode-plugin`。

4. 按方式一把三个技能复制到全局技能目录（插件不自动注册技能）。

5. 重启 OpenCode，会话内按 `5`（或 `/doc-tree`）能看到「近期功能实现」面板即安装成功。

> `cli.json` 由宿主管理，升级或重置宿主后 `plugins` 键可能被重写丢失：面板消失时重加该键并重启即可。

### 在新仓库启用治理

```bash
python3 ~/.config/opencode/plugins/governance-kit/engine/govern init <目标仓库> --with-skill
```

`init --with-skill` 铺 `.governance/config.json`、`AGENTS.md`、`进度板.md` + `进度/` 骨架，并把协议技能与 `skills/` 下全部技能装到目标仓库的 `.agents/skills/`（仓库级生效，不污染全局）。随后 `govern --repo <目标仓库> scan` 应为 0 error，接入顺序与 severity 建议见下文「快速开始」。

## 快速开始

```bash
# 1. 铺设骨架（已存在的文件跳过不覆盖）
./engine/govern init .

# 2. 按仓库实际改 .governance/config.json
#    tiers.ai_read / tiers.human_read / indexes.files+dirs / adapters / severity

# 3. 首轮只报告，不阻断
./engine/govern scan

# 4. CI 门控：有 error 即退出 1
python3 engine/govern --repo . --strict scan

# 5. 生成文档全景图（写入 govern-scan-hash，供漂移检测）
./engine/govern html

# 6. 看进度板汇总
./engine/govern board
```

- 所有命令支持 `--repo PATH` 与 `--config PATH`；`--json` 输出 `{v,cmd,repo,data,summary,truncated}` 信封，便于接其他工具。
- `init` 用引擎内置骨架；`templates/` 是同一口径的文件化版本（同样的字段表、状态枚举与预算），用于阅读、对比或手工铺设。两者字段必须保持一致。
- 进度板默认在仓库根：`进度板.md`（索引）+ `进度/`（每任务一文件）。`board.path` / `board.tasks_dir` 留空即自动发现（根目录优先，`docs/进度板.md` + `docs/进度/` 兼容旧位置）；任一键显式配置即最优先，另一键留空表示不启用。
- 首次接入把 severity 全留 `warning`，跑一轮消掉真问题，再逐条升 `error`；不要一上来全 error，否则没人看 findings。
- 接入顺序建议：AGENTS.md 预算 → 文档分级 → 进度板 → 索引 → 近期功能实现文档 → 生成物漂移 → 样式/视觉/契约适配器。
- 近期功能实现文档按业务域存为 `.governance/logic/<domain>.json` 并随 git 共享；条目结构、两层维护与同步复核流程见 `docs/近期功能实现.md`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `scan` | 内置规则 + 适配器全跑，逐条打印 findings 与 summary |
| `health` | 同一扫描，只出 repo/files/scan-hash 与 error/warning/info 计数 |
| `board` | 从任务文件 frontmatter 汇总进度板，`--status <枚举>` 过滤 |
| `check <rel> <index>` | 进度板打钩：翻转任务文件 checklist 项（`--on`/`--off` 定向，`--toggle` 或缺省即翻转） |
| `logic init <domain>` | 在 `.governance/logic/<domain>.json` 建业务域骨架 |
| `logic add <domain> <id> --file F [--symbol S] [--group 路径] [--kind 分类]` | 自动定位代码并起草逻辑条目，可指定分组与分类 |
| `logic list` | 查看全部逻辑条目与状态 |
| `logic sync [domain]` | 重定位指针行号并回写 `source_hash`，报告 `moved` / `changed` / `broken` / `unchanged` |
| `logic accept <domain> <id>` | 叙事复核后盖戳：把 `ack_hash` 盖成当前内容 hash，并刷新域级 `updated` |
| `logic check [--strict]` | 单独检查近期功能实现文档 |
| `map <rel>` | 单文件上下文地图：职责摘要、上下游调用链、相关测试候选 |
| `outline <rel>` | 结构大纲：Markdown 标题树 / Python 函数签名 |
| `view <rel>` | 带行号查看，`--lines A:B` 或 `--offset N --limit N` |
| `tree` | 目录树，`--mode all\|docs\|contracts` |
| `tests <rel>` | 该源文件的镜像测试候选路径 |
| `symbols <query>` | 按名检索符号定义位置 |
| `xref <symbol>` | 交叉引用：谁引用了这个符号 |
| `show <rel>` | 只读查看单文件：内容 + 元信息 + refs + task，`--offset N --limit N` 分页 |
| `refs` | 只读聚合 AGENTS/索引文件里的指针及其状态 |
| `docs` | 只读文档清单：tier / lines / refs / dead |
| `html` | 生成自包含全景图并写入 `govern-scan-hash`，`--out` 改输出路径 |
| `sync` | 按 `skills.canonical` → `skills.copies` 复制技能副本 |
| `init <dir>` | 铺 `.governance/config.json`、`AGENTS.md`、根位置 `进度板.md`/`进度/`；`--with-skill` 顺带安装 `skill/` 协议技能与 `skills/` 下全部技能 |
| `cache` | 引擎持久缓存：`--clear` 清本仓库、`--all --clear` 清整个缓存目录；不带 `--clear` 只看现状 |
| `pack --out <zip>` | 打包 `engine/`、`skill/`、`skills/`、`templates/`、`adapters/` 与 README/AGENTS.md |
| `upgrade` / `version` | 升级提示 / 版本号 |

通用 flag：`--repo PATH`（缺省当前目录）、`--config PATH`、`--json`、`--strict`、`--no-adapters`、`--no-cache`（跳过全部缓存）。退出码：`0` 正常；`--strict` 且存在 error → `1`；用法/IO 错误 → `2`。

### 只读四命令 + 打钩写

`board`、`show`、`refs`、`docs` 是给界面与 agent 直接消费用的只读入口，不改仓库状态：

```bash
python3 engine/govern --repo . --json show docs/开发参考/索引.md --offset 0 --limit 200
python3 engine/govern --repo . --json refs
python3 engine/govern --repo . --json docs
```

- `show <rel>`：返回该文件的内容（`--offset`/`--limit` 分页）、元信息、文件内指针（refs）与关联任务（task）。
- `refs`：聚合 AGENTS.md 与索引文件里的指针，逐条给出来源与状态。
- `docs`：文档清单，逐个文档给分级（tier）、行数（lines）、指针数（refs）、失效指针数（dead）。

`check` 只打钩：翻转任务文件正文 checklist 行的 `- [ ]` / `- [x]` 标记，缩进、分隔空白与正文逐字保留，另把 frontmatter 的 `updated` 刷成当天日期。近期功能实现文档由 `logic init` / `add` / `sync` / `accept` 写入，各自只负责骨架、草稿、机械层或复核盖戳。

```bash
python3 engine/govern --repo . check 进度/example-task.md 0      # toggle（默认）
python3 engine/govern --repo . check 进度/example-task.md 0 --on  # 定向勾选，已勾选则幂等
```

- 只接受本仓进度板任务文件（task-file 模式）；legacy 单文件进度板与非任务文件一律退出码 2。
- 围栏代码块里的 `- [ ]` 是示例文本，不计入 checklist，也不会被写。
- 序号越界（0 起，超出 checklist 项数）退出码 2；JSON 信封 `cmd=check`，`data` 给 `path`/`index`/`checked`/`line`/`updated`。
- 写后失效本仓 `show`/`agg` 两段缓存，`board` 与 `show` 立即反映新状态。

指针状态统一为四态：

| 状态 | 含义 |
| --- | --- |
| `ok` | 按当前文件所在目录解析命中 |
| `qualified` | 只能按仓库根解析命中（路径写得不完整但真实存在） |
| `under` | 只按文件名命中，路径欠限定 |
| `dead` | 不存在 |

### 近期功能实现文档

逻辑条目有 `fresh` / `stale` / `broken` / `draft` 四态，按实时源码内容判定：`source_hash` 只含内容不含行号，条目状态现算并与 `ack_hash` 比较，所以不跑 `sync` 也能被 `scan` / `logic check` 检出 `LOGIC-STALE`，只移动行号不会误报。`sync` 只重定位行号并回写 `source_hash`，绝不改 `summary` / `narrative` / `flow` / `sections` 与域级 `overview` / `crosscut`；AI 或人维护叙事层。

改完代码运行 `govern logic sync`：对报 `changed` 的条目复核叙事，再用 `govern logic accept <domain> <id>` 盖戳；只报 `moved` 的条目无需复核。条目字段为 `id`/`title`/`group`/`kind`/`summary`/`narrative`/`flow`/`sections`/`pointers`/`source_hash`/`ack_hash`，域级字段为 `overview`/`crosscut`，`group` 为斜杠分组路径、`kind` 取 `module`/`service`/`resource`/`api`/`data`/`task`，超过 5 个条目必须分组。

深度标准：条目 `sections` 至少 3 个小节，近期功能域（一功能一域，日常最常查，每域配「配额与配置总表」条目）优先用 功能逻辑 / 配置与限额 / 边界与异常 / 使用与验证，基座域用 处理流程 / 核心机制 / 数据与契约 / 并发与事务 / 配置与限额 / 失败与降级 / 观测与测试 / 部署与资源，每小节至少 3 句并写可核查细节（机制名、分支条件、配置项与默认值、表名/队列名、超时与重试、systemd 指令或端口）；条目 `pointers` 2–5 个，role 分 入口/核心/下游/配置/测试，找不到代码对应的机制不写；域 `overview` 写边界与组件总览、数据流向、进程分工（5–8 句），`crosscut` 写并发与锁 / 超时与预算 / 开关清单 / 降级链 / 观测与门控；`summary`/`narrative` 仍是概述，细节一律进 `sections`。完整状态、深度标准、git 共享与示例见 `docs/近期功能实现.md`。

### 治理可视化（不依赖 OpenCode）

`govern html` 生成单文件全景图：总览计数 / 契约树 / 文档树 / 进度板（含清单 ☑/☐ 与 done/total）/ 近期功能实现（域概览 + 条目机制小节 + 指针 + 状态）/ findings 过滤，浏览器直接打开、无外部资源、离线可用；改动后重生成并与改动同批提交（GEN-DRIFT 门控）。HTML 是只读快照，打钩用 `govern check <任务文件> <index> --toggle`。纯终端速览用 `govern scan / board / logic list / show`（均可 `--json`）。详见 `skills/govern-view/SKILL.md`。

## 仓库索引

引擎建索引时忽略 `.git`、`node_modules`、`.venv` 等常见目录，并额外忽略 `.bun`、`.npm`、`.nvm`、`.local`、`.cargo`、`.rustup`、`.gradle`、`.m2`；`go/pkg/mod` 路径整体跳过。这些目录与缓存不进扫描、finding 与文档全景图。

## 引擎持久缓存

引擎把只读结果缓存到系统临时目录的 `governance-kit/cache/<仓库哈希>.json`，按仓库路径哈希隔离，互不干扰。缓存目录可用 `GOVERN_CACHE_DIR` 整体改写。

| 缓存项 | 键 | TTL | 失效条件 |
| --- | --- | --- | --- |
| 仓库索引 | 仓库哈希 | 60s（`GOVERN_INDEX_TTL`） | TTL 到期 |
| `docs` / `refs` / `board` 聚合 | 仓库哈希 + 配置指纹 | 20s（`GOVERN_AGG_TTL`） | TTL 到期或配置改动 |
| `show` 结果 | (rel, offset, limit) + 源文件 `mtime_ns`+`size` + 索引摘要哈希 | 300s（`GOVERN_SHOW_TTL`） | 源文件 `mtime_ns` + `size` 变化即失效 |
| `git_last` | rel + 源文件 `mtime_ns`+`size` | 30s（`GOVERN_GIT_TTL`） | 源文件 `mtime_ns` + `size` 变化即失效 |

- `scan` 的规则判定现算，但复用索引缓存，避免重复遍历仓库。
- `check` 打钩写后失效本仓 `show`/`agg` 两段缓存（源文件变了但索引摘要哈希不变，键不会自动失效）。
- `--no-cache` 全局旁路：所有命令既不读也不写缓存。
- `govern cache --clear` 清空当前仓库的缓存文件；`govern cache --all --clear` 清整个缓存目录；不带 `--clear` 时 `govern cache` 只看现状。
- 缓存写入用临时文件加原子替换（tmp + replace），中途崩溃不会留下半截文件；损坏的缓存文件按空缓存处理，下次写入自动修复。

## OpenCode TUI 插件

`adapters/opencode-plugin/` 是把引擎接到 OpenCode V2 TUI 的参考实现（TypeScript + SolidJS + `@opentui/core` / `@opentui/solid`，源码由宿主加载，不做运行时打包）。

### 登记方式

TUI 插件登记在 `~/.config/opencode/cli.json` 的 `plugins`（复数）数组，条目形如 `{ "package": "<插件目录>" }`：

```json
{
  "plugins": [
    { "package": "/root/.config/opencode/plugins/governance-kit/adapters/opencode-plugin" }
  ]
}
```

`~/.config/opencode/opencode.jsonc` 的 `plugins` 是服务端插件，两处不要混用。宿主读插件目录 `package.json` 的 `name` 与 `exports`，定位到 `governance-kit-opencode-plugin/tui` → `src/index.tsx`。

插件目录必须能解析到 peer 依赖，否则宿主静默跳过：

```bash
cd /root/.config/opencode/plugins/governance-kit/adapters/opencode-plugin
npm install --no-save solid-js@^1.9.0 @opentui/core@">=0.8.1" @opentui/solid@">=0.8.1"
```

插件在启动阶段注册命令与面板槽位，安装与重载 OpenCode TUI 由开发人员手动执行。

### 宿主 API 约定

- `setup()` 内直接调 `context.keymap.layer`（带 `mode:"global"`、`priority:10`）与 `context.ui.slot`。
- 不要用 `createRoot` 包裹这些调用：脱离宿主 owner 会抛 `Keymap.Provider is missing`。

### 两种呈现

| 呈现 | 入口 | 内容 |
| --- | --- | --- |
| 全屏面板 | `/doc-tree` | 五个 tab：契约层 / docs 分级 / 进度板 / 告警 / 近期功能实现；右侧显示元信息、MD 内容预览、指针跳转与 findings |
| 侧边栏组件 | `sidebar.content` | 紧凑视图：计数、最近扫描时间、前 3 条 finding、打开提示 |

MD 内容预览默认每页 100 行（`DOC_PAGE_LINES` 可覆盖），用 `[` / `]` 翻页。

### 键盘

| 按键 | 作用 |
| --- | --- |
| `j` / `k`、`↑` / `↓` | 上下选择 |
| `Tab`、`1`–`5` | 切换 tab（`5` 为近期功能实现） |
| `[` / `]` | 内容翻页 |
| `o` | 选中指针后 `Enter` 跳转 |
| `b` | 返回 |
| `/` | 过滤 |
| `r` | 重扫 |
| `c` | 折叠 / 展开侧边栏摘要卡片 |
| `Esc` | 返回或关闭 |

近期功能实现 tab 按 domain → group 目录 → 条目 树状展示，按状态给条目着色，点击指针跳到对应代码；顶栏与侧边栏显示逻辑条目计数。

### 鼠标

面板提供可点击的动作条按钮（`↻` 重扫 / `◀` 上一页 / `下一页 ▶` / `o` 指针 / `b` 返回 / `c` 折叠 / `x` 关闭），tab、指针项、findings 项、树行、侧边栏「打开」按钮与标题折叠也可点击。每个按钮都有键盘等价（`r`、`[`、`]`、`o`、`b`、`c`、`Esc`）。

- tmux 注入环境收不到鼠标事件，鼠标路径只做了代码级验证；键盘等价路径已实测。
- 插件前端另有内存缓存：`buildSnapshot` 与 `show` 结果按 path+mtimeMs+size 缓存，watcher 或 `r` 触发失效，TTL 20s。

## 内置规则

| 规则 | 检查 | 默认级别 |
| --- | --- | --- |
| `AGENTS-LINE` | AGENTS.md 超目标预算 60 行 / 超硬上限 100 行 | warning / error |
| `AGENTS-REF` | AGENTS.md 与 ref_scope 内的链接、反引号路径是否真实存在 | warning |
| `INDEX-SYNC` | `indexes.dirs` 下一级 md 是否被 `indexes.files` 收录 | warning |
| `DOC-TIER` | 人读目录正文出现 `文件:行号`、修订痕迹标题 | warning |
| `BOARD-SCHEMA` | 状态枚举非法、必填字段缺失、已完成缺 evidence、UI 缺截图 | warning |
| `BOARD-STALE` | 进行中/待验收超 `stale_days`（14 天）未更新 | warning |
| `SKILL-SYNC` | canonical skill 与 copies 是否字节相等 | warning |
| `GEN-DRIFT` | HTML 报告的 `govern-scan-hash` 与当前扫描是否一致 | warning |
| `ADAPTER` | 适配器命令失败或输出未归一化 | warning |
| `LOGIC-BROKEN` | 逻辑条目指针失效（状态 `broken`） | error |
| `LOGIC-STALE` | 逻辑条目代码内容改过但叙事未复核（状态 `stale`，按实时内容判定） | warning |

- 级别在 `config.severity` 改；`AGENTS-LINE` 触及硬上限时固定 error。
- `LOGIC-BROKEN` 默认 error，严格扫描下阻断；`LOGIC-STALE` 默认 warning，可调成 error 升级为门控。两条规则按实时源码内容判定，不依赖是否跑过 `logic sync`。
- 阻断只发生在 `scan --strict`（或 `health --strict`）且存在 error 时；退出 1 表示门控不通过，退出 2 是用法/IO 错误。
- 配置 schema 以 `engine/governkit/core.py` 的 `DEFAULT_CONFIG` 为唯一权威，本文件与模板只做示例。

## 自检

```bash
python3 engine/govern --repo . --strict scan                        # 自扫，应零 findings
python3 tests/run.py                                              # 引擎单测
python3 reference-adapters/visual/visual_gate.py --selftest        # 视觉门控自检
python3 reference-adapters/contract/contract_gate.py --selftest    # 契约门控自检
wc -l AGENTS.md skill/SKILL.md                                     # 行数预算
```

## 许可与来源

- 本仓库当前未附 LICENSE 文件；对外分发前由维护者确定许可并补齐。
- 外部工具（stylelint、Playwright、pixelmatch 思路、oasdiff、lychee）各自遵循上游许可；本套件只以命令调用，不内嵌或 fork 其代码。
- 状态词汇（submitted/working/input-required/completed/canceled）参考 A2A 协议命名，本套件只借用词汇层，不实现 A2A 传输。
