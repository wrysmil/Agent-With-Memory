import type {
  AgentCategory,
  AgentProfile,
  ModelDescriptor,
  SkillDescriptor,
  ToolDescriptor,
} from "@/lib/agents/types";
import { AGENT_CATEGORIES } from "@/lib/agents/types";

export const MOCK_TOOLS: ToolDescriptor[] = [
  { name: "read_file", label: "读取文件", description: "按行区间读取文件内容", category: "filesystem", risk: "low", scope: "core", locked: true },
  { name: "write_file", label: "写入文件", description: "覆盖写入整个文件", category: "filesystem", risk: "medium", scope: "core" },
  { name: "edit_file", label: "编辑文件", description: "按精确字符串替换局部内容", category: "filesystem", risk: "medium", scope: "core" },
  { name: "list_dir", label: "列出目录", description: "浏览目录树结构", category: "filesystem", risk: "low", scope: "core" },
  { name: "apply_patch", label: "应用补丁", description: "以 unified diff 形式修改文件", category: "filesystem", risk: "medium", scope: "core" },
  { name: "delete_path", label: "删除路径", description: "递归删除文件或目录", category: "filesystem", risk: "high", scope: "core" },

  { name: "shell", label: "执行命令", description: "在沙箱中执行 shell 命令", category: "execution", risk: "high", scope: "core" },
  { name: "exec_session", label: "长时命令会话", description: "维持一个可交互的 shell 会话", category: "execution", risk: "high", scope: "core" },
  { name: "runtime_control", label: "运行时控制", description: "中止、查询正在运行的命令", category: "execution", risk: "medium", scope: "core" },
  { name: "process", label: "进程管理", description: "查看与管理后台进程", category: "execution", risk: "medium", scope: "subagent" },

  { name: "web_search", label: "网络搜索", description: "调用搜索引擎检索资料", category: "web", risk: "low", scope: "core" },
  { name: "web_fetch", label: "抓取网页", description: "抓取 URL 并转为可读文本", category: "web", risk: "low", scope: "core" },
  { name: "browser", label: "浏览器操作", description: "打开页面、点击、填表、截图", category: "web", risk: "medium", scope: "plugin" },

  { name: "memory_search", label: "检索记忆", description: "在长期记忆中做语义检索", category: "memory", risk: "low", scope: "core" },
  { name: "memory_write", label: "写入记忆", description: "把新知识写入长期记忆", category: "memory", risk: "medium", scope: "core" },
  { name: "session_messages", label: "读取会话历史", description: "拉取指定会话的消息记录", category: "memory", risk: "low", scope: "core" },

  { name: "cron_create", label: "创建定时任务", description: "新建一个周期性触发的任务", category: "scheduling", risk: "medium", scope: "core" },
  { name: "cron_list", label: "查看定时任务", description: "列出已注册的周期任务", category: "scheduling", risk: "low", scope: "core" },
  { name: "cron_delete", label: "删除定时任务", description: "取消一个周期任务", category: "scheduling", risk: "medium", scope: "core" },

  { name: "sessions", label: "会话管理", description: "列出、读取、重命名会话", category: "session", risk: "low", scope: "core", locked: true },
  { name: "session_send", label: "发送消息", description: "向指定会话投递消息", category: "session", risk: "medium", scope: "core" },

  { name: "spawn", label: "派生子 Agent", description: "派发一个子任务给另一个 Agent", category: "orchestration", risk: "medium", scope: "core", locked: true },
  { name: "long_task", label: "长程目标", description: "维持跨多轮的持续目标状态", category: "orchestration", risk: "medium", scope: "core" },
  { name: "mcp", label: "MCP 工具", description: "调用外部 MCP server 暴露的工具", category: "orchestration", risk: "medium", scope: "plugin" },

  { name: "image_generation", label: "生成图片", description: "调用图像模型生成插画", category: "media", risk: "low", scope: "core" },
  { name: "transcribe", label: "语音转写", description: "把音频文件转成文本", category: "media", risk: "low", scope: "core" },
];

export const MOCK_SKILLS: SkillDescriptor[] = [
  { name: "github", description: "GitHub 仓库读写、Issue 与 PR 操作", source: "builtin", tags: ["开发"] },
  { name: "summarize", description: "长文本结构化摘要，支持要点与 TL;DR", source: "builtin", tags: ["通用"] },
  { name: "cron", description: "用自然语言创建与管理周期任务", source: "builtin", tags: ["效率"] },
  { name: "tmux", description: "tmux 会话管理与窗口编排", source: "builtin", tags: ["运维"] },
  { name: "weather", description: "查询多地天气与预报", source: "builtin", tags: ["通用"] },
  { name: "image-generation", description: "端到端生成插画与配图", source: "builtin", tags: ["创作"] },
  { name: "update-setup", description: "检查并升级本地安装版本", source: "builtin", tags: ["运维"] },
  { name: "memory", description: "记忆检索与写入的最佳实践", source: "workspace", tags: ["通用"] },
  { name: "skill-creator", description: "创建、改造并评估新技能", source: "workspace", tags: ["开发"] },
  { name: "clawhub", description: "从技能市场安装与更新技能", source: "workspace", tags: ["开发"] },
  { name: "my", description: "个人偏好与长期设定的读写入口", source: "workspace", tags: ["通用"] },
  { name: "avatar-studio", description: "生成一致风格的头像与角色形象", source: "plugin", tags: ["创作"] },
  { name: "chart-builder", description: "把结构化数据渲染成图表", source: "plugin", tags: ["研究"] },
];

export const MOCK_MODELS: ModelDescriptor[] = [
  { id: "claude-opus-5", label: "claude-opus-5", provider: "anthropic", contextWindow: 200_000, health: "healthy", vision: true, toolUse: true },
  { id: "claude-sonnet-5", label: "claude-sonnet-5", provider: "anthropic", contextWindow: 200_000, health: "healthy", vision: true, toolUse: true },
  { id: "claude-haiku-4-5", label: "claude-haiku-4-5", provider: "anthropic", contextWindow: 200_000, health: "healthy", vision: false, toolUse: true },
  { id: "gpt-5.2", label: "gpt-5.2", provider: "openai", contextWindow: 400_000, health: "healthy", vision: true, toolUse: true },
  { id: "gpt-5.2-mini", label: "gpt-5.2-mini", provider: "openai", contextWindow: 400_000, health: "degraded", statusNote: "响应延迟偏高", vision: false, toolUse: true },
  { id: "gemini-3-pro", label: "gemini-3-pro", provider: "google", contextWindow: 1_000_000, health: "healthy", vision: true, toolUse: true },
  { id: "glm-4.7", label: "glm-4.7", provider: "zhipu", contextWindow: 200_000, health: "unavailable", statusNote: "未配置 API Key", vision: false, toolUse: true },
  { id: "qwen3-max", label: "qwen3-max", provider: "alibaba", contextWindow: 256_000, health: "unavailable", statusNote: "未配置 API Key", vision: false, toolUse: false },
];

const now = Date.parse("2026-09-29T09:00:00Z");
const hoursAgo = (h: number) => new Date(now - h * 3_600_000).toISOString();

export const MOCK_AGENTS: AgentProfile[] = [
  {
    id: "default",
    name: "小秋",
    description: "通用全能助手，拥有所有技能",
    type: "system",
    customized: true,
    categoryId: "general",
    icon: "🦊",
    color: "#4A90D9",
    prompt:
      "你是 {{name}}，一个 {{description}}。\n" +
      "当前可用技能：{{skills}}\n" +
      "当前可用工具：{{tools}}\n" +
      "今天是 {{date}}。",
    modelId: null,
    tools: { mode: "all", entries: [] },
    skills: { mode: "all", entries: [] },
    subAgents: { mode: "include", entries: ["seo-writer", "data-analyst", "code-review"] },
    hidden: false,
    updatedAt: hoursAgo(2),
  },
  {
    id: "translator",
    name: "翻译官",
    description: "中英日互译，保持术语一致与原文语气",
    type: "custom",
    customized: false,
    categoryId: "writing",
    icon: "🈯",
    color: "#E67E22",
    prompt: "你是专业译者。保持术语表一致，输出时给出译文与关键注释。",
    modelId: null,
    tools: { mode: "include", entries: ["read_file", "write_file", "web_fetch", "sessions"] },
    skills: { mode: "include", entries: ["summarize", "my"] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: hoursAgo(30),
  },
  {
    id: "seo-writer",
    name: "SEO 写手",
    description: "关键词研究、SERP 分析与长文结构规划",
    type: "custom",
    customized: false,
    categoryId: "writing",
    icon: "🔍",
    color: "#16A085",
    prompt: "先做关键词聚类，再输出文章大纲，最后才写正文。每一步都等我确认。",
    modelId: "gpt-5.2",
    tools: { mode: "include", entries: ["read_file", "write_file", "web_search", "web_fetch", "sessions"] },
    skills: { mode: "include", entries: ["summarize"] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: hoursAgo(70),
  },
  {
    id: "data-analyst",
    name: "数据分析师",
    description: "清洗分析数据集，产出图表与结论",
    type: "custom",
    customized: false,
    categoryId: "research",
    icon: "📊",
    color: "#8E44AD",
    prompt: "先复述你对数据质量的判断，再给分析步骤。不要跳过缺失值处理。",
    modelId: null,
    tools: { mode: "include", entries: ["read_file", "write_file", "shell", "exec_session", "sessions", "spawn"] },
    skills: { mode: "include", entries: ["chart-builder", "summarize"] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: hoursAgo(96),
  },
  {
    id: "code-review",
    name: "代码审查",
    description: "逐行审查变更，只报真实问题，不提风格偏好",
    type: "custom",
    customized: false,
    categoryId: "coding",
    icon: "⚙️",
    color: "#2C3E50",
    prompt: "只报告会导致错误、崩溃或安全问题的发现。每条给出文件:行号与复现路径。",
    modelId: "claude-opus-5",
    tools: { mode: "exclude", entries: ["delete_path", "cron_create", "cron_delete", "image_generation"] },
    skills: { mode: "include", entries: ["github"] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: hoursAgo(120),
  },
  {
    id: "oncall",
    name: "值班员",
    description: "监控告警、定位故障、执行预案",
    type: "custom",
    customized: false,
    categoryId: "ops",
    icon: "🕐",
    color: "#C0392B",
    prompt: "先复述告警现象与影响面，再给处置步骤。破坏性操作前必须二次确认。",
    modelId: "claude-sonnet-5",
    tools: { mode: "all", entries: [] },
    skills: { mode: "include", entries: ["cron", "tmux", "update-setup", "github"] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: hoursAgo(200),
  },
  {
    id: "illustrator",
    name: "画师",
    description: "为文章生成风格统一的配图",
    type: "custom",
    customized: false,
    categoryId: "efficiency",
    icon: "🎨",
    color: "#D4AF37",
    prompt: "先描述你想要的画面与色调，确认后再出图。保持角色形象前后一致。",
    modelId: "gemini-3-pro",
    tools: { mode: "include", entries: ["read_file", "write_file", "image_generation", "sessions"] },
    skills: { mode: "include", entries: ["image-generation", "avatar-studio"] },
    subAgents: { mode: "all", entries: [] },
    hidden: false,
    updatedAt: hoursAgo(260),
  },
  {
    id: "briefing",
    name: "简报助手",
    description: "每天早上汇总日程、待办与昨日进展",
    type: "custom",
    customized: false,
    categoryId: "efficiency",
    icon: "📮",
    color: "#27AE60",
    prompt: "输出一页纸简报：今日重点、待确认事项、风险提示。",
    modelId: "claude-haiku-4-5",
    tools: { mode: "include", entries: ["read_file", "cron_list", "sessions", "memory_search"] },
    skills: { mode: "include", entries: ["summarize", "my"] },
    subAgents: { mode: "all", entries: [] },
    hidden: true,
    updatedAt: hoursAgo(320),
  },
];

export const MOCK_CATEGORIES: AgentCategory[] = AGENT_CATEGORIES;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Stands in for listAgents(token) until the backend route lands. */
export async function loadAgents(): Promise<AgentProfile[]> {
  await delay(120);
  return clone(MOCK_AGENTS);
}

export async function loadAgentCatalog(): Promise<{
  tools: ToolDescriptor[];
  skills: SkillDescriptor[];
  models: ModelDescriptor[];
  categories: AgentCategory[];
}> {
  await delay(120);
  return {
    tools: clone(MOCK_TOOLS),
    skills: clone(MOCK_SKILLS),
    models: clone(MOCK_MODELS),
    categories: clone(MOCK_CATEGORIES),
  };
}
