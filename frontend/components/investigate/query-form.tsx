"use client";

import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { localInputToIso } from "@/lib/format";
import { browserTimeZone } from "@/lib/queries";
import type { QueryInput, Understood } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The question, the cameras (none chosen = all) and an optional time range. Camera names and
 * time words in the question become filters too (step 4.1); what is chosen here wins.
 */
export function QueryForm({ busy, onSubmit }: { busy: boolean; onSubmit: (input: QueryInput) => void }) {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [text, setText] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  function toggle(id: string) {
    setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
  }

  return (
    <form
      className="grid gap-3 rounded-2xl border bg-card p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!text.trim()) return;
        onSubmit({
          query_text: text.trim(),
          camera_ids: chosen.length ? chosen : null,
          start: localInputToIso(from),
          end: localInputToIso(to),
          timezone: browserTimeZone(),
        });
      }}
    >
      <div className="flex gap-2">
        <Input
          aria-label="Question"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={500}
          placeholder="e.g. someone left a bag near the door yesterday afternoon"
          className="h-10 flex-1 text-sm"
        />
        <Button type="submit" size="lg" disabled={busy || !text.trim()}>
          <Search /> {busy ? "Searching…" : "Search"}
        </Button>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1">
          <span className="text-[10px] font-medium">Cameras</span>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Cameras">
            <Button
              type="button"
              size="sm"
              variant={chosen.length === 0 ? "secondary" : "outline"}
              aria-pressed={chosen.length === 0}
              onClick={() => setChosen([])}
            >
              All
            </Button>
            {cameras.data?.map((camera) => (
              <Button
                key={camera.id}
                type="button"
                size="sm"
                variant={chosen.includes(camera.id) ? "secondary" : "outline"}
                aria-pressed={chosen.includes(camera.id)}
                onClick={() => toggle(camera.id)}
              >
                {camera.display_name}
              </Button>
            ))}
          </div>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="q-from" className="text-[10px]">From</Label>
          <Input id="q-from" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="q-to" className="text-[10px]">To</Label>
          <Input id="q-to" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
      </div>
    </form>
  );
}

/** What the question was understood as; removing a chip searches again without that filter. */
export function UnderstoodChips({
  understood,
  searchText,
  onRemove,
}: {
  understood: Understood[];
  searchText: string | null;
  onRemove?: (kind: Understood["kind"]) => void;
}) {
  if (understood.length === 0 && !searchText) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs" aria-label="Understood">
      {understood.length > 0 && <span className="text-muted-foreground">Understood:</span>}
      {understood.map((u) => {
        const label = `${u.kind === "camera" ? "Camera" : "Time"}: ${u.label}`;
        return (
          <span
            key={`${u.kind}-${u.label}`}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5",
              u.kind === "camera" ? "border-blue/45 text-blue" : "border-green/45 text-green",
            )}
          >
            {label}
            {onRemove && (
              <button type="button" aria-label={`Remove ${label}`} onClick={() => onRemove(u.kind)}>
                <X className="size-3" />
              </button>
            )}
          </span>
        );
      })}
      {searchText && (
        <span className="text-muted-foreground">
          searching for <q className="text-foreground">{searchText}</q>
        </span>
      )}
    </div>
  );
}
