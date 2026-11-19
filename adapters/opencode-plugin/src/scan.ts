/**
 * 调用 governance-kit 引擎的 `scan --json`，并把统一信封归一化成面板数据。
 *
 * 上游：index.tsx 在文件变更后调用 scanner.scan()。
 * 下游：panel.tsx 消费 findings / summary / checker 状态。
 * 约束：引擎或解释器缺失时只返回 unavailable，不抛错——树渲染与检查器解耦。
 */
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import type { CheckerState, Finding, ScanSummary, Severity } from "./types.ts";
import { emptySummary } from "./types.ts";

export interface ScanOutcome {
  findings: Finding[];
  summary: ScanSummary;
  checker: CheckerState;
  at?: string;
}

export interface ScannerOptions {
  repo: string;
  governPath: string;
  python: string;
  timeoutMs?: number;
}

export interface Scanner {
  scan: () => Promise<ScanOutcome>;
  dispose: () => void;
  readonly governPath: string;
}

const SEVERITY_ORDER: Record<Severity, number> = { error: 0, warning: 1, info: 2 };

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function firstString(record: Record<string, unknown> | undefined, keys: string[]): string | undefined {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function pickFindings(raw: unknown): unknown[] {
  if (Array.isArray(raw)) return raw;
  const root = asRecord(raw);
  if (!root) return [];
  const candidates = [
    root.findings,
    root.results,
    root.items,
    asRecord(root.data)?.findings,
    asRecord(root.result)?.findings,
    asRecord(root.payload)?.findings,
    asRecord(root.scan)?.findings,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) return candidate;
  }
  return [];
}

function normalizeSeverity(value: unknown): Severity {
  const raw = typeof value === "string" ? value.toLowerCase() : "warning";
  if (raw === "error" || raw === "critical" || raw === "fatal") return "error";
  if (raw === "info" || raw === "note" || raw === "hint") return "info";
  return "warning";
}

function normalizeFinding(raw: unknown): Finding | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  const file =
    firstString(record, ["file", "path", "artifact", "uri"]) ?? "";
  const lineValue = record.line ?? record.startLine ?? record.start_line ?? record.lineno ?? 0;
  const line = typeof lineValue === "number" ? lineValue : Number.parseInt(String(lineValue), 10);
  const rule = firstString(record, ["rule", "code", "id", "kind"]) ?? "UNKNOWN";
  const message =
    firstString(record, ["message", "text", "title", "detail"]) ?? "（检查器未提供说明）";
  return {
    rule,
    severity: normalizeSeverity(record.severity ?? record.level),
    file,
    line: Number.isFinite(line) ? line : 0,
    message,
    fix: firstString(record, ["fix", "hint", "suggestion"]),
  };
}

function summarize(findings: Finding[]): ScanSummary {
  const summary = emptySummary();
  for (const finding of findings) summary[finding.severity] += 1;
  summary.total = findings.length;
  return summary;
}

function validSummary(value: unknown): ScanSummary | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const numbers = ["error", "warning", "info", "total"].map((key) => record[key]);
  if (!numbers.every((item) => typeof item === "number" && Number.isFinite(item))) return undefined;
  return {
    error: record.error as number,
    warning: record.warning as number,
    info: record.info as number,
    total: record.total as number,
  };
}

/** 统一信封兼容：顶层 findings / data.findings / result.findings，或裸数组。 */
export function parseScanEnvelope(raw: unknown): ScanOutcome {
  const root = asRecord(raw);
  const findings = pickFindings(raw)
    .map(normalizeFinding)
    .filter((finding): finding is Finding => Boolean(finding))
    .sort(
      (a, b) =>
        SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] ||
        a.file.localeCompare(b.file) ||
        a.line - b.line ||
        a.rule.localeCompare(b.rule),
    );

  const explicitError = firstString(root, ["error"]);
  const ok = root?.ok !== false && root?.success !== false && explicitError === undefined;
  const errorText = explicitError ?? firstString(root, ["message", "detail"]);
  const at =
    firstString(root, [
      "generatedAt",
      "generated_at",
      "scannedAt",
      "scanned_at",
      "timestamp",
      "time",
    ]) ??
    firstString(asRecord(root?.scan), ["finishedAt", "finished_at", "generatedAt", "generated_at"]) ??
    new Date().toISOString();

  return {
    findings,
    summary: validSummary(root?.summary) ?? summarize(findings),
    checker: ok
      ? { status: "ok" }
      : { status: "degraded", message: errorText ?? "检查器返回 ok=false" },
    at,
  };
}

function unavailable(message: string): ScanOutcome {
  return {
    findings: [],
    summary: emptySummary(),
    checker: { status: "unavailable", message },
  };
}

function tail(text: string, limit = 300): string {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  const last = lines.slice(-3).join(" / ");
  return last.length > limit ? `${last.slice(0, limit)}…` : last;
}

/**
 * 串行扫描器：同一时刻最多一个子进程；dispose 会杀掉在跑的子进程并让 scan()
 * 以 unavailable 结束，调用方不需要自己管理 ChildProcess。
 */
export function createScanner(options: ScannerOptions): Scanner {
  const timeoutMs = options.timeoutMs ?? 30_000;
  let disposed = false;
  let active:
    | {
        proc: ChildProcess;
        timer: ReturnType<typeof setTimeout>;
        finish: (outcome: ScanOutcome) => void;
      }
    | undefined;

  const dispose = (): void => {
    disposed = true;
    if (!active) return;
    const current = active;
    active = undefined;
    clearTimeout(current.timer);
    try {
      current.proc.kill();
    } catch {
      // 进程可能已退出；finish 仍会解除等待。
    }
    current.finish(unavailable("插件已卸载，扫描已停止"));
  };

  const scan = async (): Promise<ScanOutcome> => {
    if (disposed) return unavailable("插件已卸载");
    if (!existsSync(options.governPath)) {
      return unavailable(
        `未找到检查器：${options.governPath}（engine/govern 尚未就绪）`,
      );
    }
    if (active) {
      const previous = active;
      active = undefined;
      clearTimeout(previous.timer);
      try {
        previous.proc.kill();
      } catch {
        // ignore
      }
      previous.finish(unavailable("扫描被新的变更取代"));
    }

    return await new Promise<ScanOutcome>((resolve) => {
      let settled = false;
      let stdout = "";
      let stderr = "";
      let proc: ChildProcess;
      try {
        proc = spawn(
          options.python,
          [options.governPath, "--repo", options.repo, "--json", "scan"],
          {
            cwd: options.repo,
            env: process.env,
            stdio: ["ignore", "pipe", "pipe"],
          },
        );
      } catch (error) {
        resolve(
          unavailable(
            `无法启动解释器 ${options.python}：${(error as Error).message}；可用 DOC_HEALTH_PYTHON 指定`,
          ),
        );
        return;
      }

      const finish = (outcome: ScanOutcome): void => {
        if (settled) return;
        settled = true;
        if (active?.proc === proc) active = undefined;
        clearTimeout(timer);
        resolve(outcome);
      };

      const timer = setTimeout(() => {
        try {
          proc.kill();
        } catch {
          // ignore
        }
        finish(unavailable(`扫描超时（${timeoutMs}ms），已终止子进程`));
      }, timeoutMs);

      active = { proc, timer, finish };

      proc.stdout?.on("data", (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });
      proc.stderr?.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });
      proc.on("error", (error: Error) => {
        finish(
          unavailable(
            `无法启动解释器 ${options.python}：${error.message}；可用 DOC_HEALTH_PYTHON 指定`,
          ),
        );
      });
      proc.on("close", (code) => {
        if (disposed) {
          finish(unavailable("插件已卸载，扫描已停止"));
          return;
        }
        if (code !== 0) {
          const detail = tail(stderr || stdout);
          finish(
            unavailable(
              `检查器退出码 ${code ?? "unknown"}${detail ? `：${detail}` : ""}`,
            ),
          );
          return;
        }
        try {
          finish(parseScanEnvelope(JSON.parse(stdout || "{}")));
        } catch (error) {
          finish(
            unavailable(
              `检查器输出不是合法 JSON：${(error as Error).message}；stdout 前 200 字：${stdout.slice(0, 200)}`,
            ),
          );
        }
      });
    });
  };

  return { scan, dispose, governPath: options.governPath };
}