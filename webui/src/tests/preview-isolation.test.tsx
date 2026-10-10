import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import PreviewApp from "@/preview/PreviewApp";

// The preview must never talk to the backend. Making the business transports
// throw means any accidental fetch/WebSocket call surfaces as a render failure
// instead of silently hitting the network.
function forbidTransports() {
  const originalFetch = globalThis.fetch;
  const originalWebSocket = (globalThis as { WebSocket?: unknown }).WebSocket;
  const throwNoNetwork = () => {
    throw new Error("preview must not issue business requests");
  };
  Object.defineProperty(globalThis, "fetch", {
    value: throwNoNetwork,
    configurable: true,
  });
  Object.defineProperty(globalThis, "WebSocket", {
    value: throwNoNetwork,
    configurable: true,
  });
  return () => {
    Object.defineProperty(globalThis, "fetch", {
      value: originalFetch,
      configurable: true,
    });
    Object.defineProperty(globalThis, "WebSocket", {
      value: originalWebSocket,
      configurable: true,
    });
  };
}

describe("PreviewApp isolation", () => {
  let restore: (() => void) | null = null;

  beforeEach(() => {
    restore = forbidTransports();
  });

  afterEach(() => {
    restore?.();
    restore = null;
  });

  it("renders the navigation shell and home view with transports disabled", () => {
    expect(() => render(<PreviewApp />)).not.toThrow();
    expect(screen.getByRole("navigation")).toBeInTheDocument();
    expect(screen.getByText("知序")).toBeInTheDocument();
  });
});
