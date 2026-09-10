import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { MarkdownText } from "@/components/MarkdownText";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { fetchScratchpad, saveScratchpad } from "@/lib/api";
import type { ScratchpadPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

type SaveState = "idle" | "saving" | "saved" | "error";

const SAVE_DEBOUNCE_MS = 800;

const EMPTY_SCRATCHPAD: ScratchpadPayload = {
  user_id: "",
  workspace_id: "",
  updated_at: "",
  content: "",
  active_projects: [],
  current_focus: "",
  open_questions: [],
  next_steps: [],
};

const LIST_FIELDS = [
  { key: "active_projects", labelKey: "settings.memory.fieldActiveProjects", defaultLabel: "Active projects" },
  { key: "open_questions", labelKey: "settings.memory.fieldOpenQuestions", defaultLabel: "Open questions" },
  { key: "next_steps", labelKey: "settings.memory.fieldNextSteps", defaultLabel: "Next steps" },
] as const;

export function ScratchpadEditor() {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const { client, token } = useClient();

  const [entry, setEntry] = useState(EMPTY_SCRATCHPAD);
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const draftRef = useRef(EMPTY_SCRATCHPAD);
  draftRef.current = entry;
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await fetchScratchpad(token);
        if (!cancelled && result.scratchpad) setEntry(result.scratchpad);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const flush = useCallback(async () => {
    const next = draftRef.current;
    setSaveState("saving");
    try {
      await saveScratchpad(client, {
        content: next.content,
        activeProjects: next.active_projects,
        currentFocus: next.current_focus,
        openQuestions: next.open_questions,
        nextSteps: next.next_steps,
      });
      setSaveState("saved");
    } catch {
      setSaveState("error");
    }
  }, [client]);

  const patch = useCallback(
    (changes: Partial<ScratchpadPayload>) => {
      setEntry((current) => ({ ...current, ...changes }));
      setSaveState("idle");
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        void flush();
      }, SAVE_DEBOUNCE_MS);
    },
    [flush],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between text-[12.5px] text-muted-foreground">
        <span>{tx("settings.memory.scratchpadHint", "Notes kept alongside the session")}</span>
        <span className="flex items-center gap-2" aria-live="polite">
          {saveState === "saving" && tx("settings.memory.saving", "Saving...")}
          {saveState === "saved" && tx("settings.memory.saved", "Saved")}
          {saveState === "error" && (
            <span className="text-destructive">{tx("settings.memory.saveFailed", "Save failed")}</span>
          )}
          {saveState === "error" && (
            <Button size="sm" variant="outline" onClick={() => void flush()}>
              {tx("settings.memory.retry", "Retry")}
            </Button>
          )}
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Textarea
          value={entry.content}
          onChange={(event) => patch({ content: event.target.value })}
          rows={14}
          className="min-h-[220px] resize-y border-border/45 bg-background font-mono text-[13px] leading-6"
          placeholder={tx("settings.memory.scratchpadPlaceholder", "## Current project\n- ...")}
          aria-label={tx("settings.memory.scratchpadContent", "Content (Markdown)")}
        />
        <div className="max-h-[360px] overflow-auto rounded-panel border border-border/55 bg-settings-surface px-4 py-3 text-[13px]">
          <MarkdownText>{entry.content}</MarkdownText>
        </div>
      </div>

      <div className="grid gap-1.5">
        <span className="block text-[12.5px] font-medium text-muted-foreground">
          {tx("settings.memory.fieldCurrentFocus", "Current focus")}
        </span>
        <Input
          value={entry.current_focus}
          onChange={(event) => patch({ current_focus: event.target.value })}
          className="border-border/45 bg-background"
        />
      </div>

      {LIST_FIELDS.map(({ key, labelKey, defaultLabel }) => (
        <TagEditor
          key={key}
          label={tx(labelKey, defaultLabel)}
          values={entry[key]}
          onChange={(values) => patch({ [key]: values })}
        />
      ))}
    </div>
  );
}

function TagEditor({
  label,
  values,
  onChange,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div>
      <span className="mb-1.5 block text-[12.5px] font-medium text-muted-foreground">{label}</span>
      <div className="flex flex-wrap items-center gap-1.5">
        {values.map((value) => (
          <span
            key={value}
            className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-[12px] text-foreground"
          >
            {value}
            <button
              type="button"
              onClick={() => onChange(values.filter((item) => item !== value))}
              className="text-muted-foreground transition-colors hover:text-destructive"
              aria-label={`Remove ${value}`}
            >
              ×
            </button>
          </span>
        ))}
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || !draft.trim()) return;
            event.preventDefault();
            onChange([...values, draft.trim()]);
            setDraft("");
          }}
          placeholder="+"
          aria-label={`Add ${label}`}
          className="h-7 w-24 rounded-control border border-input bg-background px-2 text-[12px] text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50"
        />
      </div>
    </div>
  );
}
