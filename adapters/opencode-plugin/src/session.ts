/**
 * 插件会话状态：树快照 + 扫描结果 + 详情窗口 + 导航栈。
 *
 * 上游：index.tsx 在 setup() 中创建会话并把动作注入面板与侧边栏。
 * 下游：panel.tsx / detailpane.tsx / sidebar.tsx 只读消费 state 与 runtime。
 * 约束：所有动作都在这里收口，组件不直接触碰文件系统或子进程。
 */
import { createSignal, type Accessor } from "solid-js";
import { watch, type FSWatcher } from "node:fs";
import * as path from "node:path";
import type {
  AppState,
  PanelRuntime,
  DocRef,
  Finding,
  NavEntry,
  TabId,
  TreeRow,
  TreeSnapshot,
} from "./types.ts";
import { TAB_ORDER, emptySummary } from "./types.ts";
import { buildFindingRows, buildSnapshot, invalidateSnapshotCache } from "./tree.ts";
import { collectWatchDirs, discoverRepo, indexFindings, invalidateRepoCache } from "./repo.ts";
import { clearTextCache } from "./cache.ts";
import { createScanner } from "./scan.ts";
import { createDetailLoader, resolvePageLines, resolveRefTarget, type DetailLoader } from "./detail.ts";
import { applyOptimisticCheck, boardDegradedMessage, buildBoardRows, createBoardLoader, type BoardLoader } from "./board.ts";
import { createLogicLoader, type LogicLoader } from "./logic.ts";
import { buildLogicRows, logicCounts, type LogicEntry, type LogicRow } from "./logicrows.ts";
import { narrativeTextWidth, wrapNarrativeWithSections } from "./textwrap.ts";

const DEBOUNCE_MS = 500;
/** 选中项变化到发起 show 的防抖窗口：避免连续 j/k 时反复起进程。 */
const DETAIL_DEBOUNCE_MS = 120;
/** 详情框宽度的兜底值（列）：面板未量到宽度时按它折行。 */
const DEFAULT_DETAIL_COLUMNS = 80;

export interface DocSessionOptions {
  repo: string;
  governPath: string;
  python: string;
  scanTimeoutMs?: number;
  /** 用户可见提示（打钩结果、降级原因等）。 */
  notify?: (message: string, variant: "info" | "success" | "warning" | "error") => void;
}

export interface DocSessionActions {
  selectRow: (id: string) => void;
  selectTab: (tab: TabId) => void;
  /** 在三个 tab 之间循环（Tab / Shift+Tab 走宿主 keymap 时用）。 */
  cycleTab: (delta: 1 | -1) => void;
  refresh: () => void;
  jumpToRef: (ref: DocRef) => void;
  back: () => void;
  page: (delta: 1 | -1, narrativeWidth?: number) => void;
  cycleRef: () => void;
  /** 折叠/展开一个目录节点；折叠时若选中项在其子树内，选中回落到目录行。 */
  toggleDir: (id: string) => void;
  /** 打钩：调引擎 `check <rel> <index> --toggle`，成功后重拉进度板。 */
  toggleCheck: (id: string) => void;
  /** 打开告警行对应的文件（告警 tab 的 Enter/点击）：详情切到该文件，可 `b` 退回列表。 */
  openFinding: (path: string) => void;
  /** 按仓库相对路径打开文件（指针跳转、findings 点击用）；给出行号时从该行开始预览。 */
  openPath: (target: string, line?: number) => void;
  setFilter: (text: string) => void;
  setFilterMode: (active: boolean) => void;
}

export interface DocSession {
  state: Accessor<AppState>;
  runtime: Accessor<PanelRuntime>;
  actions: DocSessionActions;
  dispose: () => void;
}

function initialState(repo: string): AppState {
  return {
    repo,
    tab: "contract",
    selectedId: "",
    rows: { contract: [], docs: [], board: [], findings: [], logic: [] },
    counts: { contract: 0, docs: 0, board: 0, findings: 0, logic: 0 },
    docTiers: { ai: 0, human: 0, unclassified: 0 },
    truncated: false,
    boardStatuses: [],
    findings: [],
    findingsByFile: {},
    summary: emptySummary(),
    checker: { status: "idle" },
    watcher: { ok: true },
    logic: { domains: [], dir: "" },
    logicLoaded: false,
    treeLoaded: false,
  };
}

function initialRuntime(): PanelRuntime {
  return {
    loading: false,
    path: "",
    offset: 1,
    navStack: [],
    refIndex: -1,
    filter: "",
    filterMode: false,
    dirExpanded: {},
    lastSelected: {},
  };
}

/** 行是否可加载详情：文件、任务卡与清单项有 path（清单项指向任务文件），分组与目录节点没有。 */
function hasDetail(row: TreeRow | undefined): row is TreeRow {
  return Boolean(
    row && (row.kind === "file" || row.kind === "task" || row.kind === "check") && row.path,
  );
}

/**
 * 默认选中首个「文件」行：分组/目录行没有可预览内容，选中它会让右侧详情空白。
 */
function firstSelectableRow(rows: TreeRow[] | undefined): TreeRow | undefined {
  if (!rows || rows.length === 0) return undefined;
  return rows.find((row) => hasDetail(row)) ?? rows[0];
}

/** 近期功能实现 tab 的默认选中行：首个条目行（分组与目录行没有条目详情）。 */
function firstLogicEntry(rows: TreeRow[] | undefined): TreeRow | undefined {
  return rows?.find((row) => row.kind === "entry");
}

function applySnapshot(previous: AppState, snapshot: TreeSnapshot): AppState {
  // 告警行来自扫描结果而不是文件系统：树刷新时按已有 findings 重建，别被快照清空。
  // 近期功能实现行同理来自引擎 logic list，树刷新时保留上一轮结果，等本次拉取结果覆盖。
  const rows = {
    ...snapshot.rows,
    findings: buildFindingRows(previous.findings),
    logic: previous.rows.logic,
  };
  const list = rows[previous.tab] ?? [];
  const keep = list.some((row) => row.id === previous.selectedId);
  const fallback =
    previous.tab === "logic"
      ? (firstLogicEntry(list) ?? firstSelectableRow(list))
      : firstSelectableRow(list);
  return {
    ...previous,
    ...snapshot,
    rows,
    counts: { ...snapshot.counts, findings: previous.counts.findings, logic: previous.counts.logic },
    selectedId: keep ? previous.selectedId : (fallback?.id ?? ""),
  };
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * 近期功能实现条目详情的翻页上限：行数按「叙事 + 机制小节」折行后的总行数算，
 * 保证最后一节也能翻到（页码仍按页大小对齐）。
 */
export function logicDetailMaxOffset(totalLines: number, pageLines: number): number {
  const size = Math.max(1, Math.floor(pageLines));
  return Math.max(1, Math.ceil(totalLines / size) * size - size + 1);
}

export function createDocSession(options: DocSessionOptions): DocSession {
  const repo = options.repo;
  const [state, setState] = createSignal<AppState>(initialState(repo));
  const [runtime, setRuntime] = createSignal<PanelRuntime>(initialRuntime());

  const watchers = new Map<string, FSWatcher>();
  const docsDir = path.join(repo, "docs");
  const governanceDir = path.join(repo, ".governance");
  const scanner = createScanner({
    repo,
    governPath: options.governPath,
    python: options.python,
    timeoutMs: options.scanTimeoutMs,
  });
  const loader: DetailLoader = createDetailLoader({
    repo,
    governPath: options.governPath,
    python: options.python,
    timeoutMs: options.scanTimeoutMs,
  });
  const board: BoardLoader = createBoardLoader({
    repo,
    governPath: options.governPath,
    python: options.python,
    timeoutMs: options.scanTimeoutMs,
  });
  const logicLoader: LogicLoader = createLogicLoader({
    repo,
    governPath: options.governPath,
    python: options.python,
    timeoutMs: options.scanTimeoutMs,
  });

  let disposed = false;
  let debounce: ReturnType<typeof setTimeout> | undefined;
  let detailTimer: ReturnType<typeof setTimeout> | undefined;
  let detailToken = 0;
  let queue: Promise<void> = Promise.resolve();
  let watchError: string | undefined;
  /** 打钩 FIFO 队列：UI 先乐观翻转，引擎写入串行执行；previous 供失败回滚。 */
  const checkQueue: { rel: string; index: number; previous: boolean }[] = [];
  let checkRunning = false;

  /**
   * 串行消费打钩队列：成功后不做全树刷新与全量扫描——watcher 会感知任务文件
   * 写入并自动刷新；仅 watcher 失效时降级为 board 单拉，保住选中项。
   */
  const drainChecks = async (): Promise<void> => {
    if (checkRunning) return;
    checkRunning = true;
    try {
      while (!disposed && checkQueue.length > 0) {
        const item = checkQueue.shift();
        if (!item) break;
        const result = await board.check(item.rel, item.index);
        if (disposed) return;
        if (!result.ok) {
          setState((previous) => ({
            ...previous,
            rows: {
              ...previous.rows,
              board: applyOptimisticCheck(previous.rows.board, item.rel, item.index, item.previous),
            },
          }));
          options.notify?.(`打钩失败：${result.reason}`, "error");
          continue;
        }
        const optimistic = !item.previous;
        // 引擎结果与乐观值不一致（如文件被外部改过）时以引擎为准。
        if (result.checked !== undefined && result.checked !== optimistic) {
          setState((previous) => ({
            ...previous,
            rows: {
              ...previous.rows,
              board: applyOptimisticCheck(previous.rows.board, item.rel, item.index, result.checked as boolean),
            },
          }));
        }
        const finalChecked = result.checked ?? optimistic;
        options.notify?.(finalChecked ? "已打钩" : "已取消打钩", "success");
        if (state().watcher.ok === false) {
          const fetched = await board.fetch();
          if (disposed) return;
          if (fetched.ok) {
            setState((previous) => {
              const boardRows = buildBoardRows(fetched.board);
              const keep =
                previous.tab !== "board" || boardRows.some((row) => row.id === previous.selectedId);
              const selectedId = keep
                ? previous.selectedId
                : (firstSelectableRow(boardRows)?.id ?? "");
              return {
                ...previous,
                rows: { ...previous.rows, board: boardRows },
                counts: { ...previous.counts, board: fetched.board.tasks.length },
                boardStatuses: fetched.board.statuses,
                selectedId,
              };
            });
          }
        }
      }
    } finally {
      checkRunning = false;
    }
  };

  const findRow = (id: string): TreeRow | undefined => {
    for (const tab of TAB_ORDER) {
      const row = state().rows[tab]?.find((item) => item.id === id);
      if (row) return row;
    }
    return undefined;
  };

  /** 压入导航栈条目：记下来源路径、tab 与选中项，`b` 才能原样退回去。 */
  const pushNav = (previous: PanelRuntime, path: string): NavEntry[] => [
    ...previous.navStack,
    { path, tab: state().tab, selectedId: state().selectedId },
  ];

  /** 当前选中的近期功能实现条目（近期功能实现 tab 且选中条目行时）。 */
  const selectedLogicEntry = (): LogicEntry | undefined => {
    const current = state();
    if (current.tab !== "logic") return undefined;
    const row = current.rows.logic?.find((item) => item.id === current.selectedId);
    return (row as unknown as Partial<LogicRow> | undefined)?.logicEntry;
  };

  const selectRowByPath = (target: string): void => {
    for (const tab of TAB_ORDER) {
      const row = state().rows[tab]?.find((item) => item.path === target);
      if (row) {
        setState((previous) => ({ ...previous, tab, selectedId: row.id }));
        return;
      }
    }
  };

  // ---- 详情窗口（show） -------------------------------------------------

  const applyDetail = (path: string, offset: number): void => {
    if (!path) {
      setRuntime((previous) => ({
        ...previous,
        path: "",
        offset: 1,
        detail: undefined,
        degraded: undefined,
        loading: false,
        refIndex: -1,
      }));
      return;
    }
    setRuntime((previous) => ({
      ...previous,
      path,
      offset,
      loading: true,
      refIndex: -1,
    }));
  };

  const runDetail = async (path: string, offset: number, token: number): Promise<void> => {
    const result = await loader.load(path, offset);
    if (disposed || token !== detailToken) return;
    setRuntime((previous) => ({
      ...previous,
      loading: false,
      detail: result.detail,
      degraded: result.degraded,
      offset: result.detail.offset,
    }));
  };

  /** 选中文件变化时按需调 show：防抖 + 缓存（缓存由 loader 负责）。 */
  const scheduleDetail = (path: string, offset: number): void => {
    if (detailTimer) clearTimeout(detailTimer);
    const token = (detailToken += 1);
    applyDetail(path, offset);
    if (!path) return;
    detailTimer = setTimeout(() => {
      detailTimer = undefined;
      void runDetail(path, offset, token);
    }, DETAIL_DEBOUNCE_MS);
  };

  /** 树刷新后让详情跟上当前选中项（首次加载与文件变更都走这里）。 */
  const syncDetailToSelection = (force: boolean): void => {
    const current = runtime();
    const row = state().rows[state().tab]?.find((item) => item.id === state().selectedId);
    const path = hasDetail(row) ? row.path : "";
    if (!path) {
      if (current.path) scheduleDetail("", 1);
      return;
    }
    if (current.path !== path) {
      // 指针跳到树外文件时，树选区仍停在原文件；刷新应继续展示跳转目标，
      // 而不是把详情拽回旧选区（树外文件改动时原地重读当前窗口）。
      if (current.navStack.length > 0 && current.path) {
        if (force) scheduleDetail(current.path, current.offset);
        return;
      }
      scheduleDetail(path, 1);
      return;
    }
    // 同一文件：文件可能已改动，重读当前窗口（保持翻页位置）。
    if (force) scheduleDetail(path, current.offset);
  };

  // ---- 树与扫描 ---------------------------------------------------------

  const updateWatcher = (): void => {
    if (disposed) return;
    const nextOk = !watchError;
    setState((previous) =>
      previous.watcher.ok === nextOk && previous.watcher.message === watchError
        ? previous
        : { ...previous, watcher: { ok: nextOk, message: watchError } },
    );
  };

  const isUnder = (dir: string, root: string): boolean =>
    dir === root || dir.startsWith(`${root}${path.sep}`);

  /** docs 全树与 .governance 关心所有事件；其他目录只关心文档/配置类文件名。 */
  const shouldReact = (dir: string, filename: string | Buffer | null): boolean => {
    if (!filename) return true;
    const name = filename.toString();
    if (isUnder(dir, docsDir) || dir === governanceDir) return true;
    return (
      /\.(md|markdown|json|jsonc|toml)$/i.test(name) ||
      /^AGENTS/i.test(name) ||
      !path.extname(name)
    );
  };

  const requestRefresh = (): void => {
    if (disposed) return;
    queue = queue
      .then(async () => {
        if (disposed) return;
        // watcher 事件到达即视为文件可能已变：三处前端缓存全部失效后再重建。
        clearTextCache();
        invalidateRepoCache();
        invalidateSnapshotCache();
        await refreshTree();
        if (disposed) return;
        await scanAndApply();
      })
      .catch((error) => {
        setState((previous) => ({
          ...previous,
          watcher: { ok: false, message: `刷新失败：${errorText(error)}` },
        }));
      });
  };

  const schedule = (): void => {
    if (disposed) return;
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(() => {
      debounce = undefined;
      requestRefresh();
    }, DEBOUNCE_MS);
  };

  const syncWatchers = (extraDirs: string[] = []): void => {
    watchError = undefined;
    try {
      const plan = collectWatchDirs(repo, extraDirs);
      for (const dir of plan.dirs) {
        if (watchers.has(dir)) continue;
        try {
          const watcher = watch(dir, { persistent: false }, (_event, filename) => {
            if (!disposed && shouldReact(dir, filename)) schedule();
          });
          watcher.on("error", (error) => {
            watchError = `监听 ${dir} 失败：${error.message}`;
            updateWatcher();
          });
          watchers.set(dir, watcher);
        } catch (error) {
          watchError = `监听 ${dir} 失败：${errorText(error)}`;
        }
      }
      for (const [dir, watcher] of watchers) {
        if (plan.dirs.has(dir)) continue;
        try {
          watcher.close();
        } catch {
          // 已关闭的 watcher 再关一次无害。
        }
        watchers.delete(dir);
      }
      if (plan.truncated) {
        watchError = "监听目录超过上限，实时刷新已截断；可按 r 手动重扫";
      }
    } catch (error) {
      watchError = `建立文件监听失败：${errorText(error)}`;
    }
    updateWatcher();
  };

  const refreshTree = async (): Promise<TreeSnapshot | undefined> => {
    try {
      // 树、进度板、近期功能实现文档并行拉取：任一失败只降级自己，不阻塞其它 tab。
      const [snapshot, boardResult, logicResult] = await Promise.all([
        buildSnapshot(repo),
        board.fetch(),
        logicLoader.fetch(),
      ]);
      if (disposed) return undefined;
      // 进度板优先用引擎数据（含 checklist）；引擎不可用时退回文件系统直读的结果。
      let merged = snapshot;
      let degraded: string | undefined;
      if (boardResult.ok) {
        merged = {
          ...snapshot,
          rows: { ...snapshot.rows, board: buildBoardRows(boardResult.board) },
          counts: { ...snapshot.counts, board: boardResult.board.tasks.length },
          boardStatuses: boardResult.board.statuses,
        };
        degraded = boardDegradedMessage({
          hasChecklist: boardResult.board.hasChecklist,
          taskCount: boardResult.board.tasks.length,
        });
      } else {
        degraded = boardDegradedMessage({ failure: boardResult });
      }
      setState((previous) => {
        const base = applySnapshot(previous, merged);
        if (logicResult.ok) {
          const counts = logicCounts(logicResult.domains);
          return {
            ...base,
            rows: { ...base.rows, logic: buildLogicRows(logicResult.domains) },
            counts: { ...base.counts, logic: counts.total },
            logic: { domains: logicResult.domains, dir: logicResult.dir },
            logicLoaded: true,
            treeLoaded: true,
            logicError: undefined,
          };
        }
        // 近期功能实现文档不可用：保留上一轮行与计数，只记降级原因，其它 tab 不受影响。
        return { ...base, logicLoaded: true, treeLoaded: true, logicError: `近期功能实现文档不可用：${logicResult.error}` };
      });
      // 板文件与任务目录不再固定在 docs/ 下：按发现结果补挂监听；
      // 板文件在仓库根时其目录就是 repo 本身（已监听），跳过以免整仓 addTree。
      const extra = [
        snapshot.boardPath ? path.dirname(path.join(repo, snapshot.boardPath)) : "",
        snapshot.boardTasksDir ? path.join(repo, snapshot.boardTasksDir) : "",
      ].filter((dir) => dir !== "" && dir !== repo);
      syncWatchers(extra);
      loader.invalidate();
      syncDetailToSelection(true);
      return merged;
    } catch (error) {
      setState((previous) => ({
        ...previous,
        treeLoaded: true,
        watcher: { ok: false, message: `读取文件系统失败：${errorText(error)}` },
      }));
      return undefined;
    }
  };

  const scanAndApply = async (): Promise<void> => {
    setState((previous) => ({
      ...previous,
      checker: { ...previous.checker, status: "loading" },
    }));
    const outcome = await scanner.scan();
    if (disposed) return;
    setState((previous) => ({
      ...previous,
      checker: outcome.checker,
      findings: outcome.findings,
      findingsByFile: indexFindings(repo, outcome.findings),
      summary: outcome.summary,
      lastScanAt: outcome.at ?? previous.lastScanAt,
      rows: { ...previous.rows, findings: buildFindingRows(outcome.findings) },
      counts: { ...previous.counts, findings: outcome.summary.total },
    }));
  };

  // ---- 动作 -------------------------------------------------------------

  const actions: DocSessionActions = {
    selectRow: (id: string) => {
      setState((previous) => ({ ...previous, selectedId: id }));
      const row = findRow(id);
      if (!hasDetail(row)) {
        // 分组/目录/近期功能实现条目行没有文件详情：清空右侧预览（条目详情由条目自身数据渲染）。
        scheduleDetail("", 1);
        return;
      }
      // 告警 tab：j/k 只移动选中项，详情由 Enter/点击打开（`b` 才退得回列表）。
      if (row.tab === "findings") return;
      scheduleDetail(row.path, 1);
    },
    selectTab: (tab: TabId) => {
      const current = state();
      const tabRows = current.rows[tab];
      // 记住离开 tab 的选中位置；切回时优先恢复原位，行已不存在才回落首行。
      setRuntime((previous) => ({
        ...previous,
        lastSelected: { ...previous.lastSelected, [current.tab]: current.selectedId },
      }));
      const remembered = runtime().lastSelected[tab];
      const restored = remembered ? tabRows?.find((row) => row.id === remembered) : undefined;
      // 近期功能实现 tab 选中首个条目行：分组与目录行没有条目详情。
      const first =
        restored ??
        (tab === "logic"
          ? (firstLogicEntry(tabRows) ?? firstSelectableRow(tabRows))
          : firstSelectableRow(tabRows));
      setState((previous) => ({ ...previous, tab, selectedId: first?.id ?? "" }));
      if (tab === "findings" || tab === "logic") {
        // 告警 / 近期功能实现 tab 不预载文件：右侧先给总览，Enter/点击才打开。
        scheduleDetail("", 1);
        return;
      }
      if (!hasDetail(first)) {
        scheduleDetail("", 1);
        return;
      }
      scheduleDetail(first.path, 1);
    },
    cycleTab: (delta: 1 | -1) => {
      const index = TAB_ORDER.indexOf(state().tab);
      const next = TAB_ORDER[(index + delta + TAB_ORDER.length) % TAB_ORDER.length];
      actions.selectTab(next);
    },
    refresh: () => {
      clearTextCache();
      invalidateRepoCache();
      invalidateSnapshotCache();
      loader.invalidate();
      const current = runtime();
      requestRefresh();
      if (current.path) scheduleDetail(current.path, current.offset);
    },
    jumpToRef: (ref: DocRef) => {
      const target = resolveRefTarget(repo, runtime().path, ref);
      if (!target) return;
      actions.openPath(target);
    },
    back: () => {
      const stack = runtime().navStack;
      const entry = stack[stack.length - 1];
      if (!entry) return;
      setRuntime((prev) => ({ ...prev, navStack: stack.slice(0, -1) }));
      // 回到原 tab 与原选中项：告警 tab 里 `b` 才能退回列表而不是跳去别的树。
      setState((prev) => ({ ...prev, tab: entry.tab, selectedId: entry.selectedId }));
      // 近期功能实现 tab 的条目详情由 state 渲染，不需要加载文件。
      scheduleDetail(entry.tab === "logic" ? "" : entry.path, 1);
    },
    page: (delta: 1 | -1, narrativeWidth?: number) => {
      const current = runtime();
      if (!current.path) {
        // 近期功能实现条目详情：叙事与机制小节按固定行数分页（与文件预览同一套翻页键与行数口径）。
        // 行数按折行后的展示行算，否则长段落与小节翻到底也看不完。
        const entry = selectedLogicEntry();
        if (!entry) return;
        const total = wrapNarrativeWithSections(
          entry.narrative,
          entry.sections ?? [],
          narrativeWidth ?? narrativeTextWidth(DEFAULT_DETAIL_COLUMNS),
        ).length;
        const size = resolvePageLines();
        const maxOffset = logicDetailMaxOffset(total, size);
        const next =
          delta === 1 ? Math.min(maxOffset, current.offset + size) : Math.max(1, current.offset - size);
        if (next === current.offset) return;
        setRuntime((previous) => ({ ...previous, offset: next }));
        return;
      }
      const detail = current.detail;
      let next = current.offset;
      if (delta === 1) {
        if (!detail || detail.next === null) return;
        next = detail.next;
      } else {
        // 回退步长取 loader 的有效 page size（DOC_PAGE_LINES 覆盖后仍对齐）。
        next = Math.max(1, current.offset - resolvePageLines());
      }
      scheduleDetail(current.path, next);
    },
    cycleRef: () => {
      const refs = runtime().detail?.refs ?? [];
      if (refs.length === 0) {
        // 近期功能实现条目详情：指针列表复用同一套 o 循环选择。
        const pointers = selectedLogicEntry()?.pointers ?? [];
        if (pointers.length === 0) return;
        setRuntime((previous) => ({
          ...previous,
          refIndex: (previous.refIndex + 1) % pointers.length,
        }));
        return;
      }
      setRuntime((previous) => ({
        ...previous,
        refIndex: (previous.refIndex + 1) % refs.length,
      }));
    },
    toggleDir: (id: string) => {
      const row = findRow(id);
      if (!row || !(row.kind === "dir" || row.collapsible)) return;
      const expanded = runtime().dirExpanded[id] ?? row.defaultOpen ?? row.depth <= 1;
      const next = !expanded;
      setRuntime((previous) => ({
        ...previous,
        dirExpanded: { ...previous.dirExpanded, [id]: next },
      }));
      if (next) return;
      // 折叠后选中项若在子树内会不可见；选中回落到目录行，避免"选中丢失"。
      const selected = state().rows[state().tab]?.find((item) => item.id === state().selectedId);
      if (selected?.path && (row.scopePaths ?? []).includes(selected.path)) {
        actions.selectRow(id);
      }
    },
    toggleCheck: (id: string) => {
      const row = findRow(id);
      if (!row || row.kind !== "check" || row.checkIndex === undefined) return;
      const rel = row.path;
      const index = row.checkIndex;
      const previous = row.checked === true;
      // 乐观更新：☐/☑ 与任务行 done/total 立即翻转，不等引擎写入。
      setState((prev) => ({
        ...prev,
        rows: { ...prev.rows, board: applyOptimisticCheck(prev.rows.board, rel, index, !previous) },
      }));
      checkQueue.push({ rel, index, previous });
      void drainChecks();
    },
    openFinding: (target: string) => {
      if (!target || target === ".") return;
      const current = runtime();
      // 打开告警文件前先压栈：`b` 退回告警列表（路径可能不在树里，压的是 tab+选中项+路径）。
      setRuntime((previous) => ({ ...previous, navStack: pushNav(previous, current.path) }));
      scheduleDetail(target, 1);
    },
    openPath: (target: string, line?: number) => {
      if (!target || target === ".") return;
      const current = runtime();
      const fromLogic = state().tab === "logic";
      // 跳转到别的文件时把来源压栈，`b` 可逐级返回（指针跳转与 findings 点击共用）。
      // 从近期功能实现条目跳到代码文件时也压栈：`b` 退回条目详情而不是留在代码预览。
      setRuntime((previous) => ({
        ...previous,
        navStack:
          (current.path || fromLogic) && current.path !== target
            ? pushNav(previous, current.path)
            : previous.navStack,
      }));
      selectRowByPath(target);
      scheduleDetail(target, line && line > 0 ? line : 1);
    },
    setFilter: (text: string) => {
      setRuntime((previous) => ({ ...previous, filter: text }));
    },
    setFilterMode: (active: boolean) => {
      setRuntime((previous) => ({ ...previous, filterMode: active }));
    },
  };

  // 首次加载：先建监听，再拉树与扫描；后续由 watcher 500ms 防抖触发。
  syncWatchers();
  requestRefresh();

  return {
    state,
    runtime,
    actions,
    dispose: () => {
      disposed = true;
      checkQueue.length = 0;
      if (debounce) clearTimeout(debounce);
      if (detailTimer) clearTimeout(detailTimer);
      debounce = undefined;
      detailTimer = undefined;
      for (const watcher of watchers.values()) {
        try {
          watcher.close();
        } catch {
          // ignore
        }
      }
      watchers.clear();
      scanner.dispose();
      loader.dispose();
      board.dispose();
    },
  };
}

export { discoverRepo };
