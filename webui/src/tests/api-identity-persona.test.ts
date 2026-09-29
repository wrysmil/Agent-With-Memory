import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getActivePersona,
  setActivePersona,
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

describe("active persona api helpers", () => {
  beforeEach(() => {
    requestMutation.mockReset();
    requestMutation.mockResolvedValue({ active: "" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ active: "tech_expert", options: ["balanced", "tech_expert"] }),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads the active persona over HTTP with the bearer token", async () => {
    await getActivePersona("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/identity/persona",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
        credentials: "same-origin",
      }),
    );
  });

  it("reads the active persona from a custom base URL", async () => {
    await getActivePersona("tok", "http://localhost:8765");

    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8765/api/settings/identity/persona",
      expect.objectContaining({ headers: { Authorization: "Bearer tok" } }),
    );
  });

  it("sets the active persona over the WebSocket", async () => {
    await setActivePersona(mutationTransport, "tech_expert");

    expect(requestMutation).toHaveBeenCalledWith(
      "identity.persona.set",
      { stem: "tech_expert" },
      20_000,
    );
    expect(fetch).not.toHaveBeenCalled();
  });

  it("clears the active persona with an empty stem", async () => {
    await setActivePersona(mutationTransport, "");

    expect(requestMutation).toHaveBeenCalledWith(
      "identity.persona.set",
      { stem: "" },
      20_000,
    );
  });
});
