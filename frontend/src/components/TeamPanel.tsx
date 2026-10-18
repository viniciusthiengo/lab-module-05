"use client";

import { AGENT_ORDER, AGENTS } from "@/lib/agents";
import { cx } from "@/lib/dom";
import { usd } from "@/lib/format";
import { type LiveRun, working } from "@/lib/run";
import type { Agent } from "@/lib/types";

import { AgentBadge } from "./AgentBadge";
import styles from "./Team.module.css";
import ui from "./ui.module.css";

/** The team, and who is working on what right now. */
export function TeamPanel({ agents, run }: { agents: Agent[]; run: LiveRun | null }) {
  const busy = run ? working(run) : [];
  const models = Object.fromEntries(agents.map((agent) => [agent.name, agent]));
  const spent = Object.fromEntries((run?.view?.agents ?? []).map((usage) => [usage.agent, usage]));
  const steps = run?.activity.filter((item) => item.kind === "step") ?? [];

  return (
    <section className={ui.panel} aria-labelledby="team-title">
      <div className={ui.panelHeader}>
        <h2 id="team-title">Team</h2>
        <span className={ui.hint}>{busy.length ? `${busy.length} working` : "idle"}</span>
      </div>
      <ul className={styles.team} aria-live="polite">
        {AGENT_ORDER.map((name) => {
          const now = busy.filter((entry) => entry.agent === name);
          const done = steps.filter(
            (step) => step.agent === name && step.status === "completed",
          ).length;
          const agent = models[name];
          return (
            <li
              key={name}
              className={cx(styles.member, now.length > 0 && styles.busy)}
              data-agent={name}
            >
              <div className={styles.memberHeader}>
                <AgentBadge agent={name} active={now.length > 0} />
                {agent?.searches && <span className={ui.chip}>web</span>}
              </div>
              <p className={styles.role}>{AGENTS[name].role}</p>
              {now.map((entry) => (
                <p key={entry.step ?? "supervisor"} className={styles.current}>
                  {entry.step ? `Step ${entry.step}: ` : ""}
                  {entry.task}
                </p>
              ))}
              <p className={styles.meta}>
                {agent?.model}
                {name !== "supervisor" && done > 0 && ` · ${done} done`}
                {spent[name] && ` · ${usd(spent[name].cost_usd)}`}
              </p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
