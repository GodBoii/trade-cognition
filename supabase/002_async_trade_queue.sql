-- Trade Cognition: asynchronous browser -> Supabase -> local MT5 worker bridge
--
-- Run this file after 000_RUN_THIS_IN_SUPABASE.sql (or the original
-- 001_auth_profiles.sql) in Supabase SQL Editor.
-- It is safe to run repeatedly: tables/indexes use IF NOT EXISTS and all
-- policies, triggers, grants, and functions are recreated deterministically.
--
-- SECURITY MODEL
-- --------------
-- * The browser authenticates normally with Supabase Auth and can only see rows
--   owned by auth.uid() through RLS.
-- * The local Docker worker does NOT receive the Supabase service_role key.
-- * Instead, an authenticated user calls tcq_create_worker() once.  Supabase
--   returns a high-entropy worker token once and stores only its SHA-256 hash in
--   the non-exposed trade_private schema.  Put the plaintext token in the local
--   Docker environment; never in NEXT_PUBLIC_* or Git.
-- * Worker RPCs accept that scoped token and can access only the owning user's
--   connections, commands, rules, and events.
-- * Command claims use row locks, SKIP LOCKED, a random claim token, and an
--   expiring lease.  This makes claiming atomic and allows recovery after a
--   local worker crash without executing one command concurrently twice.

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

create schema if not exists trade_private;
revoke all on schema trade_private from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- User-owned configuration
-- ---------------------------------------------------------------------------

create table if not exists public.user_trading_rules (
  user_id uuid primary key references auth.users (id) on delete cascade,
  lots_per_1000 numeric(12, 4) not null default 0.0200,
  max_risk_pct numeric(7, 4) not null default 2.0000,
  capital_basis text not null default 'balance',
  fixed_capital numeric(20, 4) not null default 0,
  ladder_preset text not null default 'runner_1_2_3',
  tp1_close_fraction numeric(7, 6) not null default 0.500000,
  tp2_close_fraction numeric(7, 6) not null default 0.250000,
  tp3_close_fraction numeric(7, 6) not null default 0.250000,
  one_active_trade_per_symbol boolean not null default true,
  require_stop_loss boolean not null default true,
  max_concurrent_positions integer not null default 0,
  max_daily_loss_pct numeric(7, 4) not null default 0,
  margin_utilisation_cap_pct numeric(7, 4) not null default 50,
  min_reward_risk numeric(10, 4) not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint user_trading_rules_lots_strict check (lots_per_1000 = 0.0200),
  constraint user_trading_rules_risk_range check (max_risk_pct > 0 and max_risk_pct <= 2),
  constraint user_trading_rules_capital_basis check (capital_basis in ('balance', 'equity', 'fixed')),
  constraint user_trading_rules_fixed_capital check (fixed_capital >= 0),
  constraint user_trading_rules_ladder check (ladder_preset in ('runner_1_2_3', 'standard_1_2_3', 'custom')),
  constraint user_trading_rules_stage_fractions check (
    tp1_close_fraction >= 0 and tp2_close_fraction >= 0 and tp3_close_fraction >= 0
    and tp1_close_fraction <= 1 and tp2_close_fraction <= 1 and tp3_close_fraction <= 1
    and abs((tp1_close_fraction + tp2_close_fraction + tp3_close_fraction) - 1) < 0.000001
  ),
  constraint user_trading_rules_concurrency check (max_concurrent_positions between 0 and 100),
  constraint user_trading_rules_daily_loss check (max_daily_loss_pct between 0 and 100),
  constraint user_trading_rules_margin_cap check (margin_utilisation_cap_pct between 0 and 100),
  constraint user_trading_rules_min_rr check (min_reward_risk between 0 and 100)
);

comment on table public.user_trading_rules is
  'Authoritative per-user strategy configuration. The local MT5 worker must revalidate every trade against these rules and live broker data.';
comment on column public.user_trading_rules.lots_per_1000 is
  'Rule 2 strict allocation: exactly 0.02 lots for each 1,000 units of selected trading capital.';
comment on column public.user_trading_rules.max_risk_pct is
  'Hard maximum monetary loss at the proposed stop, as a percentage of selected trading capital.';
comment on column public.user_trading_rules.max_concurrent_positions is
  'Zero means no portfolio-wide cap; one_active_trade_per_symbol remains independently enforced.';
comment on column public.user_trading_rules.ladder_preset is
  'runner_1_2_3 is the coherent default (50%/25%/25%); standard_1_2_3 preserves the literal legacy 50%/50% behavior; custom uses the explicit fraction columns.';

-- Deterministic upgrade path for an earlier draft that called the legacy
-- behavior half_then_half and defaulted to it. Drop the old check before data
-- normalization, then install the canonical backend-compatible values.
alter table public.user_trading_rules
  drop constraint if exists user_trading_rules_ladder;
update public.user_trading_rules
set ladder_preset = 'standard_1_2_3',
    tp1_close_fraction = 0.500000,
    tp2_close_fraction = 0.500000,
    tp3_close_fraction = 0.000000
where ladder_preset = 'half_then_half';
update public.user_trading_rules
set ladder_preset = 'runner_1_2_3'
where ladder_preset = 'standard_1_2_3'
  and tp1_close_fraction = 0.500000
  and tp2_close_fraction = 0.250000
  and tp3_close_fraction = 0.250000;
alter table public.user_trading_rules
  alter column ladder_preset set default 'runner_1_2_3';
alter table public.user_trading_rules
  add constraint user_trading_rules_ladder
  check (ladder_preset in ('runner_1_2_3', 'standard_1_2_3', 'custom'));

-- Upgrade drafts that allowed looser values. Product Rules 2 and 3 are hard
-- ceilings, not user-overridable preferences.
alter table public.user_trading_rules
  drop constraint if exists user_trading_rules_lots_positive,
  drop constraint if exists user_trading_rules_lots_strict,
  drop constraint if exists user_trading_rules_risk_range;
update public.user_trading_rules
set lots_per_1000 = 0.0200,
    max_risk_pct = least(greatest(max_risk_pct, 0.0001), 2.0000);
alter table public.user_trading_rules
  add constraint user_trading_rules_lots_strict check (lots_per_1000 = 0.0200),
  add constraint user_trading_rules_risk_range check (max_risk_pct > 0 and max_risk_pct <= 2);

-- ---------------------------------------------------------------------------
-- Worker identity and MT5 connection metadata (never MT5 credentials)
-- ---------------------------------------------------------------------------

create table if not exists public.worker_agents (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null default 'Docker MT5 worker',
  last_seen_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint worker_agents_name_length check (char_length(name) between 1 and 120),
  constraint worker_agents_id_user_unique unique (id, user_id)
);

comment on table public.worker_agents is
  'User-visible local worker registrations. Secret hashes are deliberately stored in trade_private.worker_credentials, not this API-exposed table.';

create table if not exists trade_private.worker_credentials (
  worker_id uuid primary key references public.worker_agents (id) on delete cascade,
  token_hash bytea not null unique,
  rotated_at timestamptz not null default now()
);

comment on table trade_private.worker_credentials is
  'SHA-256 hashes of scoped worker tokens. Plaintext tokens and Supabase service-role credentials are never stored here.';

create table if not exists public.mt5_connections (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  worker_id uuid not null,
  label text not null default 'My MT5 account',
  mt5_login bigint,
  server text not null default '',
  company text not null default '',
  account_name text not null default '',
  currency text not null default '',
  leverage integer,
  status text not null default 'pending',
  is_enabled boolean not null default true,
  trade_allowed boolean,
  expert_allowed boolean,
  last_balance numeric(20, 4),
  last_equity numeric(20, 4),
  last_margin numeric(20, 4),
  last_free_margin numeric(20, 4),
  last_error text not null default '',
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint mt5_connections_worker_owner_fk
    foreign key (worker_id, user_id) references public.worker_agents (id, user_id) on delete restrict,
  constraint mt5_connections_label_length check (char_length(label) between 1 and 120),
  constraint mt5_connections_login_positive check (mt5_login is null or mt5_login > 0),
  constraint mt5_connections_leverage_positive check (leverage is null or leverage > 0),
  constraint mt5_connections_status check (status in ('pending', 'online', 'offline', 'error', 'disconnected')),
  constraint mt5_connections_id_user_unique unique (id, user_id)
);

comment on table public.mt5_connections is
  'Non-secret MT5 account identity and latest worker snapshot. MT5 passwords and terminal credentials must remain only in the local MT5 terminal/worker.';
comment on column public.mt5_connections.mt5_login is
  'Broker account number reported by MT5; it is metadata, not an authentication credential.';

-- ---------------------------------------------------------------------------
-- Durable trade intent, command queue, and append-only event stream
-- ---------------------------------------------------------------------------

create table if not exists public.trade_intents (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  connection_id uuid not null,
  client_request_id uuid not null,
  symbol text not null,
  side text not null,
  order_kind text not null default 'market',
  requested_entry numeric(24, 10),
  stop_loss numeric(24, 10),
  stop_points numeric(24, 10),
  requested_volume numeric(16, 8),
  comment text not null default '',
  status text not null default 'queued',
  execute_before timestamptz not null default (now() + interval '5 minutes'),
  broker_order_ticket bigint,
  broker_position_ticket bigint,
  approved_plan jsonb,
  rules_report jsonb,
  last_error text not null default '',
  metadata jsonb not null default '{}'::jsonb,
  submitted_at timestamptz,
  opened_at timestamptz,
  closed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint trade_intents_connection_owner_fk
    foreign key (connection_id, user_id) references public.mt5_connections (id, user_id) on delete restrict,
  constraint trade_intents_request_unique unique (user_id, client_request_id),
  constraint trade_intents_id_user_unique unique (id, user_id),
  constraint trade_intents_symbol check (char_length(symbol) between 1 and 40 and symbol = upper(symbol)),
  constraint trade_intents_side check (side in ('buy', 'sell')),
  constraint trade_intents_order_kind check (order_kind in ('market', 'limit', 'stop')),
  constraint trade_intents_entry check (requested_entry is null or requested_entry > 0),
  constraint trade_intents_stop_loss check (stop_loss is null or stop_loss > 0),
  constraint trade_intents_stop_points check (stop_points is null or stop_points > 0),
  constraint trade_intents_exactly_one_stop check ((stop_loss is null) <> (stop_points is null)),
  constraint trade_intents_volume check (requested_volume is null or requested_volume > 0),
  constraint trade_intents_comment_length check (char_length(comment) <= 48),
  constraint trade_intents_status check (status in (
    'queued', 'claimed', 'validating', 'rejected', 'submitted', 'open',
    'scaling', 'closed', 'failed', 'cancelled', 'expired'
  )),
  constraint trade_intents_execution_window check (execute_before > created_at),
  constraint trade_intents_metadata_object check (jsonb_typeof(metadata) = 'object'),
  constraint trade_intents_plan_object check (approved_plan is null or jsonb_typeof(approved_plan) = 'object'),
  constraint trade_intents_rules_object check (rules_report is null or jsonb_typeof(rules_report) = 'object')
);

comment on table public.trade_intents is
  'A user request to open and manage one MT5 trade. Browser writes go through tcq_enqueue_trade_intent so validation and idempotency cannot be bypassed.';
comment on column public.trade_intents.client_request_id is
  'Browser-generated UUID idempotency key. Retrying the same request returns the original intent instead of placing a second trade.';
comment on column public.trade_intents.execute_before is
  'Latest time at which a local worker may begin executing this request. Authenticated enqueue RPCs cap this deadline at 15 minutes.';

-- Upgrade a previously applied draft deterministically.
alter table public.trade_intents
  add column if not exists execute_before timestamptz;
update public.trade_intents
set execute_before = created_at + interval '5 minutes'
where execute_before is null;
alter table public.trade_intents
  alter column execute_before set default (now() + interval '5 minutes'),
  alter column execute_before set not null;
alter table public.trade_intents drop constraint if exists trade_intents_execution_window;
alter table public.trade_intents add constraint trade_intents_execution_window
  check (execute_before > created_at);
alter table public.trade_intents drop constraint if exists trade_intents_status;
alter table public.trade_intents add constraint trade_intents_status check (status in (
  'queued', 'claimed', 'validating', 'rejected', 'submitted', 'open',
  'scaling', 'closed', 'failed', 'cancelled', 'expired'
));

create table if not exists public.trade_commands (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  connection_id uuid not null,
  intent_id uuid,
  client_request_id uuid not null,
  command_type text not null,
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'pending',
  priority smallint not null default 100,
  available_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '5 minutes'),
  claimed_by uuid,
  claim_token uuid,
  claimed_at timestamptz,
  lease_expires_at timestamptz,
  attempts integer not null default 0,
  max_attempts integer not null default 5,
  result jsonb,
  error_code text not null default '',
  error_message text not null default '',
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint trade_commands_connection_owner_fk
    foreign key (connection_id, user_id) references public.mt5_connections (id, user_id) on delete restrict,
  constraint trade_commands_intent_owner_fk
    foreign key (intent_id, user_id) references public.trade_intents (id, user_id) on delete cascade,
  constraint trade_commands_claimed_by_fk foreign key (claimed_by) references public.worker_agents (id) on delete set null,
  constraint trade_commands_request_unique unique (user_id, client_request_id),
  constraint trade_commands_command_type check (command_type in ('submit_trade', 'close_trade', 'sync_trade', 'refresh_account')),
  constraint trade_commands_payload_object check (jsonb_typeof(payload) = 'object'),
  constraint trade_commands_status check (status in ('pending', 'claimed', 'succeeded', 'rejected', 'failed', 'cancelled', 'expired')),
  constraint trade_commands_priority check (priority between 0 and 1000),
  constraint trade_commands_attempts check (attempts >= 0 and max_attempts between 1 and 20),
  constraint trade_commands_result_object check (result is null or jsonb_typeof(result) = 'object'),
  constraint trade_commands_expiry check (expires_at > created_at),
  constraint trade_commands_claim_fields check (
    (status = 'claimed' and claimed_by is not null and claim_token is not null and lease_expires_at is not null)
    or status <> 'claimed'
  )
);

comment on table public.trade_commands is
  'Durable at-least-once command queue consumed by a paired local worker. Broker actions must additionally be idempotent by intent ID/comment/magic because a crash can occur after MT5 accepts an order but before completion is recorded.';
comment on column public.trade_commands.claim_token is
  'Per-claim fencing token. A stale worker cannot complete a command after its lease has expired and another worker loop has reclaimed it.';
comment on column public.trade_commands.expires_at is
  'Hard do-not-start-after deadline. Claiming atomically marks overdue work expired instead of sending a stale order to MT5.';

-- Upgrade a previously applied draft deterministically.
alter table public.trade_commands
  add column if not exists expires_at timestamptz;
update public.trade_commands
set expires_at = created_at + interval '5 minutes'
where expires_at is null;
alter table public.trade_commands
  alter column expires_at set default (now() + interval '5 minutes'),
  alter column expires_at set not null;
alter table public.trade_commands drop constraint if exists trade_commands_expiry;
alter table public.trade_commands add constraint trade_commands_expiry
  check (expires_at > created_at);
alter table public.trade_commands drop constraint if exists trade_commands_status;
alter table public.trade_commands add constraint trade_commands_status
  check (status in ('pending', 'claimed', 'succeeded', 'rejected', 'failed', 'cancelled', 'expired'));

create table if not exists public.trade_events (
  id bigint generated by default as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  connection_id uuid not null,
  intent_id uuid,
  command_id uuid,
  event_type text not null,
  message text not null default '',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint trade_events_connection_owner_fk
    foreign key (connection_id, user_id) references public.mt5_connections (id, user_id) on delete cascade,
  constraint trade_events_intent_owner_fk
    foreign key (intent_id, user_id) references public.trade_intents (id, user_id) on delete cascade,
  constraint trade_events_command_fk foreign key (command_id) references public.trade_commands (id) on delete set null,
  constraint trade_events_type_length check (char_length(event_type) between 1 and 80),
  constraint trade_events_message_length check (char_length(message) <= 2000),
  constraint trade_events_payload_object check (jsonb_typeof(payload) = 'object')
);

comment on table public.trade_events is
  'Append-only user-visible audit stream written by trusted worker RPCs. Direct browser inserts, updates, and deletes are not granted.';

-- Queue and UI access paths.
create index if not exists worker_agents_user_idx on public.worker_agents (user_id, created_at desc);
create index if not exists mt5_connections_user_status_idx on public.mt5_connections (user_id, status, created_at desc);
create index if not exists mt5_connections_worker_idx on public.mt5_connections (worker_id, is_enabled);
create index if not exists trade_intents_user_created_idx on public.trade_intents (user_id, created_at desc);
create index if not exists trade_intents_connection_status_idx on public.trade_intents (connection_id, status, created_at);
-- The product rule is per user + derivative, not per account. Dropping before
-- recreating also upgrades an earlier draft of this migration idempotently.
drop index if exists public.trade_intents_one_active_symbol_idx;
create unique index trade_intents_one_active_symbol_idx
  on public.trade_intents (user_id, upper(symbol))
  where status in ('queued', 'claimed', 'validating', 'submitted', 'open', 'scaling');
drop index if exists public.trade_commands_claim_idx;
create index trade_commands_claim_idx
  on public.trade_commands (status, available_at, expires_at, priority, created_at)
  where status in ('pending', 'claimed');
create index if not exists trade_commands_user_created_idx on public.trade_commands (user_id, created_at desc);
create index if not exists trade_commands_intent_idx on public.trade_commands (intent_id, created_at);
create index if not exists trade_events_user_created_idx on public.trade_events (user_id, created_at desc);
create index if not exists trade_events_intent_created_idx on public.trade_events (intent_id, created_at);

-- ---------------------------------------------------------------------------
-- Timestamp/default-row triggers
-- ---------------------------------------------------------------------------

create or replace function public.tcq_set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

revoke all on function public.tcq_set_updated_at() from public;

drop trigger if exists tcq_rules_updated_at on public.user_trading_rules;
create trigger tcq_rules_updated_at before update on public.user_trading_rules
for each row execute procedure public.tcq_set_updated_at();
drop trigger if exists tcq_workers_updated_at on public.worker_agents;
create trigger tcq_workers_updated_at before update on public.worker_agents
for each row execute procedure public.tcq_set_updated_at();
drop trigger if exists tcq_connections_updated_at on public.mt5_connections;
create trigger tcq_connections_updated_at before update on public.mt5_connections
for each row execute procedure public.tcq_set_updated_at();
drop trigger if exists tcq_intents_updated_at on public.trade_intents;
create trigger tcq_intents_updated_at before update on public.trade_intents
for each row execute procedure public.tcq_set_updated_at();
drop trigger if exists tcq_commands_updated_at on public.trade_commands;
create trigger tcq_commands_updated_at before update on public.trade_commands
for each row execute procedure public.tcq_set_updated_at();

create or replace function public.tcq_create_default_rules()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.user_trading_rules (user_id) values (new.id)
  on conflict (user_id) do nothing;
  return new;
end;
$$;

revoke all on function public.tcq_create_default_rules() from public;
drop trigger if exists tcq_create_default_rules on auth.users;
create trigger tcq_create_default_rules after insert on auth.users
for each row execute procedure public.tcq_create_default_rules();

insert into public.user_trading_rules (user_id)
select id from auth.users
on conflict (user_id) do nothing;

-- Every new intent automatically creates exactly one submit command using the
-- same client idempotency UUID. The two tables have separate uniqueness scopes.
create or replace function public.tcq_enqueue_submit_command()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.trade_commands (
    user_id, connection_id, intent_id, client_request_id, command_type, payload, expires_at
  ) values (
    new.user_id,
    new.connection_id,
    new.id,
    new.client_request_id,
    'submit_trade',
    jsonb_build_object(
      'intent_id', new.id,
      'symbol', new.symbol,
      'side', new.side,
      'order_kind', new.order_kind,
      'requested_entry', new.requested_entry,
      'stop_loss', new.stop_loss,
      'stop_points', new.stop_points,
      'requested_volume', new.requested_volume,
      'comment', new.comment,
      'metadata', new.metadata,
      'execute_before', new.execute_before
    ),
    new.execute_before
  ) on conflict (user_id, client_request_id) do nothing;
  return new;
end;
$$;

revoke all on function public.tcq_enqueue_submit_command() from public;
drop trigger if exists tcq_enqueue_submit_command on public.trade_intents;
create trigger tcq_enqueue_submit_command after insert on public.trade_intents
for each row execute procedure public.tcq_enqueue_submit_command();

-- Keep the user-facing intent in step with infrastructure-level submit-command
-- transitions, including automatic lease exhaustion inside the claim RPC.
create or replace function public.tcq_sync_submit_command_status()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.command_type = 'submit_trade' and new.intent_id is not null
     and new.status is distinct from old.status then
    update public.trade_intents
    set status = case new.status
          when 'claimed' then 'claimed'
          when 'rejected' then 'rejected'
          when 'failed' then 'failed'
          when 'cancelled' then 'cancelled'
          when 'expired' then 'expired'
          else status
        end,
        last_error = case when new.status in ('rejected', 'failed')
                          then left(new.error_message, 2000) else last_error end,
        closed_at = case when new.status in ('cancelled', 'expired') then coalesce(closed_at, now()) else closed_at end
    where id = new.intent_id and user_id = new.user_id
      and status in ('queued', 'claimed', 'validating');
  end if;
  return new;
end;
$$;

revoke all on function public.tcq_sync_submit_command_status() from public;
drop trigger if exists tcq_sync_submit_command_status on public.trade_commands;
create trigger tcq_sync_submit_command_status
after update of status on public.trade_commands
for each row execute procedure public.tcq_sync_submit_command_status();

-- ---------------------------------------------------------------------------
-- Row Level Security and narrow browser grants
-- ---------------------------------------------------------------------------

alter table public.user_trading_rules enable row level security;
alter table public.worker_agents enable row level security;
alter table public.mt5_connections enable row level security;
alter table public.trade_intents enable row level security;
alter table public.trade_commands enable row level security;
alter table public.trade_events enable row level security;

drop policy if exists "tcq users own rules" on public.user_trading_rules;
create policy "tcq users own rules" on public.user_trading_rules
for all to authenticated using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

drop policy if exists "tcq users read own workers" on public.worker_agents;
create policy "tcq users read own workers" on public.worker_agents
for select to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "tcq users own connections" on public.mt5_connections;
create policy "tcq users own connections" on public.mt5_connections
for all to authenticated using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

drop policy if exists "tcq users read own intents" on public.trade_intents;
create policy "tcq users read own intents" on public.trade_intents
for select to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "tcq users read own commands" on public.trade_commands;
create policy "tcq users read own commands" on public.trade_commands
for select to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "tcq users read own events" on public.trade_events;
create policy "tcq users read own events" on public.trade_events
for select to authenticated using ((select auth.uid()) = user_id);

revoke all on table public.user_trading_rules from anon, authenticated;
revoke all on table public.worker_agents from anon, authenticated;
revoke all on table public.mt5_connections from anon, authenticated;
revoke all on table public.trade_intents from anon, authenticated;
revoke all on table public.trade_commands from anon, authenticated;
revoke all on table public.trade_events from anon, authenticated;

grant select on table public.user_trading_rules to authenticated;
grant insert (
  user_id, lots_per_1000, max_risk_pct, capital_basis, fixed_capital,
  ladder_preset, tp1_close_fraction, tp2_close_fraction, tp3_close_fraction,
  one_active_trade_per_symbol, require_stop_loss, max_concurrent_positions,
  max_daily_loss_pct, margin_utilisation_cap_pct, min_reward_risk
) on table public.user_trading_rules to authenticated;
grant update (
  lots_per_1000, max_risk_pct, capital_basis, fixed_capital,
  ladder_preset, tp1_close_fraction, tp2_close_fraction, tp3_close_fraction,
  one_active_trade_per_symbol, require_stop_loss, max_concurrent_positions,
  max_daily_loss_pct, margin_utilisation_cap_pct, min_reward_risk
) on table public.user_trading_rules to authenticated;
grant select on table public.worker_agents to authenticated;
grant select on table public.mt5_connections to authenticated;
grant insert (user_id, worker_id, label) on table public.mt5_connections to authenticated;
grant update (label, is_enabled) on table public.mt5_connections to authenticated;
grant select on table public.trade_intents to authenticated;
grant select on table public.trade_commands to authenticated;
grant select on table public.trade_events to authenticated;
grant all on table public.user_trading_rules, public.worker_agents,
  public.mt5_connections, public.trade_intents, public.trade_commands,
  public.trade_events to service_role;
grant usage, select on sequence public.trade_events_id_seq to service_role;

-- ---------------------------------------------------------------------------
-- Authenticated browser RPCs
-- ---------------------------------------------------------------------------

create or replace function public.tcq_create_worker(p_name text default 'Docker MT5 worker')
returns table (worker_id uuid, worker_token text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user uuid := auth.uid();
  v_worker uuid := extensions.gen_random_uuid();
  v_token text := 'tcw_' || encode(extensions.gen_random_bytes(32), 'hex');
begin
  if v_user is null then
    raise exception 'Authentication required' using errcode = '28000';
  end if;
  if p_name is null or char_length(btrim(p_name)) not between 1 and 120 then
    raise exception 'Worker name must contain 1 to 120 characters' using errcode = '22023';
  end if;

  insert into public.worker_agents (id, user_id, name)
  values (v_worker, v_user, btrim(p_name));
  insert into trade_private.worker_credentials (worker_id, token_hash)
  values (v_worker, extensions.digest(v_token, 'sha256'));

  return query select v_worker, v_token;
end;
$$;

comment on function public.tcq_create_worker(text) is
  'Creates a user-scoped local worker and returns its secret once. Store it only in the local Docker environment.';

create or replace function public.tcq_rotate_worker_token(p_worker_id uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user uuid := auth.uid();
  v_token text := 'tcw_' || encode(extensions.gen_random_bytes(32), 'hex');
begin
  if v_user is null then raise exception 'Authentication required' using errcode = '28000'; end if;
  if not exists (
    select 1 from public.worker_agents
    where id = p_worker_id and user_id = v_user and revoked_at is null
  ) then
    raise exception 'Active worker not found' using errcode = 'P0002';
  end if;

  insert into trade_private.worker_credentials (worker_id, token_hash, rotated_at)
  values (p_worker_id, extensions.digest(v_token, 'sha256'), now())
  on conflict (worker_id) do update
  set token_hash = excluded.token_hash, rotated_at = excluded.rotated_at;
  return v_token;
end;
$$;

create or replace function public.tcq_revoke_worker(p_worker_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then raise exception 'Authentication required' using errcode = '28000'; end if;
  update public.worker_agents set revoked_at = now()
  where id = p_worker_id and user_id = v_user and revoked_at is null;
  if not found then raise exception 'Active worker not found' using errcode = 'P0002'; end if;
  delete from trade_private.worker_credentials where worker_id = p_worker_id;
  update public.mt5_connections set status = 'disconnected', is_enabled = false
  where worker_id = p_worker_id and user_id = v_user;
end;
$$;

-- The old signature is removed so an already-applied draft cannot bypass the
-- new execution deadline through a stale PostgREST overload.
drop function if exists public.tcq_enqueue_trade_intent(uuid, uuid, text, text, text, numeric, numeric, numeric, numeric, text, jsonb);
create or replace function public.tcq_enqueue_trade_intent(
  p_connection_id uuid,
  p_client_request_id uuid,
  p_symbol text,
  p_side text,
  p_order_kind text default 'market',
  p_requested_entry numeric default null,
  p_stop_loss numeric default null,
  p_stop_points numeric default null,
  p_requested_volume numeric default null,
  p_comment text default '',
  p_metadata jsonb default '{}'::jsonb,
  p_execute_before timestamptz default null
)
returns public.trade_intents
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user uuid := auth.uid();
  v_row public.trade_intents;
  v_symbol text := upper(btrim(coalesce(p_symbol, '')));
  v_execute_before timestamptz := coalesce(p_execute_before, now() + interval '5 minutes');
begin
  if v_user is null then raise exception 'Authentication required' using errcode = '28000'; end if;
  if p_client_request_id is null then raise exception 'client_request_id is required' using errcode = '22023'; end if;
  if not exists (
    select 1 from public.mt5_connections
    where id = p_connection_id and user_id = v_user and is_enabled
  ) then
    raise exception 'Enabled MT5 connection not found' using errcode = 'P0002';
  end if;
  if jsonb_typeof(coalesce(p_metadata, '{}'::jsonb)) <> 'object' then
    raise exception 'metadata must be a JSON object' using errcode = '22023';
  end if;
  if v_execute_before <= now() or v_execute_before > now() + interval '15 minutes' then
    raise exception 'execute_before must be in the future and no more than 15 minutes from now' using errcode = '22023';
  end if;

  select * into v_row from public.trade_intents
  where user_id = v_user and client_request_id = p_client_request_id;
  if found then
    if v_row.connection_id <> p_connection_id or v_row.symbol <> v_symbol
       or v_row.side <> lower(p_side) or v_row.order_kind <> lower(p_order_kind)
       or v_row.requested_entry is distinct from p_requested_entry
       or v_row.stop_loss is distinct from p_stop_loss
       or v_row.stop_points is distinct from p_stop_points
       or v_row.requested_volume is distinct from p_requested_volume then
      raise exception 'client_request_id was already used for a different trade intent' using errcode = '23505';
    end if;
    return v_row;
  end if;

  insert into public.trade_intents (
    user_id, connection_id, client_request_id, symbol, side, order_kind,
    requested_entry, stop_loss, stop_points, requested_volume, comment, metadata
    , execute_before
  ) values (
    v_user, p_connection_id, p_client_request_id, v_symbol, lower(p_side),
    lower(p_order_kind), p_requested_entry, p_stop_loss, p_stop_points,
    p_requested_volume, coalesce(p_comment, ''), coalesce(p_metadata, '{}'::jsonb),
    v_execute_before
  ) returning * into v_row;
  return v_row;
end;
$$;

drop function if exists public.tcq_enqueue_trade_command(uuid, uuid, uuid, text, jsonb);
create or replace function public.tcq_enqueue_trade_command(
  p_connection_id uuid,
  p_intent_id uuid,
  p_client_request_id uuid,
  p_command_type text,
  p_payload jsonb default '{}'::jsonb,
  p_execute_before timestamptz default null
)
returns public.trade_commands
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user uuid := auth.uid();
  v_row public.trade_commands;
  v_kind text := lower(coalesce(p_command_type, ''));
  v_intent uuid;
  v_execute_before timestamptz := coalesce(p_execute_before, now() + interval '5 minutes');
begin
  if v_user is null then raise exception 'Authentication required' using errcode = '28000'; end if;
  if p_client_request_id is null then raise exception 'client_request_id is required' using errcode = '22023'; end if;
  if v_kind not in ('close_trade', 'sync_trade', 'refresh_account') then
    raise exception 'Unsupported user command type' using errcode = '22023';
  end if;
  if jsonb_typeof(coalesce(p_payload, '{}'::jsonb)) <> 'object' then
    raise exception 'payload must be a JSON object' using errcode = '22023';
  end if;
  if v_execute_before <= now() or v_execute_before > now() + interval '15 minutes' then
    raise exception 'execute_before must be in the future and no more than 15 minutes from now' using errcode = '22023';
  end if;
  if not exists (
    select 1 from public.mt5_connections
    where id = p_connection_id and user_id = v_user and is_enabled
  ) then raise exception 'Enabled MT5 connection not found' using errcode = 'P0002'; end if;
  if v_kind <> 'refresh_account' and not exists (
    select 1 from public.trade_intents
    where id = p_intent_id and user_id = v_user and connection_id = p_connection_id
  ) then raise exception 'Trade intent not found for this connection' using errcode = 'P0002'; end if;
  v_intent := case when v_kind = 'refresh_account' then null else p_intent_id end;

  select * into v_row from public.trade_commands
  where user_id = v_user and client_request_id = p_client_request_id;
  if found then
    if v_row.connection_id <> p_connection_id
       or v_row.intent_id is distinct from v_intent
       or v_row.command_type <> v_kind
       or v_row.payload <> coalesce(p_payload, '{}'::jsonb) then
      raise exception 'client_request_id was already used for a different command' using errcode = '23505';
    end if;
    return v_row;
  end if;

  insert into public.trade_commands (
    user_id, connection_id, intent_id, client_request_id, command_type, payload, expires_at
  ) values (
    v_user, p_connection_id, v_intent,
    p_client_request_id, v_kind, coalesce(p_payload, '{}'::jsonb), v_execute_before
  ) returning * into v_row;
  return v_row;
end;
$$;

create or replace function public.tcq_cancel_pending_command(p_command_id uuid)
returns public.trade_commands
language plpgsql
security definer
set search_path = ''
as $$
declare v_user uuid := auth.uid(); v_row public.trade_commands;
begin
  if v_user is null then raise exception 'Authentication required' using errcode = '28000'; end if;
  update public.trade_commands
  set status = 'cancelled', completed_at = now(), error_code = 'cancelled_by_user',
      error_message = 'Cancelled before the local worker claimed it.'
  where id = p_command_id and user_id = v_user and status = 'pending'
  returning * into v_row;
  if not found then raise exception 'Pending command not found' using errcode = 'P0002'; end if;
  if v_row.command_type = 'submit_trade' then
    update public.trade_intents set status = 'cancelled', closed_at = now()
    where id = v_row.intent_id and user_id = v_user and status = 'queued';
  end if;
  return v_row;
end;
$$;

-- ---------------------------------------------------------------------------
-- Scoped local-worker RPCs (call with the public anon key + worker token)
-- ---------------------------------------------------------------------------

create or replace function public.tcq_worker_list_connections(p_worker_token text)
returns setof public.mt5_connections
language plpgsql
security definer
set search_path = ''
as $$
declare v_worker uuid; v_user uuid;
begin
  select a.id, a.user_id into v_worker, v_user
  from public.worker_agents a join trade_private.worker_credentials s on s.worker_id = a.id
  where s.token_hash = extensions.digest(coalesce(p_worker_token, ''), 'sha256')
    and a.revoked_at is null;
  if v_worker is null then raise exception 'Invalid or revoked worker token' using errcode = '28000'; end if;
  update public.worker_agents set last_seen_at = now() where id = v_worker;
  return query
  select c.* from public.mt5_connections c
  where c.user_id = v_user and c.worker_id = v_worker and c.is_enabled
  order by c.created_at;
end;
$$;

create or replace function public.tcq_claim_trade_commands(
  p_worker_token text,
  p_limit integer default 10,
  p_lease_seconds integer default 90
)
returns setof public.trade_commands
language plpgsql
security definer
set search_path = ''
as $$
declare v_worker uuid; v_user uuid;
begin
  if p_limit not between 1 and 50 or p_lease_seconds not between 30 and 900 then
    raise exception 'Invalid claim limit or lease duration' using errcode = '22023';
  end if;
  select a.id, a.user_id into v_worker, v_user
  from public.worker_agents a
  join trade_private.worker_credentials s on s.worker_id = a.id
  where s.token_hash = extensions.digest(coalesce(p_worker_token, ''), 'sha256')
    and a.revoked_at is null;
  if v_worker is null then raise exception 'Invalid or revoked worker token' using errcode = '28000'; end if;

  update public.worker_agents set last_seen_at = now() where id = v_worker;
  -- Expire stale work before claiming. A live claim was allowed to *begin*
  -- before expires_at, so it must retain its lease while MT5 is executing; it
  -- becomes stale only after that lease also expires. The UPDATE fires
  -- tcq_sync_submit_command_status, so submit intents become expired and
  -- immediately release the unique active-symbol lock.
  with expired as (
    update public.trade_commands c
    set status = 'expired', completed_at = now(), error_code = 'execution_window_expired',
        error_message = 'The local worker was offline past this command execution deadline.'
    where c.user_id = v_user and c.expires_at <= now()
      and (
        c.status = 'pending'
        or (c.status = 'claimed' and c.lease_expires_at <= now())
      )
    returning c.*
  )
  insert into public.trade_events (
    user_id, connection_id, intent_id, command_id, event_type, message, payload
  )
  select user_id, connection_id, intent_id, id, 'command_expired',
         'Command expired before a local worker could safely begin it.',
         jsonb_build_object('expires_at', expires_at, 'command_type', command_type)
  from expired;

  update public.trade_commands c
  set status = 'failed', completed_at = now(), error_code = 'claim_attempts_exhausted',
      error_message = 'Worker claim lease expired too many times.'
  where c.user_id = v_user and c.status = 'claimed'
    and c.lease_expires_at <= now() and c.attempts >= c.max_attempts;

  return query
  with candidates as (
    select c.id
    from public.trade_commands c
    join public.mt5_connections m
      on m.id = c.connection_id and m.user_id = c.user_id
    where c.user_id = v_user and m.worker_id = v_worker and m.is_enabled
      and c.available_at <= now() and c.expires_at > now() and c.attempts < c.max_attempts
      and (c.status = 'pending' or (c.status = 'claimed' and c.lease_expires_at <= now()))
    order by c.priority asc, c.created_at asc
    for update of c skip locked
    limit p_limit
  )
  update public.trade_commands c
  set status = 'claimed', claimed_by = v_worker,
      claim_token = extensions.gen_random_uuid(), claimed_at = now(),
      lease_expires_at = now() + make_interval(secs => p_lease_seconds),
      attempts = c.attempts + 1, error_code = '', error_message = ''
  from candidates
  where c.id = candidates.id
  returning c.*;
end;
$$;

comment on function public.tcq_claim_trade_commands(text, integer, integer) is
  'Atomically leases commands assigned to this scoped worker. Anon API key is sufficient; the private worker token supplies authorization.';

create or replace function public.tcq_extend_command_lease(
  p_worker_token text,
  p_command_id uuid,
  p_claim_token uuid,
  p_lease_seconds integer default 90
)
returns public.trade_commands
language plpgsql
security definer
set search_path = ''
as $$
declare v_worker uuid; v_user uuid; v_row public.trade_commands;
begin
  if p_lease_seconds not between 30 and 900 then
    raise exception 'Lease duration must be between 30 and 900 seconds' using errcode = '22023';
  end if;
  select a.id, a.user_id into v_worker, v_user
  from public.worker_agents a join trade_private.worker_credentials s on s.worker_id = a.id
  where s.token_hash = extensions.digest(coalesce(p_worker_token, ''), 'sha256')
    and a.revoked_at is null;
  if v_worker is null then raise exception 'Invalid or revoked worker token' using errcode = '28000'; end if;

  update public.trade_commands
  set lease_expires_at = now() + make_interval(secs => p_lease_seconds)
  where id = p_command_id and user_id = v_user and status = 'claimed'
    and claimed_by = v_worker and claim_token = p_claim_token
    and lease_expires_at > now()
  returning * into v_row;
  if not found then
    raise exception 'Active command claim not found; it may have expired or been reclaimed' using errcode = 'P0002';
  end if;
  return v_row;
end;
$$;

create or replace function public.tcq_complete_trade_command(
  p_worker_token text,
  p_command_id uuid,
  p_claim_token uuid,
  p_outcome text,
  p_result jsonb default '{}'::jsonb,
  p_intent_status text default null,
  p_error_code text default '',
  p_error_message text default ''
)
returns public.trade_commands
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_worker uuid;
  v_user uuid;
  v_row public.trade_commands;
  v_status text := lower(p_outcome);
  v_intent_status text;
begin
  select a.id, a.user_id into v_worker, v_user
  from public.worker_agents a join trade_private.worker_credentials s on s.worker_id = a.id
  where s.token_hash = extensions.digest(coalesce(p_worker_token, ''), 'sha256')
    and a.revoked_at is null;
  if v_worker is null then raise exception 'Invalid or revoked worker token' using errcode = '28000'; end if;
  if v_status not in ('succeeded', 'rejected', 'failed') then
    raise exception 'Outcome must be succeeded, rejected, or failed' using errcode = '22023';
  end if;
  if jsonb_typeof(coalesce(p_result, '{}'::jsonb)) <> 'object' then
    raise exception 'result must be a JSON object' using errcode = '22023';
  end if;
  if p_intent_status is not null and p_intent_status not in (
    'claimed', 'validating', 'rejected', 'submitted', 'open', 'scaling', 'closed', 'failed', 'cancelled'
  ) then raise exception 'Invalid intent status' using errcode = '22023'; end if;

  update public.trade_commands
  set status = v_status, result = coalesce(p_result, '{}'::jsonb),
      error_code = left(coalesce(p_error_code, ''), 120),
      error_message = left(coalesce(p_error_message, ''), 2000),
      completed_at = now()
  where id = p_command_id and user_id = v_user and status = 'claimed'
    and claimed_by = v_worker and claim_token = p_claim_token
    and lease_expires_at > now()
  returning * into v_row;
  if not found then
    raise exception 'Active command claim not found; it may have expired or been reclaimed' using errcode = 'P0002';
  end if;

  if v_row.intent_id is not null then
    -- Derive one effective lifecycle state and use it for both the status and
    -- its timestamps. Submit outcomes must agree with their requested intent
    -- state. Control-command failures preserve the existing trade lifecycle;
    -- only a successful close/sync may publish an authoritative live/terminal
    -- state discovered from MT5.
    if v_row.command_type = 'submit_trade' then
      if v_status = 'succeeded' then
        if p_intent_status is null then
          v_intent_status := 'submitted';
        elsif p_intent_status in ('submitted', 'open', 'scaling', 'closed') then
          v_intent_status := p_intent_status;
        else
          raise exception 'A succeeded submit command requires submitted, open, scaling, or closed intent status'
            using errcode = '22023';
        end if;
      elsif v_status = 'rejected' then
        if p_intent_status is not null and p_intent_status <> 'rejected' then
          raise exception 'A rejected submit command cannot publish a different intent status'
            using errcode = '22023';
        end if;
        v_intent_status := 'rejected';
      else
        if p_intent_status is not null and p_intent_status <> 'failed' then
          raise exception 'A failed submit command cannot publish a different intent status'
            using errcode = '22023';
        end if;
        v_intent_status := 'failed';
      end if;
    elsif v_status = 'succeeded'
          and p_intent_status in ('open', 'scaling', 'closed', 'failed') then
      v_intent_status := p_intent_status;
    else
      v_intent_status := null;
    end if;

    update public.trade_intents
    set status = coalesce(v_intent_status, status),
        approved_plan = coalesce(p_result -> 'plan', approved_plan),
        rules_report = coalesce(p_result -> 'rules', rules_report),
        broker_order_ticket = coalesce(nullif(p_result ->> 'order_ticket', '')::bigint, broker_order_ticket),
        broker_position_ticket = coalesce(nullif(p_result ->> 'position_ticket', '')::bigint, broker_position_ticket),
        last_error = case when v_status in ('failed', 'rejected') then left(coalesce(p_error_message, ''), 2000) else '' end,
        submitted_at = case when coalesce(v_intent_status, '') in ('submitted', 'open', 'scaling', 'closed') then coalesce(submitted_at, now()) else submitted_at end,
        opened_at = case when coalesce(v_intent_status, '') in ('open', 'scaling', 'closed') then coalesce(opened_at, now()) else opened_at end,
        closed_at = case when coalesce(v_intent_status, '') = 'closed' then coalesce(closed_at, now()) else closed_at end
    where id = v_row.intent_id and user_id = v_user;
  end if;

  insert into public.trade_events (
    user_id, connection_id, intent_id, command_id, event_type, message, payload
  ) values (
    v_user, v_row.connection_id, v_row.intent_id, v_row.id,
    'command_' || v_status,
    case when v_status = 'succeeded' then 'Local worker completed ' || v_row.command_type || '.'
         else left(coalesce(p_error_message, initcap(v_status)), 2000) end,
    coalesce(p_result, '{}'::jsonb)
  );
  return v_row;
end;
$$;

create or replace function public.tcq_worker_heartbeat(
  p_worker_token text,
  p_connection_id uuid,
  p_snapshot jsonb default '{}'::jsonb
)
returns public.mt5_connections
language plpgsql
security definer
set search_path = ''
as $$
declare v_worker uuid; v_user uuid; v_row public.mt5_connections;
begin
  if jsonb_typeof(coalesce(p_snapshot, '{}'::jsonb)) <> 'object' then
    raise exception 'snapshot must be a JSON object' using errcode = '22023';
  end if;
  select a.id, a.user_id into v_worker, v_user
  from public.worker_agents a join trade_private.worker_credentials s on s.worker_id = a.id
  where s.token_hash = extensions.digest(coalesce(p_worker_token, ''), 'sha256')
    and a.revoked_at is null;
  if v_worker is null then raise exception 'Invalid or revoked worker token' using errcode = '28000'; end if;

  update public.worker_agents set last_seen_at = now() where id = v_worker;
  update public.mt5_connections
  set mt5_login = coalesce(nullif(p_snapshot ->> 'login', '')::bigint, mt5_login),
      server = left(coalesce(p_snapshot ->> 'server', server), 160),
      company = left(coalesce(p_snapshot ->> 'company', company), 160),
      account_name = left(coalesce(p_snapshot ->> 'account_name', account_name), 160),
      currency = left(coalesce(p_snapshot ->> 'currency', currency), 12),
      leverage = coalesce(nullif(p_snapshot ->> 'leverage', '')::integer, leverage),
      status = case when coalesce(p_snapshot ->> 'status', 'online') in ('online', 'offline', 'error')
                    then coalesce(p_snapshot ->> 'status', 'online') else 'online' end,
      trade_allowed = coalesce((p_snapshot ->> 'trade_allowed')::boolean, trade_allowed),
      expert_allowed = coalesce((p_snapshot ->> 'expert_allowed')::boolean, expert_allowed),
      last_balance = coalesce(nullif(p_snapshot ->> 'balance', '')::numeric, last_balance),
      last_equity = coalesce(nullif(p_snapshot ->> 'equity', '')::numeric, last_equity),
      last_margin = coalesce(nullif(p_snapshot ->> 'margin', '')::numeric, last_margin),
      last_free_margin = coalesce(nullif(p_snapshot ->> 'free_margin', '')::numeric, last_free_margin),
      last_error = left(coalesce(p_snapshot ->> 'error', ''), 2000),
      last_seen_at = now()
  where id = p_connection_id and user_id = v_user and worker_id = v_worker and is_enabled
  returning * into v_row;
  if not found then raise exception 'Enabled connection not assigned to this worker' using errcode = 'P0002'; end if;
  return v_row;
end;
$$;

create or replace function public.tcq_worker_get_context(p_worker_token text, p_connection_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare v_worker uuid; v_user uuid; v_result jsonb;
begin
  select a.id, a.user_id into v_worker, v_user
  from public.worker_agents a join trade_private.worker_credentials s on s.worker_id = a.id
  where s.token_hash = extensions.digest(coalesce(p_worker_token, ''), 'sha256')
    and a.revoked_at is null;
  if v_worker is null then raise exception 'Invalid or revoked worker token' using errcode = '28000'; end if;
  select jsonb_build_object('connection', to_jsonb(c), 'rules', to_jsonb(r)) into v_result
  from public.mt5_connections c
  join public.user_trading_rules r on r.user_id = c.user_id
  where c.id = p_connection_id and c.user_id = v_user and c.worker_id = v_worker and c.is_enabled;
  if v_result is null then raise exception 'Enabled connection not assigned to this worker' using errcode = 'P0002'; end if;
  return v_result;
end;
$$;

create or replace function public.tcq_worker_append_event(
  p_worker_token text,
  p_connection_id uuid,
  p_intent_id uuid,
  p_event_type text,
  p_message text default '',
  p_payload jsonb default '{}'::jsonb
)
returns public.trade_events
language plpgsql
security definer
set search_path = ''
as $$
declare v_worker uuid; v_user uuid; v_row public.trade_events;
begin
  select a.id, a.user_id into v_worker, v_user
  from public.worker_agents a join trade_private.worker_credentials s on s.worker_id = a.id
  where s.token_hash = extensions.digest(coalesce(p_worker_token, ''), 'sha256')
    and a.revoked_at is null;
  if v_worker is null then raise exception 'Invalid or revoked worker token' using errcode = '28000'; end if;
  if char_length(coalesce(p_event_type, '')) not between 1 and 80
     or jsonb_typeof(coalesce(p_payload, '{}'::jsonb)) <> 'object' then
    raise exception 'Invalid event type or payload' using errcode = '22023';
  end if;
  if not exists (
    select 1 from public.mt5_connections
    where id = p_connection_id and user_id = v_user and worker_id = v_worker
  ) then raise exception 'Connection not assigned to this worker' using errcode = 'P0002'; end if;
  if p_intent_id is not null and not exists (
    select 1 from public.trade_intents
    where id = p_intent_id and user_id = v_user and connection_id = p_connection_id
  ) then raise exception 'Trade intent not found for this connection' using errcode = 'P0002'; end if;

  insert into public.trade_events (user_id, connection_id, intent_id, event_type, message, payload)
  values (v_user, p_connection_id, p_intent_id, lower(p_event_type),
          left(coalesce(p_message, ''), 2000), coalesce(p_payload, '{}'::jsonb))
  returning * into v_row;
  return v_row;
end;
$$;

-- Ongoing TP/SL management happens after the submit command has completed, so
-- it needs a separate scoped state-update RPC. This function is intentionally
-- not a general row update: only lifecycle state, broker tickets, approved
-- calculation/rule snapshots, and an accompanying audit event may be changed.
create or replace function public.tcq_worker_update_trade_state(
  p_worker_token text,
  p_intent_id uuid,
  p_status text,
  p_event_type text,
  p_message text default '',
  p_payload jsonb default '{}'::jsonb
)
returns public.trade_intents
language plpgsql
security definer
set search_path = ''
as $$
declare v_worker uuid; v_user uuid; v_row public.trade_intents;
begin
  select a.id, a.user_id into v_worker, v_user
  from public.worker_agents a join trade_private.worker_credentials s on s.worker_id = a.id
  where s.token_hash = extensions.digest(coalesce(p_worker_token, ''), 'sha256')
    and a.revoked_at is null;
  if v_worker is null then raise exception 'Invalid or revoked worker token' using errcode = '28000'; end if;
  if p_status not in ('validating', 'rejected', 'submitted', 'open', 'scaling', 'closed', 'failed') then
    raise exception 'Invalid worker-managed trade status' using errcode = '22023';
  end if;
  if char_length(coalesce(p_event_type, '')) not between 1 and 80
     or jsonb_typeof(coalesce(p_payload, '{}'::jsonb)) <> 'object' then
    raise exception 'Invalid event type or payload' using errcode = '22023';
  end if;

  update public.trade_intents i
  set status = p_status,
      approved_plan = coalesce(p_payload -> 'plan', approved_plan),
      rules_report = coalesce(p_payload -> 'rules', rules_report),
      broker_order_ticket = coalesce(nullif(p_payload ->> 'order_ticket', '')::bigint, broker_order_ticket),
      broker_position_ticket = coalesce(nullif(p_payload ->> 'position_ticket', '')::bigint, broker_position_ticket),
      last_error = case when p_status in ('rejected', 'failed')
                        then left(coalesce(p_message, ''), 2000) else '' end,
      submitted_at = case when p_status in ('submitted', 'open', 'scaling', 'closed')
                          then coalesce(submitted_at, now()) else submitted_at end,
      opened_at = case when p_status in ('open', 'scaling', 'closed')
                       then coalesce(opened_at, now()) else opened_at end,
      closed_at = case when p_status = 'closed' then coalesce(closed_at, now()) else closed_at end
  from public.mt5_connections c
  where i.id = p_intent_id and i.user_id = v_user
    and c.id = i.connection_id and c.user_id = i.user_id
    and c.worker_id = v_worker and c.is_enabled
  returning i.* into v_row;
  if not found then raise exception 'Trade intent is not assigned to this worker' using errcode = 'P0002'; end if;

  insert into public.trade_events (user_id, connection_id, intent_id, event_type, message, payload)
  values (v_user, v_row.connection_id, v_row.id, lower(p_event_type),
          left(coalesce(p_message, ''), 2000), coalesce(p_payload, '{}'::jsonb));
  return v_row;
end;
$$;

-- Function execution is denied by default and granted only to the role that
-- needs each surface. Worker functions allow anon because authorization comes
-- from the high-entropy scoped token; the anon key alone grants nothing.
revoke all on function public.tcq_create_worker(text) from public;
revoke all on function public.tcq_rotate_worker_token(uuid) from public;
revoke all on function public.tcq_revoke_worker(uuid) from public;
revoke all on function public.tcq_enqueue_trade_intent(uuid, uuid, text, text, text, numeric, numeric, numeric, numeric, text, jsonb, timestamptz) from public;
revoke all on function public.tcq_enqueue_trade_command(uuid, uuid, uuid, text, jsonb, timestamptz) from public;
revoke all on function public.tcq_cancel_pending_command(uuid) from public;
revoke all on function public.tcq_worker_list_connections(text) from public;
revoke all on function public.tcq_claim_trade_commands(text, integer, integer) from public;
revoke all on function public.tcq_extend_command_lease(text, uuid, uuid, integer) from public;
revoke all on function public.tcq_complete_trade_command(text, uuid, uuid, text, jsonb, text, text, text) from public;
revoke all on function public.tcq_worker_heartbeat(text, uuid, jsonb) from public;
revoke all on function public.tcq_worker_get_context(text, uuid) from public;
revoke all on function public.tcq_worker_append_event(text, uuid, uuid, text, text, jsonb) from public;
revoke all on function public.tcq_worker_update_trade_state(text, uuid, text, text, text, jsonb) from public;

grant execute on function public.tcq_create_worker(text) to authenticated;
grant execute on function public.tcq_rotate_worker_token(uuid) to authenticated;
grant execute on function public.tcq_revoke_worker(uuid) to authenticated;
grant execute on function public.tcq_enqueue_trade_intent(uuid, uuid, text, text, text, numeric, numeric, numeric, numeric, text, jsonb, timestamptz) to authenticated;
grant execute on function public.tcq_enqueue_trade_command(uuid, uuid, uuid, text, jsonb, timestamptz) to authenticated;
grant execute on function public.tcq_cancel_pending_command(uuid) to authenticated;
grant execute on function public.tcq_worker_list_connections(text) to anon, authenticated;
grant execute on function public.tcq_claim_trade_commands(text, integer, integer) to anon, authenticated;
grant execute on function public.tcq_extend_command_lease(text, uuid, uuid, integer) to anon, authenticated;
grant execute on function public.tcq_complete_trade_command(text, uuid, uuid, text, jsonb, text, text, text) to anon, authenticated;
grant execute on function public.tcq_worker_heartbeat(text, uuid, jsonb) to anon, authenticated;
grant execute on function public.tcq_worker_get_context(text, uuid) to anon, authenticated;
grant execute on function public.tcq_worker_append_event(text, uuid, uuid, text, text, jsonb) to anon, authenticated;
grant execute on function public.tcq_worker_update_trade_state(text, uuid, text, text, text, jsonb) to anon, authenticated;

-- Verification summary shown by SQL Editor after a successful run.
select
  (select count(*) from auth.users) as auth_users,
  (select count(*) from public.user_trading_rules) as trading_rule_rows,
  (select count(*) from public.worker_agents) as worker_agents,
  (select count(*) from public.mt5_connections) as mt5_connections,
  (select count(*) from public.trade_intents) as trade_intents,
  (select count(*) from public.trade_commands) as queued_commands;
