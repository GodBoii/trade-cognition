"use client";

/**
 * Live dashboard feed.
 *
 * Subscribes to `/api/ws/stream` and falls back to REST polling if the socket
 * cannot be established, so the dashboard keeps updating behind proxies that
 * block WebSocket upgrades. Reconnection uses capped exponential backoff.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api, streamUrl } from "@/lib/api/client";
import type { StreamMessage, StreamSnapshot } from "@/lib/api/types";

const MAX_BACKOFF_MS = 15_000;
const POLL_INTERVAL_MS = 4_000;

export type StreamStatus = "connecting" | "live" | "polling" | "error";

interface StreamState {
  snapshot: StreamSnapshot | null;
  status: StreamStatus;
  error: string | null;
  refresh: () => void;
}

export function useStream(accountId: number | null): StreamState {
  const [snapshot, setSnapshot] = useState<StreamSnapshot | null>(null);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  const attemptRef = useRef(0);
  const closedRef = useRef(false);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  /** REST fallback: assemble the same shape the socket would have sent. */
  const pollOnce = useCallback(async () => {
    try {
      const [overview, profile] = await Promise.all([
        api.positions(accountId ?? undefined),
        api.profile(),
      ]);
      const capital =
        profile.capital_basis === "equity"
          ? overview.account.equity
          : profile.capital_basis === "fixed"
            ? profile.fixed_capital
            : overview.account.balance;

      setSnapshot({
        type: "snapshot",
        server_time: new Date().toISOString(),
        account: overview.account,
        capital,
        max_risk_money: capital * (profile.max_risk_pct / 100),
        risk_on: overview.risk_on,
        positions: overview.rows,
        active_trades: overview.rows.flatMap((row) => (row.trade ? [row.trade] : [])),
      });
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setStatus("error");
    }
  }, [accountId]);

  useEffect(() => {
    closedRef.current = false;
    attemptRef.current = 0;

    const clearTimer = () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };

    const startPolling = () => {
      if (closedRef.current) return;
      setStatus("polling");
      void pollOnce();
      clearTimer();
      timerRef.current = window.setInterval(() => void pollOnce(), POLL_INTERVAL_MS);
    };

    const connect = () => {
      if (closedRef.current) return;
      const url = streamUrl(accountId ?? undefined);
      if (!url) {
        startPolling();
        return;
      }

      setStatus("connecting");
      let socket: WebSocket;
      try {
        socket = new WebSocket(url);
      } catch {
        startPolling();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        attemptRef.current = 0;
        setStatus("live");
        setError(null);
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(String(event.data)) as StreamMessage;
          if (message.type === "snapshot") {
            setSnapshot(message);
            setError(null);
            setStatus("live");
          } else {
            setError(message.message);
          }
        } catch {
          /* ignore malformed frames rather than tearing down the socket */
        }
      };

      socket.onerror = () => {
        setError("The live feed reported an error.");
      };

      socket.onclose = () => {
        socketRef.current = null;
        if (closedRef.current) return;

        attemptRef.current += 1;
        if (attemptRef.current >= 3) {
          // The socket is not going to work here; keep the UI alive over REST.
          startPolling();
          return;
        }
        const delay = Math.min(500 * 2 ** attemptRef.current, MAX_BACKOFF_MS);
        setStatus("connecting");
        clearTimer();
        timerRef.current = window.setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      closedRef.current = true;
      clearTimer();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [accountId, nonce, pollOnce]);

  return { snapshot, status, error, refresh };
}
