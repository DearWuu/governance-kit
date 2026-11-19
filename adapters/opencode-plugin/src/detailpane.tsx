/**
 * 右侧详情区：元信息 / MD 内容预览 / 指针链接 / findings 四段。
 *
 * 上游：panel.tsx 传入 AppState 与 PanelRuntime。
 * 下游：只读消费，不发起 IO。
 */
import { For, Show, createMemo } from "solid-js";
import type { JSX } from "@opentui/solid";
import type { MouseEvent } from "@opentui/core";
import type {
  AppState,
  DocRef,
  DocTier,
  Finding,
  PanelRuntime,
  RefStatus,
  Severity,
  ThemeColors,
  TreeRow,
} from "./types.ts";
import { REF_STATUS_LABELS, TIER_LABELS, severityColor, severityMark } from "./types.ts";
import {
  logicStatusColor,
  logicStatusPrefix,
  type LogicDomain,
  type LogicEntry,
  type LogicRow,
  type LogicStatus,
} from "./logicrows.ts";
import { resolvePageLines } from "./detail.ts";
import {
  narrativeTextWidth,
  wrapNarrative,
  wrapNarrativeWithSections,
} from "./textwrap.ts";

export interface DetailPaneProps {
  theme: ThemeColors;
  state: () => AppState;
  runtime: () => PanelRuntime;
  /** 详情框宽度（列）：叙事按它软换行后再分页。 */
  width?: number;
  /** 指针项点击：等价 o 选中后 Enter 跳转。 */
  onJumpRef?: (ref: DocRef) => void;
  /** findings 项点击：按路径打开文件。 */
  onOpenPath?: (path: string, line?: number) => void;
}

/** 近期功能实现状态的中文标签。 */
const LOGIC_STATUS_LABELS: Record<LogicStatus, string> = {
  fresh: "已同步",
  stale: "待复核",
  broken: "指针失效",
  draft: "草稿",
};

/** 近期功能实现 tab 当前选中的行与条目（不在近期功能实现 tab 或未选中条目时为空）。 */
function selectedLogicRow(state: AppState): {
  row: TreeRow | undefined;
  entry?: LogicEntry;
  domain?: LogicDomain;
} {
  const row = state.rows.logic?.find((item) => item.id === state.selectedId);
  if (!row) return { row: undefined };
  const extended = row as unknown as Partial<LogicRow>;
  const domainKey = extended.logicDomain ?? "";
  // 域信息与是否选中条目无关：分组行/目录行也要能取到域概览与横切机制。
  const domain =
    state.logic.domains.find((item) => item.domain === domainKey) ??
    ({
      domain: domainKey,
      title: domainKey,
      updated: "",
      file: "",
      overview: "",
      crosscut: [],
      entries: [],
    } as LogicDomain);
  return { row, entry: extended.logicEntry, domain };
}

function refColor(ref: DocRef, theme: ThemeColors) {
  if (ref.status === "dead") return theme.error;
  if (ref.status === "under") return theme.warning;
  if (ref.status === "qualified") return theme.info;
  return theme.success;
}

function FindingItem(props: {
  finding: Finding;
  theme: ThemeColors;
  onOpen?: (path: string) => void;
}): JSX.Element {
  const open = (): void => {
    const file = props.finding.file;
    if (file && file !== ".") props.onOpen?.(file);
  };
  return (
    <box
      flexDirection="column"
      marginBottom={1}
      onMouseUp={(event: MouseEvent) => {
        if (event.button !== 0) return;
        open();
      }}
    >
      <text wrapMode="word" fg={severityColor(props.finding.severity, props.theme)}>
        {severityMark(props.finding.severity)} · {props.finding.rule} · {props.finding.file}
        {props.finding.line > 0 ? `:${props.finding.line}` : ""}
      </text>
      <text wrapMode="word" fg={props.theme.text}>
        {props.finding.message}
      </text>
      <Show when={props.finding.fix}>
        <text wrapMode="word" fg={props.theme.textMuted}>
          修复：{props.finding.fix}
        </text>
      </Show>
    </box>
  );
}

function FindingList(props: {
  findings: Finding[];
  theme: ThemeColors;
  empty?: string;
  onOpen?: (path: string) => void;
}): JSX.Element {
  const shown = () => props.findings.slice(0, 50);
  return (
    <box flexDirection="column">
      <Show
        when={props.findings.length > 0}
        fallback={<text fg={props.theme.success}>{props.empty ?? "无告警"}</text>}
      >
        <For each={shown()}>
          {(finding) => (
            <FindingItem finding={finding} theme={props.theme} onOpen={props.onOpen} />
          )}
        </For>
        <Show when={props.findings.length > 50}>
          <text fg={props.theme.textMuted}>还有 {props.findings.length - 50} 条未显示…</text>
        </Show>
      </Show>
    </box>
  );
}

function tierLabel(tier: DocTier): string {
  return TIER_LABELS[tier];
}

function refStatusLabel(status: RefStatus): string {
  return REF_STATUS_LABELS[status];
}

function MetaSection(props: { runtime: () => PanelRuntime; theme: ThemeColors }): JSX.Element {
  const detail = () => props.runtime().detail;
  /** 指针跳转的目标可能不在树里（如 skill/SKILL.md），用面包屑说明当前文件是从哪跳来的。 */
  const origin = createMemo(() => {
    const stack = props.runtime().navStack;
    return stack.length > 0 ? (stack[stack.length - 1]?.path ?? "") : "";
  });
  return (
    <box flexDirection="column">
      <Show when={detail()}>
        {(d) => (
          <>
            <text wrapMode="word" fg={props.theme.primary}>
              {d().path}
            </text>
            <text wrapMode="word" fg={props.theme.textMuted}>
              {`分级 ${tierLabel(d().tier)} · ${d().linesTotal} 行 · 最近提交 ${
                d().gitLast || "未知"
              }`}
            </text>
            <Show when={origin()}>
              {(from) => (
                <text wrapMode="word" fg={props.theme.info}>
                  {`← 跳转自 ${from()}（b 返回）`}
                </text>
              )}
            </Show>
            <Show when={!d().exists}>
              <text fg={props.theme.warning}>文件不存在或不可读</text>
            </Show>
            <Show when={d().task}>
              {(task) => (
                <box flexDirection="column" marginTop={1}>
                  <text wrapMode="word" fg={props.theme.info}>
                    {`任务：${task().title || "（无标题）"}`}
                  </text>
                  <text wrapMode="word" fg={props.theme.textMuted}>
                    {`状态 ${task().status || "-"} · 负责 ${task().owner || "-"} · 更新 ${
                      task().updated || "-"
                    }`}
                  </text>
                  <Show when={task().goal}>
                    <text wrapMode="word" fg={props.theme.text}>
                      {`目标：${task().goal}`}
                    </text>
                  </Show>
                  <Show when={task().evidence}>
                    <text wrapMode="word" fg={props.theme.text}>
                      {`证据：${task().evidence}`}
                    </text>
                  </Show>
                  <Show when={task().doc}>
                    <text wrapMode="word" fg={props.theme.textMuted}>
                      {`文档：${task().doc}`}
                    </text>
                  </Show>
                </box>
              )}
            </Show>
          </>
        )}
      </Show>
    </box>
  );
}

function ContentSection(props: { runtime: () => PanelRuntime; theme: ThemeColors }): JSX.Element {
  const detail = () => props.runtime().detail;
  const lines = createMemo(() => detail()?.content ?? []);
  const remaining = createMemo(() => {
    const d = detail();
    if (!d) return 0;
    return Math.max(0, d.linesTotal - (d.offset - 1 + lines().length));
  });
  return (
    <box flexDirection="column">
      <Show
        when={detail()}
        fallback={<text fg={props.theme.textMuted}>（未选中文件）</text>}
      >
        {(d) => (
          <>
            <text fg={props.theme.textMuted}>
              {`内容预览（L${d().offset}-${d().offset + lines().length - 1} / 共 ${d().linesTotal} 行）`}
            </text>
            <Show when={props.runtime().loading}>
              <text fg={props.theme.info}>读取中…</text>
            </Show>
            <Show when={lines().length === 0 && !props.runtime().loading}>
              <text fg={props.theme.textMuted}>（无内容）</text>
            </Show>
            <For each={lines()}>
              {(line, index) => (
                <text wrapMode="none" truncate fg={props.theme.text}>
                  {`${String(d().offset + index()).padStart(4, " ")} │ ${line}`}
                </text>
              )}
            </For>
            <Show when={remaining() > 0}>
              <text fg={props.theme.textMuted}>
                {`… 还有 ${remaining()} 行（[ / ] 翻页）`}
              </text>
            </Show>
          </>
        )}
      </Show>
    </box>
  );
}

function RefsSection(props: {
  runtime: () => PanelRuntime;
  theme: ThemeColors;
  onJump?: (ref: DocRef) => void;
}): JSX.Element {
  const refs = createMemo(() => props.runtime().detail?.refs ?? []);
  return (
    <box flexDirection="column">
      <text fg={props.theme.textMuted}>
        {`指针（${refs().length}）· o 选择 · Enter / 点击跳转`}
      </text>
      <Show when={refs().length === 0}>
        <text fg={props.theme.textMuted}>（无指针）</text>
      </Show>
      <For each={refs()}>
        {(ref, index) => (
          <box
            flexDirection="row"
            width="100%"
            backgroundColor={
              index() === props.runtime().refIndex ? props.theme.backgroundElement : undefined
            }
            onMouseUp={(event: MouseEvent) => {
              if (event.button !== 0) return;
              props.onJump?.(ref);
            }}
          >
            <text wrapMode="none" truncate fg={refColor(ref, props.theme)}>
              {`L${ref.line} → ${ref.target}（${refStatusLabel(ref.status)}）`}
            </text>
            <Show when={ref.resolved && ref.resolved !== ref.target}>
              <text wrapMode="none" truncate fg={props.theme.textMuted}>
                {` → ${ref.resolved}`}
              </text>
            </Show>
          </box>
        )}
      </For>
    </box>
  );
}

/**
 * 告警总览（告警 tab 且未打开文件）：显示选中的那条告警与操作提示。
 * 文件打开后走标准四段详情（该文件的全部 findings 在末段）。
 */
function FindingsOverview(props: {
  state: () => AppState;
  theme: ThemeColors;
}): JSX.Element {
  const theme = props.theme;
  const finding = createMemo(() => {
    const current = props.state();
    const row = current.rows[current.tab]?.find((item) => item.id === current.selectedId);
    return row?.finding;
  });
  return (
    <box flexDirection="column">
      <text wrapMode="word" fg={theme.primary}>
        {`告警总览 · 共 ${props.state().summary.total} 条`}
      </text>
      <Show
        when={finding()}
        fallback={<text fg={theme.success}>无告警</text>}
      >
        {(item) => (
          <>
            <text wrapMode="word" fg={severityColor(item().severity, theme)}>
              {`${severityMark(item().severity)} · ${item().rule} · ${item().file}:${item().line}`}
            </text>
            <text wrapMode="word" fg={theme.text}>
              {item().message}
            </text>
            <Show when={item().fix}>
              <text wrapMode="word" fg={theme.textMuted}>
                {`修复：${item().fix}`}
              </text>
            </Show>
          </>
        )}
      </Show>
      <text wrapMode="word" fg={theme.textMuted}>
        Enter / 点击 打开该文件 · b 返回列表
      </text>
    </box>
  );
}

/**
 * 近期功能实现条目详情：状态徽章 + 归属 + 摘要 + 叙事与机制（100 行分页）+ 流程 + 指针。
 * 不读文件：内容全部来自引擎 `logic list` 返回的条目数据。
 */
function LogicEntryDetail(props: {
  state: () => AppState;
  runtime: () => PanelRuntime;
  theme: ThemeColors;
  /** 详情框宽度（列）：叙事按它软换行。 */
  width?: number;
  onOpenPath?: (path: string, line?: number) => void;
}): JSX.Element {
  const theme = props.theme;
  const selected = createMemo(() => selectedLogicRow(props.state()));
  const pageLines = resolvePageLines();
  // 叙事与机制小节先按可用宽度软换行，再拼成同一条行序列按 100 行分页：长段落与小节都能翻页看完。
  const detailLines = createMemo(() =>
    wrapNarrativeWithSections(
      selected().entry?.narrative ?? "",
      selected().entry?.sections ?? [],
      narrativeTextWidth(props.width ?? 80),
    ),
  );
  const hasSections = createMemo(() => (selected().entry?.sections ?? []).length > 0);
  const shown = createMemo(() =>
    detailLines().slice(props.runtime().offset - 1, props.runtime().offset - 1 + pageLines),
  );
  const remaining = createMemo(() =>
    Math.max(0, detailLines().length - (props.runtime().offset - 1 + shown().length)),
  );
  return (
    <box flexDirection="column" paddingRight={1}>
      <Show
        when={selected().entry}
        fallback={<text fg={theme.textMuted}>（未选中近期功能实现条目）</text>}
      >
        {(entry) => (
          <>
            <text wrapMode="word" fg={theme.primary}>
              {entry().title}
            </text>
            <text wrapMode="word" fg={logicStatusColor(entry().status, theme)}>
              {`${logicStatusPrefix(entry().status)}${LOGIC_STATUS_LABELS[entry().status]}${
                entry().status_detail ? ` · ${entry().status_detail}` : ""
              }`}
            </text>
            <text wrapMode="word" fg={theme.textMuted}>
              {`${selected().domain?.title || selected().domain?.domain || "-"} · 更新 ${
                selected().domain?.updated || "未知"
              } · 类型 ${entry().kind || "-"} · 分组 ${entry().group || "未分组"}`}
            </text>
            <Show when={entry().summary}>
              <text wrapMode="word" fg={theme.text}>
                {entry().summary}
              </text>
            </Show>
            <text fg={theme.textMuted}>
              {`${hasSections() ? "叙事与机制" : "叙事"}（L${props.runtime().offset}-${
                props.runtime().offset + shown().length - 1
              } / 共 ${detailLines().length} 行）`}
            </text>
            <For each={shown()}>
              {(line, index) => (
                <text
                  wrapMode="none"
                  truncate
                  fg={line.kind === "sectionTitle" ? theme.primary : theme.text}
                >
                  {`${String(props.runtime().offset + index()).padStart(4, " ")} │ ${line.text}`}
                </text>
              )}
            </For>
            <Show when={remaining() > 0}>
              <text fg={theme.textMuted}>{`… 还有 ${remaining()} 行（[ / ] 翻页）`}</text>
            </Show>
            <Show when={entry().flow.length > 0}>
              <text fg={theme.textMuted}>{`流程（${entry().flow.length} 步）`}</text>
              <For each={entry().flow}>
                {(step, index) => (
                  <text wrapMode="word" fg={theme.text}>
                    {`${index() + 1}. ${step}`}
                  </text>
                )}
              </For>
            </Show>
            <text fg={theme.textMuted}>
              {`指针（${entry().pointers.length}）· o 选择 · Enter / 点击跳转`}
            </text>
            <Show when={entry().pointers.length === 0}>
              <text fg={theme.textMuted}>（无指针）</text>
            </Show>
            <For each={entry().pointers}>
              {(pointer, index) => (
                <box
                  flexDirection="row"
                  width="100%"
                  backgroundColor={
                    index() === props.runtime().refIndex ? theme.backgroundElement : undefined
                  }
                  onMouseUp={(event: MouseEvent) => {
                    if (event.button !== 0) return;
                    props.onOpenPath?.(pointer.file, pointer.line);
                  }}
                >
                  <text wrapMode="none" truncate fg={theme.info}>
                    {`${pointer.role} ${pointer.file}:${pointer.line}-${pointer.end}`}
                  </text>
                  <Show when={pointer.symbol}>
                    <text wrapMode="none" truncate fg={theme.textMuted}>
                      {` ${pointer.symbol}`}
                    </text>
                  </Show>
                </box>
              )}
            </For>
          </>
        )}
      </Show>
    </box>
  );
}

/**
 * 近期功能实现分组 / 目录行：域有 overview 时展示域概览，crosscut 有内容时展示横切机制小节；
 * 两者缺省时保持原有分组统计。条目详情由条目行渲染。
 */
function LogicGroupSummary(props: {
  state: () => AppState;
  theme: ThemeColors;
  /** 详情框宽度（列）：域概览与横切正文按它软换行。 */
  width?: number;
}): JSX.Element {
  const theme = props.theme;
  const selected = createMemo(() => selectedLogicRow(props.state()));
  const textWidth = createMemo(() => narrativeTextWidth(props.width ?? 80));
  const overviewLines = createMemo(() =>
    wrapNarrative(selected().domain?.overview ?? "", textWidth()),
  );
  const crosscut = createMemo(() => selected().domain?.crosscut ?? []);
  const hasOverview = createMemo(() => (selected().domain?.overview ?? "").trim() !== "");
  return (
    <box flexDirection="column" paddingRight={1}>
      <Show
        when={selected().row}
        fallback={<text fg={theme.textMuted}>（未选中近期功能实现条目）</text>}
      >
        {(row) => (
          <>
            <text wrapMode="word" fg={theme.primary}>
              {hasOverview()
                ? selected().domain?.title || selected().domain?.domain || row().label
                : row().label}
            </text>
            <Show when={hasOverview()}>
              <text wrapMode="word" fg={theme.textMuted}>
                {row().label}
              </text>
            </Show>
            <Show when={row().meta}>
              <text wrapMode="word" fg={theme.textMuted}>
                {row().meta ?? ""}
              </text>
            </Show>
            <Show when={hasOverview()}>
              <For each={overviewLines()}>
                {(line) => (
                  <text wrapMode="none" fg={theme.text}>
                    {line}
                  </text>
                )}
              </For>
            </Show>
            <For each={crosscut()}>
              {(section) => (
                <box flexDirection="column">
                  <text wrapMode="word" fg={theme.primary}>
                    {section.title}
                  </text>
                  <For each={wrapNarrative(section.body, textWidth())}>
                    {(line) => (
                      <text wrapMode="none" fg={theme.text}>
                        {line}
                      </text>
                    )}
                  </For>
                </box>
              )}
            </For>
            <text wrapMode="word" fg={theme.textMuted}>
              Enter 折叠 / 展开该分组 · 选中条目看详情
            </text>
          </>
        )}
      </Show>
    </box>
  );
}

export function DetailPane(props: DetailPaneProps): JSX.Element {
  const theme = props.theme;
  const state = () => props.state();
  const runtime = () => props.runtime();

  const selectedPath = createMemo(() => runtime().path);
  const detailFindings = createMemo<Finding[]>(() => {
    const current = state();
    const path = selectedPath();
    if (!path) return [];
    return current.findingsByFile[path] ?? [];
  });
  const repoFindings = () => state().findingsByFile["."] ?? [];
  /** 告警 tab 未打开文件时给总览，而不是空的四段详情。 */
  const overview = createMemo(() => state().tab === "findings" && !runtime().path);
  /** 近期功能实现 tab 且未跳到代码文件时：渲染条目详情或分组统计。 */
  const logicEntry = createMemo(() => {
    const current = state();
    if (current.tab !== "logic" || runtime().path) return undefined;
    return selectedLogicRow(current).entry;
  });
  const logicRow = createMemo(() => {
    const current = state();
    if (current.tab !== "logic" || runtime().path) return undefined;
    return selectedLogicRow(current).row;
  });

  return (
    <box flexDirection="column" paddingRight={1}>
      <Show
        when={!overview()}
        fallback={<FindingsOverview state={state} theme={theme} />}
      >
        <Show
          when={logicEntry()}
          fallback={
            <Show
              when={logicRow()}
              fallback={
                <>
                  <MetaSection runtime={runtime} theme={theme} />
                  <text fg={theme.border}>{"─".repeat(24)}</text>
                  <Show when={runtime().degraded}>
                    <text wrapMode="word" fg={theme.warning}>
                      {`降级：${runtime().degraded}`}
                    </text>
                  </Show>
                  <ContentSection runtime={runtime} theme={theme} />
                  <text fg={theme.border}>{"─".repeat(24)}</text>
                  <RefsSection runtime={runtime} theme={theme} onJump={props.onJumpRef} />
                  <text fg={theme.border}>{"─".repeat(24)}</text>
                  <Show
                    when={state().checker.status !== "unavailable"}
                    fallback={
                      <box flexDirection="column" marginTop={1}>
                        <text fg={theme.error}>检查器不可用</text>
                        <text wrapMode="word" fg={theme.textMuted}>
                          {state().checker.message ?? "未知原因"}
                        </text>
                        <text wrapMode="word" fg={theme.textMuted}>
                          左侧树仍来自文件系统直读。修复 engine/govern 或解释器后按 r 重试；
                          保存 docs/、AGENTS.md 或进度板也会自动重试。
                        </text>
                      </box>
                    }
                  >
                    <box flexDirection="column" marginTop={1}>
                      <Show when={state().checker.status === "degraded"}>
                        <text wrapMode="word" fg={theme.warning}>
                          检查器降级：{state().checker.message ?? "引擎报告失败"}
                        </text>
                      </Show>
                      <Show when={state().checker.status === "loading"}>
                        <text fg={theme.info}>扫描中…</text>
                      </Show>
                      <Show when={!state().watcher.ok}>
                        <text wrapMode="word" fg={theme.warning}>
                          文件监听异常：{state().watcher.message ?? "未知原因"}
                        </text>
                      </Show>
                      <text fg={theme.textMuted}>
                        {`当前文件的 findings（${detailFindings().length}）· 点击打开`}
                      </text>
                      <FindingList findings={detailFindings()} theme={theme} onOpen={props.onOpenPath} />
                      <Show when={repoFindings().length > 0}>
                        <text fg={theme.warning}>仓库级 findings（file="."）</text>
                        <FindingList findings={repoFindings()} theme={theme} />
                      </Show>
                    </box>
                  </Show>
                </>
              }
            >
              <LogicGroupSummary state={state} theme={theme} width={props.width} />
            </Show>
          }
        >
          <LogicEntryDetail
            state={state}
            runtime={runtime}
            theme={theme}
            width={props.width}
            onOpenPath={props.onOpenPath}
          />
        </Show>
      </Show>
    </box>
  );
}
