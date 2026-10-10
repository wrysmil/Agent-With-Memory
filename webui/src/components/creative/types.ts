export type ArticleStatus = "构思中" | "写作中" | "待校对" | "已完成";

export type MaterialKind = "观点" | "文本" | "代码" | "链接";

export interface Material {
  id: string;
  kind: MaterialKind;
  title: string;
  content: string;
  note?: string;
  included: boolean;
}

export interface Suggestion {
  articleId: string;
  baseRevision: number;
  target: "outline" | "body" | "materials";
  label: string;
  before: string;
  after: string;
}

export interface Article {
  id: string;
  title: string;
  template?: string;
  status: ArticleStatus;
  summary: string;
  body: string;
  outline: string[];
  materials: Material[];
  updated: string;
  revision: number;
  sample?: boolean;
}

export interface Template {
  name: string;
  desc: string;
  outline: string[];
}

export const ARTICLE_STATUSES: ArticleStatus[] = [
  "构思中",
  "写作中",
  "待校对",
  "已完成",
];

export const TEMPLATES: Template[] = [
  {
    name: "技术解析",
    desc: "把复杂概念，解释清楚",
    outline: [
      "问题从哪里来",
      "核心概念与原理",
      "用一个例子说明",
      "适用范围与限制",
      "我的理解",
    ],
  },
  {
    name: "源码阅读",
    desc: "沿着调用链，读懂设计",
    outline: [
      "这次想读懂什么",
      "入口与调用链",
      "关键实现",
      "设计取舍",
      "自己的理解",
    ],
  },
  {
    name: "实践复盘",
    desc: "记录过程，也记录取舍",
    outline: [
      "背景与目标",
      "我是怎么做的",
      "问题与排查",
      "结果与不足",
      "下一次怎么改",
    ],
  },
  {
    name: "学习总结",
    desc: "把新知识，变成自己的理解",
    outline: [
      "带着什么问题学习",
      "关键知识",
      "动手验证",
      "理解发生了什么变化",
      "还有哪些问题",
    ],
  },
];
