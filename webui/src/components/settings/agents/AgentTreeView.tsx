import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/settings/agents/shared";
import type { AgentProfile, ModelDescriptor, SkillDescriptor, ToolDescriptor } from "@/lib/agents/types";
import { resolveSelection } from "@/lib/agents/types";

function TreeNode({
  agent,
  agentsById,
  toolCount,
  skillCount,
  modelLabel,
  depth,
  path,
}: {
  agent: AgentProfile;
  agentsById: Map<string, AgentProfile>;
  toolCount: (agent: AgentProfile) => number;
  skillCount: (agent: AgentProfile) => number;
  modelLabel: (agent: AgentProfile) => string;
  depth: number;
  path: Set<string>;
}) {
  const { t } = useTranslation();
  const childIds = useMemo(() => {
    const all = [...agentsById.keys()];
    return [...resolveSelection(agent.subAgents, all)].filter((id) => !path.has(id));
  }, [agent.subAgents, agentsById, path]);

  return (
    <li>
      <div
        className="flex items-center gap-2.5 rounded-control px-2 py-1.5 transition-colors hover:bg-muted/50"
        style={{ paddingLeft: `${depth * 1.5 + 0.5}rem` }}
      >
        {depth > 0 ? (
          <span aria-hidden className="text-muted-foreground/60">
            ↳
          </span>
        ) : null}
        <span
          aria-hidden
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[15px]"
          style={{ backgroundColor: `${agent.color}1f` }}
        >
          {agent.icon}
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-foreground/90">
          {agent.name}
        </span>
        {agent.type === "system" ? (
          <Badge tone="info">{t("settings.agents.basics.systemPreset", "系统预设")}</Badge>
        ) : null}
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{agent.id}</span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          🛠 {toolCount(agent)} · 📚 {skillCount(agent)} · 🧠 {modelLabel(agent)}
        </span>
      </div>
      {childIds.length > 0 ? (
        <ul>
          {childIds.map((id) => {
            const child = agentsById.get(id);
            if (!child) return null;
            return (
              <TreeNode
                key={id}
                agent={child}
                agentsById={agentsById}
                toolCount={toolCount}
                skillCount={skillCount}
                modelLabel={modelLabel}
                depth={depth + 1}
                path={new Set([...path, child.id])}
              />
            );
          })}
        </ul>
      ) : null}
    </li>
  );
}

export function AgentTreeView({
  agents,
  tools,
  skills,
  models,
  globalModelLabel,
}: {
  agents: AgentProfile[];
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  globalModelLabel: string;
}) {
  const { t } = useTranslation();
  const agentsById = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent])),
    [agents],
  );

  const toolCount = (agent: AgentProfile) =>
    resolveSelection(
      agent.tools,
      tools.map((tool) => tool.name),
      tools.filter((tool) => tool.locked).map((tool) => tool.name),
    ).size;
  const skillCount = (agent: AgentProfile) =>
    resolveSelection(
      agent.skills,
      skills.map((skill) => skill.name),
    ).size;
  const modelLabel = (agent: AgentProfile) =>
    models.find((model) => model.id === agent.modelId)?.label ?? globalModelLabel;

  const roots = agents.filter((agent) => {
    const isChild = agents.some((other) =>
      other.id !== agent.id &&
      resolveSelection(other.subAgents, [...agentsById.keys()]).has(agent.id),
    );
    return !isChild;
  });

  return (
    <div className="rounded-panel bg-settings-surface p-3">
      <ul>
        {roots.map((agent) => (
          <TreeNode
            key={agent.id}
            agent={agent}
            agentsById={agentsById}
            toolCount={toolCount}
            skillCount={skillCount}
            modelLabel={modelLabel}
            depth={0}
            path={new Set([agent.id])}
          />
        ))}
      </ul>
      {roots.length === 0 ? (
        <p className="py-8 text-center text-[12px] text-muted-foreground">
          {t("settings.agents.tree.empty", "没有可显示的 Agent")}
        </p>
      ) : null}
    </div>
  );
}
