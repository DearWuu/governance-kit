/**
 * 近期功能实现文档数据源：调用引擎 `logic list --json` 取回各业务域的近期功能实现条目。
 *
 * 上游：session.ts 在进入「近期功能实现」tab 时拉一次近期功能实现文档。
 * 下游：引擎 `logic list`（engine/governkit/cli.py）；数据结构见 ./logicrows.ts 的 LogicDomain。
 * 约束：引擎缺失、引擎尚未提供 logic 子命令、非 0 退出、超时、JSON 非法一律降级为 ok:false，不抛错。
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import type { LogicDomain, LogicEntry, LogicPointer, LogicSection, LogicStatus } from "./logicrows.ts";

export interface LogicLoaderOptions {
  repo: string;
  governPath: string;
  python: string;
  timeoutMs?: number;
}

/** 一次近期功能实现文档拉取的结果：成功给领域列表与近期功能实现文档目录，失败给人读原因。 */
export type LogicFetchResult =
  | { ok: true; domains: LogicDomain[]; dir: string }
  | { ok: false; error: string };

export interface LogicLoader {
  fetch: () => Promise<LogicFetchResult>;
}

type Rec = Record<string, unknown>;
type RunResult = { code: number | null; stdout: string; stderr: string; spawnError?: string; timedOut?: boolean };

const asRecord = (value: unknown): Rec | undefined =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Rec) : undefined;
const str = (record: Rec, key: string): string =>
  typeof record[key] === "string" ? (record[key] as string) : "";
/** argparse 未知子命令：旧引擎没有 logic 命令时给专门提示，而不是甩 Python 栈。 */
const isUnknownCommand = (text: string): boolean =>
  /invalid choice|unknown command|unknown subcommand|unrecognized arguments|no such option|未知命令/i.test(text);
/** 引擎没给或给了未登记的状态时都按 draft 处理，避免面板拿到脏状态。 */
const LOGIC_STATUSES: readonly string[] = ["fresh", "stale", "broken", "draft"];
const normalizeStatus = (raw: string): LogicStatus =>
  LOGIC_STATUSES.includes(raw) ? (raw as LogicStatus) : "draft";

/** 归一化一个业务域：条目与指针的字段兜底，引擎少给字段也不抛错。 */
const normalizeDomain = (raw: unknown): LogicDomain | undefined => {
  const record = asRecord(raw);
  if (!record) return undefined;
  /** 小节归一化：非数组、非对象、非字符串字段一律兜底成空串。 */
  const normalizeSections = (rawSections: unknown): LogicSection[] =>
    (Array.isArray(rawSections) ? rawSections : []).map((item) => {
      const section = asRecord(item) ?? {};
      return { title: str(section, "title"), body: str(section, "body") };
    });
  const entries: LogicEntry[] = (Array.isArray(record.entries) ? record.entries : []).map((item) => {
    const entry = asRecord(item) ?? {};
    const pointers: LogicPointer[] = (
      Array.isArray(entry.pointers) ? entry.pointers : []
    ).map((rawPointer) => {
      const pointer = asRecord(rawPointer) ?? {};
      return {
        file: str(pointer, "file"),
        symbol: str(pointer, "symbol"),
        line: Number(pointer.line ?? 0) || 0,
        end: Number(pointer.end ?? 0) || 0,
        role: str(pointer, "role"),
      };
    });
    return {
      id: str(entry, "id"),
      title: str(entry, "title"),
      summary: str(entry, "summary"),
      narrative: str(entry, "narrative"),
      // 机制小节：引擎可缺省或给非数组，按空数组处理。
      sections: normalizeSections(entry.sections),
      flow: Array.isArray(entry.flow) ? entry.flow.filter((i): i is string => typeof i === "string") : [],
      pointers,
      status: normalizeStatus(str(entry, "status")),
      status_detail: str(entry, "status_detail"),
      // 分组路径与条目类型：引擎始终返回，空串表示未分组。
      group: str(entry, "group"),
      kind: str(entry, "kind"),
    };
  });
  const domain = str(record, "domain");
  return {
    domain,
    title: str(record, "title") || domain,
    updated: str(record, "updated"),
    file: str(record, "file"),
    // 域概览与横切机制：引擎可缺省或给非字符串/非数组，按空值处理。
    overview: str(record, "overview"),
    crosscut: normalizeSections(record.crosscut),
    entries,
  };
};

/** 近期功能实现文档客户端：拉 `--json logic list`，只取数不做 UI。 */
export function createLogicLoader(options: LogicLoaderOptions): LogicLoader {
  const timeoutMs = options.timeoutMs ?? 20_000;
  let disposed = false;

  // 与 board.ts:293-346 的 run 同构（board.ts 未导出可复用的运行器，此处只复制模式，不改其实现）。
  const run = (args: string[]): Promise<RunResult> =>
    new Promise((resolve) => {
      if (disposed) {
        resolve({ code: null, stdout: "", stderr: "", spawnError: "插件已卸载" });
        return;
      }
      let stdout = "";
      let stderr = "";
      let settled = false;
      let proc: ReturnType<typeof spawn>;
      const finish = (value: RunResult): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      };
      try {
        proc = spawn(options.python, [options.governPath, ...args], {
          cwd: options.repo,
          env: process.env,
          stdio: ["ignore", "pipe", "pipe"],
        });
      } catch (error) {
        finish({ code: null, stdout, stderr, spawnError: `无法启动解释器 ${options.python}：${(error as Error).message}` });
        return;
      }
      const timer = setTimeout(() => {
        try {
          proc.kill();
        } catch {
          // 进程可能已退出；finish 仍会解除等待。
        }
        finish({ code: null, stdout, stderr: `${stderr}\n超时 ${timeoutMs}ms`, timedOut: true });
      }, timeoutMs);
      proc.stdout?.on("data", (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });
      proc.stderr?.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });
      proc.on("error", (error: Error) =>
        finish({ code: null, stdout, stderr, spawnError: `无法启动解释器 ${options.python}：${error.message}` }),
      );
      proc.on("close", (code) => finish({ code, stdout, stderr }));
    });

  const fetch = async (): Promise<LogicFetchResult> => {
    if (!existsSync(options.governPath)) {
      return { ok: false, error: `未找到检查器：${options.governPath}` };
    }
    const result = await run(["--repo", options.repo, "--json", "logic", "list"]);
    if (result.spawnError) return { ok: false, error: result.spawnError };
    if (result.timedOut) return { ok: false, error: `引擎超时（${timeoutMs}ms），已终止子进程` };
    if (result.code !== 0) {
      if (isUnknownCommand(result.stderr)) return { ok: false, error: "引擎尚未提供 logic 命令" };
      const detail = (result.stderr || result.stdout).trim().split(/\r?\n/).filter(Boolean).slice(-2).join(" / ");
      return { ok: false, error: `退出码 ${result.code ?? "unknown"}${detail ? `：${detail}` : ""}` };
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(result.stdout || "{}");
    } catch (error) {
      if (isUnknownCommand(result.stdout || result.stderr)) {
        return { ok: false, error: "引擎尚未提供 logic 命令" };
      }
      return { ok: false, error: `输出不是合法 JSON：${(error as Error).message}` };
    }
    const envelope = asRecord(parsed);
    const failed = str(envelope ?? {}, "error") || (envelope?.ok === false ? "引擎返回失败" : "");
    if (failed) return { ok: false, error: failed };
    const data = asRecord(envelope?.data);
    if (!data) return { ok: false, error: "引擎输出缺少 data 段" };
    const domains = Array.isArray(data.domains)
      ? data.domains.map(normalizeDomain).filter((item): item is LogicDomain => item !== undefined)
      : [];
    return { ok: true, domains, dir: str(data, "dir") };
  };

  return { fetch };
}
