/**
 * 进度板数据源：调引擎 `--json board` 拿任务与 checklist，构造成树行；打钩走引擎 `check`。
 *
 * 上游：session.ts 在树刷新后拉一次进度板，并在用户打钩后重拉。
 * 下游：panel.tsx 消费 BoardTask/TreeRow；副作用只有子进程调用。
 * 约束：引擎缺失、版本过旧（无 checklist）、退出码非 0 一律降级，不抛错。
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import type {
  BoardCheckItem,
  BoardStatusCount,
  BoardTask,
  TabId,
  TreeRow,
} from "./types.ts";
import { BOARD_STATUS_ORDER } from "./types.ts";

/** 引擎进度板的一次拉取结果。 */
export interface EngineBoard {
  tasks: BoardTask[];
  /** 各状态任务数（按看板顺序排好；直接由解析出的任务统计）。 */
  statuses: BoardStatusCount[];
  /** 引擎是否认识 checklist 字段（旧引擎没有，打钩不可用）。 */
  hasChecklist: boolean;
}

export interface BoardLoaderOptions {
  repo: string;
  governPath: string;
  python: string;
  timeoutMs?: number;
}

export interface BoardFetchOk {
  ok: true;
  board: EngineBoard;
}

export interface BoardFetchFail {
  ok: false;
  reason: string;
}

export interface BoardCheckOk {
  ok: true;
  /** 引擎回传的新勾选态；引擎没回传时为 undefined。 */
  checked?: boolean;
}

export interface BoardCheckFail {
  ok: false;
  reason: string;
}

export interface BoardLoader {
  fetch: () => Promise<BoardFetchOk | BoardFetchFail>;
  /** 打钩：引擎 `check <rel> <index> --toggle`，成功后由调用方重拉进度板。 */
  check: (rel: string, index: number) => Promise<BoardCheckOk | BoardCheckFail>;
  dispose: () => void;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function str(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function num(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function normalizeChecklist(raw: unknown): BoardCheckItem[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const items: BoardCheckItem[] = [];
  raw.forEach((entry, position) => {
    const record = asRecord(entry);
    if (!record) return;
    const text = str(record, "text").trim();
    if (!text) return;
    const rawIndex = record.index;
    items.push({
      // 引擎序号从 0 起，原样回传（0 不能当缺省值丢掉）。
      index: typeof rawIndex === "number" && Number.isFinite(rawIndex) ? rawIndex : position,
      checked: record.checked === true,
      text,
      line: num(record, "line"),
    });
  });
  return items;
}

function normalizeTask(raw: unknown): { task: BoardTask; hasChecklist: boolean } | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  const file = str(record, "file").trim();
  if (!file) return undefined;
  const checklist = normalizeChecklist(record.checklist);
  return {
    hasChecklist: checklist !== undefined,
    task: {
      file,
      title: str(record, "title").trim() || file,
      status: str(record, "status").trim(),
      owner: str(record, "owner").trim(),
      updated: str(record, "updated").trim(),
      goal: str(record, "goal").trim(),
      evidence: str(record, "evidence").trim(),
      doc: str(record, "doc").trim(),
      checklist: checklist ?? [],
      mode: str(record, "mode").trim() || "task-file",
      line: num(record, "line"),
    },
  };
}

/** 状态排序位次：看板顺序在前，未登记状态沉底。 */
function statusRank(status: string): number {
  const index = BOARD_STATUS_ORDER.indexOf(status);
  return index < 0 ? BOARD_STATUS_ORDER.length : index;
}

/**
 * 归一化 `--json board` 的信封：data.groups[].tasks[] 或裸 tasks 数组都认。
 * 状态计数直接由解析出的任务统计，避免引擎没给 count 时侧边栏显示 0。
 */
export function parseBoardEnvelope(raw: unknown): EngineBoard {
  const envelope = asRecord(raw);
  const data = asRecord(envelope?.data) ?? envelope;
  const groups = Array.isArray(data?.groups) ? data.groups : [];
  const tasks: BoardTask[] = [];
  let hasChecklist = false;

  const pushTask = (entry: unknown, fallbackStatus: string): void => {
    const normalized = normalizeTask(entry);
    if (!normalized) return;
    if (!normalized.task.status) normalized.task.status = fallbackStatus;
    tasks.push(normalized.task);
    if (normalized.hasChecklist) hasChecklist = true;
  };

  for (const group of groups) {
    const record = asRecord(group);
    if (!record) continue;
    const status = str(record, "status").trim() || "未标注";
    const items = Array.isArray(record.tasks) ? record.tasks : [];
    for (const item of items) pushTask(item, status);
  }
  if (groups.length === 0 && Array.isArray(data?.tasks)) {
    for (const item of data.tasks as unknown[]) pushTask(item, "");
  }

  return { tasks, statuses: countByStatus(tasks), hasChecklist };
}

/** 按状态统计任务数，看板顺序在前、未登记状态沉底。 */
export function countByStatus(tasks: BoardTask[]): BoardStatusCount[] {
  const counts = new Map<string, number>();
  for (const task of tasks) {
    const status = task.status || "未标注";
    counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([status, count]) => ({ status, count }))
    .sort((a, b) => statusRank(a.status) - statusRank(b.status) || a.status.localeCompare(b.status));
}

/**
 * 进度板降级提示：引擎不可用（failure）或引擎版本过旧（没有 checklist 字段）。
 * 返回 undefined 表示无需提示。
 */
export function boardDegradedMessage(input: {
  failure?: BoardFetchFail;
  hasChecklist?: boolean;
  taskCount?: number;
}): string | undefined {
  if (input.failure) return `引擎待升级：${input.failure.reason}`;
  if (input.taskCount && !input.hasChecklist) {
    return "引擎待升级：board 未返回 checklist，打钩不可用";
  }
  return undefined;
}

/** 任务行 id：legacy 条目同属一个文件，用行号区分。 */
function taskRowId(task: BoardTask): string {
  return task.mode === "legacy"
    ? `board::task::${task.file}#L${task.line ?? 0}`
    : `board::task::${task.file}`;
}

function checkRowId(task: BoardTask, index: number): string {
  return `board::check::${task.file}#${index}`;
}

/** 任务行文案：`owner · updated · title`；清单全勾时前缀 ✔。 */
function taskLabel(task: BoardTask): string {
  const owner = task.owner || "未指派";
  const updated = task.updated || "未更新";
  const done = task.checklist.filter((item) => item.checked).length;
  const allDone = task.checklist.length > 0 && done === task.checklist.length;
  return `${allDone ? "✔ " : ""}${owner} · ${updated} · ${task.title}`;
}

/**
 * 把引擎进度板构造成树行：状态组（可折叠）→ 任务行（可折叠，默认收起）→ checklist 项。
 */
export function buildBoardRows(board: EngineBoard, tab: TabId = "board"): TreeRow[] {
  const rows: TreeRow[] = [];
  const groups = new Map<string, BoardTask[]>();
  for (const task of board.tasks) {
    const status = task.status || "未标注";
    const list = groups.get(status);
    if (list) list.push(task);
    else groups.set(status, [task]);
  }
  const statuses = [...groups.keys()].sort(
    (a, b) => statusRank(a) - statusRank(b) || a.localeCompare(b),
  );
  for (const status of statuses) {
    const tasks = groups.get(status) ?? [];
    if (tasks.length === 0) continue;
    rows.push({
      id: `${tab}::group::status:${status}`,
      tab,
      kind: "group",
      path: "",
      label: `${status}（${tasks.length}）`,
      depth: 0,
      collapsible: true,
      defaultOpen: true,
      scopePaths: tasks.map((task) => task.file),
    });
    for (const task of tasks) {
      const done = task.checklist.filter((item) => item.checked).length;
      rows.push({
        id: taskRowId(task),
        tab,
        kind: "task",
        path: task.file,
        label: taskLabel(task),
        depth: 1,
        collapsible: true,
        defaultOpen: false,
        scopePaths: [task.file],
        meta: task.checklist.length > 0 ? `${done}/${task.checklist.length}` : undefined,
      });
      for (const item of task.checklist) {
        rows.push({
          id: checkRowId(task, item.index),
          tab,
          kind: "check",
          path: task.file,
          label: `${item.checked ? "☑" : "☐"} ${item.text}`,
          depth: 2,
          checked: item.checked,
          checkIndex: item.index,
          line: item.line,
        });
      }
    }
  }
  return rows;
}

/**
 * 打钩乐观更新：立即翻转清单项的 ☐/☑ 与 checked，并按同文件全部清单项
 * 重算任务行的 done/total 与 ✔ 前缀；引擎写入失败时用原值再调一次即回滚。
 */
export function applyOptimisticCheck(
  rows: TreeRow[],
  rel: string,
  index: number,
  checked: boolean,
): TreeRow[] {
  const targetId = `board::check::${rel}#${index}`;
  if (!rows.some((row) => row.id === targetId)) return rows;
  const next = rows.map((row) =>
    row.id === targetId
      ? { ...row, checked, label: row.label.replace(/^[☑☐]/, checked ? "☑" : "☐") }
      : row,
  );
  const checks = next.filter((row) => row.kind === "check" && row.path === rel);
  const total = checks.length;
  const done = checks.filter((row) => row.checked).length;
  const allDone = total > 0 && done === total;
  return next.map((row) => {
    if (row.kind !== "task" || row.path !== rel) return row;
    const label = row.label.replace(/^✔ /, "");
    return {
      ...row,
      meta: total > 0 ? `${done}/${total}` : undefined,
      label: allDone ? `✔ ${label}` : label,
    };
  });
}

/** 引擎不可用/版本过旧时的提示文案已由 boardDegradedMessage 统一给出。 */

function tail(text: string, limit = 240): string {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  const last = lines.slice(-2).join(" / ");
  return last.length > limit ? `${last.slice(0, limit)}…` : last;
}

/** argparse 的未知子命令报错：提示升级引擎而不是让用户看 Python 栈。 */
function isUnknownCommand(text: string): boolean {
  return /invalid choice|unrecognized arguments|no such option|unknown command/i.test(text);
}

/**
 * 进度板客户端：拉 `--json board`、跑 `check --toggle`。
 * 同一时刻只允许一个子进程，dispose 会杀掉在跑的进程。
 */
export function createBoardLoader(options: BoardLoaderOptions): BoardLoader {
  const timeoutMs = options.timeoutMs ?? 20_000;
  let disposed = false;
  let active: { proc: ReturnType<typeof spawn>; kill: () => void } | undefined;

  const run = (
    args: string[],
  ): Promise<{ code: number | null; stdout: string; stderr: string; spawnError?: string }> =>
    new Promise((resolve) => {
      if (disposed) {
        resolve({ code: null, stdout: "", stderr: "", spawnError: "插件已卸载" });
        return;
      }
      let stdout = "";
      let stderr = "";
      let settled = false;
      let proc: ReturnType<typeof spawn>;
      try {
        proc = spawn(options.python, [options.governPath, ...args], {
          cwd: options.repo,
          env: process.env,
          stdio: ["ignore", "pipe", "pipe"],
        });
      } catch (error) {
        resolve({
          code: null,
          stdout: "",
          stderr: "",
          spawnError: `无法启动解释器 ${options.python}：${(error as Error).message}`,
        });
        return;
      }
      const finish = (value: { code: number | null; stdout: string; stderr: string }): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (active?.proc === proc) active = undefined;
        resolve(value);
      };
      const timer = setTimeout(() => {
        try {
          proc.kill();
        } catch {
          // ignore
        }
        finish({ code: null, stdout, stderr: `${stderr}\n超时 ${timeoutMs}ms` });
      }, timeoutMs);
      active = { proc, kill: () => proc.kill() };
      proc.stdout?.on("data", (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });
      proc.stderr?.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });
      proc.on("error", (error: Error) =>
        finish({ code: null, stdout, stderr: `无法启动解释器 ${options.python}：${error.message}` }),
      );
      proc.on("close", (code) => finish({ code, stdout, stderr }));
    });

  const fetch = async (): Promise<BoardFetchOk | BoardFetchFail> => {
    if (!existsSync(options.governPath)) {
      return { ok: false, reason: `未找到检查器：${options.governPath}` };
    }
    const result = await run(["--repo", options.repo, "--json", "board"]);
    if (result.spawnError) return { ok: false, reason: result.spawnError };
    if (result.code !== 0) {
      const detail = tail(result.stderr || result.stdout);
      return {
        ok: false,
        reason: isUnknownCommand(result.stderr)
          ? "board 子命令不可用"
          : `退出码 ${result.code ?? "unknown"}${detail ? `：${detail}` : ""}`,
      };
    }
    try {
      const parsed = parseBoardEnvelope(JSON.parse(result.stdout || "{}"));
      return { ok: true, board: parsed };
    } catch (error) {
      return { ok: false, reason: `输出不是合法 JSON：${(error as Error).message}` };
    }
  };

  const check = async (rel: string, index: number): Promise<BoardCheckOk | BoardCheckFail> => {
    if (!existsSync(options.governPath)) {
      return { ok: false, reason: `未找到检查器：${options.governPath}` };
    }
    const result = await run(["--repo", options.repo, "--json", "check", rel, String(index), "--toggle"]);
    if (result.spawnError) return { ok: false, reason: result.spawnError };
    if (result.code !== 0) {
      const detail = tail(result.stderr || result.stdout);
      if (isUnknownCommand(result.stderr)) {
        return { ok: false, reason: "引擎待升级：当前 engine/govern 不支持 check 子命令" };
      }
      return { ok: false, reason: `退出码 ${result.code ?? "unknown"}${detail ? `：${detail}` : ""}` };
    }
    let checked: boolean | undefined;
    try {
      const parsed = asRecord(JSON.parse(result.stdout || "null"));
      const data = asRecord(parsed?.data) ?? parsed;
      if (data && typeof data.checked === "boolean") checked = data.checked;
    } catch {
      // 引擎没输出 JSON 也不影响：文件已由引擎改写，重拉即可。
    }
    return { ok: true, checked };
  };

  return {
    fetch,
    check,
    dispose: () => {
      disposed = true;
      if (!active) return;
      const current = active;
      active = undefined;
      try {
        current.kill();
      } catch {
        // ignore
      }
    },
  };
}
