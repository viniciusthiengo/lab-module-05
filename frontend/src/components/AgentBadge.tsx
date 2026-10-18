import { AGENTS } from "@/lib/agents";
import { cx } from "@/lib/dom";
import type { AgentName } from "@/lib/types";

import styles from "./AgentBadge.module.css";

/** An agent's colored initial, with its name unless `compact`. */
export function AgentBadge({
  agent,
  compact = false,
  active = false,
}: {
  agent: AgentName;
  compact?: boolean;
  active?: boolean;
}) {
  const { title, initial } = AGENTS[agent];
  return (
    <span className={cx(styles.badge, active && styles.active)} data-agent={agent}>
      <span className={styles.initial} aria-hidden>
        {initial}
      </span>
      {compact ? <span className="visually-hidden">{title}</span> : <span>{title}</span>}
    </span>
  );
}
