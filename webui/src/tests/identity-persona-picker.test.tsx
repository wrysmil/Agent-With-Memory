import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IdentityView } from "@/components/settings/identity/IdentityView";
import {
  fetchIdentityFile,
  getActivePersona,
  listIdentityFiles,
  listIdentityPresets,
  setActivePersona,
  type ActivePersonaResponse,
} from "@/lib/api";
import type { IdentityFileEntry } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchIdentityFile: vi.fn(),
    getActivePersona: vi.fn(),
    listIdentityFiles: vi.fn(),
    listIdentityPresets: vi.fn(),
    setActivePersona: vi.fn(),
  };
});

const PERSONA_STEMS = ["balanced", "companion", "creative", "mentor", "tech_expert"];

const requestMutation = vi.fn();
const transport = { requestMutation } as never;

const SOUL: IdentityFileEntry = {
  name: "SOUL.md",
  group: "core",
  logicalPath: "identity/SOUL.md",
  exists: true,
  restricted: false,
  tokens: 812,
};

const TECH_EXPERT: IdentityFileEntry = {
  name: "tech_expert.md",
  group: "personas",
  logicalPath: "identity/personas/tech_expert.md",
  exists: true,
  restricted: false,
  tokens: 640,
};

function renderIdentityView() {
  return render(
    <ClientProvider client={transport} token="tok">
      <IdentityView />
    </ClientProvider>,
  );
}

function personaSelect() {
  return screen.findByRole("combobox", { name: "Active persona" });
}

describe("IdentityView active persona picker", () => {
  beforeEach(() => {
    requestMutation.mockReset().mockResolvedValue({});
    vi.mocked(listIdentityFiles).mockReset().mockResolvedValue({
      files: [SOUL, TECH_EXPERT],
      charLimit: 1500,
    });
    vi.mocked(fetchIdentityFile)
      .mockReset()
      .mockImplementation(async (_token: string, name: string) => ({
        name,
        content: `# ${name}`,
        exists: true,
      }));
    vi.mocked(listIdentityPresets)
      .mockReset()
      .mockResolvedValue({ presets: [] });
    vi.mocked(setActivePersona).mockReset().mockResolvedValue({ active: "" });
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query === "(min-width: 1280px)",
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists every persona the backend reports and defaults to None", async () => {
    vi.mocked(getActivePersona)
      .mockReset()
      .mockResolvedValue({ active: "", options: PERSONA_STEMS } satisfies ActivePersonaResponse);

    renderIdentityView();

    const select = await screen.findByRole("combobox", { name: "Active persona" });
    expect(select).toHaveValue("");
    expect(within(select).getByRole("option", { name: "None" })).toHaveValue("");

    const labels = within(select)
      .getAllByRole("option")
      .map((option) => [option.getAttribute("value"), option.textContent]);
    // "None" first, then one entry per persona stem, labelled from
    // settings.identity.preset.{stem}.label.
    expect(labels).toEqual([
      ["", "None"],
      ["balanced", "Balanced assistant"],
      ["companion", "Casual companion"],
      ["creative", "Creative partner"],
      ["mentor", "Strict mentor"],
      ["tech_expert", "Tech expert"],
    ]);
  });

  it("reflects the active persona the backend already has", async () => {
    vi.mocked(getActivePersona)
      .mockReset()
      .mockResolvedValue({ active: "tech_expert", options: PERSONA_STEMS });

    renderIdentityView();

    expect(await personaSelect()).toHaveValue("tech_expert");
  });

  it("persists the selection through the identity.persona.set mutation", async () => {
    const user = userEvent.setup();
    vi.mocked(getActivePersona)
      .mockReset()
      .mockResolvedValue({ active: "", options: PERSONA_STEMS });
    vi.mocked(setActivePersona).mockResolvedValue({ active: "tech_expert" });

    renderIdentityView();

    await user.selectOptions(await personaSelect(), "tech_expert");

    await waitFor(() => {
      expect(setActivePersona).toHaveBeenCalledWith(transport, "tech_expert");
    });
    expect(await personaSelect()).toHaveValue("tech_expert");
  });

  it("clears the active persona when switching back to None", async () => {
    const user = userEvent.setup();
    vi.mocked(getActivePersona)
      .mockReset()
      .mockResolvedValue({ active: "tech_expert", options: PERSONA_STEMS });
    vi.mocked(setActivePersona).mockResolvedValue({ active: "" });

    renderIdentityView();

    await user.selectOptions(await personaSelect(), "");

    await waitFor(() => {
      expect(setActivePersona).toHaveBeenCalledWith(transport, "");
    });
    expect(await personaSelect()).toHaveValue("");
  });

  it("rolls the picker back and surfaces the error when the mutation fails", async () => {
    const user = userEvent.setup();
    vi.mocked(getActivePersona)
      .mockReset()
      .mockResolvedValue({ active: "", options: PERSONA_STEMS });
    vi.mocked(setActivePersona).mockRejectedValue(new Error("identity.persona.set unavailable"));

    renderIdentityView();

    await user.selectOptions(await personaSelect(), "mentor");

    await waitFor(async () => {
      expect(await personaSelect()).toHaveValue("");
    });
    expect(
      await screen.findByText(/Failed to switch persona: identity\.persona\.set unavailable/),
    ).toBeInTheDocument();
  });

  it("falls back to None when the active stem is not among the options", async () => {
    vi.mocked(getActivePersona)
      .mockReset()
      .mockResolvedValue({ active: "ghost_persona", options: PERSONA_STEMS });

    renderIdentityView();

    expect(await personaSelect()).toHaveValue("");
  });

  it("falls back to the raw stem when a persona has no translated label", async () => {
    vi.mocked(getActivePersona)
      .mockReset()
      .mockResolvedValue({ active: "", options: ["untranslated_persona"] });

    renderIdentityView();

    const select = await screen.findByRole("combobox", { name: "Active persona" });
    expect(within(select).getByRole("option", { name: "untranslated_persona" })).toBeInTheDocument();
  });

  it("keeps the rest of the identity page usable when the persona lookup fails", async () => {
    vi.mocked(getActivePersona).mockReset().mockRejectedValue(new Error("gateway offline"));

    renderIdentityView();

    const select = await screen.findByRole("combobox", { name: "Active persona" });
    expect(select).toHaveValue("");
    expect(within(select).getAllByRole("option")).toHaveLength(1);
    // The file catalog and editor still render — the picker is optional UX.
    expect(await screen.findByRole("button", { name: /SOUL\.md/ })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "SOUL.md" })).toBeInTheDocument();
    expect(screen.queryByText("gateway offline")).not.toBeInTheDocument();
  });

  it("shows the backend token estimate next to each identity file", async () => {
    vi.mocked(getActivePersona)
      .mockReset()
      .mockResolvedValue({ active: "", options: PERSONA_STEMS });

    renderIdentityView();

    const soulRow = await screen.findByRole("button", { name: /SOUL\.md/ });
    expect(soulRow).toHaveTextContent("≈812 tokens");
    const personaRow = screen.getByRole("button", { name: /tech_expert\.md/ });
    expect(personaRow).toHaveTextContent("≈640 tokens");
  });
});
