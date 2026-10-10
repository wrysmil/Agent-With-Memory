import {
  type ReactNode,
  useCallback,
  useRef,
} from "react";
import {
  Bot,
  CalendarClock,
  Feather,
  Home,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Settings,
  Sparkles,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
  SidebarSelectionHighlight,
} from "@/components/SidebarSelectionHighlight";
import type {
  WorkspaceNavigationProps,
  WorkspaceNavigationSlotContext,
  WorkspaceRoute,
  WorkspaceView,
} from "@/components/workspace/contracts";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface NavItem {
  view: WorkspaceView;
  /** Reuse the legacy `sidebar.*` keys so capability pages keep their tested
   *  English accessible names (Apps/Skills/Automations/Agents/Settings). */
  labelKey: string;
  labelDefault: string;
  icon: typeof Home;
}

const PRIMARY_ITEMS: NavItem[] = [
  { view: "home", labelKey: "sidebar.home", labelDefault: "Home", icon: Home },
  { view: "chat", labelKey: "workspace.nav.assistant", labelDefault: "Assistant", icon: MessagesSquare },
  { view: "creative", labelKey: "workspace.nav.creative", labelDefault: "Creative", icon: Feather },
];

const CAPABILITY_ITEMS: NavItem[] = [
  { view: "apps", labelKey: "sidebar.apps", labelDefault: "Apps", icon: Plug },
  { view: "skills", labelKey: "sidebar.skills.title", labelDefault: "Skills", icon: Sparkles },
  { view: "automations", labelKey: "sidebar.automations", labelDefault: "Automations", icon: CalendarClock },
  { view: "agents", labelKey: "sidebar.agents", labelDefault: "Agents", icon: Bot },
];

const SETTINGS_ITEM: NavItem = {
  view: "settings",
  labelKey: "sidebar.settings",
  labelDefault: "Settings",
  icon: Settings,
};

/**
 * Global 知序 text navigation. It owns the sidebar landmark, the selection
 * highlight, the nav entries and the collapse control. The chat-context column
 * and the connection status are injected by the caller through slots so the
 * live shell keeps owning ChatList callbacks while the preview injects
 * fixtures. Non-assistant pages render only the nav column.
 */
export function WorkspaceNavigation({
  activeView,
  collapsed,
  preview,
  onNavigate,
  onToggleCollapsed,
  chatNavigation,
  connectionStatus,
  newChatActive = false,
  activeUtility = null,
  hostChromeInset = false,
}: WorkspaceNavigationProps) {
  const { t } = useTranslation();
  const activeActionRef = useRef<HTMLButtonElement>(null);
  const activeRailRef = useRef<HTMLButtonElement>(null);

  const selectionActiveId = newChatActive
    ? "new-chat"
    : activeUtility
      ? `utility:${activeUtility}`
      : null;

  const showChatColumn = activeView === "chat" && !collapsed;
  const chatSlot: ReactNode =
    typeof chatNavigation === "function"
      ? (chatNavigation as (ctx: WorkspaceNavigationSlotContext) => ReactNode)({
          activeActionRef,
        })
      : chatNavigation;

  const topInset = hostChromeInset ? "pt-12" : "pt-4";

  const navigateTo = useCallback(
    (view: WorkspaceView) => onNavigate({ view } satisfies WorkspaceRoute),
    [onNavigate],
  );

  const renderItem = (item: NavItem) => {
    const label = t(item.labelKey, { defaultValue: item.labelDefault });
    const active =
      activeView === item.view ||
      (item.view === "creative" && activeView === "article");
    const Icon = item.icon;
    return (
      <button
        key={item.view}
        type="button"
        aria-label={label}
        aria-current={active ? "page" : undefined}
        title={label}
        ref={active ? activeRailRef : undefined}
        onClick={() => navigateTo(item.view)}
        className={cn(
          "host-no-drag flex min-h-10 w-full items-center gap-3 rounded-control px-3 py-2 text-[13px] transition-colors",
          SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          active
            ? "bg-primary/10 font-medium text-primary"
            : "text-sidebar-foreground/85 hover:bg-sidebar-foreground/[0.05] hover:text-sidebar-foreground dark:hover:bg-white/[0.06]",
          collapsed && "justify-center px-0",
        )}
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden />
        {collapsed ? (
          <span className="sr-only">{label}</span>
        ) : (
          <span className="truncate">{label}</span>
        )}
      </button>
    );
  };

  return (
    <nav
      aria-label={t("sidebar.navigation")}
      className={cn(
        "workspace-navigation h-full w-full min-w-0 text-sidebar-foreground",
        hostChromeInset ? "bg-transparent" : "bg-sidebar",
      )}
    >
      <SidebarSelectionHighlight
        targetRef={newChatActive ? activeActionRef : activeRailRef}
        activeId={selectionActiveId}
        scope="actions"
        className="relative flex h-full w-full min-w-0 flex-row"
      >
        <div
          data-testid="sidebar-rail"
          className={cn(
            "workspace-navigation-rail flex min-h-0 shrink-0 flex-col gap-2 overflow-y-auto overscroll-contain border-r border-sidebar-border pb-3",
            collapsed ? "w-16 items-center" : "w-[224px]",
            topInset,
          )}
        >
          <div
            className={cn(
              "flex shrink-0 items-center gap-3 px-4 pb-3",
              collapsed && "justify-center px-0",
            )}
          >
            <span
              aria-hidden
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-mark bg-primary text-sm font-semibold text-primary-foreground"
            >
              知
            </span>
            {!collapsed && (
              <span className="truncate text-[18px] font-semibold tracking-wide text-sidebar-foreground">
                {t("workspace.brand", { defaultValue: "知序" })}
              </span>
            )}
          </div>
          {preview && !collapsed ? (
            <p
              data-preview-badge
              className="mx-3 mb-1 rounded-control border border-border/60 bg-background/60 px-2.5 py-1.5 text-[11px] leading-relaxed text-muted-foreground"
            >
              {t("workspace.preview.badge", { defaultValue: "界面预览 · 示例内容" })}
            </p>
          ) : null}
          <div className={cn("flex shrink-0 flex-col gap-1", collapsed ? "w-full px-2" : "px-3")}>{PRIMARY_ITEMS.map(renderItem)}</div>
          <div className="min-h-4 flex-1" />
          <div className={cn("flex shrink-0 flex-col gap-1 border-t border-sidebar-border pt-3", collapsed ? "w-full px-2" : "px-3")}>
            {CAPABILITY_ITEMS.map(renderItem)}
            {renderItem(SETTINGS_ITEM)}
          </div>
          <div
            className={cn(
              "mt-1 shrink-0 border-t border-sidebar-border pt-2",
              collapsed ? "flex justify-center px-0" : "px-2",
            )}
          >
            <RailToggleButton
              collapsed={collapsed}
              label={
                collapsed
                  ? t("thread.header.toggleSidebar")
                  : t("sidebar.collapse")
              }
              onClick={onToggleCollapsed}
            />
          </div>
          {!collapsed && !showChatColumn && connectionStatus ? (
            <div className="workspace-navigation-status host-no-drag shrink-0 px-4 pt-1 text-[11px] leading-relaxed text-muted-foreground">
              {connectionStatus}
            </div>
          ) : null}
        </div>
        {showChatColumn ? (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {chatSlot}
          </div>
        ) : null}
      </SidebarSelectionHighlight>
    </nav>
  );
}

function RailToggleButton({
  collapsed,
  label,
  onClick,
}: {
  collapsed: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant={null}
      size={collapsed ? "icon" : null}
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "host-no-drag flex items-center justify-center gap-2 rounded-xl text-muted-foreground/85 hover:bg-sidebar-foreground/[0.06] hover:text-sidebar-foreground",
        collapsed ? "h-9 w-9" : "h-8 w-full px-2.5 text-[12.5px] font-medium",
      )}
    >
      {collapsed ? (
        <PanelLeftOpen className="h-4 w-4" aria-hidden />
      ) : (
        <>
          <PanelLeftClose className="h-4 w-4 shrink-0" aria-hidden />
          <span className="truncate">{label}</span>
        </>
      )}
    </Button>
  );
}
