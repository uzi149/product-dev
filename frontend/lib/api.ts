import type {
  Camera,
  CameraInput,
  CameraUpdate,
  ConnectionTestResult,
  Health,
  Segment,
  StaticPeriod,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
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
  return (await response.json()) as T;
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
  testConnection: (id: string) =>
    request<ConnectionTestResult>(`/cameras/${id}/test-connection`, { method: "POST" }),
  segments: (params: { cameraId: string; start?: string; end?: string; limit?: number }) => {
    const query = new URLSearchParams({ camera_id: params.cameraId, limit: String(params.limit ?? 200) });
    if (params.start) query.set("start", params.start);
    if (params.end) query.set("end", params.end);
    return request<Segment[]>(`/segments?${query}`);
  },
  playbackUrl: (segmentId: string) => `${API_URL}/segments/${segmentId}/playback`,
  staticPeriods: (params: { cameraId: string; start?: string; end?: string }) => {
    const query = new URLSearchParams({ camera_id: params.cameraId, limit: "500" });
    if (params.start) query.set("start", params.start);
    if (params.end) query.set("end", params.end);
    return request<StaticPeriod[]>(`/static-periods?${query}`);
  },
  referenceFrameUrl: (periodId: string) => `${API_URL}/static-periods/${periodId}/reference-frame`,
  /** `version` (the snapshot time) makes the browser load a new picture when it changes. */
  snapshotUrl: (cameraId: string, version: string | null) =>
    `${API_URL}/cameras/${cameraId}/snapshot${version ? `?v=${encodeURIComponent(version)}` : ""}`,
  upload: (id: string) => request<Upload>(`/videos/uploads/${id}`),
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
        reject(
          new ApiError(typeof detail === "string" ? detail : `Upload failed (${xhr.status})`, xhr.status),
        );
      };
      xhr.send(form);
    }),
};
