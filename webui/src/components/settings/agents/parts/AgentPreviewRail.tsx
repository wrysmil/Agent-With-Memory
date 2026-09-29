import { useTranslation } from "react-i18next";

import { Badge } from "@/components/settings/agents/shared";
import type {
  AgentProfile,
  ModelDescriptor,
  PromptContext,
  SkillDescriptor,
  ToolDescriptor,
} from "@/lib/agents/types";
import { assemblePrompt } from "@/lib/agents/types";

function PreviewBlock({ label, body, tone }: { label: string; body: string; tone: string }) {
  const { t } = useTranslation();
  return (
    <div className="border-l-2 pl-2.5" style={{ borderColor: tone }}>
      <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-foreground/85">
        {body || t("settings.agents.prompt.emptySegment", "（空）")}
      </p>
    </div>
  );
}

function RailBlock({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </h4>
        {action ? <div className="ml-auto">{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

function ChipList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <p className="text-[12px] text-muted-foreground/80">{empty}</p>;
  }
  return (
    <ul className="flex flex-wrap gap-1">
      {items.map((item) => (
        <li
          key={item}
          className="rounded-md border border-border/70 bg-background px-1.5 py-0.5 text-[11px] text-foreground/80"
        >
          {item}
        </li>
      ))}
    </ul>
  );
}

export function AgentPreviewRail({
  profile,
  context,
  agents,
  tools,
  skills,
  models,
  enabledToolIds,
  enabledSkillIds,
  enabledSubAgentIds,
}: {
  profile: AgentProfile;
  context: PromptContext;
  agents: AgentProfile[];
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  enabledToolIds: Set<string>;
  enabledSkillIds: Set<string>;
  enabledSubAgentIds: Set<string>;
}) {
  const { t } = useTranslation();
  const assembled = assemblePrompt(profile, context);

  const enabledTools = tools.filter((tool) => enabledToolIds.has(tool.name));
  const enabledSkills = skills.filter((skill) => enabledSkillIds.has(skill.name));
  const subAgents = agents.filter((agent) => enabledSubAgentIds.has(agent.id));
  const model = models.find((item) => item.id === profile.modelId) ?? null;

  return (
    <div className="space-y-5">
      <RailBlock title={t("settings.agents.rail.prompt", "最终 prompt")}>
        <div className="space-y-2.5">
          <PreviewBlock
            label={t("settings.agents.prompt.segmentBase", "基础系统提示词（平台内置）")}
            body={assembled.base}
            tone="#94a3b8"
          />
          <PreviewBlock
            label={t("settings.agents.prompt.segmentIdentity", "身份段落")}
            body={assembled.identity}
            tone="#8E44AD"
          />
          <PreviewBlock
            label={t("settings.agents.prompt.segmentCustom", "本 Agent 提示词")}
            body={assembled.custom}
            tone="#4A90D9"
          />
        </div>
      </RailBlock>

      <RailBlock title={t("settings.agents.rail.capabilities", "实际能力")}>
        <dl className="space-y-1.5 text-[12px]">
          <div className="flex items-center gap-2">
            <dt className="w-16 shrink-0 text-muted-foreground">
              {t("settings.agents.noun.tools", "工具")}
            </dt>
            <dd className="min-w-0 flex-1 truncate text-foreground/85">
              {enabledTools.length} / {tools.length}
            </dd>
          </div>
          <div className="flex items-center gap-2">
            <dt className="w-16 shrink-0 text-muted-foreground">
              {t("settings.agents.noun.skills", "技能")}
            </dt>
            <dd className="min-w-0 flex-1 truncate text-foreground/85">
              {enabledSkills.length} / {skills.length}
            </dd>
          </div>
          <div className="flex items-center gap-2">
            <dt className="w-16 shrink-0 text-muted-foreground">
              {t("settings.agents.rail.model", "模型")}
            </dt>
            <dd className="min-w-0 flex-1 truncate text-foreground/85">
              {model
                ? model.label
                : t("settings.agents.card.modelAuto", "跟随全局模型")}
            </dd>
          </div>
        </dl>
      </RailBlock>

      {enabledTools.length > 0 ? (
        <RailBlock title={t("settings.agents.rail.toolList", "工具清单")}>
          <ChipList
            items={enabledTools.map((tool) => tool.label)}
            empty={t("settings.agents.rail.none", "无")}
          />
        </RailBlock>
      ) : null}

      {enabledSkills.length > 0 ? (
        <RailBlock title={t("settings.agents.rail.skillList", "技能清单")}>
          <ChipList
            items={enabledSkills.map((skill) => skill.name)}
            empty={t("settings.agents.rail.none", "无")}
          />
        </RailBlock>
      ) : null}

      <RailBlock title={t("settings.agents.rail.subAgents", "可调度子 Agent")}>
        {subAgents.length === 0 ? (
          <p className="text-[12px] text-muted-foreground/80">
            {t("settings.agents.rail.noSubAgents", "不调度任何子 Agent")}
          </p>
        ) : (
          <ul className="space-y-1">
            {subAgents.map((agent) => (
              <li key={agent.id} className="flex items-center gap-2 text-[12px]">
                <span
                  aria-hidden
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[12px]"
                  style={{ backgroundColor: `${agent.color}1f` }}
                >
                  {agent.icon}
                </span>
                <span className="min-w-0 flex-1 truncate text-foreground/85">{agent.name}</span>
                {agent.type === "system" ? (
                  <Badge tone="muted">
                    {t("settings.agents.basics.systemPreset", "系统预设")}
                  </Badge>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </RailBlock>
    </div>
  );
}
