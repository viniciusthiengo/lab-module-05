"use client";

import { useServer } from "@/lib/useServer";
import { useRuns } from "@/lib/useRuns";

import { ChatPanel } from "./ChatPanel";
import { MemoryPanel } from "./MemoryPanel";
import { StatusBar } from "./StatusBar";
import styles from "./TeamApp.module.css";
import { TeamPanel } from "./TeamPanel";

/** The page: the conversation with the team, and the team and its memory beside it. */
export function TeamApp() {
  const server = useServer();
  const tasks = useRuns(server.refresh);
  const latest = tasks.runs.at(-1) ?? null;

  return (
    <>
      <StatusBar health={server.health} error={server.error} />
      <div className={styles.layout}>
        <ChatPanel health={server.health} tasks={tasks} />
        <aside className={styles.side}>
          <TeamPanel agents={server.agents} run={latest} />
          <MemoryPanel
            notes={server.memory}
            onForget={server.forget}
            onForgetAll={server.forgetAll}
          />
        </aside>
      </div>
    </>
  );
}
