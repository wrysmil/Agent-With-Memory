import { useState } from "react";
import { useTranslation } from "react-i18next";

import { SegmentedControl } from "@/components/ui/segmented-control";
import { EpisodeListView } from "./EpisodeListView";
import { MemoryListView } from "./MemoryListView";
import { ScratchpadEditor } from "./ScratchpadEditor";

type MemoryTab = "semantic" | "episode" | "scratchpad";

const MEMORY_TABS: Array<{ value: MemoryTab; labelKey: string; defaultLabel: string }> = [
  { value: "semantic", labelKey: "settings.memory.tabSemantic", defaultLabel: "Semantic memory" },
  { value: "episode", labelKey: "settings.memory.tabEpisode", defaultLabel: "Episode memory" },
  { value: "scratchpad", labelKey: "settings.memory.tabScratchpad", defaultLabel: "Working memory" },
];

export function MemorySection() {
  const { t } = useTranslation();
  const tx = (key: string, fallback: string) => t(key, { defaultValue: fallback });
  const [tab, setTab] = useState<MemoryTab>("semantic");

  return (
    <div className="flex flex-col gap-4">
      <SegmentedControl
        mode="tabs"
        ariaLabel={tx("settings.memory.tabsLabel", "Memory sections")}
        value={tab}
        options={MEMORY_TABS.map(({ value, labelKey, defaultLabel }) => ({
          value,
          label: tx(labelKey, defaultLabel),
        }))}
        onChange={setTab}
        className="w-fit max-w-full"
      />
      {tab === "semantic" && <MemoryListView />}
      {tab === "episode" && <EpisodeListView />}
      {tab === "scratchpad" && <ScratchpadEditor />}
    </div>
  );
}
