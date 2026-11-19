/**
 * 侧边栏常驻卡片：文档树 · governance-kit 摘要。
 *
 * 上游：index.tsx 通过 sidebar.content 槽位挂载，并注入状态与打开面板的动作。
 * 下游：只读 AppState；点击标题可折叠为极简两行，「打开」按钮拉起全屏面板。
 */
import { For, Show, createMemo } from "solid-js";
import type { JSX } from "@opentui/solid";
import type { MouseEvent } from "@opentui/core";
import type { AppState, BoardStatusCount, Finding, ThemeColors } from "./types.ts";
import { TAB_LABELS, TAB_ORDER, severityColor } from "./types.ts";
import { logicCounts } from "./logicrows.ts";

export interface DocTreeSidebarProps {
  theme: ThemeColors;
  getState: () => AppState;
  /** 卡片是否折叠成极简一行。 */
  collapsed: () => boolean;
  /** 切换折叠状态。 */
  toggleCollapsed: () => void;
  /** 打开 /doc-tree 全屏面板。 */
  openPanel: () => void;
}

/** 只取高危项：error 优先，其次 warning；最多 3 条。 */
export function topRisks(findings: Finding[], limit = 3): Finding[] {
  const risky = findings.filter(
    (finding) => finding.severity === "error" || finding.severity === "warning",
  );
  return risky.slice(0, limit);
}

/** 进度摘要：最多列 3 个状态，避免侧边栏换行。 */
export function boardSummary(statuses: BoardStatusCount[], limit = 3): string {
  const active = statuses.filter((item) => item.count > 0);
  if (active.length === 0) return "进度 （空）";
  const head = active.slice(0, limit).map((item) => `${item.status} ${item.count}`).join(" · ");
  const more = active.length > limit ? ` · +${active.length - limit}` : "";
  return `进度 ${head}${more}`;
}

function formatTime(iso?: string): string {
  if (!iso) return "未扫描";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

export function DocTreeSidebar(props: DocTreeSidebarProps): JSX.Element {
  const theme = props.theme;
  const state = () => props.getState();
  const risks = createMemo(() => topRisks(state().findings));
  const counts = createMemo(() =>
    TAB_ORDER.map((tab) => `${TAB_LABELS[tab]} ${state().counts[tab]}`).join(" · "),
  );
  // 无板（未发现进度板且无任何状态计数）时隐藏进度摘要行，不出「进度 （空）」噪声。
  const hasBoard = createMemo(
    () => Boolean(state().boardPath) || state().boardStatuses.some((item) => item.count > 0),
  );
  /** 近期功能实现摘要：条目数与 stale/broken 异常数；未配置或引擎不可用时给提示。 */
  const logicSummary = createMemo(() => {
    if (state().logicError) return "近期功能实现 不可用";
    const counts = logicCounts(state().logic.domains);
    const total = state().counts.logic || counts.total;
    if (total === 0) return "近期功能实现 未配置";
    if (counts.stale + counts.broken === 0) return `近期功能实现 ${total}`;
    return `近期功能实现 ${total} · stale ${counts.stale} · broken ${counts.broken}`;
  });

  return (
    <box id="sidebar.governance-kit" flexDirection="column" gap={0}>
      <box flexDirection="row" width="100%">
        <text
          id="sidebar.governance-kit.title"
          fg={theme.primary}
          onMouseUp={(event: MouseEvent) => {
            if (event.button !== 0) return;
            props.toggleCollapsed();
          }}
        >
          {props.collapsed() ? "▸ 文档树 · governance-kit" : "▾ 文档树 · governance-kit"}
        </text>
        <box flexGrow={1} />
        <text
          id="sidebar.governance-kit.open"
          bg={theme.backgroundElement}
          fg={theme.info}
          onMouseUp={(event: MouseEvent) => {
            if (event.button !== 0) return;
            props.openPanel();
          }}
        >
          {" 打开 "}
        </text>
      </box>

      <Show when={!props.collapsed()}>
        <text id="sidebar.governance-kit.counts" fg={theme.textMuted}>
          {counts()}
        </text>
        <Show when={hasBoard()}>
          <text id="sidebar.governance-kit.board" fg={theme.textMuted}>
            {boardSummary(state().boardStatuses)}
          </text>
        </Show>
        <text
          id="sidebar.governance-kit.alerts"
          fg={state().summary.error + state().summary.warning > 0 ? theme.warning : theme.success}
        >
          {`告警 E${state().summary.error} W${state().summary.warning} · 扫描 ${
            formatTime(state().lastScanAt)
          }`}
        </text>
        <text id="sidebar.governance-kit.logic" fg={theme.textMuted}>
          {logicSummary()}
        </text>
        <Show
          when={risks().length > 0}
          fallback={<text id="sidebar.governance-kit.clean" fg={theme.success}>无高危告警</text>}
        >
          <For each={risks()}>
            {(finding) => (
              <text
                id={`sidebar.governance-kit.finding.${finding.rule}.${finding.file}`}
                wrapMode="none"
                truncate
                fg={severityColor(finding.severity, theme)}
              >
                {`${finding.severity === "error" ? "E" : "W"} ${finding.rule} ${
                  finding.file
                }${finding.line > 0 ? `:${finding.line}` : ""}`}
              </text>
            )}
          </For>
        </Show>
      </Show>
    </box>
  );
}
