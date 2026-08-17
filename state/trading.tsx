"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  createPendingConnection,
  createWorker,
  loadTradingOverview,
  revokeWorker as revokeWorkerRecord,
  rotateWorkerToken as rotateWorkerCredential,
  setConnectionEnabled,
} from "@/lib/supabase/data";
import type {
  Mt5Connection,
  TradeCommand,
  TradeEvent,
  TradeIntent,
  UserTradingRules,
  WorkerAgent,
  WorkerCredential,
} from "@/lib/supabase/types";
import { useAuth } from "@/state/auth";

const STALE_AFTER_MS = 90_000;

export type TradingStatus =
  | "idle"
  | "loading"
  | "error"
  | "unpaired"
  | "pending"
  | "online"
  | "stale"
  | "offline";

interface TradingState {
  status: TradingStatus;
  loading: boolean;
  error: unknown;
  rules: UserTradingRules | null;
  workers: WorkerAgent[];
  connections: Mt5Connection[];
  recentIntents: TradeIntent[];
  recentCommands: TradeCommand[];
  recentEvents: TradeEvent[];
  connectionId: string | null;
  connection: Mt5Connection | null;
  worker: WorkerAgent | null;
  isOffline: boolean;
  isStale: boolean;
  selectConnection: (id: string) => void;
  refresh: () => Promise<void>;
  createPairing: (workerName: string, connectionLabel: string) => Promise<WorkerCredential>;
  setEnabled: (connectionId: string, enabled: boolean) => Promise<void>;
  rotateWorkerToken: (workerId: string) => Promise<string>;
  revokeWorker: (workerId: string) => Promise<void>;
}

const TradingContext = createContext<TradingState | null>(null);

function storageKey(userId: string): string {
  return `trade-cognition.connection.${userId}`;
}

function isRecent(value: string | null, now: number): boolean {
  if (!value) return false;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && now - timestamp <= STALE_AFTER_MS;
}

function deriveStatus(
  loading: boolean,
  error: unknown,
  connection: Mt5Connection | null,
  worker: WorkerAgent | null,
  now: number,
): TradingStatus {
  if (loading) return "loading";
  if (error) return "error";
  if (!connection) return "unpaired";
  if (connection.status === "pending") return "pending";
  if (
    !connection.is_enabled ||
    connection.status === "offline" ||
    connection.status === "disconnected" ||
    worker?.revoked_at
  ) {
    return "offline";
  }
  if (connection.status === "error") return "error";
  if (!isRecent(connection.last_seen_at, now) || !isRecent(worker?.last_seen_at ?? null, now)) {
    return "stale";
  }
  return "online";
}

export function TradingProvider({ children }: { children: ReactNode }) {
  const { ready: authReady, user } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [rules, setRules] = useState<UserTradingRules | null>(null);
  const [workers, setWorkers] = useState<WorkerAgent[]>([]);
  const [connections, setConnections] = useState<Mt5Connection[]>([]);
  const [recentIntents, setRecentIntents] = useState<TradeIntent[]>([]);
  const [recentCommands, setRecentCommands] = useState<TradeCommand[]>([]);
  const [recentEvents, setRecentEvents] = useState<TradeEvent[]>([]);
  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const clear = useCallback(() => {
    setRules(null);
    setWorkers([]);
    setConnections([]);
    setRecentIntents([]);
    setRecentCommands([]);
    setRecentEvents([]);
    setConnectionId(null);
    setError(null);
    setLoading(false);
  }, []);

  const refresh = useCallback(async () => {
    if (!user) {
      clear();
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const overview = await loadTradingOverview(user.id);
      setRules(overview.rules);
      setWorkers(overview.workers);
      setConnections(overview.connections);
      setRecentIntents(overview.recentIntents);
      setRecentCommands(overview.recentCommands);
      setRecentEvents(overview.recentEvents);
      setNow(Date.now());
      setConnectionId((current) => {
        const saved = window.localStorage.getItem(storageKey(user.id));
        const candidate = current || saved;
        if (candidate && overview.connections.some((item) => item.id === candidate)) {
          return candidate;
        }
        return (
          overview.connections.find((item) => item.status === "online" && item.is_enabled)?.id ??
          overview.connections.find((item) => item.is_enabled)?.id ??
          overview.connections[0]?.id ??
          null
        );
      });
    } catch (cause) {
      setError(cause);
    } finally {
      setLoading(false);
    }
  }, [clear, user]);

  useEffect(() => {
    if (!authReady) return;
    if (!user) {
      clear();
      return;
    }
    void refresh();
  }, [authReady, clear, refresh, user]);

  // No network polling is required. This local clock only lets an old heartbeat
  // visibly transition from online to stale while the page remains open.
  useEffect(() => {
    if (!user) return;
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, [user]);

  const selectConnection = useCallback(
    (id: string) => {
      if (!connections.some((item) => item.id === id)) return;
      setConnectionId(id);
      if (user) window.localStorage.setItem(storageKey(user.id), id);
    },
    [connections, user],
  );

  const createPairing = useCallback(
    async (workerName: string, connectionLabel: string) => {
      if (!user) throw new Error("Sign in before creating an MT5 pairing.");
      const credential = await createWorker(workerName);
      try {
        await createPendingConnection(user.id, credential.workerId, connectionLabel);
      } catch (cause) {
        // Worker creation and the intentionally narrow browser connection insert
        // are separate operations. Revoke the otherwise unusable one-time token
        // when the second operation fails instead of leaving an orphaned worker.
        await revokeWorkerRecord(credential.workerId).catch(() => undefined);
        throw cause;
      }
      await refresh();
      return credential;
    },
    [refresh, user],
  );

  const setEnabled = useCallback(
    async (id: string, enabled: boolean) => {
      await setConnectionEnabled(id, enabled);
      await refresh();
    },
    [refresh],
  );

  const rotateWorkerToken = useCallback(
    async (workerId: string) => {
      const token = await rotateWorkerCredential(workerId);
      await refresh();
      return token;
    },
    [refresh],
  );

  const revokeWorker = useCallback(
    async (workerId: string) => {
      await revokeWorkerRecord(workerId);
      await refresh();
    },
    [refresh],
  );

  const connection = connections.find((item) => item.id === connectionId) ?? null;
  const worker = workers.find((item) => item.id === connection?.worker_id) ?? null;
  const status = deriveStatus(loading, error, connection, worker, now);

  const value = useMemo<TradingState>(
    () => ({
      status,
      loading,
      error,
      rules,
      workers,
      connections,
      recentIntents,
      recentCommands,
      recentEvents,
      connectionId,
      connection,
      worker,
      isOffline: status === "offline" || status === "unpaired" || status === "pending",
      isStale: status === "stale",
      selectConnection,
      refresh,
      createPairing,
      setEnabled,
      rotateWorkerToken,
      revokeWorker,
    }),
    [
      status,
      loading,
      error,
      rules,
      workers,
      connections,
      recentIntents,
      recentCommands,
      recentEvents,
      connectionId,
      connection,
      worker,
      selectConnection,
      refresh,
      createPairing,
      setEnabled,
      rotateWorkerToken,
      revokeWorker,
    ],
  );

  return <TradingContext.Provider value={value}>{children}</TradingContext.Provider>;
}

export function useTrading(): TradingState {
  const context = useContext(TradingContext);
  if (!context) throw new Error("useTrading must be used inside <TradingProvider>");
  return context;
}
