/**
 * 文档树面板：左侧三类树（可过滤），右侧四段详情（元信息/内容/指针/findings）。
 *
 * 上游：index.tsx 通过 session.panel 槽位渲染本组件，并注入状态与动作。
 * 下游：只读 AppState 与 PanelRuntime；键盘事件由 useKeyboard 接管，仅在面板聚焦时生效。
 */
import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { useKeyboard } from "@opentui/solid";
import type { JSX } from "@opentui/solid";
import type { MouseEvent, Renderable, ScrollBoxRenderable } from "@opentui/core";
import type {
  AppState,
  DocRef,
  PanelInput,
  PanelRuntime,
  TabId,
  ThemeColor,
  ThemeColors,
  TreeRow,
} from "./types.ts";
import { TAB_LABELS, TAB_ORDER } from "./types.ts";
import { filterRows, visibleRows } from "./tree.ts";
import {
  emptyLogicMessage,
  filterLogicRows,
  logicCounts,
  logicStatusColor,
  logicStatusPrefix,
  type LogicRow,
} from "./logicrows.ts";
import { DetailPane } from "./detailpane.tsx";
import { TreeRowView } from "./treerow.tsx";
import { clipTail, displayWidth, narrativeTextWidth } from "./textwrap.ts";

/** 树过滤/折叠的实现已挪到 tree.ts，这里保留转出，兼容既有调用方。 */
export { filterRows, visibleRows } from "./tree.ts";

export interface DocTreePanelProps {
  input: PanelInput;
  theme: ThemeColors;
  getState: () => AppState;
  getRuntime: () => PanelRuntime;
  /** 面板挂载/卸载时上报可见性：宿主 keymap 的 Tab 绑定需要它来决定是否生效。 */
  onVisibilityChange?: (open: boolean) => void;
  /** 折叠/展开侧边栏摘要卡片。 */
  toggleSidebar?: () => void;
  selectRow: (id: string) => void;
  selectTab: (tab: TabId) => void;
  /** 手动重跑一次树刷新 + 扫描，用于检查器恢复后重试。 */
  refresh: () => void;
  /** 指针跳转：把目标文件入栈并展示。 */
  jumpToRef: (ref: DocRef) => void;
  /** 返回导航栈上一个文件。 */
  back: () => void;
  /** 内容翻页：+1 下一段，-1 上一段；叙事分页时带上叙事可用列数。 */
  page: (delta: 1 | -1, narrativeWidth?: number) => void;
  /** 在指针列表里循环选中。 */
  cycleRef: () => void;
  /** 折叠/展开目录节点。 */
  toggleDir: (id: string) => void;
  /** 打钩：调引擎 check 命令切换清单项勾选态。 */
  toggleCheck: (id: string) => void;
  /** 用户可见提示（动作条按钮的用法提示等）。 */
  notify?: (message: string, variant: "info" | "success" | "warning" | "error") => void;
  /** 打开告警行对应的文件（告警 tab 的 Enter/点击）。 */
  openFinding: (path: string) => void;
  /** 按路径打开文件（findings 点击）。 */
  openPath: (target: string, line?: number) => void;
  /** 更新过滤文本。 */
  setFilter: (text: string) => void;
  /** 开关过滤输入态。 */
  setFilterMode: (active: boolean) => void;
}

function formatTime(iso?: string): string {
  if (!iso) return "未扫描";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function kindLabel(row: TreeRow): string {
  if (row.kind === "group") return "分组";
  if (row.kind === "dir") return "目录";
  if (row.kind === "task") return "任务卡";
  if (row.kind === "check") return "清单项";
  if (row.kind === "entry") return "功能条目";
  return "文件";
}

/** 过滤输入态下收集可打印字符；shift 字母用 sequence 还原大写。 */
function typedChar(name: string, sequence: string): string | undefined {
  if (sequence.length === 1 && !sequence.startsWith("\x1b")) return sequence;
  if (name.length === 1 && name >= " " && name <= "~") return name;
  return undefined;
}

/** 动作条按钮：鼠标左键触发，与键盘快捷键等价；背景色让按钮在面板里可见。 */
function ActionButton(props: {
  label: string;
  theme: ThemeColors;
  fg?: ThemeColor;
  onPress: () => void;
}): JSX.Element {
  return (
    <box
      flexDirection="row"
      backgroundColor={props.theme.backgroundElement}
      paddingLeft={1}
      paddingRight={1}
      onMouseUp={(event: MouseEvent) => {
        if (event.button !== 0) return;
        props.onPress();
      }}
    >
      <text wrapMode="none" truncate fg={props.fg ?? props.theme.text}>
        {props.label}
      </text>
    </box>
  );
}

/** 条目标题列的最小宽度（列）：标题再短也不被摘要挤没。 */
const LOGIC_TITLE_MIN_COLUMNS = 16;
/** 摘要最多占用的比例：其余宽度留给标题，摘要不足时自己让位。 */
const LOGIC_SUMMARY_SHARE = 0.4;

/**
 * 条目行的标题/摘要宽度预算（纯函数，供自测复用）。
 * 标题预算 = 可用宽度 - 缩进 - 摘要预留；标题按尾部省略后剩下的列数归摘要，摘要不够就自己让位。
 */
export function logicRowLayout(
  depth: number,
  title: string,
  summary: string,
  availableColumns: number,
): { indent: string; title: string; summary: string; titleBudget: number } {
  const indent = "  ".repeat(depth);
  const inner = Math.max(8, availableColumns);
  const available = Math.max(LOGIC_TITLE_MIN_COLUMNS * 2, inner);
  const summaryReserve = Math.min(24, Math.floor(available * LOGIC_SUMMARY_SHARE));
  const titleBudget = Math.max(LOGIC_TITLE_MIN_COLUMNS, available - indent.length - summaryReserve);
  const clippedTitle = clipTail(title, titleBudget);
  // 摘要按尾部省略：框架的 truncate 是中间省略，摘要被中间截断会读不成句。
  const summaryBudget = Math.max(0, inner - indent.length - displayWidth(clippedTitle) - 1);
  return { indent, title: clippedTitle, summary: clipTail(summary, summaryBudget), titleBudget };
}

/**
 * 近期功能实现条目行：状态标记 + 标题（按状态着色），右侧截断显示摘要。
 * 通用 TreeRowView 只按 kind 上色，近期功能实现条目需要按 status 上色，故单列一种行形态。
 * 标题列设最小宽度且不参与收缩，超长标题按尾部省略；摘要宽度不够时优先让位。
 */
function LogicEntryRowView(props: {
  row: TreeRow;
  selected: boolean;
  theme: ThemeColors;
  /** 树区可用列数：用于给标题留宽度。 */
  availableColumns: number;
  onSelect: () => void;
  rowRef?: (renderable: Renderable) => void;
}): JSX.Element {
  const theme = props.theme;
  const entry = (props.row as unknown as Partial<LogicRow>).logicEntry;
  const status = entry?.status ?? "draft";
  const title = `${logicStatusPrefix(status)}${entry?.title ?? props.row.label}`;
  const layout = logicRowLayout(
    props.row.depth,
    title,
    entry?.summary ?? "",
    props.availableColumns,
  );
  return (
    <box
      flexDirection="row"
      width="100%"
      backgroundColor={props.selected ? theme.backgroundElement : theme.backgroundPanel}
      onMouseUp={(event: MouseEvent) => {
        if (event.button !== 0) return;
        props.onSelect();
      }}
      ref={(renderable: Renderable) => {
        props.rowRef?.(renderable);
      }}
    >
      <box width={1} backgroundColor={props.selected ? theme.accent : undefined} />
      <text
        wrapMode="none"
        flexShrink={0}
        minWidth={LOGIC_TITLE_MIN_COLUMNS}
        fg={props.selected ? theme.primary : logicStatusColor(status, theme)}
      >
        {layout.indent}
        {layout.title}
      </text>
      <text wrapMode="none" truncate flexShrink={1} minWidth={0} fg={theme.textMuted}>
        {` ${layout.summary}`}
      </text>
    </box>
  );
}

export function DocTreePanel(props: DocTreePanelProps): JSX.Element {
  const theme = props.theme;
  const state = () => props.getState();
  const runtime = () => props.getRuntime();
  const filterActive = () => runtime().filter.trim().length > 0;
  const rows = createMemo(() => {
    const tabRows = state().rows[state().tab] ?? [];
    // 近期功能实现 tab 按 domain 标题与条目文本过滤；其余 tab 走通用过滤（label/path/meta）。
    const filtered =
      state().tab === "logic"
        ? filterLogicRows(tabRows, runtime().filter)
        : filterRows(tabRows, runtime().filter);
    return visibleRows(filtered, {
      filterActive: filterActive(),
      dirExpanded: runtime().dirExpanded,
    });
  });
  /** 顶栏近期功能实现计数：条目总数 + stale/broken 异常数（0 异常时只显示总数）。 */
  const logicCountLabel = (): string => {
    const counts = logicCounts(state().logic.domains);
    const total = state().counts.logic || counts.total;
    if (counts.stale + counts.broken === 0) return `近期功能实现 ${total}`;
    return `近期功能实现 ${total}（stale ${counts.stale} · broken ${counts.broken}）`;
  };
  const selectedRow = () => rows().find((row) => row.id === state().selectedId);
  /**
   * 页脚操作提示。
   * 近期功能实现 tab 的过滤范围（标题/摘要/叙事）写在行首：页脚整行放不下时框架按中间省略截断，
   * 只有行首的提示不会被截掉。
   */
  const footerHint = (): string => {
    const logic = state().tab === "logic";
    const filter = logic ? "/ 过滤（标题/摘要/叙事）" : "/ 过滤";
    return `${logic ? `${filter} · ` : ""}1-5 切 tab · ↑/↓ 或 j/k 选择 · Tab 下一标签 · [ ] 翻页 · o 指针 · Enter 跳转/折叠${
      state().tab === "findings" ? "/打开告警文件" : ""
    }${logic ? "/打开近期功能实现条目" : ""} · 清单项 点击/Enter/空格打钩 · b 返回 · ${logic ? "" : `${filter} · `}r 重扫 · c 折侧栏 · PgUp/PgDn 滚详情 · Esc 关闭${
      selectedRow() ? ` · ${kindLabel(selectedRow()!)}` : ""
    }`;
  };
  /** 目录/可折叠行是否展开：过滤态全展开，其余按用户覆盖或行内默认态。 */
  const dirExpanded = (row: TreeRow): boolean =>
    filterActive() || (runtime().dirExpanded[row.id] ?? row.defaultOpen ?? row.depth <= 1);

  // 两个滚动容器的句柄：详情区内容可能超出视口，需要 PageUp/PageDown 滚动查看。
  let treeBox: ScrollBoxRenderable | undefined;
  let detailBox: ScrollBoxRenderable | undefined;
  const rowNodes = new Map<string, Renderable>();
  // 树区与详情区的实际列数：条目标题宽度与叙事折行宽度都按它算。
  const [treeColumns, setTreeColumns] = createSignal(40);
  const [detailColumns, setDetailColumns] = createSignal(80);

  onMount(() => {
    // 面板刚打开时把输入焦点拿过来，Esc/方向键才生效；已聚焦则不重复调用。
    if (!props.input.focused) props.input.focus();
    props.onVisibilityChange?.(true);
  });

  onCleanup(() => {
    props.onVisibilityChange?.(false);
  });

  /** 详情区上下翻一屏；内容不足一屏时不动。 */
  const scrollDetail = (direction: -1 | 1): void => {
    const box = detailBox;
    if (!box) return;
    const viewport = Math.max(1, box.height - 2);
    const max = Math.max(0, box.scrollHeight - box.height);
    const next = Math.min(max, Math.max(0, box.scrollTop + direction * viewport));
    box.scrollTop = next;
  };

  /** 选中项移出视口时把它滚回可见范围。 */
  const scrollTreeToSelection = (): void => {
    const box = treeBox;
    if (!box) return;
    const node = rowNodes.get(state().selectedId);
    if (!node) return;
    try {
      box.scrollChildIntoView(node, { align: "nearest" });
    } catch {
      // 节点尚未完成布局时忽略，下一帧的选中变化会再试一次。
    }
  };

  createEffect(() => {
    // 依赖选中项：变化后把选中行滚进视口。
    state().selectedId;
    queueMicrotask(scrollTreeToSelection);
  });

  createEffect(() => {
    // 过滤/折叠把选中项藏起来时，详情区会停在一个看不见的文件上；跟着第一个可见文件走。
    const list = rows();
    if (list.length === 0) return;
    if (!list.some((row) => row.id === state().selectedId)) {
      const next =
        list.find((row) => (row.kind === "file" || row.kind === "task") && Boolean(row.path)) ??
        list.find((row) => row.kind !== "group") ??
        list[0];
      props.selectRow(next.id);
    }
  });

  // 详情区滚动位置只在换文件时重置；刷新/选中指针等 runtime 变化不能把位置打回顶部。
  let detailPath = "";
  createEffect(() => {
    const path = runtime().path;
    if (path === detailPath) return;
    detailPath = path;
    if (detailBox) detailBox.scrollTop = 0;
  });

  const move = (delta: number): void => {
    const list = rows();
    if (list.length === 0) return;
    const index = list.findIndex((row) => row.id === state().selectedId);
    const next = index < 0 ? 0 : Math.min(list.length - 1, Math.max(0, index + delta));
    props.selectRow(list[next].id);
  };

  useKeyboard((key) => {
    if (!props.input.focused) return;
    const current = runtime();

    // 过滤输入态：可打印字符进过滤框，回车确认，Esc 清空，退格删除。
    if (current.filterMode) {
      if (key.name === "escape") {
        key.preventDefault();
        props.setFilter("");
        props.setFilterMode(false);
        return;
      }
      if (key.name === "return") {
        key.preventDefault();
        props.setFilterMode(false);
        return;
      }
      if (key.name === "backspace") {
        key.preventDefault();
        props.setFilter(current.filter.slice(0, -1));
        return;
      }
      const char = typedChar(key.name, key.sequence);
      if (char) {
        key.preventDefault();
        props.setFilter(current.filter + char);
      }
      return;
    }

    switch (key.name) {
      case "escape":
        key.preventDefault();
        props.input.close();
        return;
      case "1":
        key.preventDefault();
        props.selectTab("contract");
        return;
      case "2":
        key.preventDefault();
        props.selectTab("docs");
        return;
      case "3":
        key.preventDefault();
        props.selectTab("board");
        return;
      case "4":
        key.preventDefault();
        props.selectTab("findings");
        return;
      case "5":
        key.preventDefault();
        props.selectTab("logic");
        return;
      case "up":
      case "k":
        key.preventDefault();
        move(-1);
        return;
      case "down":
      case "j":
        key.preventDefault();
        move(1);
        return;
      case "home":
        key.preventDefault();
        if (rows().length > 0) props.selectRow(rows()[0].id);
        return;
      case "end":
        key.preventDefault();
        if (rows().length > 0) props.selectRow(rows()[rows().length - 1].id);
        return;
      case "pageup":
        key.preventDefault();
        scrollDetail(-1);
        return;
      case "pagedown":
        key.preventDefault();
        scrollDetail(1);
        return;
      case "r":
        key.preventDefault();
        props.refresh();
        return;
      case "c":
        key.preventDefault();
        props.toggleSidebar?.();
        return;
      case "[":
        key.preventDefault();
        props.page(-1, narrativeTextWidth(detailColumns()));
        return;
      case "]":
        key.preventDefault();
        props.page(1, narrativeTextWidth(detailColumns()));
        return;
      case "o":
        key.preventDefault();
        props.cycleRef();
        return;
      case "return": {
        key.preventDefault();
        const row = selectedRow();
        if (row?.kind === "check") {
          props.toggleCheck(row.id);
          return;
        }
        if (row && (row.kind === "dir" || row.collapsible)) {
          props.toggleDir(row.id);
          return;
        }
        // 告警行：打开该文件（详情显示元信息/内容/该文件全部 findings），`b` 可退回列表。
        if (row?.finding) {
          props.openFinding(row.finding.file);
          return;
        }
        if (row?.kind === "entry") {
          // 近期功能实现条目：Enter 打开条目详情；已用 o 选中指针时跳到该指针指向的代码。
          const pointers = (row as unknown as Partial<LogicRow>).logicEntry?.pointers ?? [];
          const pointer = pointers[current.refIndex];
          if (pointer) {
            props.openPath(pointer.file, pointer.line);
            return;
          }
          props.selectRow(row.id);
          return;
        }
        const refs = current.detail?.refs ?? [];
        const ref = refs[current.refIndex];
        if (ref) props.jumpToRef(ref);
        return;
      }
      case "space": {
        // 清单项：空格等价 Enter（打钩）；其它行不响应，避免误触。
        key.preventDefault();
        const row = selectedRow();
        if (row?.kind === "check") props.toggleCheck(row.id);
        return;
      }
      case "b":
        key.preventDefault();
        props.back();
        return;
      case "/":
        key.preventDefault();
        props.setFilterMode(true);
        return;
      default:
        // 空格在不同终端下可能被报成 sequence 而不是 name，两条路都接住。
        if (key.sequence === " ") {
          key.preventDefault();
          const row = selectedRow();
          if (row?.kind === "check") props.toggleCheck(row.id);
        }
        return;
    }
  });

  return (
    <box
      flexDirection="column"
      width="100%"
      height="100%"
      backgroundColor={theme.backgroundPanel}
      border
      borderStyle="rounded"
      borderColor={theme.border}
      title=" 文档树 · governance-kit "
    >
      <box flexDirection="row" height={1} paddingLeft={1} flexShrink={0}>
        <For each={TAB_ORDER}>
          {(tab) => (
            <text
              wrapMode="none"
              truncate
              bg={tab === state().tab ? theme.backgroundElement : undefined}
              fg={tab === state().tab ? theme.accent : theme.textMuted}
              onMouseUp={(event: MouseEvent) => {
                if (event.button !== 0) return;
                props.selectTab(tab);
              }}
            >
              {tab === state().tab
                ? ` [${TAB_LABELS[tab]} ${state().counts[tab]}] `
                : `  ${TAB_LABELS[tab]} ${state().counts[tab]}  `}
            </text>
          )}
        </For>
        <box flexGrow={1} />
        <text
          wrapMode="none"
          fg={state().summary.total > 0 ? theme.warning : theme.success}
          onMouseUp={(event: MouseEvent) => {
            if (event.button !== 0) return;
            props.selectTab("findings");
          }}
        >
          {`告警 ${state().summary.total}（E${state().summary.error} W${state().summary.warning} I${state().summary.info}）`}
        </text>
        <text
          wrapMode="none"
          fg={theme.textMuted}
          onMouseUp={(event: MouseEvent) => {
            if (event.button !== 0) return;
            props.selectTab("logic");
          }}
        >
          {`  ${logicCountLabel()}  `}
        </text>
        <text wrapMode="none" fg={theme.textMuted}>
          {`  最近扫描 ${formatTime(state().lastScanAt)}`}
        </text>
      </box>

      <box flexDirection="row" height={1} paddingLeft={1} flexShrink={0}>
        <text wrapMode="none" truncate fg={theme.textMuted}>
          {state().repo}
        </text>
        <box flexGrow={1} />
        <Show when={runtime().filter || runtime().filterMode}>
          <text wrapMode="none" fg={theme.info}>
            {`${runtime().filterMode ? "过滤 ▸ " : "过滤 "}${runtime().filter || "（空）"}  `}
          </text>
        </Show>
        <text
          wrapMode="none"
          fg={
            state().checker.status === "ok"
              ? theme.success
              : state().checker.status === "loading"
                ? theme.info
                : state().checker.status === "degraded"
                  ? theme.warning
                  : theme.error
          }
        >
          {`检查器：${
            state().checker.status === "ok"
              ? "就绪"
              : state().checker.status === "loading"
                ? "扫描中"
                : state().checker.status === "degraded"
                  ? "降级"
                  : "不可用"
          }`}
        </text>
        <Show when={state().truncated}>
          <text wrapMode="none" fg={theme.warning}>
            {"  "}索引已截断
          </text>
        </Show>
        <Show when={!state().watcher.ok}>
          <text wrapMode="none" fg={theme.warning}>
            {"  "}监听异常
          </text>
        </Show>
      </box>

      <box flexDirection="row" height={1} gap={1} paddingLeft={1} flexShrink={0}>
        <ActionButton label="↻ 重扫" theme={theme} onPress={() => props.refresh()} />
        <ActionButton
          label="◀ 上一页"
          theme={theme}
          onPress={() => props.page(-1, narrativeTextWidth(detailColumns()))}
        />
        <ActionButton
          label="下一页 ▶"
          theme={theme}
          onPress={() => props.page(1, narrativeTextWidth(detailColumns()))}
        />
        <ActionButton label="o 指针" theme={theme} onPress={() => props.cycleRef()} />
        <ActionButton label="b 返回" theme={theme} onPress={() => props.back()} />
        <ActionButton label="c 折叠" theme={theme} onPress={() => props.toggleSidebar?.()} />
        <ActionButton
          label="☑ 打钩"
          theme={theme}
          fg={theme.success}
          onPress={() => {
            const row = selectedRow();
            if (row?.kind === "check") {
              props.toggleCheck(row.id);
              return;
            }
            props.notify?.("先在进度板里展开任务、选中清单项，再打钩", "info");
          }}
        />
        <ActionButton label="x 关闭" theme={theme} fg={theme.warning} onPress={() => props.input.close()} />
      </box>

      <box flexDirection="row" flexGrow={1} minHeight={0}>
        <scrollbox
          width="42%"
          border
          borderStyle="single"
          borderColor={theme.borderSubtle}
          title={` 树 · ${TAB_LABELS[state().tab]} `}
          scrollY
          paddingLeft={1}
          ref={(renderable: ScrollBoxRenderable) => {
            treeBox = renderable;
            setTreeColumns(renderable.width);
          }}
          onSizeChange={() => {
            if (treeBox) setTreeColumns(treeBox.width);
          }}
        >
          <Show when={state().tab === "board" && state().boardDegraded}>
            <text wrapMode="none" fg={theme.warning}>
              {`⚠ ${state().boardDegraded}`}
            </text>
          </Show>
          <Show when={state().tab === "logic" && state().logicError}>
            <text wrapMode="none" fg={theme.textMuted}>
              {`⚠ ${state().logicError}`}
            </text>
          </Show>
          <For each={rows()}>
            {(row) => (
              <Show
                when={row.kind === "entry"}
                fallback={
                  <TreeRowView
                    row={row}
                    selected={row.id === state().selectedId}
                    theme={theme}
                    availableColumns={treeColumns() - 4}
                    collapsible={row.kind === "dir" || Boolean(row.collapsible)}
                    expanded={dirExpanded(row)}
                    onSelect={() => props.selectRow(row.id)}
                    onToggleDir={() => props.toggleDir(row.id)}
                    onToggleCheck={() => props.toggleCheck(row.id)}
                    onOpenFinding={(path) => props.openFinding(path)}
                    rowRef={(renderable: Renderable) => {
                      rowNodes.set(row.id, renderable);
                    }}
                  />
                }
              >
                <LogicEntryRowView
                  row={row}
                  selected={row.id === state().selectedId}
                  theme={theme}
                  availableColumns={treeColumns() - 4}
                  onSelect={() => props.selectRow(row.id)}
                  rowRef={(renderable: Renderable) => {
                    rowNodes.set(row.id, renderable);
                  }}
                />
              </Show>
            )}
          </For>
          <Show when={rows().length === 0}>
            <Show
              when={state().tab === "logic"}
              fallback={
                <Show
                  when={state().tab === "findings"}
                  fallback={
                    <Show
                      when={state().tab === "board" && !runtime().filter && state().treeLoaded}
                      fallback={
                        <text fg={theme.textMuted}>
                          {!state().treeLoaded
                            ? "加载中…（首轮刷新可达 10 秒）"
                            : runtime().filter
                              ? "没有匹配的文件"
                              : "（空）"}
                        </text>
                      }
                    >
                      {/* 提示行拆两行：树区 42% 宽放不下整句，单行会被硬截断 */}
                      <text fg={theme.textMuted}>未发现进度板：在仓库根建 进度板.md 与 进度/，</text>
                      <text fg={theme.textMuted}>或用 govern init 生成</text>
                    </Show>
                  }
                >
                  <text fg={runtime().filter ? theme.textMuted : theme.success}>
                    {runtime().filter
                      ? "没有匹配的告警"
                      : state().checker.status === "loading"
                        ? "扫描中…（冷缓存首扫可达 10 秒）"
                        : "无告警"}
                  </text>
                </Show>
              }
            >
              <text fg={theme.textMuted}>
                {runtime().filter ? "没有匹配的近期功能实现条目" : emptyLogicMessage(state().logicLoaded)}
              </text>
            </Show>
          </Show>
        </scrollbox>

        <scrollbox
          flexGrow={1}
          border
          borderStyle="single"
          borderColor={theme.borderSubtle}
          title=" 详情 "
          scrollY
          paddingLeft={1}
          ref={(renderable: ScrollBoxRenderable) => {
            detailBox = renderable;
            setDetailColumns(renderable.width);
          }}
          onSizeChange={() => {
            if (detailBox) setDetailColumns(detailBox.width);
          }}
        >
          <Show
            when={selectedRow() || runtime().path}
            fallback={
              <text fg={theme.textMuted}>
                {rows().length === 0 ? "（空）" : "左侧没有可显示条目"}
              </text>
            }
          >
            <DetailPane
              theme={theme}
              state={state}
              runtime={runtime}
              width={detailColumns()}
              onJumpRef={props.jumpToRef}
              onOpenPath={props.openPath}
            />
          </Show>
        </scrollbox>
      </box>

      <box flexDirection="row" height={1} paddingLeft={1} flexShrink={0}>
        <text wrapMode="none" truncate fg={theme.textMuted}>
          {footerHint()}
        </text>
      </box>
    </box>
  );
}
