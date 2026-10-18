// How each agent looks in the interface.

import type { AgentName } from "./types";

export const AGENTS: Record<AgentName, { title: string; initial: string; role: string }> = {
  supervisor: { title: "Supervisor", initial: "S", role: "Plans, delegates and synthesizes" },
  researcher: { title: "Researcher", initial: "R", role: "Finds and summarizes information" },
  writer: { title: "Writer", initial: "W", role: "Produces the polished result" },
  reviewer: { title: "Reviewer", initial: "V", role: "Checks the draft and asks for fixes" },
  fact_checker: { title: "Fact-checker", initial: "F", role: "Verifies the claims one by one" },
};

export const AGENT_ORDER: AgentName[] = [
  "supervisor",
  "researcher",
  "writer",
  "reviewer",
  "fact_checker",
];

export function agentTitle(agent: AgentName): string {
  return AGENTS[agent]?.title ?? agent;
}
