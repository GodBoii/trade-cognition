-- Trade Cognition: complete Supabase-side database setup
--
-- Copy this entire file into Supabase Dashboard -> SQL Editor and click Run.
-- It is safe to run more than once.
--
-- This file installs the Auth profile mirror. After it succeeds, run
-- 002_async_trade_queue.sql to install the asynchronous browser/worker control
-- plane. MT5 passwords remain local and are never added to either migration.

begin;

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  full_name text not null default '',
  phone text not null default '',
  avatar_url text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint profiles_phone_length check (char_length(phone) <= 24),
  constraint profiles_full_name_length check (char_length(full_name) <= 120)
);

comment on table public.profiles is
  'User-facing profile data paired one-to-one with Supabase Auth users.';
comment on column public.profiles.phone is
  'Signup contact number supplied by the user; it is not treated as verified.';

alter table public.profiles enable row level security;

revoke all on table public.profiles from anon;
grant usage on schema public to authenticated;
grant select on table public.profiles to authenticated;
grant update (full_name, phone, avatar_url) on table public.profiles to authenticated;
grant all on table public.profiles to service_role;

drop policy if exists "Users can read their own profile" on public.profiles;
create policy "Users can read their own profile"
on public.profiles
for select
to authenticated
using ((select auth.uid()) is not null and (select auth.uid()) = id);

drop policy if exists "Users can update their own profile" on public.profiles;
create policy "Users can update their own profile"
on public.profiles
for update
to authenticated
using ((select auth.uid()) is not null and (select auth.uid()) = id)
with check ((select auth.uid()) is not null and (select auth.uid()) = id);

create or replace function public.set_profile_updated_at()
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

drop trigger if exists set_profiles_updated_at on public.profiles;
create trigger set_profiles_updated_at
before update on public.profiles
for each row execute procedure public.set_profile_updated_at();

create or replace function public.sync_auth_user_profile()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email, full_name, phone, avatar_url)
  values (
    new.id,
    coalesce(new.email, ''),
    coalesce(
      nullif(new.raw_user_meta_data ->> 'full_name', ''),
      nullif(new.raw_user_meta_data ->> 'name', ''),
      nullif(new.raw_user_meta_data ->> 'user_name', ''),
      ''
    ),
    coalesce(new.raw_user_meta_data ->> 'phone', ''),
    coalesce(
      nullif(new.raw_user_meta_data ->> 'avatar_url', ''),
      nullif(new.raw_user_meta_data ->> 'picture', ''),
      ''
    )
  )
  on conflict (id) do update
  set email = excluded.email,
      full_name = excluded.full_name,
      phone = excluded.phone,
      avatar_url = excluded.avatar_url;
  return new;
end;
$$;

revoke all on function public.sync_auth_user_profile() from public;
revoke all on function public.set_profile_updated_at() from public;

drop trigger if exists sync_auth_user_profile on auth.users;
create trigger sync_auth_user_profile
after insert or update of email, raw_user_meta_data on auth.users
for each row execute procedure public.sync_auth_user_profile();

-- Backfill users who signed up before this file was run, including Google users.
insert into public.profiles (id, email, full_name, phone, avatar_url)
select
  users.id,
  coalesce(users.email, ''),
  coalesce(
    nullif(users.raw_user_meta_data ->> 'full_name', ''),
    nullif(users.raw_user_meta_data ->> 'name', ''),
    nullif(users.raw_user_meta_data ->> 'user_name', ''),
    ''
  ),
  coalesce(users.raw_user_meta_data ->> 'phone', ''),
  coalesce(
    nullif(users.raw_user_meta_data ->> 'avatar_url', ''),
    nullif(users.raw_user_meta_data ->> 'picture', ''),
    ''
  )
from auth.users as users
on conflict (id) do update
set email = excluded.email,
    full_name = excluded.full_name,
    phone = excluded.phone,
    avatar_url = excluded.avatar_url;

commit;

-- Verification output. These numbers should match after the backfill.
select
  (select count(*) from auth.users) as auth_users,
  (select count(*) from public.profiles) as public_profiles,
  (
    select count(*)
    from auth.users as users
    left join public.profiles as profiles on profiles.id = users.id
    where profiles.id is null
  ) as users_missing_profiles;
