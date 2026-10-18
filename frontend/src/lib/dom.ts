/** Class names joined, skipping the falsy ones. */
export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

/** Saves `text` as a file named `name` in the visitor's downloads. */
export function downloadText(name: string, text: string, type = "text/plain"): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
