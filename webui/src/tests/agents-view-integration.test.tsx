import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgentsView } from "@/components/settings/agents/AgentsView";
import type { AgentProfile } from "@/lib/agents/types";

const listAgents = vi.fn();
const loadAgentCatalog = vi.fn();
const saveAgent = vi.fn();

vi.mock("@/lib/agents/api", () => ({
  listAgents: (...args: unknown[]) => listAgents(...args),
  loadAgentCatalog: (...args: unknown[]) => loadAgentCatalog(...args),
  saveAgent: (...args: unknown[]) => saveAgent(...args),
  deleteAgent: vi.fn(),
  resetAgent: vi.fn(),
  setAgentVisibility: vi.fn(),
}));

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({
    client: { requestMutation: vi.fn() },
    token: "tok-1",
    getToken: async () => "tok-1",
    modelName: "claude-sonnet-5",
    ingressLimits: { maxUploadBytes: 1 },
  }),
}));

vi.stubGlobal("matchMedia", (query: string) => ({
  matches: false,
  media: query,
  addEventListener: () => {},
  removeEventListener: () => {},
}));

const agent: AgentProfile = {
  id: "code-review",
  name: "代码审查",
  description: "逐行审查变更",
  type: "custom",
  customized: false,
  categoryId: "coding",
  icon: "⚙️",
  color: "#2C3E50",
  prompt: "ORIGINAL_PROMPT",
  modelId: null,
  tools: { mode: "all", entries: [] },
  skills: { mode: "all", entries: [] },
  subAgents: { mode: "all", entries: [] },
  hidden: false,
  updatedAt: "2026-09-01T00:00:00Z",
};

const catalog = { tools: [], skills: [], models: [], mcpServers: [], categories: [] };

describe("agents 编辑提示词", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAgents.mockResolvedValue({ agents: [agent] });
    loadAgentCatalog.mockResolvedValue(catalog);
    saveAgent.mockImplementation(async (_t: unknown, a: AgentProfile) => ({ agent: a }));
  });

  it("改完提示词保存时必须打到后端 save", async () => {
    const user = userEvent.setup();
    render(<AgentsView />);

    await user.click(await screen.findByText("代码审查"));

    const box = await screen.findByLabelText("System prompt");
    await user.clear(box);
    await user.type(box, "NEW_PROMPT");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveAgent).toHaveBeenCalledTimes(1));
    expect(saveAgent.mock.calls[0][1].prompt).toBe("NEW_PROMPT");
  });

  it("工具/技能分类默认折叠，点开才展开", async () => {
    const user = userEvent.setup();
    listAgents.mockResolvedValue({ agents: [agent] });
    loadAgentCatalog.mockResolvedValue({
      ...catalog,
      tools: [
        { name: "read_file", label: "read_file", description: "读", category: "filesystem", risk: "low", scope: "core" },
        { name: "shell", label: "shell", description: "执行", category: "execution", risk: "high", scope: "core" },
      ],
    });
    render(<AgentsView />);

    await user.click(await screen.findByText("代码审查"));
    // 折叠时组内条目不渲染（预览栏的清单不算，用 role 区分）
    expect(screen.queryByRole("checkbox", { name: /read_file/ })).toBeNull();

    await user.click(screen.getByRole("button", { name: /Filesystem/i }));
    expect(await screen.findByRole("checkbox", { name: /read_file/ })).toBeTruthy();
  });

  it("展开编辑后仍写同一个 draft", async () => {
    const user = userEvent.setup();
    render(<AgentsView />);

    await user.click(await screen.findByText("代码审查"));
    await user.click(await screen.findByRole("button", { name: "Expand" }));

    const box = (await screen.findByLabelText("System prompt")) as HTMLTextAreaElement;
    await user.clear(box);
    await user.type(box, "FOCUS_MODE");

    await user.click(screen.getByRole("button", { name: "Done" }));

    const back = (await screen.findByLabelText("System prompt")) as HTMLTextAreaElement;
    expect(back.value).toBe("FOCUS_MODE");
  });

  it("system 档案不给删除入口 —— 后端对它一律返 409", async () => {
    const user = userEvent.setup();
    listAgents.mockResolvedValue({ agents: [{ ...agent, type: "system" }] });
    render(<AgentsView />);

    await screen.findByText("代码审查");
    await user.click(screen.getByRole("button", { name: "More actions" }));

    await screen.findByRole("menuitem", { name: /Edit/ });
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
  });

  it("custom 档案保留删除入口", async () => {
    const user = userEvent.setup();
    render(<AgentsView />);

    await screen.findByText("代码审查");
    await user.click(screen.getByRole("button", { name: "More actions" }));

    expect(await screen.findByRole("menuitem", { name: "Delete" })).toBeTruthy();
  });
});
