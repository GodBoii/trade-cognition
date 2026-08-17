"use client";

import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type {
  Mt5Connection,
  TradeCommand,
  TradeEvent,
  TradeIntent,
  TradingOverview,
  UserTradingRules,
  WorkerAgent,
  WorkerCredential,
} from "@/lib/supabase/types";

function messageOf(error: { message?: string } | null): string {
  return error?.message || "Supabase returned an unknown error.";
}

function assertData<T>(data: T | null, error: { message?: string } | null): T {
  if (error) throw new Error(messageOf(error));
  if (data === null) throw new Error("Supabase returned no data.");
  return data;
}

export async function getTradingRules(userId: string): Promise<UserTradingRules | null> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("user_trading_rules")
    .select("*")
    .eq("user_id", userId)
    .maybeSingle();
  if (error) throw new Error(messageOf(error));
  return data as UserTradingRules | null;
}

export type TradingRulesUpdate = Pick<
  UserTradingRules,
  | "capital_basis"
  | "fixed_capital"
  | "max_concurrent_positions"
  | "max_daily_loss_pct"
  | "margin_utilisation_cap_pct"
  | "min_reward_risk"
>;

/** Save the configurable guards while preserving the non-overridable strategy. */
export async function saveTradingRules(
  userId: string,
  changes: TradingRulesUpdate,
): Promise<UserTradingRules> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("user_trading_rules")
    .upsert(
      {
        user_id: userId,
        ...changes,
        lots_per_1000: 0.02,
        max_risk_pct: 2,
        ladder_preset: "runner_1_2_3",
        tp1_close_fraction: 0.5,
        tp2_close_fraction: 0.25,
        tp3_close_fraction: 0.25,
        one_active_trade_per_symbol: true,
        require_stop_loss: true,
      },
      { onConflict: "user_id" },
    )
    .select("*")
    .single();
  return assertData(data, error) as UserTradingRules;
}

export async function listWorkerAgents(userId: string): Promise<WorkerAgent[]> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("worker_agents")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false });
  return (assertData(data, error) ?? []) as WorkerAgent[];
}

export async function listMt5Connections(userId: string): Promise<Mt5Connection[]> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("mt5_connections")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false });
  return (assertData(data, error) ?? []) as Mt5Connection[];
}

export async function listTradeIntents(userId: string, limit = 100): Promise<TradeIntent[]> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("trade_intents")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false })
    .limit(limit);
  return (assertData(data, error) ?? []) as TradeIntent[];
}

export async function getTradeIntent(userId: string, intentId: string): Promise<TradeIntent> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("trade_intents")
    .select("*")
    .eq("user_id", userId)
    .eq("id", intentId)
    .single();
  return assertData(data, error) as TradeIntent;
}

export async function listTradeCommands(userId: string, limit = 100): Promise<TradeCommand[]> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("trade_commands")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false })
    .limit(limit);
  return (assertData(data, error) ?? []) as TradeCommand[];
}

export async function listTradeEvents(userId: string, limit = 200): Promise<TradeEvent[]> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("trade_events")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false })
    .limit(limit);
  return (assertData(data, error) ?? []) as TradeEvent[];
}

export async function listTradeEventsForIntent(
  userId: string,
  intentId: string,
): Promise<TradeEvent[]> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("trade_events")
    .select("*")
    .eq("user_id", userId)
    .eq("intent_id", intentId)
    .order("created_at", { ascending: false });
  return (assertData(data, error) ?? []) as TradeEvent[];
}

export interface EnqueueTradeIntentInput {
  connectionId: string;
  clientRequestId: string;
  symbol: string;
  side: "buy" | "sell";
  orderKind: "market" | "limit" | "stop";
  requestedEntry: number | null;
  stopLoss: number | null;
  stopPoints: number | null;
  requestedVolume?: number | null;
  comment?: string;
  executeBefore?: string;
}

/** Queue one idempotent trade intent; the worker remains the execution authority. */
export async function enqueueTradeIntent(
  input: EnqueueTradeIntentInput,
): Promise<TradeIntent> {
  const { data, error } = await getSupabaseBrowserClient().rpc("tcq_enqueue_trade_intent", {
    p_connection_id: input.connectionId,
    p_client_request_id: input.clientRequestId,
    p_symbol: input.symbol.trim().toUpperCase(),
    p_side: input.side,
    p_order_kind: input.orderKind,
    p_requested_entry: input.requestedEntry,
    p_stop_loss: input.stopLoss,
    p_stop_points: input.stopPoints,
    p_requested_volume: input.requestedVolume ?? null,
    p_comment: (input.comment ?? "").trim().slice(0, 48),
    p_metadata: { source: "vercel_web" },
    p_execute_before:
      input.executeBefore ?? new Date(Date.now() + 5 * 60 * 1000).toISOString(),
  });
  const row = (Array.isArray(data) ? data[0] : data) as TradeIntent | null;
  return assertData(row, error);
}

export async function enqueueTradeCommand(input: {
  connectionId: string;
  intentId: string | null;
  commandType: "close_trade" | "sync_trade" | "refresh_account";
  clientRequestId: string;
}): Promise<TradeCommand> {
  const { data, error } = await getSupabaseBrowserClient().rpc("tcq_enqueue_trade_command", {
    p_connection_id: input.connectionId,
    p_intent_id: input.intentId,
    p_client_request_id: input.clientRequestId,
    p_command_type: input.commandType,
    p_payload: {},
    p_execute_before: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
  });
  const row = (Array.isArray(data) ? data[0] : data) as TradeCommand | null;
  return assertData(row, error);
}

export async function loadTradingOverview(userId: string): Promise<TradingOverview> {
  const [rules, workers, connections, recentIntents, recentCommands, recentEvents] =
    await Promise.all([
    getTradingRules(userId),
    listWorkerAgents(userId),
    listMt5Connections(userId),
    listTradeIntents(userId, 50),
    listTradeCommands(userId, 50),
    listTradeEvents(userId, 100),
  ]);
  return { rules, workers, connections, recentIntents, recentCommands, recentEvents };
}

export async function createWorker(name: string): Promise<WorkerCredential> {
  const { data, error } = await getSupabaseBrowserClient().rpc("tcq_create_worker", {
    p_name: name.trim(),
  });
  if (error) throw new Error(messageOf(error));

  const row = (Array.isArray(data) ? data[0] : data) as
    | { worker_id?: string; worker_token?: string }
    | null;
  if (!row?.worker_id || !row.worker_token) {
    throw new Error("Supabase did not return the one-time worker credential.");
  }
  return { workerId: row.worker_id, workerToken: row.worker_token };
}

export async function createPendingConnection(
  userId: string,
  workerId: string,
  label: string,
): Promise<Mt5Connection> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("mt5_connections")
    .insert({ user_id: userId, worker_id: workerId, label: label.trim() })
    .select("*")
    .single();
  return assertData(data, error) as Mt5Connection;
}

export async function setConnectionEnabled(
  connectionId: string,
  enabled: boolean,
): Promise<Mt5Connection> {
  const { data, error } = await getSupabaseBrowserClient()
    .from("mt5_connections")
    .update({ is_enabled: enabled })
    .eq("id", connectionId)
    .select("*")
    .single();
  return assertData(data, error) as Mt5Connection;
}

export async function rotateWorkerToken(workerId: string): Promise<string> {
  const { data, error } = await getSupabaseBrowserClient().rpc("tcq_rotate_worker_token", {
    p_worker_id: workerId,
  });
  if (error) throw new Error(messageOf(error));
  if (typeof data !== "string" || !data) {
    throw new Error("Supabase did not return the replacement worker token.");
  }
  return data;
}

export async function revokeWorker(workerId: string): Promise<void> {
  const { error } = await getSupabaseBrowserClient().rpc("tcq_revoke_worker", {
    p_worker_id: workerId,
  });
  if (error) throw new Error(messageOf(error));
}
