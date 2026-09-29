import { useTranslation } from "react-i18next";

import type { ModelDescriptor, SkillDescriptor, ToolDescriptor } from "@/lib/agents/types";

/**
 * 编辑器右侧的能力摘要：勾了什么、最终会拿到什么。
 *
 * 刻意**不含**「最终 prompt」预览：提示词现在就是左边那块可直接编辑的 markdown，
 * 再把拼装结果回显一遍既冗余，又让人以为真正生效的是右边那份只读文本。
 */
export function AgentRailSummary({
  modelId,
  tools,
  skills,
  models,
  enabledToolIds,
  enabledSkillIds,
}: {
  modelId: string | null;
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  enabledToolIds: Set<string>;
  enabledSkillIds: Set<string>;
}) {
  const { t } = useTranslation();
  const enabledTools = tools.filter((tool) => enabledToolIds.has(tool.name));
  const enabledSkills = skills.filter((skill) => enabledSkillIds.has(skill.name));
  const model = models.find((item) => item.id === modelId) ?? null;

  return (
    <div className="space-y-5">
      <section>
        <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("settings.agents.rail.capabilities", "实际能力")}
        </h4>
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
      </section>

      {enabledTools.length > 0 ? (
        <section>
          <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("settings.agents.rail.toolList", "工具清单")}
          </h4>
          <ChipList items={enabledTools.map((tool) => tool.label)} />
        </section>
      ) : null}

      {enabledSkills.length > 0 ? (
        <section>
          <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("settings.agents.rail.skillList", "技能清单")}
          </h4>
          <ChipList items={enabledSkills.map((skill) => skill.name)} />
        </section>
      ) : null}
    </div>
  );
}

function ChipList({ items }: { items: string[] }) {
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
