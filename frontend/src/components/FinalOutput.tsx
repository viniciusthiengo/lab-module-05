"use client";

import { useState } from "react";

import { cx, downloadText } from "@/lib/dom";
import { fileName } from "@/lib/format";
import type { Source } from "@/lib/types";

import styles from "./Chat.module.css";
import { Markdown } from "./Markdown";
import ui from "./ui.module.css";

interface Props {
  task: string;
  final: string;
  forced: boolean;
  note: string | null;
  sources: Source[];
  /** The result waits for approval. */
  draft: boolean;
}

/** The Supervisor's final result, formatted, with what the workers cited. */
export function FinalOutput({ task, final, forced, note, sources, draft }: Props) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(final);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // The clipboard can be unavailable (an insecure context): nothing to do.
    }
  }

  return (
    <section className={styles.final} aria-label="Final result">
      <div className={styles.finalHeader}>
        <h3>{draft ? "Result for your approval" : "Final result"}</h3>
        {forced && (
          <span
            className={cx(ui.chip, ui.chipWarning)}
            title="The decision limit or the cost cap forced the end"
          >
            forced
          </span>
        )}
        <span className={styles.finalActions}>
          <button type="button" className={ui.link} onClick={() => void copy()}>
            {copied ? "Copied" : "Copy"}
          </button>
          <button
            type="button"
            className={ui.link}
            onClick={() => downloadText(fileName(task), final, "text/markdown")}
          >
            Download .md
          </button>
        </span>
      </div>
      {note && <p className={ui.hint}>{note}</p>}
      <Markdown>{final}</Markdown>
      {sources.length > 0 && (
        <details className={styles.sources}>
          <summary>Pages the workers cited ({sources.length})</summary>
          <ol>
            {sources.map((source) => (
              <li key={source.url}>
                <a href={source.url} target="_blank" rel="noopener noreferrer">
                  {source.title}
                </a>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
