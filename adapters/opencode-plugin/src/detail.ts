/**
 * 单文件详情读取：调用 engine/govern 的 `show` 子命令，引擎不可用时降级为文件系统直读。
 *
 * 上游：index.tsx 在选中文件变化、翻页、指针跳转时调用 load()。
 * 下游：panel.tsx / detailpane.tsx 消费 DocDetail。
 * 约束：引擎缺失、退出码非 0、输出不合法时一律降级，不抛错。
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import * as path from "node:path";
import type { DocDetail, DocRef, DocTask, RefStatus } from "./types.ts";
import { readTextCached, statStamp, stampKey } from "./cache.ts";

/** 每页默认行数；`DOC_PAGE_LINES` 可覆盖。 */
export const DEFAULT_PAGE_LINES = 100;

/** 解析每页行数：环境变量优先，非法值回落到默认值。 */
export function resolvePageLines(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(env.DOC_PAGE_LINES);
  return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : DEFAULT_PAGE_LINES;
}

/**
 * 默认每页行数。右侧详情四段（元信息 / 内容 / 指针 / findings）在 100 行时不再同屏，
 * 内容区因此单独可滚（PgUp/PgDn），剩余段落继续用外层面板滚动查看。
 */
export const PAGE_LINES = resolvePageLines();

export interface DetailLoaderOptions {
  repo: string;
  governPath: string;
  python: string;
  timeoutMs?: number;
  pageLines?: number;
}

export interface LoadedDetail {
  detail: DocDetail;
  /** 降级原因；未降级时为空。 */
  degraded?: string;
}

export interface DetailLoader {
  load(rel: string, offset: number): Promise<LoadedDetail>;
  /** 丢弃缓存（文件变更或手动重扫后调用）。 */
  invalidate(): void;
  dispose(): void;
}

/** 仓库相对 POSIX 归一化；越界（..）返回空串。 */
function normalizeInside(base: string, target: string): string {
  const raw = target.trim().replace(/\\/g, "/");
  if (!raw || raw.startsWith("/")) return "";
  const parts: string[] = [];
  for (const segment of raw.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      if (parts.length === 0) return "";
      parts.pop();
      continue;
    }
    parts.push(segment);
  }
  const joined = base ? `${base}/${parts.join("/")}` : parts.join("/");
  return joined.replace(/^\/+/, "");
}

/**
 * 指针跳转目标：优先用引擎给出的 resolved；否则按「相对当前文件目录 → 根相对」顺序在仓库里找。
 * 找不到可用文件时返回 undefined（不跳转）。
 */
export function resolveRefTarget(
  repo: string,
  currentPath: string,
  ref: DocRef,
): string | undefined {
  const candidates: string[] = [];
  if (ref.resolved) candidates.push(ref.resolved);
  const dir = currentPath.includes("/") ? currentPath.slice(0, currentPath.lastIndexOf("/")) : "";
  const relative = normalizeInside(dir, ref.target);
  if (relative) candidates.push(relative);
  const rooted = normalizeInside("", ref.target);
  if (rooted) candidates.push(rooted);
  for (const candidate of candidates) {
    if (!candidate) continue;
    if (existsSync(path.join(repo, candidate))) return candidate;
  }
  return undefined;
}

const REF_STATUSES: readonly RefStatus[] = ["ok", "qualified", "under", "dead"];

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function str(record: Record<string, unknown> | undefined, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
}

function num(record: Record<string, unknown> | undefined, key: string): number {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function nullableNum(record: Record<string, unknown> | undefined, key: string): number | null {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeRef(raw: unknown): DocRef | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  const status = str(record, "status");
  return {
    line: num(record, "line"),
    target: str(record, "target"),
    status: (REF_STATUSES as readonly string[]).includes(status)
      ? (status as RefStatus)
      : "dead",
    resolved: typeof record.resolved === "string" && record.resolved ? record.resolved : null,
  };
}

function normalizeTask(raw: unknown): DocTask | null {
  const record = asRecord(raw);
  if (!record) return null;
  return {
    title: str(record, "title"),
    status: str(record, "status"),
    owner: str(record, "owner"),
    updated: str(record, "updated"),
    goal: str(record, "goal"),
    evidence: str(record, "evidence"),
    doc: str(record, "doc"),
  };
}

/** 把引擎 `show` 的 data 段归一化成 DocDetail；字段缺失时给保守默认值。 */
export function parseShowPayload(raw: unknown): DocDetail | undefined {
  const envelope = asRecord(raw);
  if (!envelope) return undefined;
  const data = asRecord(envelope.data) ?? envelope;
  const content = Array.isArray(data.content)
    ? data.content.filter((line): line is string => typeof line === "string")
    : [];
  const tier = str(data, "tier");
  const refs = Array.isArray(data.refs)
    ? data.refs.map(normalizeRef).filter((ref): ref is DocRef => Boolean(ref))
    : [];
  const error = str(data, "error");
  return {
    path: str(data, "path"),
    exists: data.exists === true,
    tier: tier === "ai" || tier === "human" ? tier : "none",
    linesTotal: num(data, "lines_total"),
    gitLast: str(data, "git_last"),
    offset: num(data, "offset") || 1,
    next: nullableNum(data, "next"),
    content,
    refs,
    task: normalizeTask(data.task),
    error: error || undefined,
    source: "engine",
  };
}

/** 引擎不可用时的降级视图：直接读文件（带 path+mtimeMs+size 缓存），按 offset/limit 切窗口。 */
async function fsDetail(
  repo: string,
  rel: string,
  offset: number,
  limit: number,
  reason: string,
): Promise<LoadedDetail> {
  const abs = path.join(repo, rel);
  const text = await readTextCached(abs);
  const exists = text !== undefined;
  let lines: string[] = [];
  if (text !== undefined) {
    lines = text.split(/\r?\n/);
    if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  }
  const start = Math.max(1, offset);
  const content = exists ? lines.slice(start - 1, start - 1 + limit) : [];
  const end = start - 1 + content.length;
  return {
    degraded: reason,
    detail: {
      path: rel,
      exists,
      tier: "none",
      linesTotal: exists ? lines.length : 0,
      gitLast: "",
      offset: start,
      next: exists && end < lines.length ? end + 1 : null,
      content,
      refs: [],
      task: null,
      source: "fs",
    },
  };
}

/** show 结果缓存 TTL：超过后重跑一次引擎（引擎自身另有持久缓存）。 */
const SHOW_CACHE_TTL_MS = 20_000;
/** show 结果缓存条目上限。 */
const SHOW_CACHE_MAX = 64;

interface ShowCacheEntry {
  value: LoadedDetail;
  /** 文件指纹（path+mtimeMs+size）；文件不存在时为 `${abs}|missing`。 */
  fingerprint: string;
  at: number;
}

/**
 * 详情加载器：同一路径+偏移在 TTL 内只跑一次引擎；文件 mtime/size 变化立即失效。
 */
export function createDetailLoader(options: DetailLoaderOptions): DetailLoader {
  const pageLines = options.pageLines ?? PAGE_LINES;
  const timeoutMs = options.timeoutMs ?? 20_000;
  const cache = new Map<string, ShowCacheEntry>();
  let disposed = false;

  const cacheKey = (rel: string, offset: number): string => `${rel}@${offset}`;

  /** 文件指纹：mtimeMs+size 变化即视为内容变化；文件不存在时固定为 missing。 */
  const fingerprintOf = async (rel: string): Promise<string> => {
    const abs = path.join(options.repo, rel);
    const stamp = await statStamp(abs);
    return stamp ? stampKey(abs, stamp) : `${abs}|missing`;
  };

  const remember = (key: string, fingerprint: string, value: LoadedDetail): LoadedDetail => {
    if (cache.size >= SHOW_CACHE_MAX && !cache.has(key)) {
      const oldest = cache.keys().next();
      if (!oldest.done) cache.delete(oldest.value);
    }
    cache.set(key, { value, fingerprint, at: Date.now() });
    return value;
  };

  const runEngine = (
    rel: string,
    offset: number,
  ): Promise<{ value?: LoadedDetail; reason?: string }> =>
    new Promise((resolve) => {
      let stdout = "";
      let stderr = "";
      let settled = false;
      let proc: ReturnType<typeof spawn>;
      try {
        proc = spawn(
          options.python,
          [
            options.governPath,
            "--repo",
            options.repo,
            "--json",
            "show",
            rel,
            "--offset",
            String(offset),
            "--limit",
            String(pageLines),
          ],
          { cwd: options.repo, env: process.env, stdio: ["ignore", "pipe", "pipe"] },
        );
      } catch (error) {
        resolve({ reason: `无法启动解释器 ${options.python}：${(error as Error).message}` });
        return;
      }
      const finish = (value: { value?: LoadedDetail; reason?: string }): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      };
      const timer = setTimeout(() => {
        try {
          proc.kill();
        } catch {
          // ignore
        }
        finish({ reason: `检查器 show 超时（${timeoutMs}ms），已终止子进程` });
      }, timeoutMs);
      proc.stdout?.on("data", (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });
      proc.stderr?.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });
      proc.on("error", (error: Error) =>
        finish({ reason: `无法启动解释器 ${options.python}：${error.message}` }),
      );
      proc.on("close", (code) => {
        if (disposed) {
          finish({ reason: "插件已卸载" });
          return;
        }
        try {
          const parsed = parseShowPayload(JSON.parse(stdout || "{}"));
          if (parsed) {
            // 引擎对越界/敏感路径返回退出码 2，但 stdout 里仍有可用 data。
            if (parsed.error) {
              finish({ value: { degraded: `引擎拒绝读取：${parsed.error}`, detail: parsed } });
              return;
            }
            finish({ value: { detail: parsed } });
            return;
          }
        } catch {
          // 落到下面的失败分支。
        }
        const tail = (stderr || stdout).trim().split(/\r?\n/).filter(Boolean).slice(-2).join(" / ");
        finish({ reason: `检查器退出码 ${code ?? "unknown"}${tail ? `：${tail}` : ""}` });
      });
    });

  const load = async (rel: string, offset: number): Promise<LoadedDetail> => {
    if (disposed) {
      return await fsDetail(options.repo, rel, offset, pageLines, "插件已卸载");
    }
    if (!rel) {
      return await fsDetail(options.repo, rel, offset, pageLines, "未选中文件");
    }
    const key = cacheKey(rel, offset);
    const fingerprint = await fingerprintOf(rel);
    const cached = cache.get(key);
    if (
      cached &&
      cached.fingerprint === fingerprint &&
      Date.now() - cached.at <= SHOW_CACHE_TTL_MS
    ) {
      return cached.value;
    }
    if (!existsSync(options.governPath)) {
      return remember(
        key,
        fingerprint,
        await fsDetail(
          options.repo,
          rel,
          offset,
          pageLines,
          `检查器不可用：未找到 ${options.governPath}`,
        ),
      );
    }
    const engine = await runEngine(rel, offset);
    if (engine.value) return remember(key, fingerprint, engine.value);
    return remember(
      key,
      fingerprint,
      await fsDetail(
        options.repo,
        rel,
        offset,
        pageLines,
        `检查器不可用：${engine.reason ?? "引擎 show 未返回可解析结果"}；已改用文件系统直读`,
      ),
    );
  };

  return {
    load,
    invalidate: () => cache.clear(),
    dispose: () => {
      disposed = true;
      cache.clear();
    },
  };
}
