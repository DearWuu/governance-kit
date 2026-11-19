# 适配器接线指南

## 定位：编排器，不是 linter 重实现

引擎只做三件事：读配置、跑内置组合规则、把外部工具的输出归一化成同一种 finding。**不内嵌、不 fork 任何 linter**，也不复制它们的规则。

本文件覆盖两类接入：

| 类别 | 形态 | 接线位置 |
| --- | --- | --- |
| 外部工具适配器 | 引擎主动调 shell 命令，归一化成 findings | `config.adapters`（见下） |
| 宿主接入适配器 | 宿主主动调引擎子进程，取 JSON 消费 | `adapters/opencode-plugin`、`adapters/mcp`（见第五节） |

`config.adapters` 每个键值对 = 适配器名 + 一条 shell 命令，引擎按配置顺序执行：

| 条件 | 行为 |
| --- | --- |
| 命令首 token 不在 PATH | 静默跳过，不报 finding（本地没装不红） |
| 命令串含 `sarif` | 解析 stdout 的 SARIF 2.1.0：ruleId / level / artifactLocation / region |
| 命令串含 `lychee` | 解析 lychee JSON 的 `failures` / `errors` → 死链 finding |
| 其余 | generic-exit：退出码非 0 记一条 `ADAPTER:<name>`，消息取输出最后一行 |

- 超时 120 秒，工作目录 = 仓库根，命令应只读。
- 归一化分支靠命令串关键字：SARIF 类命令必须让 `sarif` 出现在命令行里（formatter 模块路径里的 `sarif` 也算），lychee 必须出现在命令里，否则掉进 generic-exit。
- 严重级别取工具自己的 level（SARIF error/warning/note → error/warning/info），只有 error 在 `scan --strict` 下阻断。调级别改 `config.severity` 里的套件规则，不改工具配置。
- 只有三态归一化：generic-exit 只区分「通过 / 不通过」，工具的 UNABLE（环境不可用）会被记成不通过。要保留三态就必须让工具或 wrapper 输出 SARIF，用 `warning` 表示 UNABLE。

## 通用纪律

- 命令可重复、无不可控副作用（网络只读或显式声明）；CI 与本地跑**同一条**命令，不写两套参数。
- 「本机绿」不等于「CI 绿」：没装就跳过，CI 必须装齐适配器依赖，否则门控静默失效。
- 输出到 stdout：引擎读 stdout，`--output-file` 那类参数会让解析拿到空串。
- 新增适配器同时补 `severity` 条目与本文接线示例，避免下一个人重复试错。

## 一、样式：stylelint

参考配置在 `reference-adapters/stylelint/.stylelintrc.json`（`defaultSeverity: error`），门控口径：

- token 强制：`declaration-property-value-disallowed-list`，color / background-color / font-size / margin / padding / border-radius / box-shadow 等属性只允许 `var(--*)`、`calc()/min()/max()/clamp()/color-mix()` 或 `0`、`auto`、`inherit`、`none`、`transparent`、`currentColor` 一类无字面量的值。
- 想更直接地表达「值必须以 `var(--*)` 开头」可装 `stylelint-declaration-strict-value` 替换该规则，二者选一条，不叠加。
- 禁 `!important`：`declaration-no-important`。
- 类名：`selector-class-pattern`，小写 kebab，落在 `obg-` / `app-` / `ui-` 前缀域内。
- 附带 `selector-max-specificity`（≤ `0,3,0`）与 `no-descending-specificity`。

```bash
# 本地手跑：退出码非 0 即失败；--max-warnings=0 必须带，否则 warning 不拦截
npx stylelint "src/**/*.{css,scss,vue}" \
  --config reference-adapters/stylelint/.stylelintrc.json --max-warnings=0
```

接进引擎两种写法：

```json
"adapters": {
  "stylelint": "npx stylelint \"src/**/*.{css,scss}\" --config reference-adapters/stylelint/.stylelintrc.json --max-warnings=0 --custom-formatter=node_modules/stylelint-formatter-sarif"
}
```

- 装了 SARIF formatter 就走上面这条，每条违规带文件行号。
- 只有 JSON formatter（`--formatter json`）时，用一个薄 wrapper 把 JSON 转 SARIF 再输出；直接接 JSON 会掉进 generic-exit，只剩「最后一行 + 退出码」。
- 插件缺失时 stylelint 直接报错退出非 0，属 UNABLE 而非 PASS；编排器只能记成失败，需人工判断。

## 二、视觉：visual_gate.py + Playwright

参考实现在 `reference-adapters/visual/visual_gate.py`，只做确定性像素比对，不做审美判断。

```bash
python3 visual_gate.py --baseline baseline.png --actual actual.png --json
python3 visual_gate.py --baseline baseline.png --actual actual.png --threshold 0.002
python3 visual_gate.py --selftest
```

- 参数：`--baseline` / `--actual` / `--threshold`（差异像素占比上限，默认 0.002）/ `--pixel-tolerance`（单通道差值容差，默认 8）/ `--json`。
- 退出码：`PASS=0`、`FAIL=1`、`UNABLE=2`。只支持 8-bit RGB/RGBA 非隔行 PNG；格式不支持、尺寸不一致、解码失败一律 UNABLE。
- **CI 单环境基线**：固定 OS、字体、字体渲染、DPR、视口、浏览器版本；本机或其他容器生成的图只能作诊断参考，不得入库当基线。
- **权限分离**：agent 只产出截图与比对证据；基线更新必须人 review 后提交。agent 不得改基线、不得调 `--threshold` / `--pixel-tolerance` 让门控变绿，也不得把 UNABLE 当通过。
- 截图：Playwright 驱动真实渲染进程；Electron 用 CDP `Page.captureScreenshot`（`webContents.debugger` 或 Playwright/Puppeteer 的 CDP session），不用 experimental 的 `_electron`，也不做 OS 级窗口截屏（受合成器、缩放、遮挡影响，不可复现）。截图前固定窗口尺寸、DPR、缩放、动画与滚动位置，动效/时间戳区域先遮罩。
- 接线：本项目自建一个薄 wrapper 调 `visual_gate.py --json`，把 `FAIL` 映射成 SARIF `error`、`UNABLE` 映射成 SARIF `warning`、`PASS` 输出空 results，然后让命令串带 `sarif`：

```json
"adapters": {
  "visual": "python3 scripts/visual_sarif.py --sarif"
}
```

直接写 `python3 .../visual_gate.py ...` 也能跑，但 generic-exit 会把 FAIL 与 UNABLE 合并成同一条适配器失败，三态信息丢失。

## 三、契约：contract_gate.py + oasdiff

参考实现在 `reference-adapters/contract/contract_gate.py`，对比两份 OpenAPI JSON（old=基线、new=当次生成物）。

```bash
python3 contract_gate.py --old openapi.baseline.json --new openapi.json --json
python3 contract_gate.py --old old.json --new new.json --internal   # 强制内置实现，离线/自检用
python3 contract_gate.py --selftest
```

- 退出码：`0` 无破坏性变更、`1` 有 breaking、`2` 文件不可读或非法 JSON（UNABLE）。
- 有 `oasdiff` 时优先委托，没有则内置实现兜底；两者结论不一致要核对 oasdiff 版本与规范版本（3.0/3.1），不允许直接删掉内置判断。
- **`--fail-on ERR` 必须显式加**：`oasdiff breaking` 默认只打印报告，即使存在 ERR 级破坏也返回 0，等于永不拦截。
- 生成物防漂移：契约是入库生成物——重生成后必须与入库版本一致，否则说明改了代码没提交契约。

```bash
python3 scripts/gen_openapi.py > openapi.json
git diff --exit-code openapi.json
python3 reference-adapters/contract/contract_gate.py \
  --old api/openapi.baseline.json --new api/openapi.json
```

```json
"adapters": {
  "contract-breaking": "python3 reference-adapters/contract/contract_gate.py --old api/openapi.baseline.json --new api/openapi.json",
  "contract-regen": "bash scripts/check_generated.sh"
}
```

- 与内置 `GEN-DRIFT` 分工：`GEN-DRIFT` 只核对 HTML 报告里的 `govern-scan-hash`；其他生成物交给 regen 适配器。
- 契约先行顺序：改契约 → 跑门控 → 人评审破坏性清单 → 改服务端 → 契约测试 → 改客户端。确需破坏性变更走版本化（v1/v2 并存）或人工签字的限期豁免，不靠放宽判定过关。

## 四、死链：lychee

```json
"adapters": {
  "links": "lychee --offline --format json ."
}
```

- 命令串含 `lychee` 走 JSON 解析，输出 `死链：<url>` 并带来源文件。
- 内网/离线环境用 `--offline`，只验证相对链接，避免 CI 因外网抖动变红。

## 五、OpenCode TUI 插件接线

`adapters/opencode-plugin/` 把引擎接到 OpenCode V2 TUI：插件起子进程调 `python3 <kit>/engine/govern --repo <repo> --json <cmd>`，解析统一信封，把结果渲染成全屏面板与侧边栏组件。源码由宿主加载，不做运行时打包。

### 登记位置

TUI 插件登记在 `~/.config/opencode/cli.json` 的 `plugins`（复数）数组，条目是 `{ "package": "<插件目录>" }`：

```json
{
  "plugins": [
    { "package": "/root/.config/opencode/plugins/governance-kit/adapters/opencode-plugin" }
  ]
}
```

`~/.config/opencode/opencode.jsonc` 的 `plugins` 数组是**服务端**插件，两处不要混用。宿主读插件目录 `package.json` 的 `name` 与 `exports`，定位 `governance-kit-opencode-plugin/tui` → `src/index.tsx`。

### peer 依赖

宿主从插件目录向上解析依赖，插件目录必须能解析到：

```bash
cd /root/.config/opencode/plugins/governance-kit/adapters/opencode-plugin
npm install --no-save solid-js@^1.9.0 @opentui/core@">=0.8.1" @opentui/solid@">=0.8.1"
```

依赖缺失时宿主**静默跳过**插件：没有报错、没有命令、面板不存在。排查「插件没生效」先确认这三包在插件目录里可解析。

### 宿主 API 约定

- `setup()` 内直接调 `context.keymap.layer`（带 `mode:"global"`、`priority:10`）与 `context.ui.slot`。
- 禁止用 `createRoot` 包裹这两个调用：脱离宿主 owner 会抛 `Keymap.Provider is missing`。
- 插件在启动阶段注册命令与面板槽位，安装与重载 TUI 由开发人员手动执行（agent 不代跑 `npm install`、不重启宿主）。

### 两种呈现

| 呈现 | 入口 | 内容 |
| --- | --- | --- |
| 全屏面板 | `/doc-tree` | 三个 tab：契约层 / docs 分级 / 进度板；右侧显示元信息、MD 内容预览、指针跳转、findings |
| 侧边栏组件 | `sidebar.content` | 紧凑视图：计数、最近扫描时间、前 3 条 finding、打开提示 |

按键：`j`/`k`、`↑`/`↓` 选择，`Tab`、`1`–`3` 切 tab，`[`/`]` 内容翻页，`o` 选中指针后 `Enter` 跳转，`b` 返回，`/` 过滤，`r` 重扫，`c` 折叠，`Esc` 返回或关闭。MD 内容预览默认每页 100 行，`DOC_PAGE_LINES` 可覆盖。

### 鼠标交互

面板提供可点击的动作条按钮：`↻` 重扫、`◀` 上一页、`下一页 ▶`、`o` 指针、`b` 返回、`c` 折叠、`x` 关闭。此外 tab、指针项、findings 项、树行、侧边栏「打开」按钮与侧边栏标题折叠都可点击。

- 每个按钮都有键盘等价：`↻`→`r`、`◀`→`[`、`下一页 ▶`→`]`、`o`→`o`、`b`→`b`、`c`→`c`、`x`→`Esc`。
- tmux 注入环境收不到鼠标事件，鼠标路径只做了代码级验证；键盘等价路径已实测。

### 前端内存缓存

`buildSnapshot` 与 `show` 结果按 path+mtimeMs+size 缓存，watcher 变更或按 `r` 重扫时失效，TTL 20s。

### 引擎持久缓存

引擎把只读结果缓存到系统临时目录的 `governance-kit/cache/<仓库哈希>.json`，按仓库路径哈希隔离；缓存目录可用 `GOVERN_CACHE_DIR` 整体改写：

| 缓存项 | 键 | TTL | 失效条件 |
| --- | --- | --- | --- |
| 仓库索引 | 仓库哈希 | 20s（`GOVERN_INDEX_TTL`） | TTL 到期 |
| `docs` / `refs` / `board` 聚合 | 仓库哈希 + 配置指纹 | 20s（`GOVERN_AGG_TTL`） | TTL 到期或配置改动 |
| `show` 结果 | (rel, offset, limit) + 源文件 `mtime_ns`+`size` + 索引摘要哈希 | 300s（`GOVERN_SHOW_TTL`） | 源文件 `mtime_ns` + `size` 变化即失效 |
| `git_last` | rel + 源文件 `mtime_ns`+`size` | 30s（`GOVERN_GIT_TTL`） | 源文件 `mtime_ns` + `size` 变化即失效 |

- `scan` 的规则判定现算，但复用索引缓存。
- `--no-cache` 全局旁路，所有命令既不读也不写缓存。
- `govern cache --clear` 清当前仓库缓存，`--all --clear` 清整个缓存目录；不带 `--clear` 时只输出缓存目录现状。
- 缓存写入用 tmp + replace 原子替换；损坏文件按空缓存处理，下次写入自动修复。

### 只读命令契约

面板消费引擎的只读命令，统一以 `--json` 取信封 `{v,cmd,repo,data,summary,truncated}`：

| 命令 | 消费方 | `data` 里的关键内容 |
| --- | --- | --- |
| `--json show <rel> [--offset N] [--limit N]` | 全屏面板右侧内容预览 | 文件内容、`--offset`/`--limit` 决定的分页区间、元信息、refs（文件内指针）、task（关联任务） |
| `--json refs` | 指针面板 | AGENTS.md 与索引文件里指针的聚合，逐条含来源与状态 |
| `--json docs` | 文档清单 | 每个文档的 tier（分级）、lines（行数）、refs（指针数）、dead（失效指针数） |

指针状态统一四态：`ok`（按当前文件目录解析命中）、`qualified`（只能按仓库根命中）、`under`（仅文件名命中，欠限定）、`dead`（不存在）。面板只展示状态，不自行判定引用有效性。

其余命令（`scan`、`health`、`board`、`map`、`outline`、`view`、`tree`、`tests`、`symbols`、`xref`、`html`、`sync`、`init`、`pack`、`upgrade`、`version`）与 CLI 用法一致。

## 失败排查

- 出现 `ADAPTER:<name>` 但手动跑同命令通过 → 检查 CI 是否缺依赖（未装是静默跳过，装了但版本不同才真失败）。
- SARIF 分支没结果 → 检查命令是否把报告写进了文件而不是 stdout。
- 同一问题两条 finding → 工具规则与套件规则重叠，关掉一边，优先保留带行号的工具侧。
- 命令含 `sarif` 却被当 generic-exit → 关键字被 shell 转义吃掉，检查引号层级。