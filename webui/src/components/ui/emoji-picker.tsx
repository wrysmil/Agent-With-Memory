import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Search, Smile } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { EMOJI_GROUP_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

const EMOJI_GROUPS: Array<{ id: string; label: string; items: string[] }> = [
  {
    id: "agents",
    label: "智能体",
    items: ["🦊", "🤖", "🧠", "🧭", "🔮", "👾", "🐙", "🦉", "🐝", "🦋", "🎯", "🧩"],
  },
  {
    id: "roles",
    label: "角色",
    items: ["🕵️", "👩‍💻", "👨‍🍳", "🧑‍🏫", "👩‍⚕️", "👷", "🧑‍🚀", "🧙", "🃏", "⚖️", "💼", "📚"],
  },
  {
    id: "work",
    label: "工作",
    items: ["📊", "📈", "📉", "🗂", "📝", "📮", "🗓", "⏰", "🔍", "🛠", "⚙️", "🧪"],
  },
  {
    id: "nature",
    label: "自然",
    items: ["🌱", "🌳", "🌊", "🔥", "⭐", "🌙", "☁️", "⛰", "🌋", "❄️", "🌈", "🍃"],
  },
  {
    id: "objects",
    label: "物品",
    items: ["💡", "🔑", "🧭", "🛡", "⚡", "🎯", "🧲", "💎", "🕯", "🪄", "🔭", "📡"],
  },
  {
    id: "symbols",
    label: "符号",
    items: ["✨", "✅", "❌", "⚠️", "❓", "💡", "🔁", "⏩", "🔗", "🎵", "💬", "🧿"],
  },
  {
    id: "food",
    label: "食物",
    items: ["🍎", "🍊", "🍋", "🍇", "🍓", "🍒", "🥑", "🍕", "🍜", "🍣", "🍰", "☕"],
  },
  {
    id: "activity",
    label: "活动",
    items: ["🎉", "🎈", "🎁", "🏆", "🎵", "🎬", "🎮", "🎲", "🏃", "🧘", "🎤", "🥇"],
  },
];
const ALL_EMOJI = EMOJI_GROUPS.flatMap((group) => group.items);

export function EmojiPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (emoji: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return EMOJI_GROUPS;
    return EMOJI_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter((item) => group.label.includes(needle) || item.includes(needle)),
    })).filter((group) => group.items.length > 0);
  }, [query]);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={t("settings.agents.icon.ariaLabel", "选择图标")}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control border border-border bg-background text-[18px] transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          {value || <Smile className="h-4 w-4 text-muted-foreground" aria-hidden />}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[19rem] p-3">
        <div className="relative mb-2">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("settings.agents.icon.search", "搜索图标")}
            aria-label={t("settings.agents.icon.search", "搜索图标")}
            className="h-8 pl-8 text-[13px]"
          />
        </div>
        {groups.length === 0 ? (
          <p className="px-1 py-6 text-center text-[12px] text-muted-foreground">
            {t("settings.agents.icon.empty", "没有匹配的图标")}
          </p>
        ) : (
          <div className="max-h-64 space-y-2 overflow-y-auto scrollbar-thin">
            {groups.map((group) => (
              <div key={group.id}>
                <p className="mb-1 px-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {t(EMOJI_GROUP_LABEL_KEY[group.id], group.label)}
                </p>
                <div className="grid grid-cols-8 gap-0.5">
                  {group.items.map((emoji) => {
                    const active = emoji === value;
                    return (
                      <button
                        key={emoji}
                        type="button"
                        aria-pressed={active}
                        aria-label={emoji}
                        onClick={() => {
                          onChange(emoji);
                          setOpen(false);
                        }}
                        className={cn(
                          "flex h-7 w-7 items-center justify-center rounded-md text-[15px] transition-colors hover:bg-muted",
                          active && "bg-muted ring-1 ring-ring",
                        )}
                      >
                        {active ? <Check className="sr-only" aria-hidden /> : null}
                        {emoji}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

export { ALL_EMOJI };
