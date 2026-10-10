import {
  type ReactNode,
  type RefObject,
  useRef,
  useState,
} from "react";
import {
  Archive,
  Blocks,
  Bot,
  Brain,
  CalendarClock,
  ChevronDown,
  Home,
  Menu,
  Search,
  Settings,
  SquarePen,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  ChatList,
  type SidebarDeleteItem,
  type SidebarPaneGroup,
} from "@/components/ChatList";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import {
  SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
  SidebarSelectionHighlight,
} from "@/components/SidebarSelectionHighlight";
import { Button } from "@/components/ui/button";
import type {
  ChatSummary,
  SidebarProjectEntry,
  SidebarViewState,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface SidebarProps {
  sessions: ChatSummary[];
  temporarySessions?: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  newChatActive: boolean;
  onNewChat: () => void;
  onSelect: (key: string) => void;
  onCloseTemporaryChat?: (key: string) => void;
  onRequestDelete: (key: string, label: string) => void;
  onRequestDeleteMany?: (items: SidebarDeleteItem[]) => void;
  onTogglePin: (key: string) => void;
  onRequestRename: (key: string, label: string) => void;
  onRequestRenameTab?: (key: string, label: string) => void;
  onToggleArchive: (key: string) => void;
  paneGroups?: Record<string, SidebarPaneGroup>;
  onSelectPane?: (tabKey: string, paneKey: string) => void;
  onCreateTab?: (paneKey: string) => void;
  onDetachPane?: (tabKey: string, paneKey: string) => void;
  onDissolveTab?: (tabKey: string) => void;
  onAttachPane?: (
    paneKey: string,
    tabKey: string,
  ) => void;
  onToggleGroup: (groupId: string) => void;
  onRequestRenameProject: (projectKey: string, label: string) => void;
  onNewChatInProject: (projectPath: string, projectName: string) => void;
  onTogglePinProject?: (projectKey: string) => void;
  onRemoveProject?: (projectKey: string, label: string) => void;
  onOpenSettings: () => void;
  onOpenApps: () => void;
  onOpenSkills: () => void;
  onOpenAutomations: () => void;
  onOpenAgents: () => void;
  onSettingsIntent?: () => void;
  onOpenSearch: () => void;
  activeUtility?: "apps" | "skills" | "automations" | "agents" | null;
  onToggleArchived: () => void;
  onCollapse: () => void;
  onExpand?: () => void;
  containActionMenus?: boolean;
  collapsed?: boolean;
  pinnedKeys?: string[];
  archivedKeys?: string[];
  pinnedPaneKeys?: string[];
  archivedPaneKeys?: string[];
  sessionOrder?: string[];
  titleOverrides?: Record<string, string>;
  projectNameOverrides?: Record<string, string>;
  pinnedProjectKeys?: string[];
  hiddenProjectKeys?: string[];
  projectEntries?: SidebarProjectEntry[];
  collapsedGroups?: Record<string, boolean>;
  runningChatIds?: string[];
  updatedChatIds?: string[];
  recoveryChatIds?: string[];
  viewState?: SidebarViewState;
  showArchived?: boolean;
  archivedCount?: number;
  defaultWorkspacePath?: string | null;
  hostChromeInset?: boolean;
}

type NavigatorWithUserAgentData = Navigator & {
  userAgentData?: { platform?: string };
};

function isApplePlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  const platform = navigator.platform || "";
  const userAgentPlatform =
    (navigator as NavigatorWithUserAgentData).userAgentData?.platform || "";
  return /mac|iphone|ipad|ipod/i.test(`${platform} ${userAgentPlatform}`);
}

function newChatShortcutLabel(): string {
  return isApplePlatform() ? "⌘⇧O" : "Ctrl+Shift+O";
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation();
  const [menuPortalContainer, setMenuPortalContainer] =
    useState<HTMLElement | null>(null);
  const collapsed = Boolean(props.collapsed);
  const toggleLabel = t("thread.header.toggleSidebar");
  const newChatShortcut = newChatShortcutLabel();
  const activeActionRef = useRef<HTMLButtonElement>(null);
  const activeRailRef = useRef<HTMLButtonElement>(null);
  const selectionActiveId = props.newChatActive
    ? "new-chat"
    : props.activeUtility
      ? `utility:${props.activeUtility}`
      : null;
  const selectionTargetRef = props.newChatActive || !props.activeUtility
    ? activeActionRef
    : activeRailRef;
  const topInset = props.hostChromeInset ? "pt-8" : "pt-2";

  return (
    <nav
      ref={props.containActionMenus ? setMenuPortalContainer : undefined}
      aria-label={t("sidebar.navigation")}
      className={cn(
        "h-full w-full min-w-0 text-sidebar-foreground",
        props.hostChromeInset ? "bg-transparent" : "bg-sidebar",
      )}
    >
      <SidebarSelectionHighlight
        targetRef={selectionTargetRef}
        activeId={selectionActiveId}
        scope="actions"
        className="relative flex h-full w-full min-w-0 flex-row"
      >
      <div
        data-testid="sidebar-rail"
        className={cn("flex w-12 shrink-0 flex-col items-center gap-1 pb-3", topInset)}
      >
        {collapsed ? (
          <RailButton
            label={toggleLabel}
            onClick={() => props.onExpand?.()}
            icon={<Menu className="h-4 w-4" />}
          />
        ) : (
          <RailButton
            label={t("sidebar.home")}
            onClick={props.onNewChat}
            active={props.newChatActive}
            selectionRef={activeRailRef}
            icon={<Home className="h-4 w-4" />}
          />
        )}
        <RailButton
          label={t("sidebar.apps")}
          onClick={props.onOpenApps}
          onIntent={props.onSettingsIntent}
          active={props.activeUtility === "apps"}
          selectionRef={activeRailRef}
          icon={<Blocks className="h-4 w-4" />}
        />
        <RailButton
          label={t("sidebar.skills.title")}
          onClick={props.onOpenSkills}
          onIntent={props.onSettingsIntent}
          active={props.activeUtility === "skills"}
          selectionRef={activeRailRef}
          icon={<Brain className="h-4 w-4" />}
        />
        <RailButton
          label={t("sidebar.automations", { defaultValue: "Automations" })}
          onClick={props.onOpenAutomations}
          onIntent={props.onSettingsIntent}
          active={props.activeUtility === "automations"}
          selectionRef={activeRailRef}
          icon={<CalendarClock className="h-4 w-4" />}
        />
        <RailButton
          label={t("sidebar.agents", { defaultValue: "Agents" })}
          onClick={props.onOpenAgents}
          onIntent={props.onSettingsIntent}
          active={props.activeUtility === "agents"}
          selectionRef={activeRailRef}
          icon={<Bot className="h-4 w-4" />}
        />
        <div className="min-h-0 flex-1" />
        <RailButton
          label={t("sidebar.settings")}
          onClick={props.onOpenSettings}
          onIntent={props.onSettingsIntent}
          icon={<Settings className="h-4 w-4" />}
        />
      </div>
      {!collapsed && (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div
            data-testid="sidebar-brand-row"
            className={cn(
              "flex items-center gap-1 px-3 pb-1",
              props.hostChromeInset ? "pt-8" : "pt-3",
            )}
          >
            <span className="truncate text-[15px] font-semibold text-sidebar-foreground">
              Mira
            </span>
            <ChevronDown
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70"
              aria-hidden
            />
            <span className="min-w-0 flex-1" />
            <Button
              variant="ghost"
              size="icon"
              aria-label={t("sidebar.searchAria")}
              onClick={props.onOpenSearch}
              className="host-no-drag h-7 w-7 rounded-lg text-muted-foreground/85 hover:bg-sidebar-accent/75 hover:text-sidebar-foreground"
            >
              <Search className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t("sidebar.collapse")}
              onClick={props.onCollapse}
              className="host-no-drag h-7 w-7 rounded-lg text-muted-foreground/85 hover:bg-sidebar-accent/75 hover:text-sidebar-foreground"
            >
              <Menu className="h-3.5 w-3.5" />
            </Button>
          </div>
          <div className="space-y-1.5 px-2 pb-2 pt-1">
            <SidebarActionButton
              label={t("sidebar.newChat")}
              onClick={props.onNewChat}
              active={props.newChatActive}
              selectionRef={activeActionRef}
              icon={<SquarePen className="h-4 w-4" />}
              shortcut={newChatShortcut}
              ariaKeyShortcuts="Meta+Shift+O Control+Shift+O"
            />
            {props.archivedCount ? (
              <SidebarActionButton
                label={props.showArchived ? t("chat.hideArchived") : t("chat.showArchived")}
                onClick={props.onToggleArchived}
                icon={<Archive className="h-4 w-4" />}
              />
            ) : null}
          </div>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <ChatList
              sessions={props.sessions}
              temporarySessions={props.temporarySessions}
              activeKey={props.activeKey}
              loading={props.loading}
              emptyLabel={t("chat.noSessions")}
              onSelect={props.onSelect}
              onCloseTemporaryChat={props.onCloseTemporaryChat}
              onRequestDelete={props.onRequestDelete}
              onRequestDeleteMany={props.onRequestDeleteMany}
              onTogglePin={props.onTogglePin}
              onRequestRename={props.onRequestRename}
              onRequestRenameTab={props.onRequestRenameTab}
              onToggleArchive={props.onToggleArchive}
              paneGroups={props.paneGroups}
              onSelectPane={props.onSelectPane}
              onCreateTab={props.onCreateTab}
              onDetachPane={props.onDetachPane}
              onDissolveTab={props.onDissolveTab}
              onAttachPane={props.onAttachPane}
              onToggleGroup={props.onToggleGroup}
              onRequestRenameProject={props.onRequestRenameProject}
              onNewChatInProject={props.onNewChatInProject}
              onTogglePinProject={props.onTogglePinProject}
              onRemoveProject={props.onRemoveProject}
              pinnedKeys={props.pinnedKeys}
              archivedKeys={props.archivedKeys}
              pinnedPaneKeys={props.pinnedPaneKeys}
              archivedPaneKeys={props.archivedPaneKeys}
              sessionOrder={props.sessionOrder}
              titleOverrides={props.titleOverrides}
              projectNameOverrides={props.projectNameOverrides}
              pinnedProjectKeys={props.pinnedProjectKeys}
              hiddenProjectKeys={props.hiddenProjectKeys}
              projectEntries={props.projectEntries}
              collapsedGroups={props.collapsedGroups}
              runningChatIds={props.runningChatIds}
              updatedChatIds={props.updatedChatIds}
              recoveryChatIds={props.recoveryChatIds}
              density={props.viewState?.density}
              showPreviews={props.viewState?.show_previews}
              showTimestamps={props.viewState?.show_timestamps}
              sort={props.viewState?.sort}
              showArchived={props.showArchived}
              defaultWorkspacePath={props.defaultWorkspacePath}
              actionMenuPortalContainer={
                props.containActionMenus ? menuPortalContainer : undefined
              }
            />
          </div>
          <div className="flex items-center gap-1 bg-sidebar/55 px-2.5 py-3 text-xs">
            <ConnectionBadge />
          </div>
        </div>
      )}
      </SidebarSelectionHighlight>
    </nav>
  );
}

function RailButton({
  label,
  icon,
  onClick,
  active = false,
  onIntent,
  selectionRef,
}: {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  active?: boolean;
  onIntent?: () => void;
  selectionRef?: RefObject<HTMLButtonElement>;
}) {
  return (
    <Button
      ref={active ? selectionRef : undefined}
      type="button"
      variant={null}
      size="icon"
      aria-label={label}
      aria-current={active ? "page" : undefined}
      title={label}
      onClick={() => onClick()}
      onFocus={onIntent}
      onPointerEnter={onIntent}
      className={cn(
        "host-no-drag h-9 w-9 shrink-0 items-center justify-center rounded-xl",
        active
          ? "bg-sidebar-foreground/[0.08] text-sidebar-foreground dark:bg-white/[0.1]"
          : "text-muted-foreground/80 hover:bg-sidebar-foreground/[0.05] hover:text-sidebar-foreground dark:hover:bg-white/[0.06]",
      )}
    >
      {icon}
    </Button>
  );
}

function SidebarActionButton({
  label,
  icon,
  onClick,
  active = false,
  className,
  shortcut,
  ariaKeyShortcuts,
  selectionRef,
}: {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  active?: boolean;
  className?: string;
  shortcut?: string;
  ariaKeyShortcuts?: string;
  selectionRef?: RefObject<HTMLButtonElement>;
}) {
  const title = shortcut ? `${label} (${shortcut})` : undefined;

  return (
    <Button
      ref={active ? selectionRef : undefined}
      type="button"
      variant={null}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      aria-keyshortcuts={ariaKeyShortcuts}
      title={title}
      onClick={() => onClick()}
      className={cn(
        "touch-target group h-8 w-full min-w-0 justify-start gap-2 overflow-hidden rounded-xl px-3 font-medium text-[12.5px]",
        SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
        active
          ? "text-sidebar-accent-foreground"
          : "text-sidebar-foreground/85 hover:bg-sidebar-foreground/[0.035] hover:text-sidebar-foreground dark:hover:bg-white/[0.05]",
        className,
      )}
    >
      <span className="flex shrink-0 items-center justify-center" aria-hidden>
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate whitespace-nowrap">
        {label}
      </span>
    </Button>
  );
}
