import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useClient } from "@/providers/ClientProvider";

interface MemoryDraftDiffModalProps {
  open: boolean;
  onClose: () => void;
}

export function MemoryDraftDiffModal({ open, onClose }: MemoryDraftDiffModalProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const [memoryMd, setMemoryMd] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) {
      setMemoryMd(null);
      setDraft(null);
      return;
    }
    setLoading(true);
    client.requestMutation<{ memory_md: string | null; draft: string | null }>("memory-get-md-content", {})
      .then((result) => {
        setMemoryMd(result.memory_md);
        setDraft(result.draft);
      })
      .catch(() => {
        setMemoryMd(null);
        setDraft(null);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open, client]);

  const placeholder = t("settings.memory.mdModalDiffPlaceholder", "（内容待后端支持读取）");

  return (
    <Dialog open={open} onOpenChange={(val) => !val && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("settings.memory.mdModalDiffTitle", "Dream 草稿对比")}</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">
              {t("settings.memory.mdModalDiffDraft", "Dream 草稿")}
            </span>
            <textarea
              readOnly
              className="h-56 w-full resize-none rounded border border-input bg-muted/30 px-3 py-2 font-mono text-xs text-foreground"
              value={loading ? t("settings.memory.mdLoading", "加载中…") : (draft ?? placeholder)}
              aria-label={t("settings.memory.mdModalDiffDraft", "Dream 草稿")}
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">
              {t("settings.memory.mdModalDiffCurrent", "当前 MEMORY.md")}
            </span>
            <textarea
              readOnly
              className="h-56 w-full resize-none rounded border border-input bg-muted/30 px-3 py-2 font-mono text-xs text-foreground"
              value={loading ? t("settings.memory.mdLoading", "加载中…") : (memoryMd ?? placeholder)}
              aria-label={t("settings.memory.mdModalDiffCurrent", "当前 MEMORY.md")}
            />
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          {t("settings.memory.mdModalDraftHint")}
        </p>
      </DialogContent>
    </Dialog>
  );
}
