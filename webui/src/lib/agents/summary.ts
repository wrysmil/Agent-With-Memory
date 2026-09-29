import type { TFunction } from "i18next";

import type { AgentSelection } from "@/lib/agents/types";
import { resolveSelection } from "@/lib/agents/types";

export interface CapabilityBreakdown {
  mode: AgentSelection["mode"];
  on: number;
  excluded: number;
  total: number;
}

export function breakdown(selection: AgentSelection, allIds: string[], lockedIds: string[] = []): CapabilityBreakdown {
  const on = resolveSelection(selection, allIds, lockedIds).size;
  const total = new Set([...allIds, ...lockedIds]).size;
  return { mode: selection.mode, on, excluded: Math.max(0, total - on), total };
}

/**
 * A count alone ("7") tells the reader nothing. Naming the mode gives the number
 * a baseline they can judge it against.
 */
export function describeCapability(
  value: CapabilityBreakdown,
  noun: string,
  t: TFunction,
): string {
  const base = `settings.agents.summary.${value.mode}`;
  if (value.mode === "all") {
    return t(base, { defaultValue: `全部${noun}`, noun });
  }
  if (value.mode === "include") {
    return t(base, { defaultValue: `${value.on} 个${noun}`, count: value.on, noun });
  }
  return t(base, {
    defaultValue: `${value.on} 个${noun} · 排除 ${value.excluded}`,
    count: value.on,
    excluded: value.excluded,
    noun,
  });
}

export function describeSubAgents(value: CapabilityBreakdown, t: TFunction): string {
  if (value.mode === "all") {
    return t("settings.agents.summary.all", { defaultValue: "全部", noun: "" });
  }
  return describeCapability(value, t("settings.agents.summary.subAgentNoun", "个子 Agent"), t);
}
