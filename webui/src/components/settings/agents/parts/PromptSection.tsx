import { useEffect, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { Maximize2, Minimize2 } from "lucide-react";

import { Textarea } from "@/components/ui/textarea";
import type { AgentProfile } from "@/lib/agents/types";
import { PROMPT_MAX_LENGTH } from "@/lib/agents/types";
import { cn } from "@/lib/utils";

type PromptProps = {
  profile: AgentProfile;
  onChange: (prompt: string) => void;
};

const PILL_CLASS =
  "inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-full bg-muted/65 px-3 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/**
 * 提示词输入框。高度随内容自动增长（把 `scrollHeight` 反写回 `style.height`），
 * 到 `maxHeightPx` 封顶后转为内部滚动 —— 长提示词不必顶着一块小框看。
 *
 * 刻意是纯 markdown 文本域：变量直接手写 `{{name}}` 即可，预览那侧会替换。
 * 之前那套「插入变量」下拉把简单的一件事做成了两步，反而挡手。
 */
function PromptTextarea({
  profile,
  onChange,
  textareaRef,
  maxHeightPx,
}: PromptProps & {
  textareaRef: RefObject<HTMLTextAreaElement>;
  maxHeightPx: number;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    // 先归零再量，否则删字后高度只增不减。
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [profile.prompt, textareaRef]);

  return (
    <Textarea
      ref={textareaRef}
      value={profile.prompt}
      onChange={(event) => onChange(event.target.value)}
      placeholder={t(
        "settings.agents.prompt.placeholder",
        "用 markdown 写这个 Agent 的职责、边界与输出风格。",
      )}
      aria-label={t("settings.agents.prompt.label", "系统提示词")}
      spellCheck={false}
      style={{ maxHeight: `${maxHeightPx}px` }}
      className="min-h-[13rem] resize-none overflow-y-auto font-mono text-[13px] leading-relaxed scrollbar-thin"
    />
  );
}

function LengthCounter({ profile }: { profile: AgentProfile }) {
  const over = profile.prompt.length > PROMPT_MAX_LENGTH;
  return (
    <span
      className={cn(
        "shrink-0 text-[12px] tabular-nums",
        over ? "font-medium text-destructive" : "text-muted-foreground",
      )}
    >
      {profile.prompt.length} / {PROMPT_MAX_LENGTH}
    </span>
  );
}

export function PromptSection({ profile, onChange }: PromptProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 展开态下不保留行内那个输入框：两个 textarea 共用一个 ref 会互相抢，
  // 变量就不知道该插进哪一个了。
  if (expanded) {
    return (
      <ExpandedPromptEditor
        profile={profile}
        onChange={onChange}
        textareaRef={textareaRef}
        onClose={() => setExpanded(false)}
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className={PILL_CLASS}
        >
          <Maximize2 className="h-3.5 w-3.5" aria-hidden />
          {t("settings.agents.prompt.expand", "展开编辑")}
        </button>
        <div className="ml-auto">
          <LengthCounter profile={profile} />
        </div>
      </div>

      <PromptTextarea
        profile={profile}
        onChange={onChange}
        textareaRef={textareaRef}
        maxHeightPx={448}
      />
    </div>
  );
}

/**
 * 全屏专注编辑。提示词是这个 Agent 里最长的一段文本，挤在弹窗左列的一小块里
 * 没法写；这里让它独占视口，写完退回原处。
 */
function ExpandedPromptEditor({
  profile,
  onChange,
  textareaRef,
  onClose,
}: PromptProps & {
  textareaRef: RefObject<HTMLTextAreaElement>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, [textareaRef]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("settings.agents.prompt.expandedTitle", "编辑提示词")}
      className="fixed inset-0 z-[60] flex flex-col bg-background"
    >
      <div className="flex shrink-0 items-center gap-3 border-b border-border/60 px-5 py-3">
        <h3 className="text-[13px] font-medium text-foreground">
          {t("settings.agents.prompt.expandedTitle", "编辑提示词")}
        </h3>
        <div className="ml-auto">
          <LengthCounter profile={profile} />
        </div>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          className={PILL_CLASS}
        >
          <Minimize2 className="h-3.5 w-3.5" aria-hidden />
          {t("settings.agents.prompt.collapse", "完成")}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <PromptTextarea
          profile={profile}
          onChange={onChange}
          textareaRef={textareaRef}
          maxHeightPx={Number.MAX_SAFE_INTEGER}
        />
      </div>
    </div>
  );
}
