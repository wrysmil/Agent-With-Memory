import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchIdentityFile,
  listIdentityFiles,
  reloadIdentity,
  saveIdentityFile,
  type WebUIMutationTransport,
} from "@/lib/api";

const requestMutation = vi.fn();
const mutationTransport: WebUIMutationTransport = {
  requestMutation: <T>(
    action: string,
    payload?: Record<string, unknown>,
    timeoutMs?: number,
  ) => requestMutation(action, payload, timeoutMs) as Promise<T>,
};

describe("identity api helpers", () => {
  beforeEach(() => {
    requestMutation.mockReset();
    requestMutation.mockResolvedValue({});
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ files: [], charLimit: 1000 }),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists identity files with no query params", async () => {
    await listIdentityFiles("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/identity/files",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
        credentials: "same-origin",
      }),
    );
  });

  it("lists identity files with custom base URL", async () => {
    await listIdentityFiles("tok", "http://localhost:8765");

    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8765/api/settings/identity/files",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("fetches a single identity file by name", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ name: "AGENT.md", content: "# Agent prompt" }),
      }),
    );

    await fetchIdentityFile("tok", "AGENT.md");

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/identity/file?name=AGENT.md",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("percent-encodes file names with special characters", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ name: "my agent.md", content: "test" }),
      }),
    );

    await fetchIdentityFile("tok", "my agent.md");

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/identity/file?name=my%20agent.md",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("saves an identity file over the WebSocket", async () => {
    await saveIdentityFile(mutationTransport, {
      name: "AGENT.md",
      content: "# My agent",
    });

    expect(requestMutation).toHaveBeenCalledWith(
      "identity.file.save",
      { name: "AGENT.md", content: "# My agent" },
      20_000,
    );
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reloads identity files over the WebSocket", async () => {
    await reloadIdentity(mutationTransport);

    expect(requestMutation).toHaveBeenCalledWith(
      "identity.reload",
      {},
      20_000,
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});
