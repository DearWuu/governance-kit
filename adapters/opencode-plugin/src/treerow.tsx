/**
 * 树区的一行：普通行（文件 / 分组 / 目录 / 清单项）与告警行两种形态。
 *
 * 上游：panel.tsx 的 <For each={rows()}> 渲染本组件。
 * 下游：只读消费 TreeRow，点击与展开回调交回 panel 处理。
 */
import { Show } from "solid-js";
import type { JSX } from "@opentui/solid";
import type { MouseEvent, Renderable } from "@opentui/core";
import type { ThemeColors, TreeRow } from "./types.ts";
import { severityColor, severityMark } from "./types.ts";
import { clipTail } from "./textwrap.ts";

export interface TreeRowViewProps {
  row: TreeRow;
  /** 该行是否被选中。 */
  selected: boolean;
  theme: ThemeColors;
  /** 树区可用列数：label 按显示宽度尾部省略；缺省按 44 列兜底。 */
  availableColumns?: number;
  /** 行是否可折叠（目录 / 状态组 / 任务行）。 */
  collapsible: boolean;
  /** 目录 / 可折叠行当前是否展开。 */
  expanded: boolean;
  /** 选中该行。 */
  onSelect: () => void;
  /** 折叠 / 展开该行。 */
  onToggleDir?: () => void;
  /** 清单项打钩。 */
  onToggleCheck?: () => void;
  /** 告警行：打开该文件。 */
  onOpenFinding?: (path: string) => void;
  /** 行渲染句柄：面板用它把选中行滚进视口。 */
  rowRef?: (renderable: Renderable) => void;
}

/** 行首缩进与折叠标记。 */
function rowPrefix(row: TreeRow, collapsible: boolean, expanded: boolean): string {
  if (row.kind === "group") return collapsible && !expanded ? "▸ " : "▾ ";
  if (row.kind === "dir") return expanded ? "▾ " : "▸ ";
  if (row.kind === "check") return "";
  return "· ";
}

export function TreeRowView(props: TreeRowViewProps): JSX.Element {
  const theme = props.theme;
  const row = props.row;
  return (
    <box
      flexDirection="row"
      width="100%"
      backgroundColor={props.selected ? theme.backgroundElement : theme.backgroundPanel}
      onMouseUp={(event: MouseEvent) => {
        if (event.button !== 0) return;
        props.onSelect();
        // 目录 / 可折叠行点击等价 Enter：选中后就地折叠 / 展开。
        if (props.collapsible) props.onToggleDir?.();
        // 清单项：单击即打钩 / 取消打钩（乐观更新即时翻转，无需二次点击）。
        if (row.kind === "check") props.onToggleCheck?.();
        // 告警行点击即打开该文件（等价 Enter）。
        if (row.finding) props.onOpenFinding?.(row.finding.file);
      }}
      ref={(renderable: Renderable) => {
        props.rowRef?.(renderable);
      }}
    >
      {/* 选中标记：左侧一列 accent 竖条，避免整行高饱和色块 */}
      <box width={1} backgroundColor={props.selected ? theme.accent : undefined} />
      <Show
        when={row.finding}
        fallback={
          <text
            wrapMode="none"
            truncate
            fg={
              props.selected
                ? theme.primary
                : row.kind === "group"
                  ? theme.textMuted
                  : row.kind === "dir"
                    ? theme.info
                    : row.kind === "check"
                      ? row.checked
                        ? theme.textMuted
                        : theme.text
                      : theme.text
            }
          >
            {"  ".repeat(row.depth)}
            {rowPrefix(row, props.collapsible, props.expanded)}
            {clipTail(row.label, Math.max(8, (props.availableColumns ?? 44) - row.depth * 2 - 2))}
          </text>
        }
      >
        {(finding) => (
          <text wrapMode="none" truncate fg={severityColor(finding().severity, theme)}>
            {`${severityMark(finding().severity)} ${finding().rule} ${finding().file}:${finding().line} · ${finding().message}`}
          </text>
        )}
      </Show>
      <Show when={row.kind === "dir" && row.meta}>
        <text wrapMode="none" fg={theme.textMuted}>
          {" "}
          {row.meta}
        </text>
      </Show>
      <Show when={row.badge}>
        <text wrapMode="none" fg={theme.textMuted}>
          {" "}
          [{row.badge}]
        </text>
      </Show>
    </box>
  );
}
