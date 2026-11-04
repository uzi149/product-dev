import type { DeletePreview } from "./types";

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

/** The delete dialog's sentence: what goes, for good (ADR 0026). */
export function deleteSummary(preview: DeletePreview): string {
  const parts = [
    plural(preview.segments, "segment"),
    plural(preview.analysed_windows, "analysed window"),
    plural(preview.static_periods, "no-change period"),
    plural(preview.uploads, "upload"),
  ];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]} are deleted for good, with their files.`;
}
