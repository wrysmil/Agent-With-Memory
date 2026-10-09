import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThreadStatusBar } from "@/components/thread/ThreadStatusBar";
import type { WorkspaceScopePayload } from "@/lib/types";

const scope = (access_mode: WorkspaceScopePayload["access_mode"]): WorkspaceScopePayload => ({
  project_path: "D:\\projects\\demo",
  project_name: "demo-app",
  access_mode,
});

describe("ThreadStatusBar", () => {
  it("shows workspace name, access mode and context percentage", () => {
    render(
      <ThreadStatusBar
        workspaceScope={scope("restricted")}
        contextUsage={{ contextTokens: 82_000, contextWindowTokens: 100_000 }}
      />,
    );
    expect(screen.getByTestId("thread-status-bar")).toBeInTheDocument();
    expect(screen.getByText("demo-app")).toBeInTheDocument();
    expect(screen.getByText("Default")).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
  });

  it("marks full access distinctly", () => {
    render(<ThreadStatusBar workspaceScope={scope("full")} />);
    expect(screen.getByText("Full")).toBeInTheDocument();
  });

  it("renders nothing without workspace scope or usage", () => {
    const { container } = render(<ThreadStatusBar />);
    expect(container).toBeEmptyDOMElement();
  });
});
