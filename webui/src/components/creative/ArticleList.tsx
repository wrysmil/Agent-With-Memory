import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Plus, Search, Copy, Trash2, ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import type { Article, ArticleStatus } from "./types";
import { ARTICLE_STATUSES, TEMPLATES } from "./types";

interface Props {
  articles: Article[];
  totalCount: number;
  query: string;
  statusFilter: ArticleStatus | "全部";
  onQueryChange: (q: string) => void;
  onStatusFilterChange: (s: ArticleStatus | "全部") => void;
  onOpen: (id: string) => void;
  onCreate: (title: string, templateName?: string) => { error: string | null };
  onDuplicate: (id: string) => void;
  onDelete: (id: string) => void;
  onRestoreDemo: () => void;
}

export function ArticleList({
  articles,
  totalCount,
  query,
  statusFilter,
  onQueryChange,
  onStatusFilterChange,
  onOpen,
  onCreate,
  onDuplicate,
  onDelete,
  onRestoreDemo,
}: Props) {
  const { t } = useTranslation();
  const [newTitle, setNewTitle] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<string | undefined>();
  const [createError, setCreateError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Article | null>(null);

  const handleCreate = () => {
    const { error } = onCreate(newTitle, selectedTemplate);
    if (error) {
      setCreateError(error);
      return;
    }
    setNewTitle("");
    setSelectedTemplate(undefined);
    setCreateError(null);
  };

  const statusTag = (status: ArticleStatus) => {
    const colors =
      status === "写作中" || status === "已完成"
        ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
        : status === "待校对"
          ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
          : "bg-muted text-muted-foreground";
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
          colors,
        )}
      >
        {status}
      </span>
    );
  };

  return (
    <section className="mx-auto w-full max-w-[1040px] px-5 py-6 sm:px-8">
      <div className="mb-6">
        <h2 className="text-[22px] font-semibold leading-tight text-foreground">
          {t("creative.title", { defaultValue: "创作空间" })}
        </h2>
        <p className="mt-1 text-[13px] text-muted-foreground">
          {t("creative.subtitle", {
            defaultValue: "把资料、代码和自己的理解，整理成一篇好文章。",
          })}
        </p>
      </div>

      {/* New article form */}
      <div className="mb-6 rounded-panel border border-border bg-settings-surface p-4">
        <div className="flex items-start gap-3">
          <div className="flex-1">
            <Textarea
              value={newTitle}
              onChange={(e) => {
                setNewTitle(e.target.value);
                setCreateError(null);
              }}
              placeholder={t("creative.new_placeholder", {
                defaultValue: "想讲清楚什么问题？",
              })}
              className="min-h-[56px] resize-none"
            />
            {createError ? (
              <p className="mt-1 text-[12px] font-medium text-destructive">
                {createError}
              </p>
            ) : null}
          </div>
          <Button onClick={handleCreate} size="sm" className="shrink-0">
            <Plus className="mr-1 h-3.5 w-3.5" />
            {t("creative.create_btn", { defaultValue: "新建" })}
          </Button>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {TEMPLATES.map((tmpl) => (
            <button
              key={tmpl.name}
              type="button"
              className={cn(
                "rounded-control border px-2.5 py-1 text-[12px] transition-colors",
                selectedTemplate === tmpl.name
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border bg-background text-muted-foreground hover:border-primary/20",
              )}
              onClick={() =>
                setSelectedTemplate(
                  selectedTemplate === tmpl.name ? undefined : tmpl.name,
                )
              }
            >
              {tmpl.name}
            </button>
          ))}
        </div>
      </div>

      {/* Filter tabs + search */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1" role="group" aria-label="状态筛选">
          {(["全部", ...ARTICLE_STATUSES] as const).map((s) => (
            <button
              key={s}
              type="button"
              className={cn(
                "rounded-control px-2.5 py-1 text-[12px] transition-colors",
                statusFilter === s
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
              onClick={() => onStatusFilterChange(s)}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="relative min-w-[180px]">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder={t("creative.search_placeholder", {
              defaultValue: "搜索标题或摘要",
            })}
            className="h-8 pl-8 text-[12px]"
          />
        </div>
      </div>

      {/* Article grid */}
      {articles.length === 0 ? (
        <div className="rounded-panel border border-dashed border-border py-12 text-center text-[13px] text-muted-foreground">
          {totalCount === 0 ? (
            <>
              <p>{t("creative.empty", { defaultValue: "还没有文章" })}</p>
              <Button variant="outline" size="sm" className="mt-3" onClick={onRestoreDemo}>
                {t("creative.restore_demo", { defaultValue: "恢复示例" })}
              </Button>
            </>
          ) : (
            <p>{t("creative.no_match", { defaultValue: "没有符合条件的文章" })}</p>
          )}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {articles.map((a) => (
            <div
              key={a.id}
              className="group flex flex-col rounded-panel border border-border bg-settings-surface p-4 transition-colors hover:border-primary/25"
            >
              <button
                type="button"
                onClick={() => onOpen(a.id)}
                className="flex-1 text-left"
              >
                <div className="mb-2 flex items-center justify-between">
                  <FileText className="h-4 w-4 text-primary/60" />
                  {statusTag(a.status)}
                </div>
                <h3 className="mb-1 text-[15px] font-medium leading-snug text-foreground">
                  {a.title}
                </h3>
                <p className="line-clamp-2 text-[12px] text-muted-foreground">
                  {a.summary || t("creative.no_summary", { defaultValue: "暂无摘要" })}
                </p>
              </button>
              <div className="mt-3 flex items-center justify-between border-t border-border pt-2 text-[11px] text-muted-foreground">
                <span>{a.updated}</span>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    aria-label="复制"
                    onClick={() => onDuplicate(a.id)}
                    className="rounded p-1 hover:bg-muted"
                  >
                    <Copy className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    aria-label="删除"
                    onClick={() => setDeleteTarget(a)}
                    className="rounded p-1 text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    aria-label="编辑"
                    onClick={() => onOpen(a.id)}
                    className="rounded p-1 hover:bg-muted"
                  >
                    <ArrowRight className="h-3 w-3" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <AlertDialog open={!!deleteTarget} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("creative.confirm_delete", { defaultValue: "确认删除？" })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget ? `「${deleteTarget.title}」将被移除，此操作无法撤销。` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => { if (deleteTarget) onDelete(deleteTarget.id); setDeleteTarget(null); }}>
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <p className="mt-4 text-[11px] text-muted-foreground">
        {t("creative.note_storage", {
          defaultValue: "当前内容仅在本次访问内保留，刷新前请导出草稿。",
        })}
      </p>
    </section>
  );
}
