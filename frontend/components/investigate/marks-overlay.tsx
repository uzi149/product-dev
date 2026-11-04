"use client";

import { type RefObject, useEffect, useRef } from "react";
import { boxesAt, pictureRect } from "@/lib/marks";
import type { MarksState } from "@/lib/types";

type FrameVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (callback: () => void) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

/**
 * Draws the marks (step 5.7) over the video, for the segment playing and its current time. The
 * canvas covers the video element; boxes are placed on the picture itself (inside the black bars
 * of `object-fit: contain`), so they line up at any player size. Clicks go through to the video.
 */
export function MarksOverlay({
  videoRef,
  segmentId,
  marks,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  segmentId: string;
  marks: MarksState;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const video = videoRef.current as FrameVideo | null;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    let stopped = false;
    let handle = 0;

    const draw = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const ratio = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
        canvas.width = Math.round(w * ratio);
        canvas.height = Math.round(h * ratio);
      }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const rect = pictureRect(w, h, video.videoWidth, video.videoHeight);
      const font = Math.max(10, Math.min(14, rect.width / 50));
      ctx.font = `600 ${font}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textBaseline = "top";
      for (const mark of boxesAt(marks, segmentId, video.currentTime * 1000)) {
        const [x1, y1, x2, y2] = mark.box;
        const x = rect.x + x1 * rect.width;
        const y = rect.y + y1 * rect.height;
        const bw = (x2 - x1) * rect.width;
        const bh = (y2 - y1) * rect.height;
        ctx.strokeStyle = mark.colour;
        ctx.lineWidth = mark.strong ? 2.5 : 1;
        ctx.strokeRect(x, y, bw, bh);
        if (!mark.strong) continue;
        const text = ctx.measureText(mark.label).width + 8;
        const top = y - font - 6 >= rect.y ? y - font - 6 : y;
        ctx.fillStyle = mark.colour;
        ctx.fillRect(x, top, text, font + 6);
        ctx.fillStyle = "#141414";
        ctx.fillText(mark.label, x + 4, top + 3);
      }
    };

    // Every video frame where the browser can tell us; otherwise every animation frame.
    const loop = () => {
      if (stopped) return;
      draw();
      handle = video.requestVideoFrameCallback
        ? video.requestVideoFrameCallback(loop)
        : window.requestAnimationFrame(loop);
    };
    loop();
    // Seeking or pausing doesn't produce a video frame callback in every browser: draw then too.
    const redraw = () => draw();
    video.addEventListener("seeked", redraw);
    video.addEventListener("pause", redraw);
    video.addEventListener("loadedmetadata", redraw);
    window.addEventListener("resize", redraw);
    return () => {
      stopped = true;
      if (video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(handle);
      else window.cancelAnimationFrame(handle);
      video.removeEventListener("seeked", redraw);
      video.removeEventListener("pause", redraw);
      video.removeEventListener("loadedmetadata", redraw);
      window.removeEventListener("resize", redraw);
    };
  }, [videoRef, segmentId, marks]);

  return (
    <canvas
      ref={canvasRef}
      data-testid="marks-overlay"
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 size-full"
    />
  );
}
