/**
 * governance-kit OpenCode 适配器的共享类型。
 *
 * 上游：tree.ts / scan.ts / detail.ts 产出数据，index.tsx 持有响应式状态。
 * 下游：panel.tsx、detailpane.tsx、sidebar.tsx 只读消费；本文件不引入任何运行时依赖。
 */
import type { RGBA } from "@opentui/core";
// 近期功能实现文档类型的单一来源在 ./logicrows.ts；这里只做 type-only 导入，不引入运行时依赖。
import type { LogicDomain } from "./logicrows.ts";

export type Severity = "error" | "warning" | "info";
export type TabId = "contract" | "docs" | "board" | "findings" | "logic";
/** group=分组标题；dir=目录节点；file=文件；task=任务卡；check=清单项；entry=近期功能实现条目。 */
export type RowKind = "file" | "task" | "group" | "dir" | "check" | "entry";
export type CheckerStatus = "idle" | "loading" | "ok" | "degraded" | "unavailable";

/**
 * 冷色板：面板所有强调色都从这里取，不再跟随宿主主题的 accent（宿主 accent 可能是橙色）。
 * 主题只提供文本底色（前景字色与面板背景）。
 */
export const PALETTE = {
  /** 主色：选中竖条、当前 tab、主按钮文字。 */
  accent: "#7AA2F7",
  /** 选中行背景：低饱和蓝灰，不再用整行高饱和色块。 */
  selectedBg: "#2E3C5E",
  error: "#F7768E",
  warning: "#E0AF68",
  success: "#9ECE6A",
  muted: "#565F89",
  border: "#3B4261",
} as const;

/** 进度板状态展示顺序：与 engine/governkit/board.py 的 STATUS_ORDER 一致。 */
export const BOARD_STATUS_ORDER: readonly string[] = [
  "进行中",
  "待验收",
  "待启动",
  "挂起",
  "已完成",
];

/** 告警严重度排序：error → warning → info。 */
export const SEVERITY_ORDER: Record<Severity, number> = { error: 0, warning: 1, info: 2 };

/** 与 engine/governkit/core.py 的 Finding.to_dict() 对齐；额外字段一律可选。 */
export interface Finding {
  rule: string;
  severity: Severity;
  file: string;
  line: number;
  message: string;
  fix?: string;
}

export interface ScanSummary {
  error: number;
  warning: number;
  info: number;
  total: number;
}

export interface CheckerState {
  status: CheckerStatus;
  /** 降级/不可用原因；ok 时为空。 */
  message?: string;
}

export interface DocsTiers {
  ai: number;
  human: number;
  unclassified: number;
}

/** 面板左侧树的一行。分组行也可选中，用于显示聚合告警。 */
export interface TreeRow {
  /** 稳定键：`${tab}::${path}`、`${tab}::dir::${dirPath}` 或 `${tab}::group::${name}`。 */
  id: string;
  tab: TabId;
  kind: RowKind;
  /** 仓库相对 POSIX 路径；分组行为空。 */
  path: string;
  label: string;
  depth: number;
  badge?: string;
  meta?: string;
  /** 任务卡在文件中的起始行，仅用于展示。 */
  line?: number;
  /** 分组行覆盖的文件，用于聚合 findings。 */
  scopePaths?: string[];
  /**
   * 祖先目录行 id（自外到内）：任一祖先被折叠时本行隐藏。
   * 目录行自身的祖先不包含自己（自己的展开态决定它下面的行）。
   */
  ancestors?: string[];
  /** 可折叠父行：进度板的状态组与任务行（挂 checklist 子行）。 */
  collapsible?: boolean;
  /** 折叠默认态；缺省按 depth<=1 展开（进度板任务行默认折叠）。 */
  defaultOpen?: boolean;
  /** checklist 项是否已勾选。 */
  checked?: boolean;
  /** checklist 项在引擎清单里的下标（打钩时传给引擎）。 */
  checkIndex?: number;
  /** 告警 tab 的行：本行对应的 finding（虚拟行，文件不一定在树里）。 */
  finding?: Finding;
}

export interface TreeSnapshot {
  rows: Record<TabId, TreeRow[]>;
  /** 每个 tab 的文件/任务条数（不含分组行）。 */
  counts: Record<TabId, number>;
  docTiers: DocsTiers;
  configSource?: string;
  /** 发现到的进度板文件（仓库相对），用于补挂文件监听。 */
  boardPath?: string;
  /** 发现到的任务目录（仓库相对），用于补挂文件监听；无板时缺省。 */
  boardTasksDir?: string;
  /** 文件索引达到上限时为 true，面板会提示树不完整。 */
  truncated: boolean;
  /** 进度板各状态的任务数（侧边栏摘要与降级态用）。 */
  boardStatuses: BoardStatusCount[];
}

/** 进度板状态计数。 */
export interface BoardStatusCount {
  status: string;
  count: number;
}

export interface AppState extends TreeSnapshot {
  repo: string;
  tab: TabId;
  selectedId: string;
  findings: Finding[];
  findingsByFile: Record<string, Finding[]>;
  summary: ScanSummary;
  checker: CheckerState;
  lastScanAt?: string;
  watcher: {
    ok: boolean;
    message?: string;
  };
  /** 进度板降级原因：引擎不可用或版本过旧（缺 checklist）时给出提示。 */
  boardDegraded?: string;
  /** 近期功能实现文档数据：引擎 `logic list` 返回的域与条目（域列表来自 .governance/logic/*.json）。 */
  logic: {
    domains: LogicDomain[];
    /** 近期功能实现文档目录（仓库相对），通常是 .governance/logic。 */
    dir: string;
  };
  /** 近期功能实现文档是否已拉取过一次：决定空态是「加载中…」还是「未发现近期功能实现文档」。 */
  logicLoaded: boolean;
  /** 树是否已完成首轮刷新：未完成时空态显示「加载中…」而不是「（空）/未发现」。 */
  treeLoaded: boolean;
  /** 近期功能实现文档降级原因：引擎不可用或尚未提供 logic 子命令；不阻塞其它 tab。 */
  logicError?: string;
}

/** 引擎指针四态：ok（目录相对命中）/qualified（根相对命中）/under（仅同名命中）/dead（不存在）。 */
export type RefStatus = "ok" | "qualified" | "under" | "dead";

/** 文档分级：ai（AI 读）/human（人读）/none（未分级）。 */
export type DocTier = "ai" | "human" | "none";

export interface DocRef {
  /** 指针所在行号（从 1 起）。 */
  line: number;
  /** 原始目标文本。 */
  target: string;
  status: RefStatus;
  /** 解析到的仓库相对路径；未命中为 null。 */
  resolved: string | null;
}

export interface DocTask {
  title: string;
  status: string;
  owner: string;
  updated: string;
  goal: string;
  evidence: string;
  doc: string;
}

/** 引擎进度板里的一个 checklist 项（`--json board` 契约）。 */
export interface BoardCheckItem {
  /** 引擎清单下标：打钩时原样回传给 `check <rel> <index>`。 */
  index: number;
  checked: boolean;
  text: string;
  /** 该项在任务文件里的行号（从 1 起）。 */
  line: number;
}

/** 引擎进度板里的一个任务（`--json board` 契约）。 */
export interface BoardTask {
  file: string;
  title: string;
  status: string;
  owner: string;
  updated: string;
  goal: string;
  evidence: string;
  doc: string;
  checklist: BoardCheckItem[];
  /** task-file=任务文件；legacy=旧式进度板条目。 */
  mode: string;
  /** legacy 条目在进度板文件里的行号。 */
  line?: number;
}

/** engine/govern show 的 data 段（字段名与引擎一致）。 */
export interface DocDetail {
  path: string;
  exists: boolean;
  tier: DocTier;
  linesTotal: number;
  gitLast: string;
  /** 当前内容窗口的起始行（从 1 起）。 */
  offset: number;
  /** 下一窗口起始行；null 表示已到文件末尾。 */
  next: number | null;
  content: string[];
  refs: DocRef[];
  task: DocTask | null;
  /** 引擎自报的路径错误（越界、敏感文件）。 */
  error?: string;
  /** 数据来源：engine=引擎 show；fs=文件系统直读（降级）。 */
  source: "engine" | "fs";
}

/** 导航栈条目：记录「从哪来」，返回时才能回到原 tab 与原选中项。 */
export interface NavEntry {
  /** 当时的仓库相对路径；空串表示当时没打开文件。 */
  path: string;
  /** 当时的 tab。 */
  tab: TabId;
  /** 当时的选中行 id。 */
  selectedId: string;
}

/** 面板运行时状态：详情窗口 + 导航栈 + 过滤，由 index.tsx 持有（面板重建不丢）。 */
export interface PanelRuntime {
  loading: boolean;
  /** 当前详情对象；未选中文件时为 undefined。 */
  detail?: DocDetail;
  /** 当前详情对应的仓库相对路径（可能来自指针跳转，不在树里）。 */
  path: string;
  offset: number;
  /** 指针跳转历史，`b` 逐级返回。 */
  navStack: NavEntry[];
  /** 当前选中的指针下标；-1 表示未选中。 */
  refIndex: number;
  /** 树过滤文本。 */
  filter: string;
  /** 是否处于过滤输入态。 */
  filterMode: boolean;
  /** 目录节点展开覆盖：键为目录行 id，缺省时首层（depth<=1）展开、更深层折叠。 */
  dirExpanded: Record<string, boolean>;
  /** 每个 tab 最近一次选中的行 id：切回时恢复原位，而不是回到首行。 */
  lastSelected: Partial<Record<TabId, string>>;
  /** 降级原因（引擎不可用而走 fs 直读）。 */
  degraded?: string;
}

/** 宿主 V2 TUI 插件的 session.panel 输入（仅声明本适配器用到的字段）。 */
export type PanelPresentation = "panel" | "fullscreen";

export interface PanelInput {
  readonly name: string;
  readonly sessionID: string;
  readonly width: number;
  readonly presentation: PanelPresentation;
  readonly focused: boolean;
  readonly focus: () => void;
  readonly close: () => void;
  readonly toggleFullscreen: () => void;
}

/** 宿主 sidebar.content 槽位的输入。 */
export interface SidebarInput {
  readonly sessionID: string;
}

/** 宿主主题里的颜色值：RGBA 对象或色值字符串。 */
export type ThemeColor = RGBA | string;

/**
 * 宿主主题中本适配器实际读取的字段：只有文本底色（前景字色、面板背景）。
 * 强调色/语义色不在这里取——一律走 PALETTE，避免宿主 accent（可能是橙色）渗进高亮。
 */
export interface ThemeSource {
  readonly text?: {
    readonly base?: ThemeColor;
  };
  readonly background?: {
    readonly base?: ThemeColor;
    readonly raised?: { readonly base?: ThemeColor };
  };
  readonly primary?: ThemeColor;
  readonly backgroundPanel?: ThemeColor;
}

export interface ThemeColors {
  readonly primary: ThemeColor;
  readonly accent: ThemeColor;
  readonly text: ThemeColor;
  readonly textMuted: ThemeColor;
  readonly error: ThemeColor;
  readonly warning: ThemeColor;
  readonly info: ThemeColor;
  readonly success: ThemeColor;
  readonly border: ThemeColor;
  readonly borderSubtle: ThemeColor;
  readonly backgroundElement: ThemeColor;
  readonly backgroundPanel: ThemeColor;
}

function firstColor(fallback: ThemeColor, ...values: (ThemeColor | undefined)[]): ThemeColor {
  for (const value of values) {
    if (typeof value === "string") return value;
    if (value && typeof value === "object") return value;
  }
  return fallback;
}

/**
 * 把宿主主题摊平成面板直接可用的颜色。
 *
 * 只有「文本底色」（前景字色与面板背景）取自宿主主题；强调色、语义色、边框色一律用
 * PALETTE 冷色板，避免宿主 accent（可能是橙色）渗进选中高亮。
 */
export function resolveTheme(source: ThemeSource | undefined): ThemeColors {
  const text = source?.text;
  const background = source?.background;
  return {
    primary: PALETTE.accent,
    accent: PALETTE.accent,
    text: firstColor("#c0caf5", text?.base, source?.primary),
    textMuted: PALETTE.muted,
    error: PALETTE.error,
    warning: PALETTE.warning,
    info: PALETTE.accent,
    success: PALETTE.success,
    border: PALETTE.border,
    borderSubtle: PALETTE.border,
    backgroundElement: PALETTE.selectedBg,
    backgroundPanel: firstColor(
      "#1a1b26",
      background?.base,
      source?.backgroundPanel,
      background?.raised?.base,
    ),
  };
}

/** 告警严重度配色：error 红 / warning 黄 / info 蓝。 */
export function severityColor(severity: Severity, theme: ThemeColors): ThemeColor {
  if (severity === "error") return theme.error;
  if (severity === "info") return theme.info;
  return theme.warning;
}

/** 告警严重度单字母标记：E / W / I。 */
export function severityMark(severity: Severity): string {
  if (severity === "error") return "E";
  if (severity === "info") return "I";
  return "W";
}

export const TAB_ORDER: readonly TabId[] = ["contract", "docs", "board", "findings", "logic"];

export const TAB_LABELS: Record<TabId, string> = {
  contract: "契约层",
  docs: "docs 分级",
  board: "进度板",
  findings: "告警",
  logic: "近期功能实现",
};

export const TIER_LABELS: Record<DocTier, string> = {
  ai: "AI 读",
  human: "人读",
  none: "未分级",
};

export const REF_STATUS_LABELS: Record<RefStatus, string> = {
  ok: "命中",
  qualified: "根相对命中",
  under: "同名命中",
  dead: "失效",
};

export function emptySummary(): ScanSummary {
  return { error: 0, warning: 0, info: 0, total: 0 };
}
