import type {
  Camera,
  CameraInput,
  CameraUpdate,
  ConnectionTestResult,
  DeletePreview,
  EventCounts,
  EventDetail,
  EventItem,
  EventList,
  EventStatus,
  EvidenceItem,
  ExportState,
  FootageConflict,
  Health,
  MarksState,
  QueryInput,
  QueryList,
  QueryState,
  Segment,
  SettingSection,
  StaticPeriod,
  StatusCounts,
  Trigger,
  TriggerInput,
  TriggerTest,
  TriggerTestInput,
  TriggerUpdate,
  Upload,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

/** Thrown for any failed request. `unreachable` means the API did not answer at all. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly unreachable = false,
  ) {
    super(message);
  }
}

/** The API refused an upload because its time overlaps footage on the camera (HTTP 409, ADR 0037). */
export class OverlapError extends ApiError {
  constructor(
    message: string,
    readonly conflicts: FootageConflict[],
  ) {
    super(message, 409);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    // react-query is the only cache for API data; never answer from the browser's HTTP cache.
    response = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError("The API is not reachable", null, true);
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      // keep the generic message
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return null as T;
  return (await response.json()) as T;
}

/** A media URL for <img>/<video>; parameters that are null are left out. */
export function mediaUrl(path: string, params: Record<string, string | number | null> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null) query.set(key, String(value));
  return `${API_URL}${path}${query.size ? `?${query}` : ""}`;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<Health>("/health"),
  cameras: () => request<Camera[]>("/cameras"),
  createCamera: (input: CameraInput) => request<Camera>("/cameras", json("POST", input)),
  updateCamera: (id: string, input: CameraUpdate) =>
    request<Camera>(`/cameras/${id}`, json("PATCH", input)),
  deletePreview: (id: string) => request<DeletePreview>(`/cameras/${id}/delete-preview`),
  /** Deletes the camera with all its footage, analysis and uploads, for good (HTTP 204). */
  deleteCamera: (id: string) => request<null>(`/cameras/${id}`, { method: "DELETE" }),
  testConnection: (id: string) =>
    request<ConnectionTestResult>(`/cameras/${id}/test-connection`, { method: "POST" }),
  segments: (params: { cameraId: string; start?: string; end?: string; limit?: number }) => {
    const query = new URLSearchParams({ camera_id: params.cameraId, limit: String(params.limit ?? 200) });
    if (params.start) query.set("start", params.start);
    if (params.end) query.set("end", params.end);
    return request<Segment[]>(`/segments?${query}`);
  },
  segmentStatusCounts: (params: { cameraId: string; start?: string; end?: string }) => {
    const query = new URLSearchParams({ camera_id: params.cameraId });
    if (params.start) query.set("start", params.start);
    if (params.end) query.set("end", params.end);
    return request<StatusCounts>(`/segments/status-counts?${query}`);
  },
  /** Analyse a failed or stuck segment again (HTTP 202). */
  reanalyseSegment: (id: string) =>
    request<{ queued: number }>(`/segments/${id}/reanalyse`, { method: "POST" }),
  /** Analyse every failed segment of the camera within the range again. */
  reanalyseFailed: (params: { cameraId: string; start?: string; end?: string }) =>
    request<{ queued: number }>(
      "/segments/reanalyse",
      json("POST", { camera_id: params.cameraId, start: params.start ?? null, end: params.end ?? null }),
    ),
  createQuery: (input: QueryInput) => request<QueryState>("/queries", json("POST", input)),
  query: (id: string) => request<QueryState>(`/queries/${id}`),
  /** Query history, newest first (step 4.6); `q`: only questions with every one of its words. */
  queries: (params: { limit: number; offset: number; q?: string }) => {
    const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    if (params.q?.trim()) query.set("q", params.q.trim());
    return request<QueryList>(`/queries?${query}`);
  },
  /** Asks the stored question again as a new query, relative times read now (HTTP 202). */
  rerunQuery: (id: string) => request<QueryState>(`/queries/${id}/rerun`, { method: "POST" }),
  /** Deletes the query with its results, for good (HTTP 204). */
  deleteQuery: (id: string) => request<null>(`/queries/${id}`, { method: "DELETE" }),
  queryResults: (id: string) => request<EvidenceItem[]>(`/queries/${id}/results`),
  // Triggers (steps 5.1, 5.4)
  triggers: () => request<Trigger[]>("/triggers"),
  trigger: (id: string) => request<Trigger>(`/triggers/${id}`),
  createTrigger: (input: TriggerInput) => request<Trigger>("/triggers", json("POST", input)),
  updateTrigger: (id: string, input: TriggerUpdate) =>
    request<Trigger>(`/triggers/${id}`, json("PATCH", input)),
  compileTrigger: (id: string) => request<Trigger>(`/triggers/${id}/compile`, { method: "POST" }),
  enableTrigger: (id: string) => request<Trigger>(`/triggers/${id}/enable`, { method: "POST" }),
  disableTrigger: (id: string) => request<Trigger>(`/triggers/${id}/disable`, { method: "POST" }),
  /** Deletes the trigger with its events and tests, for good (HTTP 204, ADR 0034). */
  deleteTrigger: (id: string) => request<null>(`/triggers/${id}`, { method: "DELETE" }),
  testTrigger: (id: string, input: TriggerTestInput) =>
    request<TriggerTest>(`/triggers/${id}/test`, json("POST", input)),
  triggerTests: (id: string) => request<TriggerTest[]>(`/triggers/${id}/tests`),
  triggerTest: (id: string, testId: string) =>
    request<TriggerTest>(`/triggers/${id}/tests/${testId}`),
  /** Uploaded videos of every camera, most recent first (a trigger test's source). */
  allUploads: () => request<Upload[]>(`/videos/uploads?${new URLSearchParams({ limit: "50" })}`),
  // Events (step 5.3)
  events: (params: {
    status?: EventStatus | null;
    cameraId?: string | null;
    triggerId?: string | null;
    limit: number;
    offset?: number;
  }) => {
    const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset ?? 0) });
    if (params.status) query.set("status", params.status);
    if (params.cameraId) query.set("camera_id", params.cameraId);
    if (params.triggerId) query.set("trigger_id", params.triggerId);
    return request<EventList>(`/events?${query}`);
  },
  eventCounts: () => request<EventCounts>("/events/counts"),
  event: (id: string) => request<EventDetail>(`/events/${id}`),
  acknowledgeEvent: (id: string, status: EventStatus, note?: string | null) =>
    request<EventItem>(
      `/events/${id}/acknowledge`,
      json("POST", note === undefined ? { status } : { status, note }),
    ),
  /** `marks`: the second file, with the marks drawn in (step 5.7). */
  exportEvent: (id: string, marks = false) =>
    request<ExportState>(`/events/${id}/export${marks ? "?marks=true" : ""}`, { method: "POST" }),
  exportState: (id: string, marks = false) =>
    request<ExportState>(`/events/${id}/export${marks ? "?marks=true" : ""}`),
  eventMarks: (id: string) => request<MarksState>(`/events/${id}/marks`),
  /** Marks a result the operator opened (step 5.7); asking again changes nothing. */
  markResult: (queryId: string, resultId: string) =>
    request<MarksState>(`/queries/${queryId}/results/${resultId}/marks`, { method: "POST" }),
  resultMarks: (queryId: string, resultId: string) =>
    request<MarksState>(`/queries/${queryId}/results/${resultId}/marks`),
  /** Deletes the event with its evidence rows and export (HTTP 204, ADR 0034). */
  deleteEvent: (id: string) => request<null>(`/events/${id}`, { method: "DELETE" }),
  /** New events as they are reported (step 5.2). */
  liveEventsUrl: () => `${API_URL.replace(/^http/, "ws")}/live/events`,
  settings: () => request<SettingSection[]>("/settings"),
  /** The progress WebSocket: the API's address with ws:// or wss://. */
  queryProgressUrl: (id: string) => `${API_URL.replace(/^http/, "ws")}/queries/${id}/progress`,
  keyframeUrl: (keyframeId: string, attempt = 0) =>
    mediaUrl(`/keyframes/${keyframeId}/image`, { r: attempt || null }),
  /** `attempt` > 0 after a failed load: a new URL, so the API signs a new MinIO link. */
  playbackUrl: (segmentId: string, attempt = 0) =>
    mediaUrl(`/segments/${segmentId}/playback`, { r: attempt || null }),
  staticPeriods: (params: { cameraId: string; start?: string; end?: string }) => {
    const query = new URLSearchParams({ camera_id: params.cameraId, limit: "500" });
    if (params.start) query.set("start", params.start);
    if (params.end) query.set("end", params.end);
    return request<StaticPeriod[]>(`/static-periods?${query}`);
  },
  referenceFrameUrl: (periodId: string, attempt = 0) =>
    mediaUrl(`/static-periods/${periodId}/reference-frame`, { r: attempt || null }),
  /** `version` (the snapshot time) makes the browser load a new picture when it changes. */
  snapshotUrl: (cameraId: string, version: string | null, attempt = 0) =>
    mediaUrl(`/cameras/${cameraId}/snapshot`, { v: version, r: attempt || null }),
  upload: (id: string) => request<Upload>(`/videos/uploads/${id}`),
  /** Footage on the camera that [start, end) would overlap; without `end`, the footage at `start`. */
  footageOverlap: (cameraId: string, startIso: string, endIso?: string | null) => {
    const query = new URLSearchParams({ camera_id: cameraId, start: startIso });
    if (endIso) query.set("end", endIso);
    return request<FootageConflict[]>(`/videos/overlap?${query}`);
  },
  uploads: (cameraId: string) =>
    request<Upload[]>(`/videos/uploads?${new URLSearchParams({ camera_id: cameraId, limit: "10" })}`),
  /**
   * Sends the file for background ingestion; resolves with the queued upload (HTTP 202).
   * Uses XMLHttpRequest because fetch can't report upload progress.
   */
  uploadVideo: (
    args: { cameraId: string; startTimeIso: string; file: File },
    onProgress: (fraction: number) => void,
  ) =>
    new Promise<Upload>((resolve, reject) => {
      const form = new FormData();
      form.set("camera_id", args.cameraId);
      form.set("start_time", args.startTimeIso);
      form.set("file", args.file);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_URL}/videos/upload`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(event.loaded / event.total);
      };
      xhr.onerror = () => reject(new ApiError("The API is not reachable", null, true));
      xhr.onload = () => {
        let body: unknown = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          // not JSON
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(body as Upload);
          return;
        }
        const detail = (body as { detail?: unknown } | null)?.detail;
        const overlap = detail as { message?: unknown; conflicts?: unknown } | null;
        if (xhr.status === 409 && overlap && Array.isArray(overlap.conflicts)) {
          reject(new OverlapError(String(overlap.message ?? "Overlapping footage"), overlap.conflicts));
          return;
        }
        reject(
          new ApiError(typeof detail === "string" ? detail : `Upload failed (${xhr.status})`, xhr.status),
        );
      };
      xhr.send(form);
    }),
};
