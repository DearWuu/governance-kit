/**
 * 三类树快照、进度板解析与告警行构造。
 *
 * 上游：index.tsx 在 setup 与文件变更后调用 buildSnapshot()；session.ts 用扫描结果调 buildFindingRows()。
 * 下游：panel.tsx 消费 TreeSnapshot 与 readTextMeta()。
 * 约束：树来自文件系统直读，检查器不可用时仍可渲染；快照按仓库缓存，watcher/r 时失效。
 */
import * as fs from "node:fs/promises";
import * as fsSync from "node:fs";
import * as path from "node:path";
import type {
  BoardStatusCount,
  DocsTiers,
  Finding,
  TabId,
  TreeRow,
  TreeSnapshot,
} from "./types.ts";
import { BOARD_STATUS_ORDER, SEVERITY_ORDER } from "./types.ts";
import { createTtlCache, stampKey, statStamp } from "./cache.ts";
import {
  AGENT_FILE_RE,
  MD_FILE_RE,
  MAX_TEXT_BYTES,
  TEXT_FILE_RE,
  classifyDoc,
  loadConfig,
  normalizeRel,
  readTextFile,
  resolveBoardPaths,
  walkFiles,
} from "./repo.ts";

/** 快照缓存 TTL：与前端其它缓存口径一致。 */
const SNAPSHOT_TTL_MS = 20_000;

/**
 * 快照缓存：键是「仓库 + 关键文件指纹」，指纹取 path+mtimeMs+size，
 * 文件增删或治理配置改动会换键自然失效；watcher/r 另外显式清空。
 */
const snapshotCache = createTtlCache<TreeSnapshot>({ ttlMs: SNAPSHOT_TTL_MS, maxEntries: 4 });

/** 清空树快照缓存（watcher 变更或手动重扫时调用）。 */
export function invalidateSnapshotCache(): void {
  snapshotCache.clear();
}

/** 治理配置候选路径；与 repo.ts 的 loadConfig 保持同一顺序。 */
const CONFIG_CANDIDATES = [
  ".governance/config.json",
  ".governance/config.jsonc",
  ".governance/config.toml",
];

/** 快照指纹：仓库根目录（条目增删会改 mtime）与治理配置的 path+mtimeMs+size。 */
async function snapshotKey(repo: string): Promise<string> {
  const parts = [repo];
  for (const rel of CONFIG_CANDIDATES) {
    const abs = path.join(repo, rel);
    const stamp = await statStamp(abs);
    if (stamp) parts.push(stampKey(abs, stamp));
  }
  const rootStamp = await statStamp(repo);
  if (rootStamp) parts.push(stampKey(repo, rootStamp));
  return parts.join("|");
}

const LEGACY_STATUS: Record<string, string> = {
  待办: "待启动",
  部分完成: "进行中",
  完成: "已完成",
};

interface BoardTask {
  file: string;
  line?: number;
  title: string;
  status: string;
  owner?: string;
  updated?: string;
  goal?: string;
  evidence?: string;
  mode: "task-file" | "legacy";
}

/** 配置里允许写绝对路径，树与监听统一使用仓库相对路径。 */
function toRepoRel(repo: string, value: string): string {
  return path.isAbsolute(value)
    ? normalizeRel(repo, value)
    : value.replace(/\\/g, "/").replace(/^\.\/+/, "").replace(/\/+$/, "");
}

function fileRow(
  tab: TabId,
  rel: string,
  options: { label?: string; badge?: string; meta?: string; line?: number; idSuffix?: string } = {},
): TreeRow {
  return {
    id: `${tab}::${rel}${options.idSuffix ?? ""}`,
    tab,
    kind: "file",
    path: rel,
    label: options.label ?? rel,
    depth: 0,
    badge: options.badge,
    meta: options.meta,
    line: options.line,
  };
}

function taskRow(tab: TabId, task: BoardTask): TreeRow {
  const suffix = task.mode === "legacy" ? `#L${task.line ?? 0}` : "";
  return {
    id: `${tab}::${task.file}${suffix}`,
    tab,
    kind: "task",
    path: task.file,
    label: task.title,
    depth: 0,
    badge: task.status,
    meta: [task.owner ? `负责 ${task.owner}` : "", task.updated ? `更新 ${task.updated}` : ""]
      .filter(Boolean)
      .join(" · "),
    line: task.line,
  };
}

/**
 * 告警行：每条 finding 一行。文件不在树里（docs/文档全景图.html、板任务文件等）也照样列出，
 * 行是 kind:"file" 的虚拟行，path 指向真实文件，详情区按 path 找 findings 与内容。
 * 排序：severity（error→warning→info）→ rule → file → line。
 */
export function buildFindingRows(findings: Finding[]): TreeRow[] {
  const seen = new Map<string, number>();
  return findings
    .filter((finding) => Boolean(finding.file) && finding.file !== ".")
    .sort(
      (a, b) =>
        SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] ||
        a.rule.localeCompare(b.rule) ||
        a.file.localeCompare(b.file) ||
        a.line - b.line,
    )
    .map((finding) => {
      const key = `${finding.file}:${finding.line}:${finding.rule}`;
      const hit = (seen.get(key) ?? 0) + 1;
      seen.set(key, hit);
      return {
        id: `findings::${key}${hit > 1 ? `#${hit}` : ""}`,
        tab: "findings" as const,
        kind: "file" as const,
        path: finding.file,
        label: `${finding.file}:${finding.line}`,
        depth: 0,
        meta: `${finding.rule} ${finding.message}`,
        finding,
      };
    });
}

function groupRow(
  tab: TabId,
  key: string,
  label: string,
  children: TreeRow[],
  meta?: string,
): TreeRow {
  const files = children.filter((child) => Boolean(child.path));
  return {
    id: `${tab}::group::${key}`,
    tab,
    kind: "group",
    path: "",
    label,
    depth: 0,
    meta: meta ?? `${files.length} 项`,
    scopePaths: files.map((child) => child.path),
  };
}

/**
 * 分组段落：children 为空时整段不渲染（标题也不出）。
 * 空分组只会让树里出现「配置」「AI 读文档（0）」这类噪声标题。
 */
function section(tab: TabId, key: string, label: string, children: TreeRow[], meta?: string): TreeRow[] {
  if (children.length === 0) return [];
  const group = groupRow(tab, key, label, children, meta);
  return [group, ...children.map((child) => ({ ...child, depth: child.depth + 1 }))];
}

/**
 * 目录节点：id 用「tab + section + 路径」保证稳定且唯一
 * （docs 分级 tab 里同一目录可能同时出现在 AI 读与人读两个分组下）。
 * scopePaths 覆盖全部后代文件（过滤/聚合用）。
 */
function dirRow(
  tab: TabId,
  sectionKey: string,
  dirPath: string,
  subtree: TreeRow[],
  meta?: string,
): TreeRow {
  const scopePaths = subtree.map((row) => row.path).filter(Boolean);
  return {
    id: `${tab}::dir::${sectionKey}::${dirPath}`,
    tab,
    kind: "dir",
    path: "",
    label: `${dirPath}/`,
    depth: 0,
    meta: meta ?? `${scopePaths.length} 项`,
    scopePaths,
  };
}

interface NestedNode {
  files: TreeRow[];
  dirs: Map<string, NestedNode>;
}

function insertNested(root: NestedNode, rel: string, row: TreeRow): void {
  const dir = path.posix.dirname(rel);
  const segments = dir === "." || dir === "" ? [] : dir.split("/");
  let node = root;
  for (const segment of segments) {
    let child = node.dirs.get(segment);
    if (!child) {
      child = { files: [], dirs: new Map() };
      node.dirs.set(segment, child);
    }
    node = child;
  }
  node.files.push(row);
}

/** DFS 展开：本层文件在前、子目录在后；depth 从 0 起，由 section() 统一加一。 */
function flattenNested(
  tab: TabId,
  sectionKey: string,
  node: NestedNode,
  prefix: string,
  depth: number,
): TreeRow[] {
  const rows: TreeRow[] = [];
  const files = [...node.files].sort((a, b) => a.label.localeCompare(b.label));
  for (const file of files) rows.push({ ...file, depth });
  const names = [...node.dirs.keys()].sort((a, b) => a.localeCompare(b));
  for (const name of names) {
    const child = node.dirs.get(name) as NestedNode;
    const dirPath = prefix ? `${prefix}/${name}` : name;
    const subtree = flattenNested(tab, sectionKey, child, dirPath, depth + 1);
    rows.push({ ...dirRow(tab, sectionKey, dirPath, subtree), depth });
    rows.push(...subtree);
  }
  return rows;
}

/** 把一组相对路径按目录段嵌套成「目录行 + 文件行」的平面列表。 */
function nestedSection(
  tab: TabId,
  key: string,
  label: string,
  rels: string[],
  make: (rel: string) => TreeRow,
  meta?: string,
): TreeRow[] {
  const root: NestedNode = { files: [], dirs: new Map() };
  for (const rel of rels) insertNested(root, rel, make(rel));
  return section(tab, key, label, flattenNested(tab, key, root, "", 0), meta);
}

function parseFrontmatter(text: string): { meta: Record<string, string>; body: string } {
  const lines = text.split(/\r?\n/);
  if (lines[0]?.trim() !== "---") return { meta: {}, body: text };
  const meta: Record<string, string> = {};
  let end = -1;
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i].trim() === "---") {
      end = i;
      break;
    }
    const idx = lines[i].indexOf(":");
    if (idx > 0) {
      meta[lines[i].slice(0, idx).trim()] = lines[i]
        .slice(idx + 1)
        .trim()
        .replace(/^["']|["']$/g, "");
    }
  }
  if (end < 0) return { meta: {}, body: text };
  return { meta, body: lines.slice(end + 1).join("\n") };
}

function firstHeading(text: string): string {
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("#")) return line.replace(/^#+\s*/, "").trim();
  }
  return "";
}

/** 旧式进度板的 legacy 状态映射；任务卡 frontmatter 不做映射，非法状态原样展示。 */
function normalizeLegacyStatus(raw: string, statuses: string[]): string {
  const status = raw.trim();
  if (status && statuses.includes(status)) return status;
  return LEGACY_STATUS[status] ?? status;
}

async function parseTaskFiles(repo: string, tasksDir: string): Promise<BoardTask[]> {
  if (!tasksDir) return [];
  let entries: fsSync.Dirent[];
  try {
    entries = await fs.readdir(path.join(repo, tasksDir), { withFileTypes: true });
  } catch {
    return [];
  }
  const tasks: BoardTask[] = [];
  for (const entry of entries) {
    if (!entry.isFile() || !MD_FILE_RE.test(entry.name)) continue;
    const rel = `${tasksDir.replace(/\/+$/, "")}/${entry.name}`;
    const text = await readTextFile(repo, rel);
    if (!text) continue;
    const { meta, body } = parseFrontmatter(text);
    tasks.push({
      file: rel,
      title: meta.title || firstHeading(body) || entry.name.replace(MD_FILE_RE, ""),
      status: (meta.status ?? "").trim(),
      owner: meta.owner,
      updated: meta.updated,
      goal: meta.goal,
      evidence: meta.evidence,
      mode: "task-file",
    });
  }
  return tasks;
}

/** 兼容旧式 `## [状态] 标题` 进度板；只取标题、状态、负责人、更新日期。 */
function parseLegacyTasks(text: string, boardPath: string, statuses: string[]): BoardTask[] {
  const tasks: BoardTask[] = [];
  let current: BoardTask | undefined;
  const flush = (): void => {
    if (current) tasks.push(current);
  };
  for (const [idx, line] of text.split(/\r?\n/).entries()) {
    const heading = /^##\s*\[(.+?)\]\s*(.*)$/.exec(line);
    if (heading) {
      flush();
      current = {
        file: boardPath,
        line: idx + 1,
        title: heading[2].trim() || "(无标题)",
        status: normalizeLegacyStatus(heading[1], statuses),
        mode: "legacy",
      };
      continue;
    }
    if (!current) continue;
    const date = /(\d{4}-\d{2}-\d{2})/.exec(line);
    if (date && !current.updated) current.updated = date[1];
    const owner = /负责\s*[:：]\s*`?([^\s|`]+)`?/.exec(line);
    if (owner && !current.owner) current.owner = owner[1];
  }
  flush();
  return tasks;
}

function buildBoardRows(
  boardPath: string | undefined,
  tasks: BoardTask[],
  statuses: string[],
): TreeRow[] {
  const rows: TreeRow[] = [];
  const boardFile = boardPath ? [fileRow("board", boardPath, { badge: "进度板", meta: "进度板文件" })] : [];
  rows.push(...section("board", "board-file", "进度板文件", boardFile, boardPath ?? "未配置进度板"));

  const order = [...statuses];
  for (const task of tasks) {
    if (!order.includes(task.status)) order.push(task.status);
  }
  for (const status of order) {
    const group = tasks.filter((task) => task.status === status);
    if (group.length === 0) continue;
    rows.push(
      ...section(
        "board",
        `status:${status || "unknown"}`,
        `${status || "未知状态"}（${group.length}）`,
        group.map((task) => taskRow("board", task)),
      ),
    );
  }
  return rows;
}

/** 状态排序位次：看板顺序在前，未登记状态沉底。 */
function statusRank(status: string): number {
  const index = BOARD_STATUS_ORDER.indexOf(status);
  return index < 0 ? BOARD_STATUS_ORDER.length : index;
}

/** 统计各状态的任务数（引擎不可用时的降级数据源）。 */
export function countStatuses(tasks: BoardTask[]): BoardStatusCount[] {
  const counts = new Map<string, number>();
  for (const task of tasks) {
    const status = task.status || "未标注";
    counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([status, count]) => ({ status, count }))
    .sort((a, b) => statusRank(a.status) - statusRank(b.status) || a.status.localeCompare(b.status));
}

/** 过滤树：命中 label、path 或 meta 的条目保留，分组/目录行在子项命中时保留。 */
export function filterRows(rows: TreeRow[], query: string): TreeRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  const fileHit = new Set<string>();
  const keep = new Set<TreeRow>();
  for (const row of rows) {
    if (row.kind === "group" || row.kind === "dir") continue;
    const haystack = `${row.label} ${row.path} ${row.meta ?? ""}`.toLowerCase();
    if (!haystack.includes(q)) continue;
    fileHit.add(row.path);
    keep.add(row);
  }
  for (const row of rows) {
    if (row.kind !== "group" && row.kind !== "dir") continue;
    const labelHit = row.label.toLowerCase().includes(q);
    const childHit = (row.scopePaths ?? []).some((path) => fileHit.has(path));
    if (labelHit || childHit) keep.add(row);
  }
  return rows.filter((row) => keep.has(row));
}

/**
 * 折叠过滤：目录节点收起后隐藏其整棵子树；过滤生效时忽略折叠状态，
 * 让命中文件的祖先目录自动展开。可折叠行（进度板状态组/任务行）同样参与。
 */
export function visibleRows(
  rows: TreeRow[],
  options: { filterActive: boolean; dirExpanded: Record<string, boolean> },
): TreeRow[] {
  const stack: Array<{ depth: number; collapsed: boolean }> = [];
  const visible: TreeRow[] = [];
  for (const row of rows) {
    while (stack.length > 0 && row.depth <= stack[stack.length - 1].depth) stack.pop();
    const hidden = stack.some((entry) => entry.collapsed);
    if (row.kind === "dir" || row.collapsible) {
      const expanded =
        options.filterActive ||
        (options.dirExpanded[row.id] ?? row.defaultOpen ?? row.depth <= 1);
      stack.push({ depth: row.depth, collapsed: !expanded });
    }
    if (!hidden) visible.push(row);
  }
  return visible;
}

/** 构建三棵树的完整快照；任何 IO 失败都退化为空组而不是抛错。 */
export async function buildSnapshot(repo: string): Promise<TreeSnapshot> {
  const key = await snapshotKey(repo);
  const cached = snapshotCache.get(key);
  if (cached) return cached;
  const snapshot = await buildSnapshotUncached(repo);
  snapshotCache.set(key, snapshot);
  return snapshot;
}

/** 实际扫描文件系统构建快照（buildSnapshot 的未缓存实现）。 */
async function buildSnapshotUncached(repo: string): Promise<TreeSnapshot> {
  const { files, truncated } = await walkFiles(repo);
  const { config, source } = await loadConfig(repo);
  const useHeuristic = config.tiers.ai_read.length === 0 && config.tiers.human_read.length === 0;

  const agents = files.filter((rel) => AGENT_FILE_RE.test(path.posix.basename(rel)));
  const docs = files.filter(
    (rel) => MD_FILE_RE.test(rel) && (rel === "docs/README.md" || rel.startsWith("docs/")),
  );

  const aiDocs: string[] = [];
  const humanDocs: string[] = [];
  const unclassifiedDocs: string[] = [];
  for (const rel of docs) {
    const tier = classifyDoc(rel, config, useHeuristic);
    if (tier === "ai") aiDocs.push(rel);
    else if (tier === "human") humanDocs.push(rel);
    else unclassifiedDocs.push(rel);
  }

  const docTiers: DocsTiers = {
    ai: aiDocs.length,
    human: humanDocs.length,
    unclassified: unclassifiedDocs.length,
  };

  const contractRows: TreeRow[] = [];
  const configRows = source
    ? [fileRow("contract", source, { badge: "配置", meta: "治理配置" })]
    : [];
  contractRows.push(...section("contract", "config", "配置", configRows, source ? undefined : "未发现 .governance/config"));
  contractRows.push(
    ...nestedSection(
      "contract",
      "agents",
      `指令文件（AGENTS.md 层级，${agents.length}）`,
      agents,
      (rel) =>
        fileRow("contract", rel, {
          badge: path.posix.basename(rel).startsWith("CLAUDE") ? "CLAUDE" : "AGENTS",
        }),
      agents.length === 0 ? "未发现 AGENTS.md" : undefined,
    ),
  );
  contractRows.push(
    ...nestedSection(
      "contract",
      "ai-docs",
      `AI 读文档（${aiDocs.length}）`,
      aiDocs,
      (rel) => fileRow("contract", rel, { badge: "AI 读" }),
    ),
  );

  const docsRows: TreeRow[] = [
    ...nestedSection("docs", "ai", `AI 读（${aiDocs.length}）`, aiDocs, (rel) =>
      fileRow("docs", rel, { badge: "AI 读" }),
    ),
    ...nestedSection("docs", "human", `人读（${humanDocs.length}）`, humanDocs, (rel) =>
      fileRow("docs", rel, { badge: "人读" }),
    ),
    ...nestedSection(
      "docs",
      "unclassified",
      `未分级（${unclassifiedDocs.length}）`,
      unclassifiedDocs,
      (rel) => fileRow("docs", rel, { badge: "未分级" }),
    ),
  ];

  // 与引擎 board_paths 同口径的三级发现：显式配置 > 根 进度板.md|进度/ > docs/ 旧位置 > 无板。
  const boardPaths = resolveBoardPaths(repo, config);
  const boardCandidate = boardPaths.path ? toRepoRel(repo, boardPaths.path) : "";
  const boardRel =
    boardCandidate && boardCandidate !== "." && fsSync.existsSync(path.join(repo, boardCandidate))
      ? boardCandidate
      : undefined;
  const tasksDirRel = boardPaths.tasksDir ? toRepoRel(repo, boardPaths.tasksDir) : "";
  const boardText = boardRel ? await readTextFile(repo, boardRel) : "";
  const tasks = [
    ...(await parseTaskFiles(repo, tasksDirRel)),
    ...(boardRel && boardText ? parseLegacyTasks(boardText, boardRel, config.board.statuses) : []),
  ];
  const boardRows = buildBoardRows(boardRel, tasks, config.board.statuses);

  const snapshot: TreeSnapshot = {
    // findings 行不来自文件系统：由扫描结果派生（见 buildFindingRows）。
    // logic 行同理：由引擎 `logic list` 派生，快照里先留空（见 session.ts）。
    rows: { contract: contractRows, docs: docsRows, board: boardRows, findings: [], logic: [] },
    counts: {
      contract: configRows.length + agents.length + aiDocs.length,
      docs: docs.length,
      board: tasks.length + (boardRel ? 1 : 0),
      findings: 0,
      logic: 0,
    },
    docTiers,
    configSource: source,
    boardPath: boardRel,
    boardTasksDir: tasksDirRel || undefined,
    truncated,
    boardStatuses: countStatuses(tasks),
  };
  return snapshot;
}

/** 面板详情用：读文本并返回行数，超限截断。 */
export function readTextMeta(repo: string, rel: string): { lines: number; text: string } | undefined {
  if (!rel || !TEXT_FILE_RE.test(rel)) return undefined;
  try {
    const buf = fsSync.readFileSync(path.join(repo, rel));
    const text = buf.subarray(0, MAX_TEXT_BYTES).toString("utf8");
    return { lines: text.length === 0 ? 0 : text.split("\n").length, text };
  } catch {
    return undefined;
  }
}