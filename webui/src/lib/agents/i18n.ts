import type { ModelHealth, SkillSource, ToolRisk } from "@/lib/agents/types";

const PREFIX = "settings.agents.";

export const AGENT_CATEGORY_LABEL_KEY: Record<string, string> = {
  general: `${PREFIX}category.general`,
  coding: `${PREFIX}category.coding`,
  writing: `${PREFIX}category.writing`,
  research: `${PREFIX}category.research`,
  ops: `${PREFIX}category.ops`,
  efficiency: `${PREFIX}category.efficiency`,
};

export const TOOL_CATEGORY_LABEL_KEY: Record<string, string> = {
  filesystem: `${PREFIX}toolCategory.filesystem`,
  execution: `${PREFIX}toolCategory.execution`,
  web: `${PREFIX}toolCategory.web`,
  memory: `${PREFIX}toolCategory.memory`,
  scheduling: `${PREFIX}toolCategory.scheduling`,
  session: `${PREFIX}toolCategory.session`,
  orchestration: `${PREFIX}toolCategory.orchestration`,
  media: `${PREFIX}toolCategory.media`,
};

export const SKILL_SOURCE_LABEL_KEY: Record<SkillSource, string> = {
  builtin: `${PREFIX}skillSource.builtin`,
  workspace: `${PREFIX}skillSource.workspace`,
  plugin: `${PREFIX}skillSource.plugin`,
};

export const TOOL_RISK_LABEL_KEY: Record<ToolRisk, string> = {
  low: `${PREFIX}risk.low`,
  medium: `${PREFIX}risk.medium`,
  high: `${PREFIX}risk.high`,
};

export const MODEL_HEALTH_LABEL_KEY: Record<ModelHealth, string> = {
  healthy: `${PREFIX}health.healthy`,
  degraded: `${PREFIX}health.degraded`,
  unavailable: `${PREFIX}health.unavailable`,
};

export const EMOJI_GROUP_LABEL_KEY: Record<string, string> = {
  agents: `${PREFIX}emoji.agents`,
  roles: `${PREFIX}emoji.roles`,
  work: `${PREFIX}emoji.work`,
  nature: `${PREFIX}emoji.nature`,
  objects: `${PREFIX}emoji.objects`,
  symbols: `${PREFIX}emoji.symbols`,
  food: `${PREFIX}emoji.food`,
  activity: `${PREFIX}emoji.activity`,
};

export const PROMPT_VARIABLE_LABEL_KEY: Record<string, string> = {
  "{{name}}": `${PREFIX}variable.name`,
  "{{description}}": `${PREFIX}variable.description`,
  "{{skills}}": `${PREFIX}variable.skills`,
  "{{tools}}": `${PREFIX}variable.tools`,
  "{{model}}": `${PREFIX}variable.model`,
  "{{date}}": `${PREFIX}variable.date`,
  "{{user_profile}}": `${PREFIX}variable.userProfile`,
  "{{workspace}}": `${PREFIX}variable.workspace`,
};
