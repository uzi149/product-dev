import { describe, expect, it } from "vitest";
import {
  durationWording,
  percent,
  ruleWording,
  sourceWording,
  testWording,
  triggerState,
  verificationWording,
} from "./triggers";
import type { CompiledRule, Trigger, TriggerTest } from "./types";

const RULE: CompiledRule = {
  mode: "structured",
  required_entities: ["person", "portable_object"],
  required_action_sequence: ["place_object", "leave"],
  alternative_action_sequences: [["leave_object_behind"]],
  maximum_sequence_gap_seconds: 30,
  requires_vlm_verification: true,
  verification_set_by_operator: false,
  search_text: "someone places an object and leaves it behind",
  source_rule: "Report when someone places an object and leaves it behind",
  prompt_version: 2,
  model: "m",
};

describe("ruleWording", () => {
  it("names the sequences and the gap", () => {
    expect(ruleWording(RULE)).toBe("place object → leave, or leave object behind, within 30 s");
    expect(ruleWording({ ...RULE, alternative_action_sequences: [] })).toBe("place object → leave, within 30 s");
  });
  it("has no gap for a single action", () => {
    expect(ruleWording({ ...RULE, required_action_sequence: ["fall"], alternative_action_sequences: [] })).toBe(
      "fall",
    );
  });
  it("quotes the text of a similarity rule", () => {
    expect(ruleWording({ ...RULE, mode: "semantic", search_text: "a crowd gathers" })).toBe(
      "Similar to “a crowd gathers”",
    );
  });
});

describe("durationWording and percent", () => {
  it.each([
    [30, "30 s"],
    [120, "2 min"],
    [90, "1 min 30 s"],
    [3600, "1 h"],
    [8040, "2 h 14 min"],
    [0, "0 s"],
  ])("%i s -> %s", (seconds, words) => {
    expect(durationWording(seconds)).toBe(words);
  });
  it("rounds percentages", () => {
    expect(percent(0.8)).toBe("80 %");
    expect(percent(0.925)).toBe("93 %");
  });
});

function trigger(overrides: Partial<Trigger>): Trigger {
  return {
    id: "t",
    name: "Object left behind",
    natural_language_rule: RULE.source_rule,
    cameras: [],
    enabled: false,
    compile_status: "done",
    compile_error: null,
    compiled: RULE,
    events: { new: 0, total: 0 },
    created_at: "",
    updated_at: "",
    minimum_confidence: 0.8,
    cooldown_seconds: 120,
    pre_roll_seconds: 10,
    post_roll_seconds: 20,
    ...overrides,
  };
}

describe("triggerState", () => {
  it("shows compiling and failure before enabled", () => {
    expect(triggerState(trigger({ compile_status: "pending", enabled: true })).label).toBe("Compiling…");
    expect(triggerState(trigger({ compile_status: "failed" }))).toEqual({ label: "Couldn't compile", tone: "red" });
    expect(triggerState(trigger({ enabled: true }))).toEqual({ label: "Enabled", tone: "green" });
    expect(triggerState(trigger({}))).toEqual({ label: "Disabled", tone: "muted" });
  });
});

describe("verificationWording", () => {
  it.each([
    ["verified", "Verified by the VLM"],
    ["not_verified_local", "Not verified (local model)"],
    ["unverifiable", "Couldn't be verified"],
    ["not_checked", "Not checked (verification limit)"],
    [null, "—"],
  ])("%s", (status, words) => {
    expect(verificationWording(status)).toBe(words);
  });
});

function test_(overrides: Partial<TriggerTest>): TriggerTest {
  return {
    id: "x",
    trigger_id: "t",
    source: "last_hour",
    upload_id: null,
    upload_filename: null,
    camera_ids: [],
    start: "",
    end: "",
    status: "done",
    stage: "done",
    error: null,
    created_at: "",
    finished_at: null,
    rule: RULE,
    minimum_confidence: 0.8,
    cooldown_seconds: 120,
    candidates: 12,
    footage_seconds: 8040,
    counts: { would_report: 3, rejected: 1, below_confidence: 0 },
    matches: [],
    ...overrides,
  };
}

describe("testWording", () => {
  it("sums up a finished test", () => {
    expect(testWording(test_({}))).toBe("3 would report, 1 rejected by the VLM in 2 h 14 min of stored footage");
    expect(testWording(test_({ counts: { would_report: 0, rejected: 0, below_confidence: 2 }, footage_seconds: 0 }))).toBe(
      "0 would report, 2 below the confidence",
    );
  });
  it("follows the stages", () => {
    expect(testWording(test_({ status: "pending" }))).toBe("Waiting for the query worker…");
    expect(testWording(test_({ status: "running", stage: "verifying" }))).toBe("Verifying the matches with the VLM…");
    expect(testWording(test_({ status: "failed", error: "boom" }))).toBe("The test failed: boom");
  });
  it("names the source", () => {
    expect(sourceWording({ source: "video", upload_filename: "LeftBag.mpg" })).toBe("Video “LeftBag.mpg”");
    expect(sourceWording({ source: "last_24h", upload_filename: null })).toBe("The last 24 hours");
  });
});
