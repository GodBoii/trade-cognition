/** Public rows and RPC results exposed by the asynchronous MT5 queue schema. */

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[];

export type ConnectionStatus = "pending" | "online" | "offline" | "error" | "disconnected";
export type TradeIntentStatus =
  | "queued"
  | "claimed"
  | "validating"
  | "rejected"
  | "submitted"
  | "open"
  | "scaling"
  | "closed"
  | "failed"
  | "cancelled"
  | "expired";
export type TradeCommandStatus =
  | "pending"
  | "claimed"
  | "succeeded"
  | "rejected"
  | "failed"
  | "cancelled"
  | "expired";

export interface UserTradingRules {
  user_id: string;
  lots_per_1000: number;
  max_risk_pct: number;
  capital_basis: "balance" | "equity" | "fixed";
  fixed_capital: number;
  ladder_preset: "runner_1_2_3" | "standard_1_2_3" | "custom";
  tp1_close_fraction: number;
  tp2_close_fraction: number;
  tp3_close_fraction: number;
  one_active_trade_per_symbol: boolean;
  require_stop_loss: boolean;
  max_concurrent_positions: number;
  max_daily_loss_pct: number;
  margin_utilisation_cap_pct: number;
  min_reward_risk: number;
  created_at: string;
  updated_at: string;
}

export interface WorkerAgent {
  id: string;
  user_id: string;
  name: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Mt5Connection {
  id: string;
  user_id: string;
  worker_id: string;
  label: string;
  mt5_login: number | null;
  server: string;
  company: string;
  account_name: string;
  currency: string;
  leverage: number | null;
  status: ConnectionStatus;
  is_enabled: boolean;
  trade_allowed: boolean | null;
  expert_allowed: boolean | null;
  last_balance: number | null;
  last_equity: number | null;
  last_margin: number | null;
  last_free_margin: number | null;
  last_error: string;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TradeIntent {
  id: string;
  user_id: string;
  connection_id: string;
  client_request_id: string;
  symbol: string;
  side: "buy" | "sell";
  order_kind: "market" | "limit" | "stop";
  requested_entry: number | null;
  stop_loss: number | null;
  stop_points: number | null;
  requested_volume: number | null;
  comment: string;
  status: TradeIntentStatus;
  broker_order_ticket: number | null;
  broker_position_ticket: number | null;
  approved_plan: Record<string, Json> | null;
  rules_report: Record<string, Json> | null;
  last_error: string;
  metadata: Record<string, Json>;
  execute_before: string;
  submitted_at: string | null;
  opened_at: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TradeCommand {
  id: string;
  user_id: string;
  connection_id: string;
  intent_id: string | null;
  client_request_id: string;
  command_type: "submit_trade" | "close_trade" | "sync_trade" | "refresh_account";
  payload: Record<string, Json>;
  status: TradeCommandStatus;
  priority: number;
  available_at: string;
  expires_at: string;
  claimed_by: string | null;
  claim_token: string | null;
  claimed_at: string | null;
  lease_expires_at: string | null;
  attempts: number;
  max_attempts: number;
  result: Record<string, Json> | null;
  error_code: string;
  error_message: string;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TradeEvent {
  id: number;
  user_id: string;
  connection_id: string;
  intent_id: string | null;
  command_id: string | null;
  event_type: string;
  message: string;
  payload: Record<string, Json>;
  created_at: string;
}

export interface WorkerCredential {
  workerId: string;
  /** Returned once by Supabase. Never persist this in browser storage. */
  workerToken: string;
}

export interface TradingOverview {
  rules: UserTradingRules | null;
  workers: WorkerAgent[];
  connections: Mt5Connection[];
  recentIntents: TradeIntent[];
  recentCommands: TradeCommand[];
  recentEvents: TradeEvent[];
}
