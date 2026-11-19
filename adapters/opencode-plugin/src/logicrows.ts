/**
 * 近期功能实现文档条目的类型、状态呈现与行构建（纯函数，无进程调用）。
 *
 * 上游：logic.ts 取 `govern logic list --json` 的 data 段。
 * 下游：panel.tsx / session.ts 消费行与状态工具。
 */
import type { TreeRow, ThemeColors, ThemeColor, RowKind } from "./types.ts";

/** fresh=叙事与代码同步；stale=代码改过未复核；broken=指针失效；draft=新起草未复核。 */
export type LogicStatus = "fresh" | "stale" | "broken" | "draft";

/** 代码指针：文件、符号与行区间（role 说明该指针在近期功能实现里的职责）。 */
export interface LogicPointer {
  file: string;
  symbol: string;
  line: number;
  end: number;
  role: string;
}

/** 机制小节：标题 + 正文（条目级 sections 与域级 crosscut 共用同形结构）。 */
export interface LogicSection {
  title: string;
  body: string;
}

/** 一条近期功能实现文档条目。 */
export interface LogicEntry {
  id: string;
  title: string;
  summary: string;
  narrative: string;
  /** 机制小节：叙事之后的分节说明，按顺序渲染（引擎可缺省，按空数组处理）。 */
  sections: LogicSection[];
  status_detail: string;
  flow: string[];
  pointers: LogicPointer[];
  status: LogicStatus;
  /** 斜杠分隔的分组路径；空串表示未分组（直接挂在 domain 下）。 */
  group: string;
  /** 条目类型：module / service / resource / api / data / task。 */
  kind: string;
}

/** 一个近期功能实现域（对应 .governance/logic 下的一个文件）。 */
export interface LogicDomain {
  domain: string;
  title: string;
  updated: string;
  file: string;
  /** 域概览：分组行/目录行详情里展示（引擎可缺省，按空串处理）。 */
  overview: string;
  /** 横切机制小节：跨条目的共性说明，按顺序渲染。 */
  crosscut: LogicSection[];
  entries: LogicEntry[];
}

/** 近期功能实现条目行：树行 + 所属 domain 与条目本体。 */
export type LogicRow = TreeRow & { logicDomain: string; logicEntry: LogicEntry };
/** 近期功能实现分组行：树行 + 所属 domain（分组行没有条目本体）。 */
export type LogicGroupRow = TreeRow & { logicDomain: string };
/** 近期功能实现目录行：树行 + 所属 domain（group 路径生成的目录节点）。 */
export type LogicDirRow = TreeRow & { logicDomain: string; logicGroupPath: string };

/** 状态严重度排序：broken > stale > draft > fresh（越靠前越需要处理）。 */
const LOGIC_STATUS_ORDER: Record<LogicStatus, number> = {
  broken: 0,
  stale: 1,
  draft: 2,
  fresh: 3,
};

/** 状态标记：已同步不显示标记 / ● 待复核 / ✗ 指针失效 / ○ 草稿。 */
export function logicStatusMark(status: LogicStatus): string {
  if (status === "fresh") return "";
  if (status === "stale") return "●";
  if (status === "broken") return "✗";
  return "○";
}

/** 状态前缀：已同步无标记（树行传 blankSlot 留空位保持标题对齐），异常态为标记 + 空格。 */
export function logicStatusPrefix(status: LogicStatus, blankSlot = false): string {
  const mark = logicStatusMark(status);
  if (mark) return `${mark} `;
  return blankSlot ? "  " : "";
}

/** 状态配色：沿用面板既有语义色（成功 / 警告 / 错误 / 次要文字），不引入新色板。 */
export function logicStatusColor(status: LogicStatus, theme: ThemeColors): ThemeColor {
  if (status === "fresh") return theme.success;
  if (status === "stale") return theme.warning;
  if (status === "broken") return theme.error;
  return theme.textMuted;
}

/** domain 展示名：标题优先，缺省回落到 domain 键。 */
function domainTitle(domain: LogicDomain): string {
  return domain.title?.trim() || domain.domain?.trim() || "";
}

/** 条目排序：状态严重度优先，再按标题与 id。 */
function sortEntries(entries: LogicEntry[]): LogicEntry[] {
  return [...entries].sort(
    (a, b) =>
      LOGIC_STATUS_ORDER[a.status] - LOGIC_STATUS_ORDER[b.status] ||
      a.title.localeCompare(b.title) ||
      a.id.localeCompare(b.id),
  );
}

/** group 路径节点：一棵按斜杠路径展开的目录树。 */
interface GroupNode {
  /** 完整路径（不含 domain），如 "核心/鉴权"。 */
  path: string;
  /** 末段名称，如 "鉴权"。 */
  name: string;
  children: Map<string, GroupNode>;
  /** 直接挂在该节点下的条目。 */
  entries: LogicEntry[];
}

/** 把条目按 group 路径挂到目录树上：空 group 的条目留在根（domain 下）。 */
function buildGroupTree(entries: LogicEntry[]): GroupNode {
  const root: GroupNode = { path: "", name: "", children: new Map(), entries: [] };
  for (const entry of entries) {
    const segments = (entry.group ?? "")
      .split("/")
      .map((part) => part.trim())
      .filter(Boolean);
    if (segments.length === 0) {
      root.entries.push(entry);
      continue;
    }
    let node = root;
    let path = "";
    for (const segment of segments) {
      path = path ? `${path}/${segment}` : segment;
      let child = node.children.get(segment);
      if (!child) {
        child = { path, name: segment, children: new Map(), entries: [] };
        node.children.set(segment, child);
      }
      node = child;
    }
    node.entries.push(entry);
  }
  return root;
}

/** 子树条目数：目录行的计数用。 */
function subtreeEntryCount(node: GroupNode): number {
  let count = node.entries.length;
  for (const child of node.children.values()) count += subtreeEntryCount(child);
  return count;
}

/**
 * 把近期功能实现文档摊平成树行：domain 分组行 → group 目录行（可折叠）→ 条目行。
 * 排序：domain 按标题；目录按名称；条目按状态严重度（broken > stale > draft > fresh）再标题。
 * 分组行字段照 tree.ts 的 groupRow / dirRow 约定：path 为空、meta 放标题或计数、scopePaths 覆盖子行路径。
 */
export function buildLogicRows(domains: LogicDomain[]): TreeRow[] {
  const rows: TreeRow[] = [];
  const sortedDomains = [...(domains ?? [])].sort(
    (a, b) => domainTitle(a).localeCompare(domainTitle(b)) || a.domain.localeCompare(b.domain),
  );
  for (const domain of sortedDomains) {
    const entries = sortEntries(domain.entries ?? []);
    const stale = entries.filter((entry) => entry.status === "stale").length;
    const broken = entries.filter((entry) => entry.status === "broken").length;
    const anomalies = stale + broken;
    const title = domainTitle(domain);
    const label =
      anomalies > 0
        ? `${title} · ${entries.length} 条 · 异常 ${anomalies}`
        : `${title} · ${entries.length} 条`;
    const group: LogicGroupRow = {
      id: `logic:group:${domain.domain}`,
      tab: "logic",
      kind: "group",
      path: "",
      label,
      depth: 0,
      meta: anomalies > 0 ? `stale ${stale} · broken ${broken}` : "",
      collapsible: true,
      defaultOpen: true,
      scopePaths: entries.map(() => domain.file ?? ""),
      logicDomain: domain.domain,
    };
    rows.push(group);

    const tree = buildGroupTree(entries);
    const entryRow = (entry: LogicEntry, depth: number): void => {
      const row: LogicRow = {
        id: `logic:${domain.domain}:${entry.id}`,
        tab: "logic",
        kind: "entry" as RowKind,
        path: domain.file ?? "",
        label: `${logicStatusPrefix(entry.status, true)}${entry.title}`,
        depth,
        meta: entry.summary ?? "",
        logicDomain: domain.domain,
        logicEntry: entry,
      };
      rows.push(row);
    };

    // 未分组的条目直接挂在 domain 下；目录行放在其后（照 tree.ts「本层在前、子目录在后」）。
    for (const entry of sortEntries(tree.entries)) entryRow(entry, 1);

    const emitDirs = (node: GroupNode, depth: number): void => {
      const children = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name));
      for (const child of children) {
        const count = subtreeEntryCount(child);
        const dir: LogicDirRow = {
          id: `logicdir:${domain.domain}:${child.path}`,
          tab: "logic",
          kind: "dir",
          path: "",
          label: `${child.name}（${count} 条）`,
          depth,
          meta: "",
          scopePaths: [],
          logicDomain: domain.domain,
          logicGroupPath: child.path,
          // 近期功能实现域内的所有层级目录默认展开：首屏就要能看到条目，不必逐层手动展开。
          defaultOpen: true,
        };
        rows.push(dir);
        for (const entry of sortEntries(child.entries)) entryRow(entry, depth + 1);
        emitDirs(child, depth + 1);
      }
    };
    emitDirs(tree, 1);
  }
  return rows;
}

/** 读取行上的近期功能实现扩展字段：TreeRow 未声明 logicDomain / logicEntry。 */
function rowExtensions(row: TreeRow): { logicDomain?: string; logicEntry?: LogicEntry } {
  return row as unknown as { logicDomain?: string; logicEntry?: LogicEntry };
}

/** 从目录行 id 取 group 路径：id 形如 `logicdir:{domain}:{路径}`。 */
function dirPathFromId(id: string): string {
  const prefix = "logicdir:";
  if (!id.startsWith(prefix)) return "";
  const rest = id.slice(prefix.length);
  const separator = rest.indexOf(":");
  return separator < 0 ? rest : rest.slice(separator + 1);
}

/** 按 depth 还原父子关系（行按 DFS 顺序给出）。 */
function buildHierarchy(rows: TreeRow[]): Map<string, TreeRow[]> {
  const children = new Map<string, TreeRow[]>();
  const stack: TreeRow[] = [];
  for (const row of rows) {
    while (stack.length > 0 && stack[stack.length - 1].depth >= row.depth) stack.pop();
    const parent = stack[stack.length - 1];
    if (parent) {
      const list = children.get(parent.id) ?? [];
      list.push(row);
      children.set(parent.id, list);
    }
    stack.push(row);
  }
  return children;
}

/** 把整棵子树标记为保留（目录名命中时保留该目录与子树）。 */
function keepSubtree(row: TreeRow, children: Map<string, TreeRow[]>, kept: Set<TreeRow>): void {
  for (const child of children.get(row.id) ?? []) {
    kept.add(child);
    keepSubtree(child, children, kept);
  }
}

/** 子树里是否有已保留的行（用于隐藏空目录 / 空分组）。 */
function hasKeptDescendant(
  row: TreeRow,
  children: Map<string, TreeRow[]>,
  kept: Set<TreeRow>,
): boolean {
  for (const child of children.get(row.id) ?? []) {
    if (kept.has(child)) return true;
    if (hasKeptDescendant(child, children, kept)) return true;
  }
  return false;
}

/**
 * 过滤近期功能实现行：命中 domain 标题、目录名或条目 title / summary / narrative 的行保留；
 * 目录名命中时保留该目录与整棵子树；目录与分组在子树无命中项时隐藏。
 */
export function filterLogicRows(rows: TreeRow[], query: string): TreeRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  const children = buildHierarchy(rows);
  const domainTitles = new Map<string, string>();
  for (const row of rows) {
    if (row.kind !== "group") continue;
    // 分组行 meta 已改为异常计数，域标题从 label 前缀取（label 形如「标题 · N 条 · 异常 N」）。
    const separator = row.label.lastIndexOf(" · ");
    domainTitles.set(
      rowExtensions(row).logicDomain ?? "",
      separator < 0 ? row.label : row.label.slice(0, separator),
    );
  }
  const kept = new Set<TreeRow>();
  for (const row of rows) {
    if (row.kind === "group") {
      const haystack = `${row.label} ${row.meta ?? ""}`.toLowerCase();
      if (haystack.includes(q)) {
        kept.add(row);
        keepSubtree(row, children, kept);
      }
      continue;
    }
    if (row.kind === "dir") {
      const path = dirPathFromId(row.id);
      const haystack = `${row.label} ${path}`.toLowerCase();
      if (haystack.includes(q)) {
        kept.add(row);
        keepSubtree(row, children, kept);
      }
      continue;
    }
    const entry = rowExtensions(row).logicEntry;
    if (!entry) continue;
    const domain = rowExtensions(row).logicDomain ?? "";
    const haystack = [
      row.label,
      row.meta ?? "",
      entry.title,
      entry.summary,
      entry.narrative,
      domainTitles.get(domain) ?? "",
    ]
      .join(" ")
      .toLowerCase();
    if (haystack.includes(q)) kept.add(row);
  }
  // 目录与分组：自身命中或子树有命中项才保留（否则隐藏空目录）。
  for (const row of rows) {
    if (row.kind !== "dir" && row.kind !== "group") continue;
    if (kept.has(row)) continue;
    if (hasKeptDescendant(row, children, kept)) kept.add(row);
  }
  return rows.filter((row) => kept.has(row));
}

/** 统计条目状态分布：总数与四种状态各多少条。 */
export function logicCounts(domains: LogicDomain[]): {
  total: number;
  stale: number;
  broken: number;
  draft: number;
  fresh: number;
} {
  const counts = { total: 0, stale: 0, broken: 0, draft: 0, fresh: 0 };
  for (const domain of domains ?? []) {
    for (const entry of domain.entries ?? []) {
      counts.total += 1;
      if (entry.status === "stale") counts.stale += 1;
      else if (entry.status === "broken") counts.broken += 1;
      else if (entry.status === "draft") counts.draft += 1;
      else counts.fresh += 1;
    }
  }
  return counts;
}

/** 空态文案：未加载完提示加载中，加载完无数据提示生成命令。 */
export function emptyLogicMessage(loaded: boolean): string {
  return loaded ? "未发现近期功能实现文档：govern logic init <domain> 生成" : "加载中…";
}
