import { describe, expect, it } from "vitest";
import {
  canAcceptSuggestion,
  createArticle,
  searchArticles,
  filterByStatus,
  isSafeUrl,
  buildExportFilename,
} from "@/components/creative/article-model";
import type { Article } from "@/components/creative/types";

function fakeArticle(overrides: Partial<Article> = {}): Article {
  return {
    id: "art-1",
    title: "Test",
    status: "写作中",
    summary: "",
    body: "",
    outline: [],
    materials: [],
    updated: "刚刚",
    revision: 0,
    ...overrides,
  };
}

describe("article-model", () => {
  describe("canAcceptSuggestion", () => {
    it("returns false for different articles", () => {
      expect(
        canAcceptSuggestion(
          { id: "art-1", revision: 0 },
          { articleId: "art-2", baseRevision: 0 },
        ),
      ).toBe(false);
    });

    it("returns false for old revision", () => {
      expect(
        canAcceptSuggestion(
          { id: "art-1", revision: 3 },
          { articleId: "art-1", baseRevision: 1 },
        ),
      ).toBe(false);
    });

    it("returns true for matching article and revision", () => {
      expect(
        canAcceptSuggestion(
          { id: "art-1", revision: 2 },
          { articleId: "art-1", baseRevision: 2 },
        ),
      ).toBe(true);
    });
  });

  describe("createArticle", () => {
    it("rejects blank title", () => {
      const { article, error } = createArticle("");
      expect(article).toBeUndefined();
      expect(error).toBeTruthy();
    });

    it("creates article with template outline", () => {
      const { article, error } = createArticle("My Post", "源码阅读");
      expect(error).toBeNull();
      expect(article!.title).toBe("My Post");
      expect(article!.status).toBe("构思中");
      expect(article!.outline.length).toBeGreaterThan(0);
    });
  });

  describe("searchArticles", () => {
    const articles = [
      fakeArticle({ id: "1", title: "Alpha", summary: "about cats" }),
      fakeArticle({ id: "2", title: "Beta", summary: "about dogs" }),
    ];

    it("filters by title", () => {
      expect(searchArticles(articles, "alpha").length).toBe(1);
    });

    it("returns empty on no match", () => {
      expect(searchArticles(articles, "xyz").length).toBe(0);
    });

    it("returns all for empty query", () => {
      expect(searchArticles(articles, "").length).toBe(2);
    });
  });

  describe("filterByStatus", () => {
    const articles = [
      fakeArticle({ id: "1", status: "写作中" }),
      fakeArticle({ id: "2", status: "构思中" }),
    ];

    it("filters to one status", () => {
      expect(filterByStatus(articles, "写作中").length).toBe(1);
    });

    it("returns all for 全部", () => {
      expect(filterByStatus(articles, "全部").length).toBe(2);
    });
  });

  describe("isSafeUrl", () => {
    it("rejects javascript: protocol", () => {
      expect(isSafeUrl("javascript:alert(1)")).toBe(false);
    });

    it("rejects data: protocol", () => {
      expect(isSafeUrl("data:text/html,<script>")).toBe(false);
    });

    it("accepts https", () => {
      expect(isSafeUrl("https://example.com")).toBe(true);
    });
  });

  describe("buildExportFilename", () => {
    it("cleans invalid characters", () => {
      const name = buildExportFilename("Hello: World/<>?");
      expect(name).not.toContain(":");
      expect(name).not.toContain("/");
      expect(name).not.toContain("<");
      expect(name).toMatch(/\.md$/);
    });
  });
});
