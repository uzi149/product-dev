"use client";

import { useEffect, useRef } from "react";
import { api } from "./api";
import type { LiveNotice } from "./types";

/** Reconnect delays after the socket drops: 1 s, 2 s, 4 s … up to 30 s. */
export function reconnectDelay(attempt: number): number {
  return Math.min(30_000, 1_000 * 2 ** attempt);
}

/**
 * Calls `onNotice` for every event reported from now on (`WS /live/events`, step 5.2). One
 * connection for the whole app; it reconnects with back-off when the API restarts.
 */
export function useLiveEvents(onNotice: (notice: LiveNotice) => void): void {
  const latest = useRef(onNotice);
  useEffect(() => {
    latest.current = onNotice;
  });

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let stopped = false;

    function connect() {
      if (stopped) return;
      try {
        socket = new WebSocket(api.liveEventsUrl());
      } catch {
        retry();
        return;
      }
      socket.onopen = () => {
        attempt = 0;
      };
      socket.onmessage = (event) => {
        try {
          latest.current(JSON.parse(event.data) as LiveNotice);
        } catch {
          // not a notice
        }
      };
      socket.onclose = () => retry();
    }

    function retry() {
      if (stopped) return;
      timer = setTimeout(connect, reconnectDelay(attempt));
      attempt += 1;
    }

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      if (!socket) return;
      // Closing a socket that is still connecting logs a browser warning: close it once open.
      if (socket.readyState === WebSocket.CONNECTING) socket.onopen = () => socket?.close();
      else if (socket.readyState === WebSocket.OPEN) socket.close();
    };
  }, []);
}
