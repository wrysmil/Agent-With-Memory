import { useMemo } from "react";
import { AlertTriangle, Hand, Folder } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ComposerContextUsage } from "@/components/thread/ComposerUsagePopover";
import type { WorkspaceScopePayload } from "@/lib/types";
import { projectNameFromPath } from "@/lib/workspace";
import { cn } from "@/lib/utils";

interface ThreadStatusBarProps {
  workspaceScope?: WorkspaceScopePayload | null;
  contextUsage?: ComposerContextUsage | null;
  className?: string;
}

/**
 * Read-only per-pane footer surfacing the session's workspace, access mode and
 * context usage — the Codex/Qoder bottom status bar. Git branch/remote is a
 * v2 backend concern and intentionally omitted here.
 */
export function ThreadStatusBar({
  workspaceScope,
  contextUsage,
  className,
}: ThreadStatusBarProps) {
  const { t } = useTranslation();
  const projectName = workspaceScope
    ? workspaceScope.project_name || projectNameFromPath(workspaceScope.project_path)
    : null;
  const isFullAccess = workspaceScope?.access_mode === "full";
  const accessLabel = isFullAccess
    ? t("thread.composer.workspace.fullShort", { defaultValue: "Full" })
    : t("thread.composer.workspace.defaultShort", { defaultValue: "Default" });

  const contextPercentage = useMemo(() => {
    if (!contextUsage) return null;
    const { contextTokens, contextWindowTokens } = contextUsage;
    if (
      !Number.isFinite(contextTokens)
      || contextTokens < 0
      || !Number.isFinite(contextWindowTokens)
      || (contextWindowTokens ?? 0) <= 0
    ) {
      return null;
    }
    return Math.min(100, Math.round((contextTokens / contextWindowTokens!) * 100));
  }, [contextUsage]);

  if (!projectName && contextPercentage === null) return null;

  const meterStatus = contextPercentage === null
    ? "normal"
    : contextPercentage >= 90
      ? "critical"
      : contextPercentage >= 75
        ? "caution"
        : "normal";

  return (
    <div
      data-testid="thread-status-bar"
      className={cn(
        "flex h-7 shrink-0 items-center gap-3 border-t border-border/40 bg-background px-3 text-[11.5px] leading-none text-muted-foreground",
        className,
      )}
    >
      {projectName ? (
        <span
          className="inline-flex min-w-0 max-w-[16rem] items-center gap-1.5"
          title={workspaceScope?.project_path}
        >
          <Folder className="h-3 w-3 shrink-0" aria-hidden />
          <span className="truncate font-medium text-muted-foreground/90">{projectName}</span>
        </span>
      ) : null}
      {workspaceScope ? (
        <span
          className="inline-flex shrink-0 items-center gap-1"
          title={t("thread.composer.workspace.accessAria", {
            defaultValue: "Workspace access mode",
          })}
        >
          {isFullAccess ? (
            <AlertTriangle className="h-3 w-3 shrink-0 text-orange-600 dark:text-orange-300" aria-hidden />
          ) : (
            <Hand className="h-3 w-3 shrink-0" aria-hidden />
          )}
          <span
            className={cn(
              "font-medium",
              isFullAccess && "text-orange-600 dark:text-orange-300",
            )}
          >
            {accessLabel}
          </span>
        </span>
      ) : null}
      {contextPercentage !== null ? (
        <span
          className="ml-auto inline-flex shrink-0 items-center gap-1.5"
          title={t("thread.composer.context.tooltip", {
            defaultValue: "Context {{percent}}%",
            percent: contextPercentage,
          })}
        >
          <span className="h-1 w-14 overflow-hidden rounded-full bg-muted">
            <span
              className={cn(
                "block h-full rounded-full transition-[width]",
                meterStatus === "critical"
                  ? "bg-destructive"
                  : meterStatus === "caution"
                    ? "bg-amber-500"
                    : "bg-foreground/45",
              )}
              style={{ width: `${contextPercentage}%` }}
            />
          </span>
          <span className="tabular-nums text-muted-foreground/85">{contextPercentage}%</span>
        </span>
      ) : (
        <span className="ml-auto" aria-hidden />
      )}
    </div>
  );
}
