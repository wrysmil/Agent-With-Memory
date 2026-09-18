import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, FileText, GitCompare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useClient } from "@/providers/ClientProvider";

export type MemoryMdStats = {
  last_refresh_at: string | null;
  last_refresh_trigger: "manual" | "auto" | null;
  draft_exists: boolean;
  draft_age_seconds: number | null;
  current_chars: number;
  max_chars: number;
};

export type MemoryMdContent = {
  memory_md: string | null;
  draft: string | null;
  memory_md_exists: boolean;
  draft_exists: boolean;
};

export type MemoryMdCardProps = {
  onViewMemoryMd: () => void;
  onViewDraftDiff: () => void;
};

const DISMISSED_KEY = "memory_draft_dismissed";

function formatRelativeTime(isoString: string | null): string {
  if (!isoString) return "—";
  const diffMs = Date.now() - new Date(isoString).getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec} 秒前`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHr = Math.floor(diffMin / 60);
  return `${diffHr} 小时前`;
}

function formatDraftAge(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  const min = Math.floor(seconds / 60);
  if (min < 60) return `${min} 分钟`;
  const hr = Math.floor(min / 60);
  return `${hr} 小时`;
}

export function MemoryMdCard({ onViewMemoryMd, onViewDraftDiff }: MemoryMdCardProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const [stats, setStats] = useState<MemoryMdStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [draftDismissed, setDraftDismissed] = useState(
    () => localStorage.getItem(DISMISSED_KEY) === "1",
  );

  async function fetchStats() {
    try {
      const result = await client.requestMutation<{ memory_md: MemoryMdStats }>(
        "memory-stats",
        {},
      );
      setStats(result.memory_md);
    } catch {
      // stats are non-critical; leave as null
    }
  }

  useEffect(() => {
    const interval = setInterval(fetchStats, 30_000);
    void fetchStats();
    return () => clearInterval(interval);
  }, []);

  async function handleRefresh() {
    setLoading(true);
    try {
      await client.requestMutation("memory-refresh-md", {}, 30_000);
      await fetchStats();
    } catch {
      // non-critical
    } finally {
      setLoading(false);
    }
  }

  function handleDismissDraft() {
    localStorage.setItem(DISMISSED_KEY, "1");
    setDraftDismissed(true);
  }

  return (
    <div className="rounded-lg border border-border/60 bg-card">
      <div className="px-4 pt-4">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-muted-foreground" />
          <span className="font-medium text-sm">{t("settings.memory.mdCardTitle")}</span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("settings.memory.mdCardDescription")}
        </p>
      </div>

      {stats && (
        <div className="mt-3 mx-4 grid grid-cols-2 gap-x-4 gap-y-1 rounded border border-border/40 bg-muted/30 px-3 py-2 text-xs">
          <span className="text-muted-foreground">
            {t("settings.memory.mdStatsLastRefresh", {
              when: formatRelativeTime(stats.last_refresh_at),
            })}
          </span>
          <span className="text-muted-foreground">
            {t("settings.memory.mdStatsSize", {
              current: stats.current_chars.toLocaleString(),
              max: stats.max_chars.toLocaleString(),
            })}
          </span>
          <span className="text-muted-foreground">
            {stats.last_refresh_trigger === "manual"
              ? t("settings.memory.mdStatsTriggerManual")
              : t("settings.memory.mdStatsTriggerAuto")}
          </span>
          <span className="text-muted-foreground">
            {t("settings.memory.mdStatsCurrentChars", {
              count: stats.current_chars.toLocaleString(),
            })}
          </span>
        </div>
      )}

      {stats?.draft_exists && !draftDismissed && (
        <div className="mx-4 mt-3 flex items-center justify-between rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs dark:border-amber-800 dark:bg-amber-950">
          <span className="text-amber-700 dark:text-amber-400">
            {t("settings.memory.mdDraftNotice", {
              age: formatDraftAge(stats.draft_age_seconds ?? 0),
            })}
          </span>
          <button
            type="button"
            onClick={handleDismissDraft}
            className="ml-2 shrink-0 text-muted-foreground hover:text-foreground"
            aria-label={t("common.dismiss", "Dismiss")}
          >
            ×
          </button>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 px-4 pb-4">
        <Button
          size="sm"
          variant="outline"
          onClick={handleRefresh}
          disabled={loading}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""} mr-1.5`} />
          {loading
            ? t("settings.memory.mdButtonRefreshing")
            : t("settings.memory.mdButtonRefresh")}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onViewMemoryMd}
        >
          <FileText className="h-3.5 w-3.5 mr-1.5" />
          {t("settings.memory.mdButtonView", "查看 MEMORY.md")}
        </Button>
        {stats?.draft_exists && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onViewDraftDiff}
          >
            <GitCompare className="h-3.5 w-3.5 mr-1.5" />
            {t("settings.memory.mdButtonDiff", "对比草稿")}
          </Button>
        )}
      </div>
    </div>
  );
}
