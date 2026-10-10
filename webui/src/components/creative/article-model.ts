import type { Article, ArticleStatus } from "./types";
import { TEMPLATES } from "./types";

let counter = 100;
export function nextId(): string {
  return `art-${++counter}`;
}

export function canAcceptSuggestion(
  article: { id: string; revision: number },
  suggestion: { articleId: string; baseRevision: number },
): boolean {
  return (
    article.id === suggestion.articleId &&
    article.revision === suggestion.baseRevision
  );
}

export function createArticle(
  title: string,
  templateName?: string,
): { article?: Article; error: string | null } {
  if (!title.trim()) {
    return { error: "请输入文章主题" };
  }
  const template = TEMPLATES.find((t) => t.name === templateName);
  const article: Article = {
    id: nextId(),
    title: title.trim(),
    template: template?.name,
    status: "构思中",
    summary: "",
    body: "",
    outline: template ? template.outline.slice() : [],
    materials: [],
    updated: "刚刚",
    revision: 0,
  };
  return { article, error: null };
}

export function duplicateArticle(article: Article): Article {
  return {
    ...article,
    id: nextId(),
    title: `${article.title}（副本）`,
    sample: false,
    revision: 0,
    updated: "刚刚",
    materials: article.materials.map((m) => ({ ...m, id: `m-${nextId()}` })),
  };
}

export function touch(article: Article): Article {
  return {
    ...article,
    revision: article.revision + 1,
    updated: "刚刚",
  };
}

export function searchArticles(
  articles: Article[],
  query: string,
): Article[] {
  if (!query.trim()) return articles;
  const lower = query.toLowerCase();
  return articles.filter(
    (a) =>
      a.title.toLowerCase().includes(lower) ||
      a.summary.toLowerCase().includes(lower),
  );
}

export function filterByStatus(
  articles: Article[],
  status: ArticleStatus | "全部",
): Article[] {
  if (status === "全部") return articles;
  return articles.filter((a) => a.status === status);
}

export function buildExportFilename(title: string): string {
  const cleaned = title
    .split("")
    .filter((ch) => ch.charCodeAt(0) >= 0x20 && !'<>:"/\\|?*'.includes(ch))
    .join("")
    .trim()
    .replace(/\s+/g, "-");
  return `${cleaned || "未命名文章"}.md`;
}

export function generateMarkdown(article: Article): string {
  let out = `# ${article.title}\n\n`;
  if (article.summary) out += `> ${article.summary}\n\n`;
  out += article.body;
  return out;
}

const DANGEROUS_PROTOCOLS = ["javascript:", "data:", "vbscript:"];

export function isSafeUrl(url: string): boolean {
  const lower = url.trim().toLowerCase();
  return !DANGEROUS_PROTOCOLS.some((p) => lower.startsWith(p));
}
