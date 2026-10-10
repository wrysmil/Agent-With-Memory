import { type ReactNode, useState } from "react";
import {
  Blocks,
  Bot,
  Brain,
  FilePlus2,
  FolderInput,
  ListChecks,
  Palette,
  Plug,
  Plus,
  Target,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { AgentProfile } from "@/lib/agents/types";
import type { CliAppInfo, McpPresetInfo, SkillSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ComposerPlusMenuProps {
  isHero: boolean;
  /** Trigger disabled (composer not interactive). */
  disabled?: boolean;
  /** "Files and folders" disabled (attachment limit reached). */
  filesDisabled?: boolean;
  skills: SkillSummary[];
  plugins: CliAppInfo[];
  mcpPresets: McpPresetInfo[];
  agents: AgentProfile[];
  canWorkInProject: boolean;
  onPickFiles: () => void;
  onWorkInProject: () => void;
  onInsertToken: (token: string) => void;
}

export function ComposerPlusMenu({
  isHero,
  disabled = false,
  filesDisabled = false,
  skills,
  plugins,
  mcpPresets,
  agents,
  canWorkInProject,
  onPickFiles,
  onWorkInProject,
  onInsertToken,
}: ComposerPlusMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const run = (action: () => void) => {
    setOpen(false);
    action();
  };

  const visibleSkills = skills.filter(
    (skill) => skill.enabled !== false && skill.available,
  );
  const installedPlugins = plugins.filter((app) => app.installed);
  const installedMcp = mcpPresets.filter(
    (preset) => preset.installed && preset.configured,
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          disabled={disabled}
          aria-label={t("thread.composer.menu.aria")}
          aria-haspopup="menu"
          aria-expanded={open}
          className={cn(
            "thread-composer-action touch-target rounded-full text-muted-foreground hover:text-foreground",
            isHero
              ? "h-8 w-8 border border-border/55 bg-card shadow-[0_2px_8px_rgba(15,23,42,0.05)] hover:bg-card"
              : "h-9 w-9 border border-border/55 bg-card shadow-[0_2px_8px_rgba(15,23,42,0.05)] hover:bg-card",
            open && "bg-card text-foreground",
          )}
        >
          <Plus className={cn(isHero ? "h-[18px] w-[18px]" : "h-4 w-4")} />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-[21rem] max-w-[calc(100vw-2rem)] rounded-prominent border-border/70 bg-popover p-1.5 shadow-[0_12px_36px_rgba(15,23,42,0.14)]"
      >
        <MenuSection label={t("thread.composer.menu.add")}>
          <MenuItem
            icon={<FilePlus2 className="h-4 w-4" />}
            title={t("thread.composer.menu.files")}
            description={t("thread.composer.menu.filesDesc")}
            disabled={filesDisabled}
            onClick={() => run(onPickFiles)}
          />
          {canWorkInProject ? (
            <MenuItem
              icon={<FolderInput className="h-4 w-4" />}
              title={t("thread.composer.menu.project")}
              description={t("thread.composer.menu.projectDesc")}
              onClick={() => run(onWorkInProject)}
            />
          ) : null}
          <MenuItem
            icon={<Target className="h-4 w-4" />}
            title={t("thread.composer.menu.goal")}
            description={t("thread.composer.menu.goalDesc")}
            onClick={() => run(() => onInsertToken("/goal"))}
          />
          <MenuItem
            icon={<ListChecks className="h-4 w-4" />}
            title={t("thread.composer.menu.plan")}
            description={t("thread.composer.menu.planDesc")}
            onClick={() => run(() => onInsertToken("/plan"))}
          />
          <MenuItem
            icon={<Palette className="h-4 w-4" />}
            title={t("thread.composer.menu.draw")}
            description={t("thread.composer.menu.drawDesc")}
            onClick={() => run(() => onInsertToken("$image-generation"))}
          />
        </MenuSection>
        <MenuSection
          label={t("thread.composer.menu.skills")}
          emptyLabel={t("thread.composer.menu.noSkills")}
          empty={visibleSkills.length === 0}
        >
          {visibleSkills.map((skill) => (
            <MenuItem
              key={`skill:${skill.name}`}
              icon={<Brain className="h-4 w-4" />}
              title={skill.name}
              description={skill.description || undefined}
              onClick={() => run(() => onInsertToken(`$${skill.name}`))}
            />
          ))}
        </MenuSection>
        <MenuSection
          label={t("thread.composer.menu.plugins")}
          emptyLabel={t("thread.composer.menu.noPlugins")}
          empty={installedPlugins.length === 0}
        >
          {installedPlugins.map((app) => (
            <MenuItem
              key={`plugin:${app.name}`}
              icon={<Blocks className="h-4 w-4" />}
              title={app.display_name || app.name}
              description={app.description || undefined}
              onClick={() => run(() => onInsertToken(`@${app.name}`))}
            />
          ))}
        </MenuSection>
        <MenuSection
          label={t("thread.composer.menu.mcp")}
          emptyLabel={t("thread.composer.menu.noMcp")}
          empty={installedMcp.length === 0}
        >
          {installedMcp.map((preset) => (
            <MenuItem
              key={`mcp:${preset.name}`}
              icon={<Plug className="h-4 w-4" />}
              title={preset.display_name || preset.name}
              description={preset.description || undefined}
              onClick={() => run(() => onInsertToken(`@${preset.name}`))}
            />
          ))}
        </MenuSection>
        <MenuSection
          label={t("thread.composer.menu.agents")}
          emptyLabel={t("thread.composer.menu.noAgents")}
          empty={agents.length === 0}
        >
          {agents.map((agent) => (
            <MenuItem
              key={`agent:${agent.id}`}
              icon={<Bot className="h-4 w-4" />}
              title={agent.name}
              description={agent.description || undefined}
              onClick={() =>
                run(() =>
                  onInsertToken(
                    t("thread.composer.menu.agentInsert", {
                      name: agent.name,
                      defaultValue: "Run this with the agent \"{{name}}\": ",
                    }),
                  ),
                )}
            />
          ))}
        </MenuSection>
      </PopoverContent>
    </Popover>
  );
}

function MenuSection({
  label,
  children,
  empty = false,
  emptyLabel,
}: {
  label: string;
  children: ReactNode;
  empty?: boolean;
  emptyLabel?: string;
}) {
  return (
    <div className="pb-1 last:pb-0">
      <div className="px-2 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground/70">
        {label}
      </div>
      {empty ? (
        <div className="px-2 py-1 text-[12px] text-muted-foreground/65">
          {emptyLabel}
        </div>
      ) : (
        <div className="space-y-0.5">{children}</div>
      )}
    </div>
  );
}

function MenuItem({
  icon,
  title,
  description,
  disabled = false,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  description?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex w-full min-w-0 items-start gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        disabled
          ? "cursor-not-allowed opacity-50"
          : "hover:bg-muted/70 dark:hover:bg-white/[0.06]",
      )}
    >
      <span className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden>
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium text-foreground">
          {title}
        </span>
        {description ? (
          <span className="mt-0.5 line-clamp-2 block text-[11.5px] leading-snug text-muted-foreground">
            {description}
          </span>
        ) : null}
      </span>
    </button>
  );
}
