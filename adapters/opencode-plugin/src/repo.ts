/**
 * 仓库只读视图：文件索引、配置装载、监听目录与 docs 分级。
 *
 * 上游：tree.ts 的 buildSnapshot() 调用 walkFiles()/loadConfig()/classifyDoc()/resolveBoardPaths()。
 * 下游：index.tsx 用 discoverRepo()/collectWatchDirs()/indexFindings()。
 * 约束：纯 node 内置模块；引擎缺失不影响这里。文件读取按 path+mtimeMs+size 缓存。
 */
import * as fs from "node:fs/promises";
import * as fsSync from "node:fs";
import * as path from "node:path";
import type { Finding } from "./types.ts";
import { readTextCached } from "./cache.ts";

/** 与 engine/governkit/core.py 的 IGNORE_DIRS 保持同一口径。 */
const IGNORE_DIRS = new Set([
  ".git", ".hg", ".svn", "node_modules", "dist", "build", "out", "target",
  ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
  ".ruff_cache", ".next", ".turbo", ".cache", "vendor", "coverage",
  ".idea", ".vscode", "site-packages",
]);

export const AGENT_FILE_RE = /^(AGENTS(\.override)?|CLAUDE)\.md$/i;
export const MD_FILE_RE = /\.(md|markdown)$/i;
export const TEXT_FILE_RE = /\.(md|markdown|txt|json|jsonc|toml|ya?ml)$/i;
const MAX_FILES = 40_000;
const MAX_WATCH_DIRS = 1_500;
export const MAX_TEXT_BYTES = 512 * 1024;

const DEFAULT_CONFIG: GovernConfig = {
  tiers: { ai_read: [], human_read: [] },
  board: {
    // 空串 = 自动发现（同 engine/governkit/core.py 默认配置）：根 进度板.md|进度/ 优先，docs/ 兼容旧位置。
    path: "",
    tasks_dir: "",
    statuses: ["待启动", "进行中", "待验收", "已完成", "挂起"],
  },
};

const AI_HINTS = ["docs/开发参考", "docs/README.md"];
const HUMAN_HINTS = ["docs/方案", "docs/报告", "docs/运维", "docs/接口", "docs/技术复盘"];

export interface GovernConfig {
  tiers: { ai_read: string[]; human_read: string[] };
  board: { path: string; tasks_dir: string; statuses: string[] };
}

export interface LoadedConfig {
  config: GovernConfig;
  source?: string;
}

export interface WatchPlan {
  dirs: Set<string>;
  truncated: boolean;
}

/** 从 context 给出的目录向上找仓库根，与 engine/core.py 的 discover_repo 同序。 */
export function discoverRepo(start: string): string {
  const initial = path.resolve(start);
  let cur = initial;
  for (;;) {
    if (fsSync.existsSync(path.join(cur, ".git"))) return cur;
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  cur = initial;
  for (;;) {
    if (
      fsSync.existsSync(path.join(cur, ".governance")) ||
      fsSync.existsSync(path.join(cur, "AGENTS.md"))
    ) {
      return cur;
    }
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return initial;
}

function shouldIgnoreDir(name: string): boolean {
  return IGNORE_DIRS.has(name) || (name.startsWith(".git") && name !== ".governance");
}

export async function readTextFile(repo: string, rel: string, limit = MAX_TEXT_BYTES): Promise<string> {
  const text = await readTextCached(path.join(repo, rel), limit);
  return text ?? "";
}

/** 把 finding 的 file 统一成仓库相对 POSIX 路径；仓库级条目返回 "."。 */
export function normalizeRel(repo: string, file: string): string {
  if (!file) return ".";
  let rel = file.replace(/\\/g, "/").trim();
  if (rel === "." || rel === "./") return ".";
  if (path.isAbsolute(rel)) {
    rel = path.relative(repo, rel).replace(/\\/g, "/");
  }
  rel = rel.replace(/^\.\/+/, "").replace(/\/+$/, "");
  return rel || ".";
}

export function indexFindings(repo: string, findings: Finding[]): Record<string, Finding[]> {
  const index: Record<string, Finding[]> = {};
  for (const finding of findings) {
    const rel = normalizeRel(repo, finding.file);
    (index[rel] ??= []).push(finding);
  }
  return index;
}

/** 目录清单缓存 TTL：与快照缓存一致，watcher 正常时会被显式失效。 */
const WALK_TTL_MS = 20_000;

/** 单趟异步遍历；跳过依赖/构建目录，达到上限即截断。结果按仓库缓存，watcher/r 时失效。 */
let walkCache: { repo: string; files: string[]; truncated: boolean; at: number } | undefined;

/** 监听目录清单缓存：目录结构不变时避免重复全仓 readdir。 */
let watchDirCache: { key: string; plan: WatchPlan } | undefined;

/** 清空目录清单缓存（watcher 变更或手动重扫时调用）。 */
export function invalidateRepoCache(): void {
  walkCache = undefined;
  watchDirCache = undefined;
}

export async function walkFiles(repo: string): Promise<{ files: string[]; truncated: boolean }> {
  if (walkCache && walkCache.repo === repo && Date.now() - walkCache.at <= WALK_TTL_MS) {
    return { files: walkCache.files, truncated: walkCache.truncated };
  }
  const files: string[] = [];
  const stack: string[] = [""];
  let truncated = false;
  while (stack.length > 0) {
    const relDir = stack.pop() as string;
    let entries: fsSync.Dirent[];
    try {
      entries = await fs.readdir(relDir ? path.join(repo, relDir) : repo, { withFileTypes: true });
    } catch {
      continue;
    }
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      const rel = relDir ? `${relDir}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        if (!shouldIgnoreDir(entry.name)) stack.push(rel);
        continue;
      }
      if (!entry.isFile() || entry.name === ".DS_Store") continue;
      files.push(rel);
      if (files.length >= MAX_FILES) {
        truncated = true;
        stack.length = 0;
        break;
      }
    }
  }
  files.sort();
  walkCache = { repo, files, truncated, at: Date.now() };
  return { files, truncated };
}

/**
 * 建立监听目录集合：docs/ 全树、.governance/、各级 AGENTS.md 所在目录，
 * 以及调用方补充的目录（例如自定义进度板所在目录）。
 */
export function collectWatchDirs(repo: string, extraDirs: string[] = []): WatchPlan {
  const cacheKey = `${repo}\u0000${[...extraDirs].sort().join("\u0000")}`;
  if (watchDirCache && watchDirCache.key === cacheKey) {
    return watchDirCache.plan;
  }
  const dirs = new Set<string>([repo]);
  let truncated = false;

  const addTree = (root: string): void => {
    const stack = [root];
    while (stack.length > 0) {
      if (dirs.size >= MAX_WATCH_DIRS) {
        truncated = true;
        return;
      }
      const dir = stack.pop() as string;
      let entries: fsSync.Dirent[];
      try {
        entries = fsSync.readdirSync(dir, { withFileTypes: true });
      } catch {
        continue;
      }
      dirs.add(dir);
      for (const entry of entries) {
        if (entry.isDirectory() && !shouldIgnoreDir(entry.name)) {
          stack.push(path.join(dir, entry.name));
        }
      }
    }
  };

  addTree(path.join(repo, "docs"));
  addTree(path.join(repo, ".governance"));
  for (const extra of extraDirs) addTree(extra);

  const stack = [repo];
  while (stack.length > 0) {
    if (dirs.size >= MAX_WATCH_DIRS) {
      truncated = true;
      break;
    }
    const dir = stack.pop() as string;
    let entries: fsSync.Dirent[];
    try {
      entries = fsSync.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!shouldIgnoreDir(entry.name)) stack.push(path.join(dir, entry.name));
      } else if (entry.isFile() && AGENT_FILE_RE.test(entry.name)) {
        dirs.add(dir);
      }
    }
  }

  const plan: WatchPlan = { dirs, truncated };
  watchDirCache = { key: cacheKey, plan };
  return plan;
}

function stripJsonc(text: string): string {
  let out = "";
  let inString = false;
  let escaped = false;
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (inString) {
      out += ch;
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      i += 1;
      continue;
    }
    if (ch === '"') {
      inString = true;
      out += ch;
      i += 1;
      continue;
    }
    if (ch === "/" && text[i + 1] === "/") {
      while (i < text.length && text[i] !== "\n") i += 1;
      continue;
    }
    if (ch === "/" && text[i + 1] === "*") {
      i += 2;
      while (i + 1 < text.length && !(text[i] === "*" && text[i + 1] === "/")) i += 1;
      i += 2;
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

/** 只覆盖 .governance/config.toml 里常见的 section + 标量/数组写法。 */
function parseSimpleToml(text: string): Record<string, unknown> {
  const root: Record<string, unknown> = {};
  let section = root;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const header = /^\[([^\]]+)\]$/.exec(line);
    if (header) {
      let cur = root;
      for (const key of header[1].split(".").map((part) => part.trim())) {
        const next = cur[key];
        if (!next || typeof next !== "object") cur[key] = {};
        cur = cur[key] as Record<string, unknown>;
      }
      section = cur;
      continue;
    }
    const pair = /^([A-Za-z0-9_.-]+)\s*=\s*(.+)$/.exec(line);
    if (!pair) continue;
    const value = pair[2].trim();
    try {
      section[pair[1]] = JSON.parse(value.replace(/'/g, '"'));
    } catch {
      section[pair[1]] = value.replace(/^["']|["']$/g, "");
    }
  }
  return root;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

function mergeConfig(raw: Record<string, unknown>): GovernConfig {
  const tiers = asRecord(raw.tiers) ?? {};
  const board = asRecord(raw.board) ?? {};
  const statuses = stringArray(board.statuses);
  return {
    tiers: {
      ai_read: stringArray(tiers.ai_read),
      human_read: stringArray(tiers.human_read),
    },
    board: {
      path: typeof board.path === "string" && board.path ? board.path : DEFAULT_CONFIG.board.path,
      tasks_dir:
        typeof board.tasks_dir === "string" && board.tasks_dir
          ? board.tasks_dir
          : DEFAULT_CONFIG.board.tasks_dir,
      statuses: statuses.length > 0 ? statuses : [...DEFAULT_CONFIG.board.statuses],
    },
  };
}

/** 进度板发现结果：仓库相对路径；任一为空串表示该项不启用，两项都空表示本仓无板。 */
export interface BoardPaths {
  path: string;
  tasksDir: string;
}

/**
 * 进度板三级发现，与 engine/governkit/core.py 的 board_paths 同口径：
 * 显式配置（任一键非空即整对生效）> 仓库根 `进度板.md`|`进度/` > `docs/` 旧位置 > 无板。
 * 调用方：tree.ts 的 buildSnapshot()。
 */
export function resolveBoardPaths(repo: string, config: GovernConfig): BoardPaths {
  const explicitPath = config.board.path.trim();
  const explicitTasksDir = config.board.tasks_dir.trim();
  if (explicitPath || explicitTasksDir) {
    return { path: explicitPath, tasksDir: explicitTasksDir };
  }
  if (fsSync.existsSync(path.join(repo, "进度板.md")) || fsSync.existsSync(path.join(repo, "进度"))) {
    return { path: "进度板.md", tasksDir: "进度" };
  }
  if (
    fsSync.existsSync(path.join(repo, "docs/进度板.md")) ||
    fsSync.existsSync(path.join(repo, "docs/进度"))
  ) {
    return { path: "docs/进度板.md", tasksDir: "docs/进度" };
  }
  return { path: "", tasksDir: "" };
}

export async function loadConfig(repo: string): Promise<LoadedConfig> {
  const candidates = [
    ".governance/config.json",
    ".governance/config.jsonc",
    ".governance/config.toml",
  ];
  for (const rel of candidates) {
    const abs = path.join(repo, rel);
    let text: string;
    try {
      const cached = await readTextCached(abs);
      if (cached === undefined) continue;
      text = cached;
    } catch {
      continue;
    }
    try {
      const raw =
        rel.endsWith(".toml")
          ? parseSimpleToml(text)
          : (JSON.parse(stripJsonc(text)) as Record<string, unknown>);
      return { config: mergeConfig(raw), source: rel };
    } catch {
      // 配置坏了不阻塞树：回退默认值，面板通过 findings/降级信息提示。
      return { config: structuredClone(DEFAULT_CONFIG), source: rel };
    }
  }
  return { config: structuredClone(DEFAULT_CONFIG) };
}

function matchesPrefix(rel: string, prefixes: string[]): boolean {
  for (const prefix of prefixes) {
    const clean = prefix.replace(/\\/g, "/").replace(/^\.\/+/, "").replace(/\/+$/, "");
    if (!clean) continue;
    if (rel === clean || rel.startsWith(`${clean}/`)) return true;
  }
  return false;
}

export function classifyDoc(
  rel: string,
  config: GovernConfig,
  useHeuristic: boolean,
): "ai" | "human" | "unclassified" {
  if (matchesPrefix(rel, config.tiers.ai_read)) return "ai";
  if (matchesPrefix(rel, config.tiers.human_read)) return "human";
  if (!useHeuristic) return "unclassified";
  if (AI_HINTS.some((hint) => rel === hint || rel.startsWith(`${hint}/`))) return "ai";
  if (HUMAN_HINTS.some((hint) => rel.startsWith(`${hint}/`))) return "human";
  return "unclassified";
}

