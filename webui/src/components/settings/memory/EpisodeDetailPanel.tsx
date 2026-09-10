import { useTranslation } from "react-i18next";

import type { EpisodePayload } from "@/lib/types";

interface EpisodeDetailPanelProps {
  episode: EpisodePayload;
  onClose: () => void;
}

export function EpisodeDetailPanel({ episode, onClose }: EpisodeDetailPanelProps) {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const fields: Array<[string, string]> = [
    ["goal", episode.goal],
    ["outcome", episode.outcome],
    ["entities", episode.entities.join(", ")],
    ["tools_used", episode.tools_used.join(", ")],
  ];

  return (
    <div className="mt-3 rounded-control border border-border/55 bg-muted/22 px-3.5 py-3 text-[13px]">
      <dl className="grid grid-cols-[88px_1fr] gap-x-3 gap-y-1.5">
        {fields.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-[12px] font-medium text-muted-foreground">{label}</dt>
            <dd className="min-w-0 break-words text-foreground">{value || "—"}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-3.5 border-t border-border/45 pt-3">
        <div className="text-[12px] font-medium text-muted-foreground">
          {tx("settings.memory.actionChain", "Tool call chain")}
        </div>
        {episode.action_nodes.length === 0 ? (
          <div className="mt-1.5 text-[12px] text-muted-foreground">
            {tx("settings.memory.noActions", "No tool calls")}
          </div>
        ) : (
          <ol className="mt-1.5 flex flex-col gap-1">
            {episode.action_nodes.map((node, index) => (
              <li key={index} className="flex items-baseline gap-2 text-[12px] text-foreground">
                <span className="tabular-nums text-muted-foreground">{index + 1}</span>
                <span className="font-medium">
                  {typeof node.tool === "string" ? node.tool : "?"}
                </span>
                <span className="min-w-0 truncate text-muted-foreground">
                  {JSON.stringify(node.input ?? "").slice(0, 60)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>

      {episode.linked_memory_ids.length > 0 && (
        <div className="mt-3 text-[12px] text-muted-foreground">
          {t("settings.memory.linkedMemories", {
            defaultValue: "{{count}} linked memories",
            count: episode.linked_memory_ids.length,
          })}
        </div>
      )}

      <div className="mt-3.5 flex justify-end">
        <button
          type="button"
          onClick={onClose}
          className="text-[12.5px] text-primary underline-offset-2 hover:underline"
        >
          {tx("settings.memory.close", "Close")}
        </button>
      </div>
    </div>
  );
}
