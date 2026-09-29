import { useTranslation } from "react-i18next";

import { CapabilityPicker, type CapabilityItem } from "@/components/settings/agents/parts/CapabilityPicker";
import type { AgentProfile, AgentSelection } from "@/lib/agents/types";
import { cycleBlockedIds } from "@/lib/agents/types";

export function RelationsSection({
  agents,
  editingId,
  selection,
  onChange,
}: {
  agents: AgentProfile[];
  editingId: string | null;
  selection: AgentSelection;
  onChange: (next: AgentSelection) => void;
}) {
  const { t } = useTranslation();
  const blocked = cycleBlockedIds(agents, editingId);
  const groupLabel = t("settings.agents.relations.group", "可调度子 Agent");

  const items: CapabilityItem[] = agents
    .filter((agent) => !agent.hidden || agent.id === editingId)
    .map((agent) => ({
      id: agent.id,
      label: agent.name,
      description: agent.description || agent.id,
      group: "subagents",
      blocked: blocked.has(agent.id),
      blockedReason:
        agent.id === editingId
          ? t("settings.agents.relations.self", "不能调度自己")
          : t("settings.agents.relations.cycle", "会形成循环依赖"),
    }));

  return (
    <CapabilityPicker
      items={items}
      selection={selection}
      onChange={onChange}
      modeLabel={t("settings.agents.relations.modeLabel", "子 Agent 模式")}
      groupLabelFor={() => groupLabel}
      lockedNote={t(
        "settings.agents.relations.hint",
        "被调度的子 Agent 各自保留自己的工具、技能与模型设置。",
      )}
    />
  );
}
