import type { CompiledRule, Trigger, TriggerTest, Verdict } from "./types";

/** "place_object" -> "place object" */
export function actionWord(action: string): string {
  return action.replaceAll("_", " ");
}

/** ["place_object", "leave"] -> "place object → leave" */
export function sequenceWording(sequence: string[]): string {
  return sequence.map(actionWord).join(" → ");
}

/** 30 -> "30 s", 120 -> "2 min", 90 -> "1 min 30 s", 3600 -> "1 h". */
export function durationWording(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const parts = [];
  if (h) parts.push(`${h} h`);
  if (m) parts.push(`${m} min`);
  if (s || parts.length === 0) parts.push(`${s} s`);
  return parts.join(" ");
}

/** 0.8 -> "80 %" */
export function percent(value: number): string {
  return `${Math.round(value * 100)} %`;
}

/** What the rule was understood as, in one line (the builder's "Understood as"). */
export function ruleWording(rule: CompiledRule): string {
  if (rule.mode === "semantic") return `Similar to “${rule.search_text}”`;
  const sequences = [rule.required_action_sequence, ...rule.alternative_action_sequences]
    .filter((s) => s.length > 0)
    .map(sequenceWording);
  const gap = rule.required_action_sequence.length > 1 ? `, within ${durationWording(rule.maximum_sequence_gap_seconds)}` : "";
  return `${sequences.join(", or ")}${gap}`;
}

/** ["person", "portable_object"] -> "person, portable object" */
export function entitiesWording(rule: CompiledRule): string {
  return rule.required_entities.map(actionWord).join(", ");
}

export type StateTone = "green" | "orange" | "red" | "muted";

/** Badge colours for a tone (Tailwind classes). */
export const STATE_TONE: Record<StateTone, string> = {
  green: "border-green/45 bg-green/10 text-green",
  orange: "border-orange/45 bg-orange/10 text-orange",
  red: "border-red/45 bg-red/15 text-red",
  muted: "text-muted-foreground",
};

/** A trigger's state on the list: compiling first, then whether it runs. */
export function triggerState(trigger: Trigger): { label: string; tone: StateTone } {
  if (trigger.compile_status === "pending") return { label: "Compiling…", tone: "orange" };
  if (trigger.compile_status === "failed") return { label: "Couldn't compile", tone: "red" };
  return trigger.enabled ? { label: "Enabled", tone: "green" } : { label: "Disabled", tone: "muted" };
}

/** A match's or an event's verification, in words. */
export function verificationWording(status: string | null | undefined): string {
  switch (status) {
    case "verified":
      return "Verified by the VLM";
    case "rejected":
      return "Rejected by the VLM";
    case "not_required":
      return "No VLM verification";
    case "unverifiable":
      return "Couldn't be verified";
    case "not_verified_local":
      return "Not verified (local model)";
    case "not_checked":
      return "Not checked (verification limit)";
    default:
      return "—";
  }
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  would_report: "Would report",
  rejected: "Rejected by the VLM",
  below_confidence: "Below the confidence",
};

export function sourceWording(test: Pick<TriggerTest, "source" | "upload_filename">): string {
  switch (test.source) {
    case "video":
      return test.upload_filename ? `Video “${test.upload_filename}”` : "An uploaded video";
    case "last_hour":
      return "The last hour";
    case "last_24h":
      return "The last 24 hours";
    case "range":
      return "A custom range";
  }
}

const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

/** Where a test is, or what it found. */
export function testWording(test: TriggerTest): string {
  if (test.status === "failed") return `The test failed: ${test.error ?? "unknown error"}`;
  if (test.status === "pending") return "Waiting for the query worker…";
  if (test.status === "running") {
    switch (test.stage) {
      case "checking":
        return "Checking the rule on the candidates…";
      case "verifying":
        return "Verifying the matches with the VLM…";
      default:
        return "Searching the stored footage…";
    }
  }
  const { would_report, rejected, below_confidence } = test.counts;
  const parts = [plural(would_report, "would report", "would report")];
  if (rejected) parts.push(`${rejected} rejected by the VLM`);
  if (below_confidence) parts.push(`${below_confidence} below the confidence`);
  const footage = test.footage_seconds ? ` in ${durationWording(test.footage_seconds)} of stored footage` : "";
  return `${parts.join(", ")}${footage}`;
}

export const TEST_POLL_MS = 1_500;

export const finishedTest = (test: TriggerTest | undefined) =>
  test !== undefined && (test.status === "done" || test.status === "failed");
