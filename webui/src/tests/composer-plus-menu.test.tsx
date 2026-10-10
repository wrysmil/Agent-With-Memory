import {
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ComposerPlusMenu } from "@/components/thread/ComposerPlusMenu";
import type { AgentProfile } from "@/lib/agents/types";
import type { CliAppInfo, McpPresetInfo, SkillSummary } from "@/lib/types";

const skills = [
  {
    name: "github",
    description: "Use GitHub CLI.",
    source: "builtin",
    available: true,
  } as SkillSummary,
  {
    name: "drafted",
    description: "Disabled skill.",
    source: "workspace",
    available: true,
    enabled: false,
  } as SkillSummary,
];

const plugins = [
  { name: "claude", display_name: "Claude", description: "Coding agent", installed: true } as CliAppInfo,
  { name: "ghostty", display_name: "Ghostty", description: "Not installed", installed: false } as CliAppInfo,
];

const mcpPresets = [
  { name: "context7", display_name: "Context7", description: "Docs MCP", installed: true, configured: true } as McpPresetInfo,
];

const agents = [
  { id: "researcher", name: "Researcher", description: "Deep research", hidden: false } as AgentProfile,
];

function renderMenu(overrides: Partial<React.ComponentProps<typeof ComposerPlusMenu>> = {}) {
  const props = {
    isHero: false,
    skills,
    plugins,
    mcpPresets,
    agents,
    canWorkInProject: true,
    onPickFiles: vi.fn(),
    onWorkInProject: vi.fn(),
    onInsertToken: vi.fn(),
    ...overrides,
  };
  render(<ComposerPlusMenu {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  return props;
}

describe("ComposerPlusMenu", () => {
  it("shows the add, skills, plugins, mcp and agents sections", () => {
    renderMenu();
    expect(screen.getByRole("menuitem", { name: /Files and folders/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Work in a project/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Goal/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Plan mode/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Draw/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /github/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /drafted/ })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Claude/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Ghostty/ })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Context7/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Researcher/ })).toBeInTheDocument();
  });

  it("inserts tokens for skills, plugins, mcp and agents", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /github/ }));
    expect(props.onInsertToken).toHaveBeenCalledWith("$github");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Claude/ }));
    expect(props.onInsertToken).toHaveBeenCalledWith("@claude");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Context7/ }));
    expect(props.onInsertToken).toHaveBeenCalledWith("@context7");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Researcher/ }));
    expect(props.onInsertToken).toHaveBeenCalledWith(
      'Run this with the agent "Researcher": ',
    );
  });

  it("routes file picking and project work to their handlers", () => {
    const props = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Files and folders/ }));
    expect(props.onPickFiles).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Work in a project/ }));
    expect(props.onWorkInProject).toHaveBeenCalled();
  });

  it("hides the project item and disables files when limits are hit", () => {
    renderMenu({ canWorkInProject: false, filesDisabled: true });
    expect(screen.queryByRole("menuitem", { name: /Work in a project/ })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Files and folders/ })).toBeDisabled();
  });

  it("shows empty hints when a section has no items", () => {
    renderMenu({ skills: [], plugins: [], mcpPresets: [], agents: [] });
    expect(screen.getByText("No skills installed")).toBeInTheDocument();
    expect(screen.getByText("No plugins installed")).toBeInTheDocument();
    expect(screen.getByText("No MCP servers configured")).toBeInTheDocument();
    expect(screen.getByText("No agents available")).toBeInTheDocument();
  });
});
