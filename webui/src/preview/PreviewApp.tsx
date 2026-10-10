import { useCallback, useEffect, useState, Suspense, lazy } from "react";
import { useTranslation } from "react-i18next";
import { MessageSquareMore } from "lucide-react";

import { WorkspaceHome } from "@/components/workspace/WorkspaceHome";
import { WorkspaceNavigation } from "@/components/workspace/WorkspaceNavigation";
import type {
  WorkspaceRoute,
  WorkspaceView,
} from "@/components/workspace/contracts";
import { parseWorkspaceHash, workspaceRouteHash } from "@/workspace/routes";
import { PreviewThread } from "@/preview/PreviewThread";
import { PreviewSettings } from "@/preview/PreviewSettings";
import {
  PreviewCapabilities,
  type CapabilityKind,
} from "@/preview/PreviewCapabilities";
import { previewCapabilityCopy, previewSessions } from "@/preview/fixtures";
import { cn } from "@/lib/utils";

const CreativeWorkspace = lazy(() => import("@/components/creative/CreativeWorkspace"));

/**
 * Fully offline preview shell mounted only when the entry query carries
 * `?preview=1`. It owns no runtime, WebSocket or business fetch: navigation is
 * pure hash state, the chat column and every non-home page render fixture
 * copy, and an explicit "interface preview" badge is always visible.
 */
export default function PreviewApp() {
  const { t } = useTranslation();
  const [route, setRoute] = useState<WorkspaceRoute>(() =>
    parseWorkspaceHash(window.location.hash),
  );
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const sync = () => setRoute(parseWorkspaceHash(window.location.hash));
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const navigate = useCallback((next: WorkspaceRoute) => {
    const hash = workspaceRouteHash(next);
    setRoute(next);
    // Preserve the `?preview=1` query by assigning only the hash fragment.
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    }
  }, []);

  const openView = useCallback(
    (view: WorkspaceView) => navigate({ view }),
    [navigate],
  );

  return (
    <div className="flex h-[100dvh] w-full overflow-hidden bg-background text-foreground">
      <div
        className={cn(
          "shrink-0 border-r border-sidebar-border",
          collapsed ? "w-16" : "w-[224px]",
        )}
      >
        <WorkspaceNavigation
          activeView={route.view}
          collapsed={collapsed}
          preview
          onNavigate={navigate}
          onToggleCollapsed={() => setCollapsed((value) => !value)}
          connectionStatus={
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <span
                aria-hidden
                className="h-2 w-2 rounded-full bg-amber-500"
              />
              {t("workspace.preview.badge", {
                defaultValue: "Interface preview · sample content",
              })}
            </span>
          }
          chatNavigation={<PreviewChatColumn />}
        />
      </div>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {route.view === "home" ? (
          <WorkspaceHome
            preview
            onSubmitTask={() => openView("chat")}
            onOpenAssistant={() => openView("chat")}
            onOpenCreative={() => openView("creative")}
            onOpenCapability={openView}
          />
        ) : route.view === "chat" ? (
          <PreviewThread />
        ) : route.view === "settings" ? (
          <PreviewSettings />
        ) : route.view === "apps" ||
          route.view === "agents" ||
          route.view === "skills" ||
          route.view === "automations" ? (
          <PreviewCapabilities kind={route.view as CapabilityKind} />
        ) : route.view === "creative" || route.view === "article" ? (
          <Suspense fallback={null}>
            <CreativeWorkspace />
          </Suspense>
        ) : (
          <PlaceholderPage view={route.view} />
        )}
      </main>
    </div>
  );
}

function PreviewChatColumn() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 px-3 pt-3 pb-1">
        <span className="min-w-0 flex-1 truncate text-[15px] font-semibold text-sidebar-foreground">
          {t("sidebar.recent")}
        </span>
      </div>
      <div className="flex flex-col gap-0.5 px-2 py-1">
        {previewSessions.map((row) => (
          <div
            key={row.key}
            aria-current="false"
            className="flex flex-col gap-0.5 rounded-xl px-3 py-2 text-sidebar-foreground/85"
          >
            <span className="truncate text-[13px] font-medium">{row.title}</span>
            <span className="truncate text-xs text-muted-foreground/80">
              {row.preview}
            </span>
          </div>
        ))}
      </div>
      <div className="min-h-0 flex-1" />
    </div>
  );
}

function PlaceholderPage({ view }: { view: WorkspaceView }) {
  const { t } = useTranslation();
  const copyKey =
    view === "chat"
      ? "chat"
      : view === "creative" || view === "article"
        ? "creative"
        : view === "agents" || view === "skills" || view === "automations" || view === "apps"
          ? view
          : "settings";
  const copy = previewCapabilityCopy[copyKey];
  return (
    <div className="flex h-full w-full items-center justify-center overflow-y-auto bg-background px-6 py-10">
      <div className="flex w-full max-w-[560px] flex-col items-center gap-3 rounded-prominent border border-border/70 bg-settings-surface p-8 text-center shadow-sm">
        <span className="flex h-11 w-11 items-center justify-center rounded-mark bg-primary/10 text-primary">
          <MessageSquareMore className="h-5 w-5" aria-hidden />
        </span>
        <h2 className="text-[17px] font-semibold text-foreground">{copy.title}</h2>
        <p className="text-sm leading-relaxed text-muted-foreground">{copy.body}</p>
        <p className="mt-1 rounded-mark bg-amber-500/15 px-2.5 py-1 text-xs text-amber-700 dark:text-amber-300">
          {t("workspace.preview.badge", {
            defaultValue: "Interface preview · sample content",
          })}
        </p>
      </div>
    </div>
  );
}
