/**
 * governance-kit 的 OpenCode TUI 实时插件（参考适配器）。
 *
 * 上游：OpenCode V2 TUI 宿主调用 setup()；命令 /doc-tree 打开 session.panel。
 * 下游：panel.tsx 渲染全屏面板，sidebar.tsx 渲染侧边栏摘要，
 *       session.ts 持有状态与动作，tree.ts 直读文件系统，scan.ts / detail.ts 调 engine/govern。
 * 生命周期：setup 返回 cleanup，关闭文件监听、防抖定时器与在跑的子进程。
 */
import { fileURLToPath } from "node:url";
import * as path from "node:path";
import { createSignal } from "solid-js";
import type { JSX } from "@opentui/solid";
import { DocTreePanel } from "./panel.tsx";
import { DocTreeSidebar } from "./sidebar.tsx";
import { createDocSession } from "./session.ts";
import { discoverRepo } from "./repo.ts";
import {
  resolveTheme,
  type AppState,
  type PanelRuntime,
  type PanelInput,
  type PanelPresentation,
  type SidebarInput,
  type TabId,
  type ThemeSource,
} from "./types.ts";

const PLUGIN_ID = "governance-kit-doc-tree";
const PANEL_NAME = "doc-tree";
const SCAN_TIMEOUT_MS = 30_000;

/** 宿主 V2 TUI 插件 API 中本适配器用到的子集；宿主版本不匹配时宁可不加载。 */
interface KeymapCommand {
  id?: string;
  title?: string;
  description?: string;
  group?: string;
  palette?: true;
  slash?: { name: string; aliases?: string[] };
  bind?: false | string;
  enabled?: boolean | (() => boolean);
  run: (input?: string, event?: unknown) => void | false | Promise<void>;
}

interface KeymapLayer {
  priority?: number;
  commands?: readonly KeymapCommand[];
  bindings?: readonly string[];
}

interface PluginContext {
  readonly location?: { readonly directory: string };
  readonly data?: {
    readonly location?: { default(): { readonly directory: string } };
  };
  readonly theme: ThemeSource;
  readonly keymap: {
    layer(factory: () => KeymapLayer): void;
  };
  readonly ui: {
    readonly panel: {
      open(name: string, options?: { presentation?: PanelPresentation }): boolean;
    };
    readonly slot: (claim: SlotClaim) => () => void;
    readonly toast: {
      show(input: {
        title?: string;
        message: string;
        variant?: "info" | "success" | "warning" | "error";
        duration?: number;
      }): void;
    };
  };
}

/** 槽位声明：session.panel 与 sidebar.content 各有自己的输入类型。
 * 侧边栏位置可配：DOC_TREE_SIDEBAR=prepend（默认，插到 sidebar.content 最前）| append（排尾，随启用顺序）。 */
type SlotClaim =
  | { readonly append: "session.panel"; readonly render: (input: PanelInput) => JSX.Element }
  | ({ readonly prepend: "sidebar.content"; readonly render: (input: SidebarInput) => JSX.Element }
    | { readonly append: "sidebar.content"; readonly render: (input: SidebarInput) => JSX.Element });

export interface GovernancePlugin {
  readonly id: string;
  readonly setup: (context: PluginContext) => Promise<() => void>;
}

function resolveRepo(context: PluginContext): string {
  const candidates: string[] = [];
  if (context.location?.directory) candidates.push(context.location.directory);
  try {
    const fallback = context.data?.location?.default().directory;
    if (fallback) candidates.push(fallback);
  } catch {
    // 没有默认 location 时继续用 cwd。
  }
  candidates.push(process.cwd());
  return discoverRepo(candidates.find((candidate) => Boolean(candidate)) ?? process.cwd());
}

/** 插件源码位于 <kit>/adapters/opencode-plugin/src，engine 在 <kit>/engine。 */
function resolveKitRoot(): string {
  try {
    return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
  } catch {
    return process.cwd();
  }
}

export default {
  id: PLUGIN_ID,
  async setup(context: PluginContext): Promise<() => void> {
    const repo = resolveRepo(context);
    const kitRoot = resolveKitRoot();
    const governPath =
      process.env.DOC_HEALTH_GOVERN?.trim() || path.join(kitRoot, "engine", "govern");
    const python = process.env.DOC_HEALTH_PYTHON?.trim() || "python3";
    const timeoutOverride = Number(process.env.DOC_HEALTH_SCAN_TIMEOUT_MS);
    const scanTimeoutMs =
      Number.isFinite(timeoutOverride) && timeoutOverride > 0
        ? timeoutOverride
        : SCAN_TIMEOUT_MS;

    const session = createDocSession({
      repo,
      governPath,
      python,
      scanTimeoutMs,
      notify: (message, variant) => {
        context.ui.toast.show({ message, variant });
      },
    });
    const cleanups: Array<() => void> = [];

    // 面板是否处于打开状态：宿主会先吃掉 Tab（编辑器补全），插件要用 keymap 层抢回来，
    // 只能在面板打开时才接管，避免影响宿主的全局 Tab 行为。
    const [panelOpen, setPanelOpen] = createSignal(false);
    // 侧边栏卡片折叠态放在插件层：槽位刷新会重建组件，组件内的 signal 会丢。
    const [sidebarCollapsed, setSidebarCollapsed] = createSignal(false);

    const openPanel = (): boolean => {
      const opened = context.ui.panel.open(PANEL_NAME, { presentation: "fullscreen" });
      if (opened) {
        setPanelOpen(true);
        // 数据以 opencode 当前目录对应的仓库为准：打开面板即重拉，
        // 避免实例启动后才生成的文档（如 .governance/logic/*.json）一直显示旧状态。
        session.actions.refresh();
      }
      return opened;
    };

    const disposeSlot = context.ui.slot({
      append: "session.panel",
      render: (input: PanelInput) => {
        if (input.name !== PANEL_NAME) return <box height={0} />;
        return (
          <DocTreePanel
            input={input}
            theme={resolveTheme(context.theme)}
            getState={session.state as () => AppState}
            getRuntime={session.runtime as () => PanelRuntime}
            onVisibilityChange={setPanelOpen}
            toggleSidebar={() => setSidebarCollapsed((value) => !value)}
            selectRow={session.actions.selectRow}
            selectTab={session.actions.selectTab}
            refresh={session.actions.refresh}
            jumpToRef={session.actions.jumpToRef}
            back={session.actions.back}
            page={session.actions.page}
            cycleRef={session.actions.cycleRef}
            toggleDir={session.actions.toggleDir}
            toggleCheck={session.actions.toggleCheck}
            notify={(message, variant) => {
              context.ui.toast.show({ message, variant });
            }}
            openFinding={session.actions.openFinding}
            openPath={session.actions.openPath}
            setFilter={session.actions.setFilter}
            setFilterMode={session.actions.setFilterMode}
          />
        );
      },
    });
    cleanups.push(disposeSlot);

    // 侧边栏常驻摘要：与 superagent-agents / Qoder Credits 共存于 sidebar.content。
    // 位置：默认 append（原生内容（如 Context 块）之上不打扰、位于基底之下，随启用顺序排在 superagent 后）；
    // DOC_TREE_SIDEBAR=prepend 可强制置顶到边界内最前。
    const sidebarPlacement =
      process.env.DOC_TREE_SIDEBAR === "prepend"
        ? { prepend: "sidebar.content" as const }
        : { append: "sidebar.content" as const };
    const disposeSidebar = context.ui.slot({
      ...sidebarPlacement,
      render: () => (
        <DocTreeSidebar
          theme={resolveTheme(context.theme)}
          getState={session.state as () => AppState}
          collapsed={sidebarCollapsed}
          toggleCollapsed={() => setSidebarCollapsed((value) => !value)}
          openPanel={() => {
            if (!openPanel()) {
              context.ui.toast.show({
                variant: "warning",
                message: "无法打开文档树：当前没有活动会话",
              });
            }
          }}
        />
      ),
    });
    cleanups.push(disposeSidebar);

    // slash 命令 /doc-tree + 命令面板条目；面板以全屏呈现。
    context.keymap.layer(() => ({
      mode: "global",
      priority: 10,
      commands: [
        {
          id: "governance-kit.doc-tree.open",
          title: "文档树：打开 governance-kit 实时面板",
          description: "契约层 / docs 分级 / 进度板 / 告警、内容预览、指针与 findings",
          group: "governance-kit",
          palette: true,
          slash: { name: "doc-tree" },
          run: () => {
            session.actions.refresh();
            if (!openPanel()) {
              context.ui.toast.show({
                variant: "warning",
                message: "无法打开文档树：当前没有活动会话",
              });
            }
          },
        },
        {
          // 宿主会先消费 Tab（编辑器补全），插件层 priority 更高，只在面板打开时接管。
          id: "governance-kit.doc-tree.next-tab",
          title: "文档树：下一个 tab",
          description: "在文档树面板内切换 契约层 / docs 分级 / 进度板 / 告警",
          group: "governance-kit",
          bind: "tab",
          enabled: () => panelOpen(),
          run: () => session.actions.cycleTab(1),
        },
        {
          id: "governance-kit.doc-tree.prev-tab",
          title: "文档树：上一个 tab",
          description: "在文档树面板内反向切换 契约层 / docs 分级 / 进度板 / 告警",
          group: "governance-kit",
          bind: "shift+tab",
          enabled: () => panelOpen(),
          run: () => session.actions.cycleTab(-1),
        },
      ],
    }));

    return () => {
      for (const fn of cleanups) {
        try {
          fn();
        } catch {
          // ignore
        }
      }
      cleanups.length = 0;
      session.dispose();
    };
  },
} satisfies GovernancePlugin;

export type { TabId };
