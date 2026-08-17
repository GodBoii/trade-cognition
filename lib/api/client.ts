/**
 * Typed API client.
 *
 * One place owns the base URL, the bearer token and error translation, so no
 * component ever touches `fetch` directly. Backend errors arrive as
 * `{code, message, details}`; they are rethrown as `ApiRequestError` with those
 * fields intact so the UI can react to a specific `code` instead of matching on
 * prose.
 */

import type {
  AccountState,
  Assessment,
  Decision,
  DecisionDetail,
  Health,
  LadderInfo,
  Mt5Account,
  Performance,
  PositionsOverview,
  RiskProfile,
  StopScanRow,
  SymbolBrief,
  SymbolSpec,
  Tick,
  TokenResponse,
  Trade,
  TradeAction,
  TradeDetail,
  TradeEvent,
  TradeRequest,
  Side,
  Submission,
  User,
} from "@/lib/api/types";

/**
 * Where the API lives.
 *
 * The default `/api` is a same-origin relative path, which Next rewrites to the
 * backend (see `next.config.ts`). That keeps the backend host a server-side
 * detail: no CORS, and no rebuild when the address changes.
 *
 * Set `NEXT_PUBLIC_API_URL` to the backend's absolute API base - e.g.
 * `https://trade-cognition-api.example.com/api` - when the browser should call
 * it directly instead. That is required for a static export, and it is the only
 * way to get the live WebSocket feed through a host that cannot proxy upgrades.
 * Being a `NEXT_PUBLIC_` variable it is inlined at build time.
 */
const API_BASE =
  (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/+$/, "") || "/api";

const TOKEN_KEY = "trade-cognition.token";

export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;

  constructor(message: string, code: string, status: number, details?: unknown) {
    super(message);
    this.name = "ApiRequestError";
    this.code = code;
    this.status = status;
    this.details = details;
  }

  /** True when the caller should send the user back to the sign-in screen. */
  get isAuthFailure(): boolean {
    return this.status === 401 || this.code === "token_expired";
  }
}

// ---------------------------------------------------------------------------
// token storage
// ---------------------------------------------------------------------------
let inMemoryToken: string | null = null;

export function getToken(): string | null {
  if (inMemoryToken) return inMemoryToken;
  try {
    inMemoryToken = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    inMemoryToken = null; // private browsing / storage disabled
  }
  return inMemoryToken;
}

export function setToken(token: string | null): void {
  inMemoryToken = token;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the in-memory copy still works for this session */
  }
}

// ---------------------------------------------------------------------------
// transport
// ---------------------------------------------------------------------------
type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function request<T>(
  method: string,
  path: string,
  options: { body?: unknown; query?: Query; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${withQuery(path, options.query)}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiRequestError(
      "Cannot reach the Trade Cognition API. Check that the backend is running.",
      "network_error",
      0,
    );
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload: unknown = text ? safeParse(text) : null;

  if (!response.ok) {
    const shaped = payload as { code?: string; message?: string; details?: unknown } | null;
    throw new ApiRequestError(
      shaped?.message ?? `Request failed with status ${response.status}.`,
      shaped?.code ?? `http_${response.status}`,
      response.status,
      shaped?.details,
    );
  }

  return payload as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

// ---------------------------------------------------------------------------
// endpoints
// ---------------------------------------------------------------------------
export const api = {
  health: () => request<Health>("GET", "/health"),

  // --- auth ---------------------------------------------------------------
  register: (email: string, password: string, displayName: string) =>
    request<TokenResponse>("POST", "/auth/register", {
      body: { email, password, display_name: displayName },
    }),
  login: (email: string, password: string) =>
    request<TokenResponse>("POST", "/auth/login", { body: { email, password } }),
  me: () => request<User>("GET", "/auth/me"),

  // --- MT5 accounts -------------------------------------------------------
  listAccounts: () => request<Mt5Account[]>("GET", "/mt5/accounts"),
  connectAccount: (body: {
    login: number;
    password: string;
    server: string;
    label?: string;
    terminal_path?: string;
  }) => request<AccountState>("POST", "/mt5/accounts", { body }),
  accountState: (accountId?: number) =>
    request<AccountState>("GET", "/mt5/accounts/state", { query: { account_id: accountId } }),
  setDefaultAccount: (accountId: number) =>
    request<Mt5Account>("POST", `/mt5/accounts/${accountId}/default`),
  disconnectAccount: (accountId: number) =>
    request<void>("DELETE", `/mt5/accounts/${accountId}`),

  // --- market -------------------------------------------------------------
  symbols: (search?: string, accountId?: number) =>
    request<SymbolBrief[]>("GET", "/market/symbols", {
      query: { q: search, account_id: accountId, limit: 500 },
    }),
  symbolSpec: (symbol: string, accountId?: number) =>
    request<SymbolSpec>("GET", `/market/symbols/${encodeURIComponent(symbol)}/spec`, {
      query: { account_id: accountId },
    }),
  tick: (symbol: string, accountId?: number) =>
    request<Tick>("GET", `/market/symbols/${encodeURIComponent(symbol)}/tick`, {
      query: { account_id: accountId },
    }),

  // --- rules --------------------------------------------------------------
  profile: () => request<RiskProfile>("GET", "/rules/profile"),
  saveProfile: (profile: RiskProfile) =>
    request<RiskProfile>("PUT", "/rules/profile", { body: profile }),
  ladders: () => request<LadderInfo[]>("GET", "/calculator/ladders"),

  // --- calculator ---------------------------------------------------------
  preview: (body: TradeRequest, signal?: AbortSignal) =>
    request<Assessment>("POST", "/calculator/preview", { body, signal }),
  stopScan: (symbol: string, side: Side, stopPoints: number[], accountId?: number) =>
    request<StopScanRow[]>("POST", "/calculator/stop-scan", {
      body: { symbol, side, stop_points: stopPoints, account_id: accountId },
    }),

  // --- trades -------------------------------------------------------------
  submitTrade: (body: TradeRequest) => request<Submission>("POST", "/trades", { body }),
  trades: (query?: { status?: string; symbol?: string; active_only?: boolean; limit?: number }) =>
    request<Trade[]>("GET", "/trades", { query: query as Query }),
  trade: (id: number) => request<TradeDetail>("GET", `/trades/${id}`),
  closeTrade: (id: number, volume?: number) =>
    request<TradeAction>("POST", `/trades/${id}/close`, { body: { volume: volume ?? null } }),
  syncTrade: (id: number) => request<TradeAction>("POST", `/trades/${id}/sync`),
  manageTrade: (id: number) => request<TradeAction>("POST", `/trades/${id}/manage`),
  positions: (accountId?: number) =>
    request<PositionsOverview>("GET", "/positions", { query: { account_id: accountId } }),

  // --- journal ------------------------------------------------------------
  events: (query?: { trade_id?: number; limit?: number }) =>
    request<TradeEvent[]>("GET", "/journal/events", { query: query as Query }),
  decisions: (query?: { approved?: boolean; symbol?: string; limit?: number }) =>
    request<Decision[]>("GET", "/journal/decisions", { query: query as Query }),
  decision: (id: number) => request<DecisionDetail>("GET", `/journal/decisions/${id}`),
  performance: (days = 30) =>
    request<Performance>("GET", "/journal/performance", { query: { days } }),
};

/**
 * WebSocket URL for the live dashboard stream.
 *
 * Resolves against `API_BASE`, so it follows the API wherever it lives, and
 * upgrades the scheme to `ws`/`wss` to match. Returns `null` when there is no
 * token; the caller then falls back to REST polling.
 *
 * Note: platforms that only serve static files (Vercel, Netlify) cannot proxy a
 * WebSocket. Point `NEXT_PUBLIC_API_URL` straight at the backend when using
 * this legacy client so the socket bypasses them.
 */
export function streamUrl(accountId?: number): string | null {
  const token = getToken();
  if (!token) return null;

  const url = new URL(`${API_BASE}/ws/stream`, window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("token", token);
  if (accountId) url.searchParams.set("account_id", String(accountId));
  return url.toString();
}
