import { cx } from "@/lib/dom";
import { usd } from "@/lib/format";
import type { Health } from "@/lib/types";

import styles from "./StatusBar.module.css";
import ui from "./ui.module.css";

/** What the server runs: models, web search, today's spending. */
export function StatusBar({ health, error }: { health: Health | null; error: string | null }) {
  if (error && !health) {
    return (
      <p className={cx(ui.alert, styles.bar)} role="alert">
        {error}
      </p>
    );
  }
  if (!health) {
    return (
      <p className={cx(styles.bar, ui.muted)} aria-live="polite">
        <span className={ui.spinner} aria-hidden /> Connecting to the server…
      </p>
    );
  }
  return (
    <ul className={styles.bar} aria-label="Server status">
      <li className={cx(ui.chip, health.ready ? ui.chipSuccess : ui.chipWarning)}>
        {health.ready ? "Team ready" : "No Claude API key on the server"}
      </li>
      {health.supervisor_model && (
        <li className={ui.chip}>Supervisor: {health.supervisor_model}</li>
      )}
      {health.worker_model && <li className={ui.chip}>Workers: {health.worker_model}</li>}
      <li className={ui.chip}>Web search {health.web_search ? "on" : "off"}</li>
      <li className={ui.chip}>
        Spent today: {usd(health.spent_today_usd)} of {usd(health.daily_budget_usd)}
      </li>
    </ul>
  );
}
