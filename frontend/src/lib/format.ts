// How costs, durations and counts are shown.

/** US dollars, with the precision small amounts need: "$0.0042", "$0.35", "$1.20". */
export function usd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  if (value < 1) return `$${value.toFixed(3)}`;
  return `$${value.toFixed(2)}`;
}

/** `850` → "850 ms", `12_300` → "12.3 s", `95_000` → "1 min 35 s". */
export function duration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

// One formatter for every number: `toLocaleString` builds a new one on each call, which is
// slow enough to stall rendering.
const NUMBER = new Intl.NumberFormat("en-US");

/** "1 step", "3 steps". */
export function plural(value: number, word: string, many = `${word}s`): string {
  return `${NUMBER.format(value)} ${value === 1 ? word : many}`;
}

/** A list in words: "A", "A and B", "A, B and C". */
export function listing(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** A file name for a task's result: "explain-solid-state-batteries.md". */
export function fileName(task: string): string {
  const slug = task
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60)
    .replace(/-$/, "");
  return `${slug || "result"}.md`;
}
