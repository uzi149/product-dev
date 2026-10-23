// Mirrors shared/schemas/ on the backend. Written by hand (no code generator in the MVP).

export type SourceType = "rtsp" | "file";

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
}

export interface CameraInput {
  display_name: string;
  source_type: SourceType;
  source_uri?: string | null;
  enabled: boolean;
  retention_days: number;
}

export type CameraUpdate = Partial<Omit<CameraInput, "source_type">>;

export interface Segment {
  id: string;
  camera_id: string;
  start_time: string;
  end_time: string;
  duration_ms: number;
  activity_score: number | null;
  status: "pending" | "processing" | "done" | "failed";
  codec: string | null;
  width: number | null;
  height: number | null;
  created_at: string;
}

export interface ConnectionTestResult {
  ok: boolean;
  width: number | null;
  height: number | null;
  error: string | null;
}

export interface Health {
  status: "ok" | "degraded";
  database: boolean;
  storage: boolean;
}
