// Mirrors shared/schemas/ on the backend. Written by hand (no code generator in the MVP).

export type SourceType = "rtsp" | "file";

/** Live camera status from `python -m services.ingestion` (RTSP cameras only). */
export type LiveState = "connecting" | "online" | "offline" | "disabled" | "not_running" | "unknown";

export interface LiveStatus {
  state: LiveState;
  since: string | null;
  /** The change detector is in an active interval right now. */
  activity: boolean;
  last_frame_at: string | null;
  last_snapshot_at: string | null;
  error: string | null;
}

export interface Camera {
  id: string;
  display_name: string;
  source_type: SourceType;
  enabled: boolean;
  retention_days: number;
  analysis_fps: number;
  detection_config: Record<string, unknown>;
  created_at: string;
  has_source_uri: boolean;
  source_uri_masked: string | null;
  segment_count: number;
  /** null for file cameras. */
  live: LiveStatus | null;
}

export interface CameraInput {
  display_name: string;
  source_type: SourceType;
  source_uri?: string | null;
  enabled: boolean;
  retention_days: number;
}

export type CameraUpdate = Partial<Omit<CameraInput, "source_type">>;

/** What deleting a camera removes; `blocker` says why it can't be deleted now (ADR 0026). */
export interface DeletePreview {
  segments: number;
  static_periods: number;
  analysed_windows: number;
  uploads: number;
  blocker: string | null;
}

export type ProcessingStatus = "pending" | "processing" | "done" | "failed";

export interface Segment {
  id: string;
  camera_id: string;
  start_time: string;
  end_time: string;
  duration_ms: number;
  activity_score: number | null;
  /** The analysis state. */
  status: ProcessingStatus;
  codec: string | null;
  width: number | null;
  height: number | null;
  created_at: string;
  /** Failed, or stuck in `processing` (its worker stopped): it can be analysed again. */
  retryable: boolean;
}

/** Segments per analysis state; `processing` leaves out the stuck ones. */
export interface StatusCounts {
  pending: number;
  processing: number;
  stuck: number;
  done: number;
  failed: number;
}

/** A stretch where the camera saw no change (`ok`), or could not be reached (`offline`). */
export interface StaticPeriod {
  id: string;
  camera_id: string;
  start_time: string;
  end_time: string;
  health_status: "ok" | "offline";
  has_reference_frame: boolean;
}

/** An uploaded file and its background ingestion (only the active parts are stored). */
export interface Upload {
  id: string;
  camera_id: string;
  filename: string;
  start_time: string;
  status: ProcessingStatus;
  segments_created: number | null;
  static_periods_created: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface ConnectionTestResult {
  ok: boolean;
  width: number | null;
  height: number | null;
  error: string | null;
}

/** Where analysis sends the frames: an API host (OpenRouter, a local vLLM) or the local model. */
export interface VlmBackend {
  kind: "api" | "local";
  host: string | null;
}

export interface Health {
  status: "ok" | "degraded";
  database: boolean;
  storage: boolean;
  vlm_backend: VlmBackend;
}
