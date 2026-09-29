import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { CircleAlert, RotateCcw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { SectionLabel } from "@/components/settings/agents/shared";
import { BasicsSection } from "@/components/settings/agents/parts/BasicsSection";
import {
  CapabilityPicker,
  type CapabilityItem,
} from "@/components/settings/agents/parts/CapabilityPicker";
import { ModelPicker } from "@/components/settings/agents/parts/ModelPicker";
import { PromptSection } from "@/components/settings/agents/parts/PromptSection";
import { RelationsSection } from "@/components/settings/agents/parts/RelationsSection";
import { AgentPreviewRail } from "@/components/settings/agents/parts/AgentPreviewRail";
import type {
  AgentCategory,
  AgentProfile,
  ModelDescriptor,
  PromptContext,
  SkillDescriptor,
  ToolDescriptor,
} from "@/lib/agents/types";
import { PROMPT_MAX_LENGTH, TOOL_CATEGORIES, resolveSelection } from "@/lib/agents/types";
import { SKILL_SOURCE_LABEL_KEY, TOOL_CATEGORY_LABEL_KEY } from "@/lib/agents/i18n";
import { cn } from "@/lib/utils";

export interface AgentCatalog {
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  categories: AgentCategory[];
}

export function createBlankAgent(): AgentProfile {
  return {
    id: "",
    name: "",
    description: "",
    type: "custom",
    customized: false,
    categoryId: null,
    icon: "🤖",
    color: "#4A90D9",
    prompt: "",
    modelId: null,
    tools: { mode: "all", entries: [] },
    skills: { mode: "all", entries: [] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: new Date().toISOString(),
  };
}

export function AgentEditorDialog({
  open,
  agent,
  isNew,
  agents,
  catalog,
  globalModelLabel,
  onOpenChange,
  onSave,
  onReset,
}: {
  open: boolean;
  agent: AgentProfile | null;
  isNew: boolean;
  agents: AgentProfile[];
  catalog: AgentCatalog;
  globalModelLabel: string;
  onOpenChange: (open: boolean) => void;
  onSave: (agent: AgentProfile) => void;
  onReset?: (agent: AgentProfile) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<AgentProfile | null>(agent);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const twoColumn = useMediaQuery("(min-width: 1024px)");

  useEffect(() => {
    if (open) setDraft(agent ? { ...agent } : null);
  }, [open, agent]);

  const toolItems = useMemo<CapabilityItem[]>(
    () =>
      catalog.tools.map((tool) => ({
        id: tool.name,
        label: tool.label,
        description: tool.description,
        group: tool.category,
        groupLabel: tool.category,
        risk: tool.risk,
        locked: tool.locked,
      })),
    [catalog.tools],
  );

  const skillItems = useMemo<CapabilityItem[]>(
    () =>
      catalog.skills.map((skill) => ({
        id: skill.name,
        label: skill.name,
        description: skill.description,
        group: skill.source,
        groupLabel: skill.source,
        chips: skill.tags,
      })),
    [catalog.skills],
  );

  const patch = (changes: Partial<AgentProfile>) =>
    setDraft((prev) => (prev ? { ...prev, ...changes } : prev));

  const enabledToolIds = useMemo(
    () =>
      resolveSelection(
        draft?.tools ?? { mode: "all", entries: [] },
        catalog.tools.map((tool) => tool.name),
        catalog.tools.filter((tool) => tool.locked).map((tool) => tool.name),
      ),
    [draft?.tools, catalog.tools],
  );

  const enabledSkillIds = useMemo(
    () =>
      resolveSelection(
        draft?.skills ?? { mode: "all", entries: [] },
        catalog.skills.map((skill) => skill.name),
      ),
    [draft?.skills, catalog.skills],
  );

  const enabledSubAgentIds = useMemo(
    () =>
      resolveSelection(
        draft?.subAgents ?? { mode: "all", entries: [] },
        agents.map((item) => item.id).filter((id) => id !== draft?.id),
      ),
    [draft?.subAgents, agents, draft?.id],
  );

  const promptContext = useMemo<PromptContext | null>(() => {
    if (!draft) return null;
    const modelLabel =
      catalog.models.find((model) => model.id === draft.modelId)?.label ?? globalModelLabel;
    return {
      name: draft.name || t("settings.agents.untitled", "未命名"),
      description: draft.description,
      skills: catalog.skills.filter((skill) => enabledSkillIds.has(skill.name)).map((s) => s.name),
      tools: catalog.tools.filter((tool) => enabledToolIds.has(tool.name)).map((tool) => tool.label),
      model: modelLabel,
      date: new Intl.DateTimeFormat("zh-CN", { dateStyle: "long" }).format(new Date()),
      userProfile: t("settings.agents.promptContext.user", "（未配置用户档案）"),
      workspace: "nanobot",
    };
  }, [draft, catalog, enabledSkillIds, enabledToolIds, globalModelLabel, t]);

  if (!draft || !promptContext) return null;

  const dirty = JSON.stringify(draft) !== JSON.stringify(agent);
  const canSave =
    draft.name.trim().length > 0 &&
    draft.id.trim().length > 0 &&
    draft.prompt.length <= PROMPT_MAX_LENGTH;

  const requestClose = () => {
    if (dirty) setConfirmDiscard(true);
    else onOpenChange(false);
  };

  const guard = (event: Event) => {
    event.preventDefault();
    requestClose();
  };

  return (
    <>
      <Dialog open={open} onOpenChange={(next) => !next && requestClose()}>
        <DialogContent
          showCloseButton={false}
          onEscapeKeyDown={guard}
          onInteractOutside={guard}
          className="flex max-h-[88vh] w-full max-w-[min(72rem,calc(100vw-2rem))] flex-col gap-0 overflow-hidden p-0"
        >
          <header className="flex shrink-0 items-center gap-3 border-b border-border/60 px-6 py-3.5">
            <span
              aria-hidden
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control text-[20px]"
              style={{ backgroundColor: `${draft.color}1a` }}
            >
              {draft.icon}
            </span>
            <div className="min-w-0 flex-1">
              <DialogTitle className="truncate text-[15px] font-semibold">
                {isNew
                  ? t("settings.agents.editor.new", "新建 Agent")
                  : t("settings.agents.editor.edit", "编辑 · {{name}}", { name: draft.name })}
              </DialogTitle>
              <DialogDescription className="mt-0.5 truncate text-[12px]">
                {t("settings.agents.editor.subtitle", "配置该 Agent 可用的能力、模型与提示词。")}
              </DialogDescription>
            </div>
            {draft.type === "system" && onReset ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onReset(draft)}
                className="shrink-0 gap-1.5 text-[12px] text-muted-foreground"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                {t("settings.agents.editor.reset", "重置为默认")}
              </Button>
            ) : null}
            <button
              type="button"
              aria-label={t("common.close", "关闭")}
              onClick={requestClose}
              className="shrink-0 rounded-sm p-1 text-muted-foreground opacity-70 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </header>

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
            <div className="min-w-0 flex-1 space-y-6 overflow-y-auto px-6 py-5 scrollbar-thin">
              <section className="space-y-3">
                <SectionLabel index="①">
                  {t("settings.agents.section.basics", "基础信息")}
                </SectionLabel>
                <BasicsSection
                  profile={draft}
                  categories={catalog.categories}
                  isNew={isNew}
                  onChange={patch}
                />
              </section>

              <section className="space-y-3">
                <SectionLabel index="②">
                  {t("settings.agents.section.tools", "能力 · 工具")}
                </SectionLabel>
                <CapabilityPicker
                  items={toolItems}
                  selection={draft.tools}
                  onChange={(tools) => patch({ tools })}
                  modeLabel={t("settings.agents.tools.modeLabel", "搜索工具")}
                  groupLabelFor={(id) =>
                    t(TOOL_CATEGORY_LABEL_KEY[id] ?? id, TOOL_CATEGORIES.find((c) => c.id === id)?.label ?? id)
                  }
                />
              </section>

              <section className="space-y-3">
                <SectionLabel index="③">
                  {t("settings.agents.section.skills", "能力 · 技能")}
                </SectionLabel>
                <CapabilityPicker
                  items={skillItems}
                  selection={draft.skills}
                  onChange={(skills) => patch({ skills })}
                  modeLabel={t("settings.agents.skills.modeLabel", "搜索技能")}
                  groupLabelFor={(id) =>
                    t(SKILL_SOURCE_LABEL_KEY[id as keyof typeof SKILL_SOURCE_LABEL_KEY], id)
                  }
                />
              </section>

              <section className="space-y-3">
                <SectionLabel index="④">
                  {t("settings.agents.section.model", "模型")}
                </SectionLabel>
                <ModelPicker
                  models={catalog.models}
                  value={draft.modelId}
                  onChange={(modelId) => patch({ modelId })}
                  globalModelLabel={globalModelLabel}
                />
              </section>

              <section className="space-y-3">
                <SectionLabel index="⑤">
                  {t("settings.agents.section.prompt", "提示词")}
                </SectionLabel>
                <PromptSection profile={draft} onChange={(prompt) => patch({ prompt })} />
              </section>

              <section className="space-y-3">
                <SectionLabel index="⑥">
                  {t("settings.agents.section.relations", "关系与调度")}
                </SectionLabel>
                <RelationsSection
                  agents={agents}
                  editingId={draft.id || null}
                  selection={draft.subAgents}
                  onChange={(subAgents) => patch({ subAgents })}
                />
              </section>
            </div>

            <aside
              className={cn(
                "shrink-0 border-border/60 bg-muted/20 px-6 py-5",
                twoColumn
                  ? "w-[22rem] overflow-y-auto border-l scrollbar-thin"
                  : "max-h-[45vh] overflow-y-auto border-t scrollbar-thin",
              )}
            >
              <AgentPreviewRail
                profile={draft}
                context={promptContext}
                agents={agents}
                tools={catalog.tools}
                skills={catalog.skills}
                models={catalog.models}
                enabledToolIds={enabledToolIds}
                enabledSkillIds={enabledSkillIds}
                enabledSubAgentIds={enabledSubAgentIds}
              />
            </aside>
          </div>

          <footer
            className={cn(
              "flex shrink-0 items-center gap-3 border-t border-border/60 px-6 py-3",
              dirty && "bg-amber-50/70 dark:bg-amber-950/25",
            )}
          >
            {dirty ? (
              <p className="flex items-center gap-1.5 text-[12px] text-amber-800 dark:text-amber-200">
                <CircleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
                {t("settings.agents.editor.unsaved", "有未保存的修改")}
              </p>
            ) : null}
            <div className={cn("flex gap-2", !dirty && "ml-auto")}>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={requestClose}
                className="text-[13px]"
              >
                {t("common.cancel", "取消")}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={!canSave}
                onClick={() => onSave({ ...draft, updatedAt: new Date().toISOString() })}
                className="text-[13px]"
              >
                {t("settings.agents.editor.save", "保存")}
              </Button>
            </div>
          </footer>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmDiscard} onOpenChange={setConfirmDiscard}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("settings.agents.editor.discardTitle", "放弃未保存的修改？")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("settings.agents.editor.discardBody", "关闭后本次编辑不会保存。")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel", "取消")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmDiscard(false);
                onOpenChange(false);
              }}
            >
              {t("settings.agents.editor.discard", "放弃修改")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
