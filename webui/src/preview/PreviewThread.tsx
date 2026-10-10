import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Send } from "lucide-react";

import { MessageBubble } from "@/components/MessageBubble";
import { ThreadFrame } from "@/components/thread/ThreadFrame";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { UIMessage } from "@/lib/types";

const DEMO_MESSAGES: UIMessage[] = [
  {
    id: "demo-user-1",
    role: "user",
    content: "帮我把这周的记忆整理成一篇周回顾。",
    createdAt: 1,
  },
  {
    id: "demo-assistant-1",
    role: "assistant",
    content:
      "好的，我先按主题聚合本周记忆，再给出正文结构：\n\n## 本周回顾\n\n- **进展**：完成导航重构与工作台首页\n- **待办**：聊天与设置页样式统一\n\n```ts\nconst outline = draftWeeklyReview({ scope: 'this_week' });\n```\n\n（界面预览：以上为固定示例内容，不会连接模型或后端。）",
    createdAt: 2,
  },
];

const DEMO_REPLY =
  "（演示回复）已根据示例素材生成内容。界面预览模式下不会真正调用模型，也不会发送任何网络请求。";

/**
 * Offline chat surface for `?preview=1`. It reuses the shared `ThreadFrame`
 * skeleton and the real `MessageBubble` for faithful markdown/tool/reasoning
 * rendering, but keeps conversation state purely in memory: sending appends the
 * user turn plus a fixed demo reply. It deliberately does NOT mount ThreadShell
 * or any streaming/history hook.
 */
export function PreviewThread() {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<UIMessage[]>(DEMO_MESSAGES);
  const [value, setValue] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const send = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed) return;
    const now = Date.now();
    setMessages((current) => [
      ...current,
      { id: `demo-user-${now}`, role: "user", content: trimmed, createdAt: now },
      {
        id: `demo-assistant-${now}`,
        role: "assistant",
        content: DEMO_REPLY,
        createdAt: now + 1,
        turnPhase: "answer",
      },
    ]);
    setValue("");
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    });
  }, [value]);

  const header = useMemo(
    () => (
      <div className="flex h-14 items-center gap-2 px-4">
        <h1 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-foreground">
          {t("workspace.nav.assistant", { defaultValue: "助手" })}
        </h1>
        <span className="rounded-mark bg-amber-500/15 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-300">
          {t("workspace.preview.badge", { defaultValue: "界面预览 · 示例内容" })}
        </span>
      </div>
    ),
    [t],
  );

  const composer = (
    <div className="border-t border-border/70 bg-background px-4 py-3">
      <div
        className={cn(
          "mx-auto flex w-full max-w-[800px] items-end gap-2 rounded-panel",
          "border border-border/70 bg-settings-surface p-2 shadow-sm",
        )}
      >
        <textarea
          data-testid="preview-input"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              send();
            }
          }}
          rows={1}
          aria-label={t("workspace.home.taskAria", { defaultValue: "自由任务" })}
          placeholder={t("workspace.preview.composerPlaceholder", {
            defaultValue: "输入内容试看样式（预览不会发送）",
          })}
          className="max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent px-2 py-1.5 text-[15px] text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
        />
        <Button
          type="button"
          onClick={send}
          disabled={!value.trim()}
          data-testid="preview-send"
          aria-label={t("thread.composer.sendAria", { defaultValue: "Send message" })}
          className="h-9 shrink-0 gap-2 rounded-control bg-primary px-4 text-primary-foreground"
        >
          <Send className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </div>
  );

  return (
    <ThreadFrame header={header} composer={composer}>
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-6"
      >
        <div className="mx-auto flex w-full max-w-[800px] flex-col gap-5">
          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              showCopyAction={false}
            />
          ))}
        </div>
      </div>
    </ThreadFrame>
  );
}
