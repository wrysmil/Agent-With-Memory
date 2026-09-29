import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { Braces, Variable } from "lucide-react";

import { Textarea } from "@/components/ui/textarea";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AgentProfile } from "@/lib/agents/types";
import { PROMPT_MAX_LENGTH, PROMPT_VARIABLES } from "@/lib/agents/types";
import { PROMPT_VARIABLE_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

export function PromptSection({
  profile,
  onChange,
}: {
  profile: AgentProfile;
  onChange: (prompt: string) => void;
}) {
  const { t } = useTranslation();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const over = profile.prompt.length > PROMPT_MAX_LENGTH;

  const insertVariable = (token: string) => {
    const el = textareaRef.current;
    if (!el) {
      onChange(`${profile.prompt}${token}`);
      return;
    }
    const start = el.selectionStart ?? profile.prompt.length;
    const end = el.selectionEnd ?? start;
    const next = `${profile.prompt.slice(0, start)}${token}${profile.prompt.slice(end)}`;
    onChange(next);
    requestAnimationFrame(() => {
      el.focus();
      const caret = start + token.length;
      el.setSelectionRange(caret, caret);
    });
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="inline-flex h-8 items-center gap-1.5 rounded-full bg-muted/65 px-3 text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <Braces className="h-3.5 w-3.5" aria-hidden />
              {t("settings.agents.prompt.insertVariable", "插入变量")}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64">
            <DropdownMenuLabel className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {t("settings.agents.prompt.variables", "可用变量")}
            </DropdownMenuLabel>
            {PROMPT_VARIABLES.map((variable) => (
              <DropdownMenuItem
                key={variable.token}
                onSelect={() => insertVariable(variable.token)}
                className="flex items-center gap-2 text-[13px]"
              >
                <Variable className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                <code className="shrink-0 text-[11px] text-foreground/80">{variable.token}</code>
                <span className="ml-auto truncate text-[11px] text-muted-foreground">
                  {t(PROMPT_VARIABLE_LABEL_KEY[variable.token], variable.label)}
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <span
          className={cn(
            "ml-auto text-[12px]",
            over ? "font-medium text-destructive" : "text-muted-foreground",
          )}
        >
          {profile.prompt.length} / {PROMPT_MAX_LENGTH}
        </span>
      </div>

      <Textarea
        ref={textareaRef}
        value={profile.prompt}
        onChange={(event) => onChange(event.target.value)}
        placeholder={t(
          "settings.agents.prompt.placeholder",
          "描述这个 Agent 的职责、边界与输出风格。可使用 {{name}} 等变量，最终拼装结果见右侧预览。",
        )}
        aria-label={t("settings.agents.prompt.label", "系统提示词")}
        className="min-h-[9rem] text-[13px] leading-relaxed"
      />
    </div>
  );
}