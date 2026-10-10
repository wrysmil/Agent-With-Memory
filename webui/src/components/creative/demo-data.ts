import type { Article, Suggestion } from "./types";

const SEED_BODY = `## 为什么需要记忆

在实现一个个人 Agent 时，我发现一个很具体的问题：每次开启新对话，都要重新告诉它项目背景、我的习惯，以及上一次做到哪里。

这让我开始思考，除了保存聊天记录，还需要怎样的记忆结构。

## 从对话记录到长期记忆

对话记录保留发生过的事，长期记忆则需要提取其中能在以后帮助决策的信息。两者有联系，但承担的职责不同。

\`\`\`python
# 示例：先保存事实，再决定是否提取长期信息
await sessions.append(message)
await memory.consider(session_id)
\`\`\`

> 先把记录与提取分开，再讨论检索与更新的策略。

## 实现中的几个取舍

- 保留来源，便于回看上下文。
- 对可能过期的信息记录更新时间。
- 不把每句话都写进长期记忆。

## 接下来想验证什么

下一步，我想用真实的多轮工作任务，观察记忆是否减少了重复说明，以及错误记忆如何被修正。`;

export function demoArticles(): Article[] {
  return [
    {
      id: "a1",
      title: "从零构建 Agent 的记忆系统",
      template: "实践复盘",
      status: "写作中",
      summary:
        "从对话记录到长期记忆，整理这次实现过程中的思考与取舍。",
      body: SEED_BODY,
      outline: [
        "为什么需要记忆",
        "从对话记录到长期记忆",
        "实现中的几个取舍",
        "接下来想验证什么",
      ],
      materials: [
        {
          id: "m1",
          kind: "观点",
          title: "记忆不是更多的聊天记录",
          content:
            "我希望记忆能减少重复说明，并保留可追溯的来源。不是把全部聊天塞进上下文。",
          note: "保留作者观点，不扩大结论。",
          included: true,
        },
        {
          id: "m2",
          kind: "代码",
          title: "记忆写入调用示例",
          content:
            "await sessions.append(message)\nawait memory.consider(session_id)",
          note: "演示代码，尚未验证运行。",
          included: true,
        },
        {
          id: "m3",
          kind: "链接",
          title: "asyncio 文档",
          content: "https://docs.python.org/3/library/asyncio.html",
          note: "示例链接记录；未抓取网页。",
          included: true,
        },
      ],
      updated: "刚刚",
      revision: 0,
      sample: true,
    },
    {
      id: "a2",
      title: "沿着一次调用，读懂 Agent Loop",
      template: "源码阅读",
      status: "构思中",
      summary: "从消息进入到工具执行，画清楚一次任务的调用链。",
      body: `## 这次想读懂什么

先从一条消息开始，理解 Agent Loop 如何协调模型与工具。

## 入口与调用链

这里记录阅读过程中发现的关键入口。`,
      outline: [
        "这次想读懂什么",
        "入口与调用链",
        "关键实现",
        "设计取舍",
        "自己的理解",
      ],
      materials: [],
      updated: "昨天",
      revision: 0,
      sample: true,
    },
    {
      id: "a3",
      title: "学习 asyncio：从并发到任务取消",
      template: "学习总结",
      status: "待校对",
      summary: "通过几个小实验，整理协程、任务和取消传播的理解。",
      body: `## 带着什么问题学习

当一个任务被取消时，它创建的子任务会怎样？

## 动手验证

这篇草稿还需要补充实验代码和结果，暂时不下结论。`,
      outline: [
        "带着什么问题学习",
        "关键知识",
        "动手验证",
        "理解发生了什么变化",
        "还有哪些问题",
      ],
      materials: [],
      updated: "3 天前",
      revision: 0,
      sample: true,
    },
  ];
}

export function demoSuggestions(articles: Article[]): Suggestion[] {
  const a1 = articles.find((a) => a.id === "a1");
  if (!a1) return [];
  return [
    {
      articleId: "a1",
      baseRevision: a1.revision,
      target: "outline",
      label: "补充「未来展望」章节",
      before: a1.outline.join("\n"),
      after: [...a1.outline, "未来展望与开放问题"].join("\n"),
    },
  ];
}
