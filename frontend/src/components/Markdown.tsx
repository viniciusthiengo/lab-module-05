import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import styles from "./Markdown.module.css";

const components: Components = {
  a({ href, children }) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
};

/** Markdown written by the agents (raw HTML is not rendered; links open in a new tab). */
export function Markdown({ children }: { children: string }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
