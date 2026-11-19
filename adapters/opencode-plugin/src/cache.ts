/**
 * 插件前端内存缓存：文件内容按 path+mtimeMs+size 缓存，派生结果按 TTL 缓存。
 *
 * 上游：repo.ts（文件索引/配置读取）、tree.ts（buildSnapshot）、detail.ts（引擎 show）。
 * 下游：无（纯内存，供上述模块读取）。
 * 约束：只缓存可判定新鲜度的内容；文件变更（watcher）或按 r 重扫时整体失效。
 *      这里不替代引擎的持久缓存，只减少重复 fs 调用与子进程调用。
 */
import * as fs from "node:fs/promises";

/** 文件新鲜度指纹：mtimeMs + size。 */
export interface FileStamp {
  mtimeMs: number;
  size: number;
}

/** 缓存键：path + mtimeMs + size。 */
export function stampKey(absPath: string, stamp: FileStamp): string {
  return `${absPath}|${stamp.mtimeMs}|${stamp.size}`;
}

/** 读取文件指纹；文件不存在或不可读时返回 undefined。 */
export async function statStamp(absPath: string): Promise<FileStamp | undefined> {
  try {
    const stat = await fs.stat(absPath);
    return { mtimeMs: stat.mtimeMs, size: stat.size };
  } catch {
    return undefined;
  }
}

/** 单条缓存内容上限：超过这个体积不入缓存，避免大文件把内存撑爆。 */
const MAX_CACHED_TEXT = 4 * 1024 * 1024;
/** 文件内容缓存条目上限。 */
const MAX_TEXT_ENTRIES = 256;

/** 文件内容缓存：键为 path+mtimeMs+size。 */
const textCache = new Map<string, string>();
/** absPath → 当前有效键；同一路径换 mtime 后用来清掉旧条目。 */
const textIndex = new Map<string, string>();

/**
 * 读文本并缓存：命中条件是 mtimeMs+size 未变；limit 为返回的最大字符数。
 * 文件不可读时返回 undefined（调用方自行降级）。
 */
export async function readTextCached(
  absPath: string,
  limit = Number.POSITIVE_INFINITY,
): Promise<string | undefined> {
  const stamp = await statStamp(absPath);
  if (!stamp) return undefined;
  const key = stampKey(absPath, stamp);
  const hit = textCache.get(key);
  if (hit !== undefined) return hit.length > limit ? hit.slice(0, limit) : hit;
  let text: string;
  try {
    text = (await fs.readFile(absPath)).toString("utf8");
  } catch {
    return undefined;
  }
  if (text.length <= MAX_CACHED_TEXT) {
    const previous = textIndex.get(absPath);
    if (previous && previous !== key) textCache.delete(previous);
    textIndex.set(absPath, key);
    textCache.set(key, text);
    while (textCache.size > MAX_TEXT_ENTRIES) {
      const oldest = textCache.keys().next();
      if (oldest.done) break;
      textCache.delete(oldest.value);
    }
  }
  return text.length > limit ? text.slice(0, limit) : text;
}

/** 清空文件内容缓存（watcher 变更或手动重扫时调用）。 */
export function clearTextCache(): void {
  textCache.clear();
  textIndex.clear();
}

export interface TtlCache<T> {
  get(key: string): T | undefined;
  set(key: string, value: T): void;
  clear(): void;
}

/**
 * 带 TTL 的派生结果缓存：条目超过 ttlMs 后视为过期（重新计算），
 * 写入时按插入顺序淘汰旧条目，避免长会话无限增长。
 */
export function createTtlCache<T>(options: { ttlMs: number; maxEntries?: number }): TtlCache<T> {
  const ttlMs = options.ttlMs;
  const maxEntries = options.maxEntries ?? 64;
  const store = new Map<string, { value: T; at: number }>();
  return {
    get(key: string): T | undefined {
      const hit = store.get(key);
      if (!hit) return undefined;
      if (Date.now() - hit.at > ttlMs) {
        store.delete(key);
        return undefined;
      }
      return hit.value;
    },
    set(key: string, value: T): void {
      if (store.size >= maxEntries && !store.has(key)) {
        const oldest = store.keys().next();
        if (!oldest.done) store.delete(oldest.value);
      }
      store.set(key, { value, at: Date.now() });
    },
    clear(): void {
      store.clear();
    },
  };
}
