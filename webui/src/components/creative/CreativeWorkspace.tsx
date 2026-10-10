import { useState, useCallback } from "react";

import type { Article, ArticleStatus, Suggestion } from "./types";
import {
  createArticle,
  duplicateArticle,
  searchArticles,
  filterByStatus,
  touch,
  canAcceptSuggestion,
} from "./article-model";
import { demoArticles, demoSuggestions } from "./demo-data";
import { ArticleList } from "./ArticleList";
import { ArticleEditor } from "./ArticleEditor";

type View = "list" | "editor";

export default function CreativeWorkspace() {
  const [articles, setArticles] = useState<Article[]>(demoArticles);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [view, setView] = useState<View>("list");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<ArticleStatus | "全部">(
    "全部",
  );
  const [suggestions, setSuggestions] = useState<Suggestion[]>(() =>
    demoSuggestions(demoArticles()),
  );
  const [undoStack, setUndoStack] = useState<
    { articleId: string; revisionBeforeApply: number; snapshot: Article }[]
  >([]);

  const activeArticle = articles.find((a) => a.id === activeId) ?? null;

  const openEditor = useCallback((id: string) => {
    setActiveId(id);
    setView("editor");
  }, []);

  const backToList = useCallback(() => setView("list"), []);

  const handleCreate = useCallback(
    (title: string, templateName?: string) => {
      const { article, error } = createArticle(title, templateName);
      if (error || !article) return { error: error ?? "未知错误" };
      setArticles((prev) => [article, ...prev]);
      setActiveId(article.id);
      setView("editor");
      const newSuggestions = demoSuggestions([article]);
      if (newSuggestions.length) setSuggestions((prev) => [...prev, ...newSuggestions]);
      return { error: null };
    },
    [],
  );

  const handleDuplicate = useCallback((id: string) => {
    setArticles((prev) => {
      const src = prev.find((a) => a.id === id);
      if (!src) return prev;
      return [duplicateArticle(src), ...prev];
    });
  }, []);

  const handleDelete = useCallback((id: string) => {
    setArticles((prev) => prev.filter((a) => a.id !== id));
    setActiveId((prev) => (prev === id ? null : prev));
    setSuggestions((prev) => prev.filter((s) => s.articleId !== id));
  }, []);

  const updateArticle = useCallback(
    (id: string, patch: Partial<Article>) => {
      setArticles((prev) =>
        prev.map((a) => (a.id === id ? touch({ ...a, ...patch }) : a)),
      );
    },
    [],
  );

  const acceptSuggestion = useCallback(
    (suggestion: Suggestion): boolean => {
      if (!activeArticle) return false;
      if (!canAcceptSuggestion(activeArticle, suggestion)) return false;

      const snapshot = { ...activeArticle };
      setUndoStack((prev) => [
        ...prev.filter((u) => u.articleId !== suggestion.articleId),
        {
          articleId: suggestion.articleId,
          revisionBeforeApply: activeArticle.revision,
          snapshot,
        },
      ]);

      if (suggestion.target === "outline") {
        updateArticle(activeArticle.id, {
          outline: suggestion.after.split("\n").filter(Boolean),
        });
      } else if (suggestion.target === "body") {
        updateArticle(activeArticle.id, { body: suggestion.after });
      }
      setSuggestions((prev) => prev.filter((s) => s !== suggestion));
      return true;
    },
    [activeArticle, updateArticle],
  );

  const undoSuggestion = useCallback(
    (suggestion: Suggestion): boolean => {
      if (!activeArticle) return false;
      const entry = undoStack.find(
        (u) =>
          u.articleId === suggestion.articleId &&
          activeArticle.revision === u.revisionBeforeApply + 1,
      );
      if (!entry) return false;
      setArticles((prev) =>
        prev.map((a) => (a.id === entry.articleId ? entry.snapshot : a)),
      );
      setUndoStack((prev) => prev.filter((u) => u !== entry));
      setSuggestions((prev) => [...prev, suggestion]);
      return true;
    },
    [activeArticle, undoStack],
  );

  const restoreDemo = useCallback(() => {
    const demo = demoArticles();
    setArticles(demo);
    setSuggestions(demoSuggestions(demo));
  }, []);

  const filtered = searchArticles(filterByStatus(articles, statusFilter), query);

  if (view === "editor" && activeArticle) {
    return (
      <ArticleEditor
        article={activeArticle}
        suggestions={suggestions.filter((s) => s.articleId === activeArticle.id)}
        onUpdate={(patch) => updateArticle(activeArticle.id, patch)}
        onBack={backToList}
        onAcceptSuggestion={acceptSuggestion}
        onUndoSuggestion={undoSuggestion}
      />
    );
  }

  return (
    <ArticleList
      articles={filtered}
      totalCount={articles.length}
      query={query}
      statusFilter={statusFilter}
      onQueryChange={setQuery}
      onStatusFilterChange={setStatusFilter}
      onOpen={openEditor}
      onCreate={handleCreate}
      onDuplicate={handleDuplicate}
      onDelete={handleDelete}
      onRestoreDemo={restoreDemo}
    />
  );
}
