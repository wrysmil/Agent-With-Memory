import { mutation, request, type WebUIMutationTransport } from "@/lib/api";
import type { AgentCatalog } from "@/lib/agents/catalog";
import type { AgentProfile } from "@/lib/agents/types";

const AGENTS_BASE = "/api/settings/agents";

/** 读通道超时，与 `lib/api.ts` 的 API_READ_TIMEOUT_MS 同值。 */
const AGENTS_READ_TIMEOUT_MS = 20_000;

/** 写通道超时，与 `lib/api.ts` 的 API_MUTATION_TIMEOUT_MS 同值。 */
const AGENTS_MUTATION_TIMEOUT_MS = 20_000;

export interface AgentListResponse {
  agents: AgentProfile[];
}

/** 形状与 `catalog.ts` 的 `AgentCatalog` 是同一个类型，不留第二份定义。 */
export type AgentCatalogResponse = AgentCatalog;

export interface AgentMutationResponse {
  agent: AgentProfile;
}

export interface AgentDeleteResponse {
  id: string;
}

export async function listAgents(
  token: string,
  base: string = "",
): Promise<AgentListResponse> {
  return request<AgentListResponse>(
    `${base}${AGENTS_BASE}`,
    token,
    undefined,
    AGENTS_READ_TIMEOUT_MS,
  );
}

export async function loadAgentCatalog(
  token: string,
  base: string = "",
): Promise<AgentCatalogResponse> {
  return request<AgentCatalogResponse>(
    `${base}${AGENTS_BASE}/catalog`,
    token,
    undefined,
    AGENTS_READ_TIMEOUT_MS,
  );
}

export async function saveAgent(
  transport: WebUIMutationTransport,
  agent: AgentProfile,
): Promise<AgentMutationResponse> {
  return mutation<AgentMutationResponse>(
    transport,
    "agents.save",
    { agent },
    AGENTS_MUTATION_TIMEOUT_MS,
  );
}

export async function deleteAgent(
  transport: WebUIMutationTransport,
  id: string,
): Promise<AgentDeleteResponse> {
  return mutation<AgentDeleteResponse>(
    transport,
    "agents.delete",
    { id },
    AGENTS_MUTATION_TIMEOUT_MS,
  );
}

export async function resetAgent(
  transport: WebUIMutationTransport,
  id: string,
): Promise<AgentMutationResponse> {
  return mutation<AgentMutationResponse>(
    transport,
    "agents.reset",
    { id },
    AGENTS_MUTATION_TIMEOUT_MS,
  );
}

export async function setAgentVisibility(
  transport: WebUIMutationTransport,
  id: string,
  hidden: boolean,
): Promise<AgentMutationResponse> {
  return mutation<AgentMutationResponse>(
    transport,
    "agents.visibility",
    { id, hidden },
    AGENTS_MUTATION_TIMEOUT_MS,
  );
}
