/** 档案能力选择器：locked 与 blocked 的语义。 */
import { describe, expect, it } from "vitest";

import { resolveSelection } from "@/lib/agents/types";

describe("resolveSelection with the minimal locked set", () => {
  const all = ["read_file", "list_dir", "exec", "write_file", "web_search"];
  const locked = ["read_file", "list_dir"];

  it("keeps locked ids even when the include list omits them", () => {
    const got = resolveSelection({ mode: "include", entries: ["read_file"] }, all, locked);
    expect([...got].sort()).toEqual(["list_dir", "read_file"]);
  });

  it("lets include mode shrink the set when nothing extra is locked", () => {
    const got = resolveSelection({ mode: "include", entries: ["read_file"] }, all, ["read_file"]);
    expect([...got].sort()).toEqual(["read_file"]);
  });

  it("exclude mode can drop everything that is not locked", () => {
    const got = resolveSelection({ mode: "exclude", entries: ["exec", "write_file", "web_search"] }, all, locked);
    expect([...got].sort()).toEqual(["list_dir", "read_file"]);
  });

  it("all mode returns the full set", () => {
    const got = resolveSelection({ mode: "all", entries: [] }, all, locked);
    expect([...got].sort()).toEqual([...all].sort());
  });
});
