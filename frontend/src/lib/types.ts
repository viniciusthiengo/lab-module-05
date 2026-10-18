// The backend's API, as the browser sees it through the /api proxy.

export type AgentName = "supervisor" | "researcher" | "writer" | "reviewer" | "fact_checker";
export type RunStatus = "running" | "awaiting_approval" | "completed" | "failed" | "cancelled";
export type StepStatus = "pending" | "running" | "completed" | "failed";

export interface Health {
  status: "ok";
  /** Whether Claude is configured. */
  ready: boolean;
  supervisor_model: string | null;
  worker_model: string | null;
  web_search: boolean;
  active_runs: number;
  memory_notes: number;
  daily_budget_usd: number;
  spent_today_usd: number;
  run_budget_usd: number;
}

export interface Agent {
  name: AgentName;
  title: string;
  description: string;
  model: string;
  /** May search the web. */
  searches: boolean;
}

export interface RunOptions {
  max_iterations: number;
  web_search: boolean;
  require_approval: boolean;
  use_memory: boolean;
  remember: boolean;
}

export interface RunRequest extends RunOptions {
  task: string;
}

export interface Source {
  url: string;
  title: string;
}

export interface Step {
  number: number;
  agent: AgentName;
  task: string;
  /** The earlier steps the worker was given. */
  context: number[];
  round: number;
  iteration: number;
  status: StepStatus;
  result: string;
  sources: Source[];
  note: string | null;
  error: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  web_searches: number;
  cost_usd: number;
  duration_ms: number | null;
}

export interface Decision {
  round: number;
  iteration: number;
  kind: "delegate" | "final";
  reason: string;
  steps: number[];
  forced: boolean;
}

export interface MemoryNote {
  id: string;
  task: string;
  summary: string;
  created_at: number;
  score: number;
}

export interface Usage {
  model_calls: number;
  input_tokens: number;
  output_tokens: number;
  web_searches: number;
  cost_usd: number;
}

export interface AgentUsage extends Usage {
  agent: AgentName;
  title: string;
  model: string;
}

export interface RunView {
  id: string;
  task: string;
  status: RunStatus;
  options: RunOptions;
  final: string | null;
  forced: boolean;
  note: string | null;
  error: string | null;
  sources: Source[];
  decisions: Decision[];
  steps: Step[];
  iterations: number;
  round: number;
  memories: MemoryNote[];
  feedback: { round: number; feedback: string }[];
  usage: Usage;
  agents: AgentUsage[];
  cap_usd: number;
  actions: ("approve" | "reject" | "cancel")[];
  created_at: number;
  finished_at: number | null;
  duration_ms: number | null;
  version: number;
  last_event_id: number;
}

/** A delegation inside a `decision` event. */
export interface DelegatedStep {
  number: number;
  agent: AgentName;
  task: string;
  context: number[];
}

/** The events of `GET /runs/{id}/events`, one per Server-Sent Event. */
export type RunEvent = { id: number; at: number } & (
  | { type: "run_started"; task: string; options: RunOptions; cap_usd: number }
  | { type: "memory"; notes: { id: string; task: string }[] }
  | {
      type: "agent_started";
      agent: AgentName;
      step?: number;
      task?: string;
      iteration?: number;
      forced?: boolean;
    }
  | { type: "delta"; agent: AgentName; step: number | null; text: string }
  | { type: "protocol_error"; agent: "supervisor"; message: string }
  | {
      type: "decision";
      kind: "delegate" | "final";
      iteration: number;
      reason: string;
      steps?: DelegatedStep[];
      forced?: boolean;
    }
  | {
      type: "agent_finished";
      agent: AgentName;
      step: number;
      status: "completed" | "failed";
      result?: string;
      sources?: Source[];
      note?: string | null;
      error?: string;
    }
  | { type: "usage"; agent: AgentName; cost_usd: number }
  | { type: "awaiting_approval"; final: string; forced: boolean; note: string | null }
  | { type: "approved" }
  | { type: "feedback"; round: number; feedback: string }
  | {
      type: "run_finished";
      status: RunStatus;
      final: string | null;
      forced: boolean;
      note: string | null;
      error: string | null;
      cost_usd: number;
    }
);
