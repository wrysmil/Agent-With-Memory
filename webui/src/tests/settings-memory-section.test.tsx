import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { MemoryPayload } from "@/lib/types";
import {
  installSettingsViewTestHooks,
  jsonResponse,
  renderSettingsView,
  settingsPayload,
} from "@/tests/settings-test-utils";

const memory = (overrides: Partial<MemoryPayload> = {}): MemoryPayload => ({
  id: "mem-1",
  content: "User prefers terse replies",
  type: "preference",
  priority: "long_term",
  source: "manual",
  importance_score: 0.8,
  access_count: 0,
  tags: ["style"],
  subject: "",
  predicate: "",
  confidence: 1,
  decay_rate: 0,
  expires_at: null,
  last_accessed_at: null,
  superseded_by: null,
  source_episode_id: null,
  scope: "workspace",
  workspace_id: "default",
  created_at: "2026-09-10T08:00:00Z",
  updated_at: "2026-09-10T08:00:00Z",
  metadata: {},
  ...overrides,
});

function stubMemoryApi(items: MemoryPayload[] = []) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/settings") return jsonResponse(settingsPayload());
    if (url.startsWith("/api/settings/memory/memories")) return jsonResponse({ items });
    if (url.startsWith("/api/settings/memory/episodes")) return jsonResponse({ items: [] });
    if (url.startsWith("/api/settings/memory/scratchpad")) return jsonResponse({ scratchpad: null });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Settings memory section registration", () => {
  installSettingsViewTestHooks();

  it("lists memory in the settings navigation and selects it", () => {
    stubMemoryApi();
    renderSettingsView({
      initialSection: "overview",
      initialSettings: settingsPayload(),
      showSidebar: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "Memory" }));

    expect(screen.getByTestId("settings-section-transition")).toHaveAttribute(
      "data-settings-section",
      "memory",
    );
  });

  it("renders the memory tabs and semantic list for the memory section", async () => {
    const fetchMock = stubMemoryApi([memory()]);
    renderSettingsView({
      initialSection: "memory",
      initialSettings: settingsPayload(),
      showSidebar: true,
    });

    expect(screen.getByRole("tablist", { name: "Memory sections" })).toBeInTheDocument();
    for (const label of ["Semantic memory", "Episode memory", "Working memory"]) {
      expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
    }

    await waitFor(() => {
      expect(screen.getByText("User prefers terse replies")).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/memory/memories?order=created",
      expect.objectContaining({ headers: { Authorization: "Bearer tok" } }),
    );
  });
});
