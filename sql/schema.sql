-- =============================================================
-- Prono70 / ProLab — schéma Supabase
-- À exécuter une fois dans : Supabase → SQL Editor → New query
-- =============================================================

create table if not exists public.profiles (
  id uuid references auth.users(id) on delete cascade primary key,
  email text,
  prolab_active boolean not null default false,
  prolab_activated_at timestamptz,
  created_at timestamptz not null default now()
);

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email);
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

alter table public.profiles enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles
  for select using (auth.uid() = id);

create table if not exists public.live_matches (
  id text primary key,
  league text,
  home text,
  away text,
  home_logo text,
  away_logo text,
  minute int,
  status text,
  home_goals int,
  away_goals int,
  prob_home numeric,
  prob_draw numeric,
  prob_away numeric,
  pick_label text,
  confidence numeric,
  note text,
  updated_at timestamptz not null default now()
);

alter table public.live_matches enable row level security;

drop policy if exists "live_matches_select_active_prolab" on public.live_matches;
create policy "live_matches_select_active_prolab" on public.live_matches
  for select using (
    exists (
      select 1 from public.profiles
      where profiles.id = auth.uid()
        and profiles.prolab_active = true
    )
  );
