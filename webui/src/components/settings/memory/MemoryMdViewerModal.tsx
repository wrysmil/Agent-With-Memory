import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useClient } from "@/providers/ClientProvider";

interface MemoryMdViewerModalProps {
  open: boolean;
  onClose: () => void;
}

export function MemoryMdViewerModal({ open, onClose }: MemoryMdViewerModalProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) {
      setContent(null);
      return;
    }
    setLoading(true);
    client.requestMutation<{ memory_md: string | null }>("memory-get-md-content", {})
      .then((result) => {
        setContent(result.memory_md);
      })
      .catch(() => {
        setContent(null);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open, client]);

  return (
    <Dialog open={open} onOpenChange={(val) => !val && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("settings.memory.mdCardTitle")}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t(
            "settings.memory.mdModalContentInjected",
            "MEMORY.md 的内容会在 agent 启动时注入到 system prompt。",
          )}
        </p>
        <textarea
          readOnly
          className="h-64 w-full resize-none rounded border border-input bg-muted/30 px-3 py-2 font-mono text-xs text-foreground"
          value={loading ? t("settings.memory.mdLoading", "加载中…") : (content ?? "")}
          placeholder={!loading ? t(
            "settings.memory.mdModalPlaceholder",
            "（MEMORY.md 不存在或为空）",
          ) : ""}
          aria-label={t("settings.memory.mdCardTitle")}
        />
      </DialogContent>
    </Dialog>
  );
}
