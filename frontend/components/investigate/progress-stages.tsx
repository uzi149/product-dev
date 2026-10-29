import { CheckCircle2, Circle, LoaderCircle } from "lucide-react";
import { stageLines } from "@/lib/queries";
import type { QueryState } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The query's stages as scope §10 words them, ticked off as they finish. */
export function ProgressStages({ state }: { state: QueryState }) {
  return (
    <ol aria-label="Progress" className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
      {stageLines(state).map((line) => (
        <li
          key={line.text}
          data-status={line.status}
          className={cn(
            "flex items-center gap-2 rounded-lg border px-3 py-2 text-xs",
            line.status === "waiting" && "text-muted-foreground",
            line.status === "active" && "border-blue/45 text-blue",
          )}
        >
          {line.status === "done" ? (
            <CheckCircle2 className="size-3.5 shrink-0 text-green" />
          ) : line.status === "active" ? (
            <LoaderCircle className="size-3.5 shrink-0 animate-spin" />
          ) : (
            <Circle className="size-3.5 shrink-0" />
          )}
          {line.text}
        </li>
      ))}
    </ol>
  );
}
