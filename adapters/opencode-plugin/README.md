# governance-kit OpenCode TUI 实时插件（参考适配器）

这是 governance-kit 的 **参考适配器**，不是通用发行版。它把套件引擎的扫描结果接进 OpenCode TUI：全屏面板里左侧是三类治理树加一张告警总览、右侧是选中文件的四段详情（元信息 / 内容预览 / 指针 / findings）；侧边栏常驻一张摘要卡片；文件变更后自动重扫。

- 目标宿主：OpenCode V2 TUI 插件 API（`@opencode/plugin/tui` 的 `Plugin.define` 同形对象，`context.keymap.layer` / `context.ui.panel` / `context.ui.slot`）。
- 引擎：`<kit>/engine/govern`。本目录位于 `<kit>/adapters/opencode-plugin`，入口 `src/index.tsx` 到引擎的相对路径是 `../../../engine/govern`。
- 技术栈：TypeScript + SolidJS + `@opentui/core` / `@opentui/solid`，**不做运行时打包**，源码由 OpenCode 宿主加载。

```
opencode-plugin/
├── package.json          # name + exports["./tui"]，宿主据此定位入口
├── tsconfig.json         # JSX 走 @opentui/solid，仅用于类型检查/编辑器
├── README.md
└── src/
    ├── index.tsx         # 插件入口：命令、session.panel / sidebar.content 槽位、keymap 层、cleanup
    ├── session.ts        # 状态与动作中枢：树快照、扫描、详情调度、导航栈、翻页、过滤
    ├── panel.tsx         # 全屏面板：三类树 + 告警总览、键盘映射、页脚提示、选中项滚动
    ├── detailpane.tsx    # 右侧四段详情：元信息 / 内容预览 / 指针 / findings
    ├── detail.ts         # 引擎 show 客户端、缓存、fs 直读降级、指针目标解析
    ├── sidebar.tsx       # 侧边栏摘要卡片（可折叠）
    ├── tree.ts           # 三类树快照（按目录嵌套）、进度板解析、告警行构造
    ├── repo.ts           # 文件系统索引、配置装载、docs 分级、监听目录
    ├── cache.ts          # 前端内存缓存：文件内容 path+mtimeMs+size、派生结果 TTL
    ├── scan.ts           # 子进程调用 engine/govern，统一信封归一化与降级
    ├── textwrap.ts       # 终端文本宽度口径：标题尾部省略 + 叙事软换行
    └── types.ts          # 共享类型、标签与主题解析
```

## 本地启用（由开发人员执行）

> 本目录内的插件安装、OpenCode 重载必须由开发人员手动执行；agent 不代跑 `npm install`、不启动/重启服务。

1. 安装 peer 依赖（二选一）：
   - 装进本目录（宿主会从插件目录向上解析）：
     ```bash
     cd /root/.config/opencode/plugins/governance-kit/adapters/opencode-plugin
     npm install --no-save solid-js@^1.9.0 @opentui/core@">=0.8.1" @opentui/solid@">=0.8.1"
     ```
   - 或装进全局 OpenCode 配置目录：`cd ~/.config/opencode && npm install --no-save ...`
   - 只做类型检查时再加 `@types/node`；运行时不需要它。
2. 在 `~/.config/opencode/cli.json`（TUI 配置）的 `plugins` 中加入本地目录：
   ```json
   {
     "plugins": [
       { "package": "/root/.config/opencode/plugins/governance-kit/adapters/opencode-plugin" }
     ]
   }
   ```
   TUI 插件登记在 `cli.json` 的 `plugins`（复数）；`opencode.jsonc` 的 `plugins` 是服务端插件，两者不要混用。宿主的 `Host.resolve` 会读本目录 `package.json` 的 `name` 与 `exports`，定位到 `governance-kit-opencode-plugin/tui` → `src/index.tsx`。
   注意：`setup()` 内直接调用 `context.keymap.layer` / `context.ui.slot`，不要用 `createRoot` 包裹——脱离宿主 owner 会报 `Keymap.Provider is missing`。
3. **完全退出并重新启动 OpenCode TUI**（不是只 reload 会话）。插件在启动阶段注册命令与面板槽位。
4. 在 TUI 中执行 `/doc-tree`，或从命令面板选择「文档树：打开 governance-kit 实时面板」。面板以 `fullscreen` 呈现。`/doc-tree` 需要当前会话里已有一条会话消息，否则宿主没有可挂载面板的活动会话。

`package.json` 的 `peerDependencies` 声明为 `@opentui/core >=0.6.6`、`@opentui/solid *`、`solid-js ^1.9.0`。其中 `@opentui/core >=0.6.6` 是套件约定的下限；本适配器使用的 `ui.panel.open` / `session.panel` 槽位需要 V2 宿主（实测类型来自 `@opencode/plugin@2.2.7`，其对 `@opentui/core` 的要求是 `>=0.8.1`）。V1 宿主不会加载本插件。

## 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DOC_HEALTH_PYTHON` | `python3` | 运行扫描引擎的 Python 解释器。 |
| `DOC_HEALTH_GOVERN` | `<kit>/engine/govern` | 可选覆盖引擎路径；通常不需要设置。 |
| `DOC_HEALTH_SCAN_TIMEOUT_MS` | `30000` | 单次扫描 / show 超时（毫秒）；超时会杀掉子进程并进入降级。 |
| `DOC_PAGE_LINES` | `100` | MD 内容预览每页行数；`[`/`]` 翻页。 |
| `DOC_TREE_SIDEBAR` | `append` | 侧边栏卡片位置：`append`=默认（原生内容之下、随插件启用顺序）；`prepend`=强制置顶到 sidebar.content 最前（会盖在原生 Context 块之上）。 |

## 引擎契约

插件对引擎有两处调用。

**扫描**（拿 findings）：

```bash
$DOC_HEALTH_PYTHON <kit>/engine/govern --repo <repo> --json scan
```

stdout 按统一信封解析。适配器与 `engine/governkit/core.py` 的 `Finding.to_dict()` 对齐，期望：

```json
{
  "ok": true,
  "repo": "/abs/repo",
  "generatedAt": "2026-11-12T10:00:00Z",
  "findings": [
    {
      "rule": "AGENTS-LINE",
      "severity": "warning",
      "file": "AGENTS.md",
      "line": 0,
      "message": "72 行，超过目标预算 60",
      "fix": "删叙事/枚举改指针"
    }
  ],
  "summary": { "error": 0, "warning": 1, "info": 0, "total": 1 }
}
```

兼容性：

- `findings` 也可以放在 `data.findings`、`result.findings`、`payload.findings`、`scan.findings`，或直接输出 finding 数组。
- `severity` 缺失按 `warning`；`warn/note/hint` 分别归一到 `warning/info`。
- `summary` 缺失或字段不全（例如引擎只给 error/warning/info、没有 total）时按 findings 现算；`generatedAt/generated_at/scannedAt/timestamp` 任一存在即作为「最近扫描时间」，都没有则取适配器收到结果的当前时间。
- `ok: false` 或 `success: false` 进入「降级」状态：仍然展示已返回的 findings，同时显示引擎自报错误。

**近期功能实现文档**（拿业务域的架构条目）：

```bash
$DOC_HEALTH_PYTHON <kit>/engine/govern --repo <repo> logic list --json
```

`data` 段期望：

```json
{
  "dir": ".governance/logic",
  "domains": [
    {
      "domain": "auth",
      "title": "鉴权域",
      "updated": "2026-11-16",
      "file": ".governance/logic/auth.json",
      "entries": [
        {
          "id": "login",
          "title": "登录鉴权",
          "summary": "账号密码登录与会话签发",
          "narrative": "多行叙事…",
          "flow": ["校验账号", "签发令牌"],
          "pointers": [{ "file": "src/auth/login.py", "symbol": "login", "line": 20, "end": 60, "role": "实现" }],
          "status": "fresh",
          "status_detail": "2026-11-16 复核通过"
        }
      ]
    }
  ]
}
```

- 近期功能实现文档文件在 `.governance/logic/*.json`，每个文件一个业务域。
- `status` 取 `fresh`（叙事与代码同步）/ `stale`（代码改过未复核）/ `broken`（指针失效）/ `draft`（新起草未复核）。
- `group` 是斜杠分隔的分组路径（空串表示未分组），`kind` 是条目类型（module / service / resource / api / data / task）。
- 引擎没有 `logic` 子命令、非 0 退出、超时或输出非法 JSON 时，近期功能实现 tab 显示降级原因，其它 tab 照常工作。

**单文件详情**（拿内容、指针与分级）：

```bash
$DOC_HEALTH_PYTHON <kit>/engine/govern --repo <repo> --json show <rel> --offset <N> --limit <N>
```

`data` 段期望：

```json
{
  "path": "AGENTS.md",
  "exists": true,
  "tier": "none",
  "lines_total": 39,
  "git_last": "2026-11-12",
  "offset": 1,
  "next": 21,
  "content": ["# AGENTS.md", "..."],
  "refs": [{ "line": 7, "target": "skill/SKILL.md", "status": "ok", "resolved": "skill/SKILL.md" }],
  "task": null
}
```

- `offset` 从 **1** 起算；`next` 是下一段的起始 offset，末段为 `null`。
- `tier` 取 `ai` / `human` / `none`；`refs[].status` 取 `ok` / `qualified` / `under` / `dead`。
- 适配器每页取 **100 行**（`--limit 100`，可用 `DOC_PAGE_LINES` 覆盖）。右侧详情要同屏放下「元信息 / 内容预览 / 指针 / findings」四段，超出部分用 `[` / `]` 翻页或 PageUp/PageDown 滚动查看。
- 引擎侧 `show` 结果持久缓存，按 (rel, offset, limit) 命中、以源文件 `mtime_ns`+`size` 失效；需要绕过时用 `govern --no-cache`。
- 引擎缺失、解释器不可用、子进程非 0 退出、输出不是 JSON、超时，都降级为**文件系统直读**：同样按 `offset/limit` 切窗口，面板顶部显示降级原因，左侧树与内容预览照常可用。

## 面板行为

- **契约层**：`.governance/config.*`、各级 `AGENTS.md` / `AGENTS.override.md` / `CLAUDE.md`、配置中 `tiers.ai_read` 指向的文档；未配置 tiers 时按 `docs/开发参考` 与 `docs/README.md` 启发式归入 AI 读。指令文件与 AI 读文档按路径段嵌套成目录节点（`▸`/`▾` 可折叠，默认展开第一层），每个目录节点下挂该目录自己的 AGENTS.md。
- **docs 分级**：`docs/**/*.md` 按 `tiers.ai_read` / `tiers.human_read` 分组，组内同样按目录嵌套；两者都为空时用 `docs/开发参考` / `docs/方案|报告|运维|接口|技术复盘` 做启发式，其余进「未分级」。
- **进度板**：位置与引擎同口径自动发现——显式配置优先（`board.path` / `board.tasks_dir` 任一非空即整对生效，未写的那键为空即不启用），否则依次探测仓库根 `进度板.md`+`进度/`、`docs/` 旧位置；都没有时板 tab 显示「未发现进度板」空态提示，侧边栏也不出进度摘要行。任务卡读 frontmatter 的 `title/status/owner/updated/goal/evidence`，旧式 `## [状态] 标题` 条目也会被识别。非法状态保留原文并单独成组。
- **告警**（第四个 tab，按 `4` 或点顶栏「告警 N（…）」进入）：行是**全量 findings**，按 severity（error→warning→info）→ rule → file → line 排序，每行 `W BOARD-STALE 进度/xxx.md:0 · 信息截断`（severity 配色 + rule + `file:line` + message，超宽截断不换行）。`j`/`k` 移动选中，`Enter` 或点击打开该文件（右侧显示元信息、内容预览与**该文件的全部 findings**），`b` 退回告警列表；`/` 按 file / rule / message 过滤。文件不在树里（`docs/文档全景图.html`、进度板任务文件等）照样以虚拟行列出（`kind:"file"` + 真实 path），详情按 path 取内容与 findings。没有告警时左侧显示「无告警」（success 配色）。
- **近期功能实现**（第五个 tab，按 `5` 或点顶栏「近期功能实现 N（stale x · broken y）」进入）：数据来自引擎 `govern logic list --json`，近期功能实现文档文件在 `.governance/logic/*.json`（每个文件一个业务域）。树结构是**域 → 分组目录 → 条目**：域为分组行（标题 + 条目数 + 异常数），条目按 `group` 的斜杠路径挂成目录节点（`▸`/`▾`，**所有层级默认展开**，首屏即可见条目），`group` 为空的条目直接挂在域下。条目行是「状态标记 + 标题」，标题列有最小宽度且不被摘要挤压（过长时按尾部省略，摘要让位）；四种状态各有配色与含义：`✓ fresh` 已同步（success 色）、`● stale` 待复核（warning 色）、`✗ broken` 指针失效（error 色）、`○ draft` 草稿（次要文字色）。条目按 broken → stale → draft → fresh 再按标题排序。`j`/`k` 移动选中，`Enter` 打开条目详情（标题 / 状态徽章 / 归属域 / 摘要 / 叙事 / 流程 / 指针），`o` 选指针后 `Enter` 或点击跳到该指针指向的代码文件，`b` 退回条目详情；`/` 按域标题与条目标题 / 摘要 / 叙事过滤。域或目录命中过滤时保留整棵子树，条目被滤空后目录自动隐藏。详情里的叙事先按详情区可用宽度软换行、再按 100 行分页（`[` / `]`），长段落能翻页看完。引擎没有 `logic` 子命令或调用失败时左侧显示降级原因，其它 tab 不受影响。
- **右侧四段**：元信息（路径、分级、总行数、最近提交）→ 内容预览（带行号的原文，行尾提示剩余行数）→ 指针列表（可高亮、可跳转）→ 当前文件的 findings。指针跳到树外的文件时，元信息下方显示「← 跳转自 …（b 返回）」面包屑。
- **默认选中即有内容**：面板打开时自动选中第一个**文件**条目并加载预览，不停在分组节点上。
- **实时刷新**：监听 `docs/` 全树、`.governance/`、各级 AGENTS.md 所在目录、进度板文件与任务目录（自动发现到根位置时含 `进度/`）；任一路径变更后 **500ms 防抖**，先重建树，再跑一次扫描。树与检查器解耦，扫描失败不影响树。
- **降级**：`engine/govern` 不存在、`python3` 不可用、子进程非 0 退出、输出不是 JSON、超时，都会把 findings 区切到「检查器不可用」并显示原因；左侧树仍来自文件系统直读。修复后按 `r` 重试，或保存任一被监听文件触发重试。
- **前端内存缓存**：`buildSnapshot` 与 `show` 结果按 path+mtimeMs+size 缓存，watcher 变更或按 `r` 重扫时失效，TTL 20s。

## 鼠标

面板提供可点击的动作条按钮与可点击条目：

| 元素 | 鼠标动作 | 键盘等价 |
| --- | --- | --- |
| `↻` 重扫 | 点击 | `r` |
| `◀` 上一页 | 点击 | `[` |
| `下一页 ▶` | 点击 | `]` |
| `o` 指针 | 点击 | `o` |
| `b` 返回 | 点击 | `b` |
| `c` 折叠 | 点击 | `c` |
| `x` 关闭 | 点击 | `Esc` |
| tab（契约层 / docs 分级 / 进度板 / 告警 / 近期功能实现） | 点击切换 | `Tab`、`1`–`5` |
| 顶栏「告警 N（E… W… I…）」 | 点击切到告警 tab | `4` |
| 顶栏「近期功能实现 N（stale x · broken y）」 | 点击切到近期功能实现 tab | `5` |
| 指针项、findings 项、树行 | 点击选中 / 打开 | `↑`/`↓`、`j`/`k` |
| 告警行 | 点击打开该文件（详情显示该文件全部 findings） | `Enter` |
| 目录节点 | 点击折叠 / 展开（并选中该目录） | `Enter` |
| 侧边栏「打开」按钮 | 点击拉起全屏面板 | `/doc-tree` |
| 侧边栏标题 | 点击折叠 / 展开 | `c` |

- tmux 注入环境收不到鼠标事件，鼠标路径只做了代码级验证；键盘等价路径已实测。

## 键盘

快捷键只在面板聚焦时生效。

| 按键 | 作用 |
| --- | --- |
| `1` / `2` / `3` / `4` / `5` | 切到 契约层 / docs 分级 / 进度板 / 告警 / 近期功能实现。 |
| `Tab` / `Shift+Tab` | 循环切换 tab（正序 / 逆序）。 |
| `↑` `↓` / `j` `k` | 移动选中项；选中行自动滚进树的可视区。 |
| `Home` / `End` | 跳到首个 / 末个条目。 |
| `[` / `]` | 内容翻页（上一段 / 下一段）。 |
| `o` | 在指针列表里循环选中。 |
| `Enter` | 跳到选中的指针目标并压入导航栈；选中目录节点时折叠 / 展开；告警 tab 里打开该告警对应的文件；近期功能实现 tab 里打开选中的条目（已用 `o` 选中指针时跳到该指针指向的代码）。 |
| `b` | 返回导航栈上的上一个文件（告警 tab 里退回告警列表，近期功能实现 tab 里退回条目详情）。 |
| `/` | 进入过滤输入态；输入即筛树（告警 tab 按 file / rule / message 过滤，近期功能实现 tab 按域标题与条目 title / summary / narrative 过滤），`Enter` 确认，`Esc` 清空并退出。 |
| `r` | 手动重扫（重建树 + 重跑检查器）。 |
| `c` | 折叠 / 展开侧边栏摘要卡片。 |
| `PgUp` / `PgDn` | 上下滚动右侧详情区。 |
| `Esc` | 关闭面板。 |

`Tab` 由宿主 keymap 层接管：宿主会先吃掉 `Tab`（编辑器补全），插件在 `keymap.layer` 里以 `mode:"global"`、`priority:10` 注册绑定，并且只在面板打开时启用，因此面板之外 `Tab` 仍是宿主行为。

## 侧边栏

`sidebar.content` 槽位常驻一张摘要卡片，与宿主自带的其他侧边栏卡片并存：

```
▾ 文档树 · governance-kit        打开
契约层 7 · docs 分级 0 · 进度板 0 · 告警 2 · 近期功能实现 5
告警 E0 W1 · 扫描 12:22:37
近期功能实现 5 · stale 1 · broken 1
W GEN-DRIFT docs/文档全景图.html
/doc-tree 打开详情
```

- 五个 tab 的条目数、E/W 告警数、近期功能实现条目数与 stale/broken 异常数、最近扫描时间，最多 3 条高危（error 优先，其次 warning）。
- 点击标题（或按 `c`）折叠成只剩一行标题；「打开」按钮在折叠态仍可见。
- 点击「打开」或「/doc-tree 打开详情」直接拉起全屏面板。`/doc-tree` 需要当前会话里**已有一条会话消息**，否则宿主没有可挂载面板的活动会话，命令打不开（宿主约束，非插件缺陷）。

## 已验证 / 未验证边界（诚实说明）

- 已在真实 OpenCode TUI（v2.2.7，tmux 隔离实例）里跑通全流程：`/doc-tree` 打开面板、四段详情同屏渲染、100 行分页翻页、嵌套树折叠/展开、指针选择与跳转、返回、过滤、tab 切换、重扫、侧边栏摘要与折叠、面板关闭；抓屏留在 `/tmp/opencode/gk-ui-*.txt`、`gk-btn-*.txt`、`gk-tree-*.txt`。
- **鼠标点击未能在该环境验证**：tmux 注入的 SGR 鼠标事件没有到达插件槽位（宿主自带的 Qoder 侧边栏标题 `onMouseUp` 同样不触发），因此动作条按钮、tab、指针项、findings 项、树行、告警行、顶栏告警计数、侧边栏「打开」按钮与标题折叠的点击路径只做了代码级验证；这些按钮的键盘等价路径已实测。
- `Tab` 由宿主 keymap 层接管，宿主自身的 `Tab` 行为在面板打开时会被替换（面板之外不受影响）。
- 文件监听是每目录一个 `fs.watch`（不依赖 `recursive`），目录数上限 1500；超限会在面板提示「实时刷新已截断」，按 `r` 可手动刷新。仓库根目录只对 `.md/.json/.toml`、`AGENTS*` 或无扩展名（目录创建）事件敏感，其他文件变更不触发。
- 树最多索引 40000 个文件；超限显示「索引已截断」。
- 告警行按扫描结果派生：扫描失败（检查器不可用）时告警 tab 为空，此时只有顶栏计数与侧边栏高危行可见。
- **近期功能实现 tab 已在真实 TUI 里验证**：键盘路径全通——`5` 切到近期功能实现 tab、`j`/`k` 在树里移动选中、`Enter` 打开条目详情、`o` 循环选中指针、`Enter` 跳到该指针指向的代码文件、`b` 返回条目详情、`/` 按标题 / 摘要 / 叙事过滤、`Esc` 关闭面板；树层级与四态配色、条目详情与分组统计、空态、侧边栏摘要均已抓屏确认。鼠标路径仍只做代码级验证（见上一条）。
- 本适配器只面向 V2 TUI 插件 API；V1 宿主会直接加载失败。

## 自测记录

```bash
# 1. package.json 合法
python3 -c "import json;json.load(open('package.json'))"

# 2. 入口存在
grep -c "doc-tree" src/index.tsx

# 3. TSX/TS 语法转译（依赖全部 external，不安装）
bun build src/index.tsx --target node --outdir /tmp/opencode/gk-plugin-build \
  --external solid-js --external @opentui/solid --external @opentui/core

# 4. 类型检查（临时 tsconfig 映射本机缓存类型，不往本目录装依赖）
node /root/.bun/install/cache/typescript@5.9.3@@@1/lib/tsc.js -p /tmp/opencode/gk-typecheck/tsconfig.json

# 5. 入口可被 Bun 加载
bun -e 'const m = await import("./src/index.tsx"); console.log(m.default.id, typeof m.default.setup)'

# 6. 信封归一化、树过滤、指针目标解析、真实引擎 show 与分页、引擎缺失降级
bun run /tmp/opencode/gk-plugin-test.ts

# 7. 告警行构造与过滤（severity→rule→file 排序 / 虚拟行 / file·rule·message 命中）
bun run /tmp/opencode/gk-findings-test.ts

# 8. 近期功能实现树的行布局与叙事折行（目录默认展开 / meta 不重复 / 标题尾部省略 / 叙事软换行）
bun run /tmp/opencode/gk-logic-ui-test.ts
```

第 4–8 项脚本与 fixture 放在 `/tmp`，不随插件交付。
