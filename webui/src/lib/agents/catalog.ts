import type {
  AgentCategory,
  AgentProfile,
  ModelDescriptor,
  SkillDescriptor,
  ToolDescriptor,
} from "./types";

/**
 * 全局 MCP server 的**只读**描述符，形状见契约 §8.4。
 *
 * 刻意不含 `headers` / `env`（可能含凭据）也不含 `args` / `cwd`：
 * MCP 工具在运行时才注册成 `mcp_<server>_<tool>`，不在静态工具目录里，
 * 所以 Agent 档案没有 mcpServers 字段，这里只用于展示。
 */
export interface McpServerDescriptor {
  name: string;
  /** MCPServerConfig.type，省略时后端回退 "auto"。 */
  type: string;
  /** stdio 型取 command，其余为空串。 */
  command: string;
  /** http/sse 型取 url，其余为空串。 */
  url: string;
  /** enabled_tools 长度；["*"] 时为 0，配合 allTools 读。 */
  toolCount: number;
  /** enabled_tools == ["*"]。 */
  allTools: boolean;
}

/** 编辑器需要的完整能力目录。线格式见 api.ts 的 loadAgentCatalog 返回值。 */
export interface AgentCatalog {
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  categories: AgentCategory[];
  mcpServers: McpServerDescriptor[];
}

/** 新建 Agent 的空草稿。id 由调用方按现有档案去重后填入。 */
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
    // 与后端 FACTORY_TOOLS 同一口径：起步只给「读」，技能为空。
    // 一上来就开全部工具，等于让用户第一件事就是从几十项里做减法。
    tools: { mode: "include", entries: ["read_file", "list_dir"] },
    skills: { mode: "include", entries: [] },
    subAgents: { mode: "include", entries: [] },
    hidden: false,
    // 服务端在 save 时覆盖；这里给空串而不是本地时间戳，
    // 免得草稿看起来"已经改过"。
    updatedAt: "",
  };
}

/** 名称 → 合法档案 id。与后端 nanobot/agents/catalog.py 的 ID_PATTERN 对齐。 */
export function slugifyAgentId(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "agent";
}

/** 在 used 之外取第一个可用 id，形如 base / base-2 / base-3。 */
export function uniqueAgentId(base: string, used: Set<string>): string {
  let id = base;
  let suffix = 2;
  while (used.has(id)) id = `${base}-${suffix++}`;
  return id;
}
