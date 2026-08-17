/**
 * Types mirroring the backend response models in `app/api/schemas.py`.
 *
 * These are hand-maintained rather than generated. If you change a backend
 * schema, change it here too - the authoritative contract is the OpenAPI
 * document at `/openapi.json`.
 */

export type Side = "buy" | "sell";
export type OrderKind = "market" | "limit" | "stop";
export type CapitalBasis = "balance" | "equity" | "fixed";
export type LotRuleMode = "strict" | "max";
export type LadderPreset = "standard_1_2_3" | "runner_1_2_3";
export type Severity = "block" | "warn" | "info";
export type TradeStatus = "pending" | "open" | "scaling" | "closed" | "rejected" | "error";
export type StageStatus = "pending" | "filled" | "skipped" | "failed";

export interface ApiError {
  code: string;
  message: string;
  details?: unknown;
}

export interface User {
  id: number;
  email: string;
  display_name: string;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  user: User;
}

export interface Mt5Account {
  id: number;
  label: string;
  login: number;
  server: string;
  currency: string;
  company: string;
  account_name: string;
  leverage: number;
  is_default: boolean;
  is_enabled: boolean;
  last_balance: number;
  last_equity: number;
  last_verified_at: string | null;
  last_error: string;
  created_at: string;
}

export interface AccountSnapshot {
  login: number;
  name: string;
  server: string;
  currency: string;
  balance: number;
  equity: number;
  margin: number;
  margin_free: number;
  margin_level: number;
  profit: number;
  leverage: number;
  trade_allowed: boolean;
  trade_expert: boolean;
  company: string;
}

export interface AccountState {
  account: Mt5Account;
  snapshot: AccountSnapshot;
  capital: number;
  capital_basis: string;
  prescribed_lots_hint: string;
}

export interface SymbolBrief {
  name: string;
  description: string;
  path: string;
  group: string;
  digits: number;
  trade_allowed: boolean;
  bid: number;
  ask: number;
}

export interface SymbolSpec {
  name: string;
  digits: number;
  point: number;
  tick_size: number;
  tick_value_loss: number;
  tick_value_profit: number;
  contract_size: number;
  volume_min: number;
  volume_max: number;
  volume_step: number;
  stops_level_points: number;
  currency_base: string;
  currency_profit: string;
  description: string;
  trade_allowed: boolean;
  money_per_price_unit_per_lot: number;
  min_stop_distance: number;
}

export interface Tick {
  symbol: string;
  bid: number;
  ask: number;
  spread: number;
  time: string;
}

export interface RiskProfile {
  lots_per_1000: number;
  lot_rule_mode: LotRuleMode;
  max_risk_pct: number;
  capital_basis: CapitalBasis;
  fixed_capital: number;
  ladder_preset: LadderPreset;
  max_concurrent_positions: number;
  max_daily_loss_pct: number;
  margin_utilisation_cap_pct: number;
  require_stop_loss: boolean;
  min_reward_risk: number;
  allow_manual_override: boolean;
}

export interface LadderStageInfo {
  key: string;
  r_multiple: number;
  close_fraction: number;
  sl_action: string;
  note: string;
}

export interface LadderInfo {
  preset: string;
  label: string;
  description: string;
  stages: LadderStageInfo[];
}

export interface StagePlan {
  key: string;
  r_multiple: number;
  target_price: number;
  target_distance: number;
  target_points: number;
  volume: number;
  cumulative_volume: number;
  remaining_volume: number;
  money_profit: number;
  cumulative_money: number;
  sl_action: string;
  sl_after: number | null;
  sl_after_points_from_entry: number | null;
  locked_in_money: number;
  will_execute: boolean;
  note: string;
}

export interface TradePlan {
  symbol: string;
  side: Side;
  order_kind: OrderKind;

  entry_price: number;
  stop_loss: number;
  risk_distance: number;
  risk_points: number;
  bid: number;
  ask: number;
  spread: number;
  spread_points: number;
  digits: number;

  volume: number;
  prescribed_volume: number;
  volume_is_prescribed: boolean;
  volume_min: number;
  volume_max: number;
  volume_step: number;

  capital: number;
  capital_basis: string;
  account_currency: string;
  max_loss: number;
  risk_pct_of_capital: number;
  max_risk_pct: number;
  max_risk_money: number;
  risk_headroom: number;

  money_per_point: number;
  money_per_price_unit_per_lot: number;
  spread_cost: number;
  pricing_source: string;

  required_margin: number;
  margin_source: string;
  free_margin: number;
  margin_pct_of_free_margin: number;
  margin_pct_of_capital: number;

  stages: StagePlan[];
  expected_profit: number;
  expected_profit_pct_of_capital: number;
  reward_risk_final: number;
  reward_risk_blended: number;

  max_stop_distance: number;
  max_stop_points: number;
  max_stop_price: number;
  volume_for_requested_stop: number;
  min_stop_distance: number;

  ladder_preset: string;
  ladder_label: string;
  warnings: string[];
}

export interface RuleCheck {
  code: string;
  rule: string;
  passed: boolean;
  severity: Severity;
  message: string;
  overridable: boolean;
  details: Record<string, unknown>;
}

export interface RulesReport {
  approved: boolean;
  checks: RuleCheck[];
  overridden: string[];
  violations: string[];
  summary: string;
}

export interface Assessment {
  plan: TradePlan;
  rules: RulesReport;
  active_symbols: string[];
  blocking_ticket: number | null;
  ladder: LadderInfo;
}

export interface StopScanRow {
  stop_points: number;
  stop_price: number;
  loss: number;
  risk_pct: number;
  within_limit: boolean;
}

export interface TradeStage {
  stage_key: string;
  sequence: number;
  r_multiple: number;
  target_price: number;
  planned_volume: number;
  executed_volume: number;
  sl_action: string;
  sl_after: number | null;
  planned_profit: number;
  realised_pl: number;
  status: StageStatus;
  attempts: number;
  note: string;
  executed_at: string | null;
}

export interface Trade {
  id: number;
  mt5_account_id: number;
  symbol: string;
  side: Side;
  order_kind: string;
  status: TradeStatus;

  entry_price: number;
  requested_entry: number;
  initial_stop: number;
  current_stop: number;
  risk_distance: number;
  initial_volume: number;
  remaining_volume: number;
  ladder_preset: string;

  capital_at_entry: number;
  planned_risk: number;
  planned_risk_pct: number;
  planned_profit: number;
  account_currency: string;

  position_ticket: number | null;
  realised_pl: number;
  close_reason: string;
  comment: string;
  last_error: string;

  opened_at: string | null;
  closed_at: string | null;
  created_at: string;

  stages: TradeStage[];
}

export interface TradeEvent {
  id: number;
  trade_id: number | null;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface TradeDetail extends Trade {
  plan: Record<string, unknown>;
  rules: Record<string, unknown>;
  events: TradeEvent[];
}

export interface Submission {
  approved: boolean;
  executed: boolean;
  message: string;
  plan: TradePlan;
  rules: RulesReport;
  trade: Trade | null;
  fill_plan: TradePlan | null;
}

export interface TradeAction {
  trade_id: number;
  changed: boolean;
  closed: boolean;
  actions: string[];
  error: string;
  trade: Trade | null;
}

export interface Position {
  ticket: number;
  symbol: string;
  side: Side;
  volume: number;
  price_open: number;
  price_current: number;
  sl: number;
  tp: number;
  profit: number;
  swap: number;
  commission: number;
  magic: number;
  comment: string;
  opened_at: string | null;
}

export interface PositionRow {
  position: Position;
  managed: boolean;
  trade: Trade | null;
}

export interface PositionsOverview {
  account: AccountSnapshot;
  rows: PositionRow[];
  orphaned_trades: Trade[];
  risk_on: number;
}

export interface Decision {
  id: number;
  trade_id: number | null;
  symbol: string;
  side: Side;
  approved: boolean;
  executed: boolean;
  volume: number;
  entry_price: number;
  stop_loss: number;
  max_loss: number;
  risk_pct: number;
  expected_profit: number;
  reward_risk: number;
  violation_codes: string;
  summary: string;
  created_at: string;
}

export interface DecisionDetail extends Decision {
  plan: Record<string, unknown>;
  checks: RuleCheck[];
}

export interface Performance {
  window_days: number;
  closed_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  net_pl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  average_win: number;
  average_loss: number;
  decisions_approved: number;
  decisions_rejected: number;
  rule_adherence_pct: number;
  top_rejections: { codes: string; count: number }[];
}

export interface Health {
  status: string;
  app: string;
  version: string;
  environment: string;
  mt5_gateway: string;
  mt5_stats: Record<string, unknown>;
  monitor_running: boolean;
  server_time: string;
}

/** Payload of the `/api/ws/stream` WebSocket. */
export interface StreamSnapshot {
  type: "snapshot";
  server_time: string;
  account: AccountSnapshot;
  capital: number;
  max_risk_money: number;
  risk_on: number;
  positions: PositionRow[];
  active_trades: Trade[];
}

export interface StreamError {
  type: "error";
  code: string;
  message: string;
  server_time: string;
}

export type StreamMessage = StreamSnapshot | StreamError;

/** Body of `POST /api/calculator/preview` and `POST /api/trades`. */
export interface TradeRequest {
  symbol: string;
  side: Side;
  order_kind?: OrderKind;
  entry_price?: number | null;
  stop_loss?: number | null;
  stop_points?: number | null;
  volume?: number | null;
  ladder_preset?: LadderPreset | null;
  account_id?: number | null;
  override?: boolean;
  comment?: string;
}
