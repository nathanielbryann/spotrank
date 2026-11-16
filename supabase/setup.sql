-- Spotrank players and results (Supabase).
-- Paste all of this into Supabase: SQL Editor > New query > Run. Safe to run again.
--
-- How it is protected: the web page uses the publishable key, which acts as the
-- "anon" role. That role cannot read or write the tables at all (row level security
-- is on and there are no policies). It can only call the functions below, which
-- check every input and never return a player's secret code.

create table if not exists public.players (
  id         uuid primary key default gen_random_uuid(),
  secret     uuid not null unique default gen_random_uuid(),   -- the player's code; only the player knows it
  name       text not null,
  created_at timestamptz not null default now()
);
create unique index if not exists players_name_key on public.players (lower(name));

create table if not exists public.results (
  player_id    uuid not null references public.players (id) on delete cascade,
  puzzle_no    int not null,                 -- daily puzzle number (1 = 15 Nov 2026)
  price_date   date not null,                -- delivery day whose prices were ranked
  solved       boolean not null,
  tries        smallint not null,            -- attempts used (1-4)
  first_greens smallint not null,            -- zones in the right place on the first attempt (0-5)
  answer       text[] not null,              -- zones cheapest to dearest
  attempts     jsonb not null,               -- every attempt, as arrays of zone codes
  created_at   timestamptz not null default now(),
  primary key (player_id, puzzle_no)
);
create index if not exists results_puzzle on public.results (puzzle_no);

alter table public.players enable row level security;
alter table public.results enable row level security;
revoke all on public.players, public.results from anon, authenticated;

-- puzzle number <-> the day it is played
create or replace function public.spotrank_play_date(p_puzzle int) returns date
language sql immutable as $$ select date '2026-11-15' + (p_puzzle - 1) $$;

-- Register a display name. Returns the player's id and secret code.
create or replace function public.register_player(p_name text)
returns table (player_id uuid, secret uuid, name text)
language plpgsql security definer set search_path = public as $$
declare n text := regexp_replace(btrim(coalesce(p_name, '')), '\s+', ' ', 'g');
begin
  if char_length(n) < 2 or char_length(n) > 20 then
    raise exception 'name_length' using hint = 'Use 2 to 20 characters.';
  end if;
  if n ~ '[<>"\\/{}`]' or n ~ '[[:cntrl:]]' then
    raise exception 'name_chars' using hint = 'Use letters, numbers, spaces and . _ -';
  end if;
  if exists (select 1 from players p where lower(p.name) = lower(n)) then
    raise exception 'name_taken' using hint = 'That name is taken.';
  end if;
  return query insert into players (name) values (n) returning players.id, players.secret, players.name;
end $$;

-- Look up a player by secret code (for using the same player on another device).
create or replace function public.whoami(p_secret uuid)
returns table (player_id uuid, name text)
language sql stable security definer set search_path = public as $$
  select p.id, p.name from players p where p.secret = p_secret
$$;

-- Rounds in progress: each attempt is saved as it is made, so a half-played round continues
-- on another device. Attempts can only be added, never changed or taken back.
create table if not exists public.progress (
  player_id  uuid not null references public.players (id) on delete cascade,
  puzzle_no  int not null,
  price_date date not null,
  answer     text[] not null,
  attempts   jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (player_id, puzzle_no)
);
alter table public.progress enable row level security;
revoke all on public.progress from anon, authenticated;

create or replace function public.spotrank_valid_attempts(p_answer text[], p_attempts jsonb) returns boolean
language plpgsql immutable as $$
declare n int; i int;
begin
  if p_answer is null or array_length(p_answer, 1) <> 5 or p_attempts is null or jsonb_typeof(p_attempts) <> 'array' then return false; end if;
  n := jsonb_array_length(p_attempts);
  if n < 1 or n > 4 then return false; end if;
  for i in 0 .. n - 1 loop
    if jsonb_typeof(p_attempts -> i) <> 'array' or jsonb_array_length(p_attempts -> i) <> 5 then return false; end if;
  end loop;
  return true;
end $$;

-- true when the attempts in a start with every attempt in b (a has b's attempts first)
create or replace function public.spotrank_extends(a jsonb, b jsonb) returns boolean
language sql immutable as $$
  select jsonb_array_length(a) >= jsonb_array_length(b)
     and coalesce((select bool_and(a -> i = b -> i) from generate_series(0, jsonb_array_length(b) - 1) i), true)
$$;

-- Save the attempts of today's round so far. Returns the attempts now stored: if another device
-- got further, those are kept and returned, so the page can catch up.
create or replace function public.save_attempt(p_secret uuid, p_puzzle int, p_price_date date, p_answer text[], p_attempts jsonb)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare pid uuid; cur int := (current_date - date '2026-11-15') + 1; old jsonb; fin jsonb;
begin
  select id into pid from players where secret = p_secret;
  if pid is null then raise exception 'unknown_player'; end if;
  if p_puzzle is null or p_puzzle < cur - 1 or p_puzzle > cur + 1 then raise exception 'not_todays_puzzle'; end if;
  if not spotrank_valid_attempts(p_answer, p_attempts) then raise exception 'bad_attempts'; end if;
  select attempts into fin from results where player_id = pid and puzzle_no = p_puzzle;
  if fin is not null then return fin; end if;
  select attempts into old from progress where player_id = pid and puzzle_no = p_puzzle;
  if old is null then
    insert into progress (player_id, puzzle_no, price_date, answer, attempts) values (pid, p_puzzle, p_price_date, p_answer, p_attempts);
    return p_attempts;
  end if;
  if jsonb_array_length(p_attempts) > jsonb_array_length(old) and spotrank_extends(p_attempts, old) then
    update progress set attempts = p_attempts, updated_at = now() where player_id = pid and puzzle_no = p_puzzle;
    return p_attempts;
  end if;
  return old;
end $$;

-- A player's rounds still in progress (today's and yesterday's puzzle numbers).
create or replace function public.my_progress(p_secret uuid)
returns table (puzzle_no int, price_date date, answer text[], attempts jsonb)
language sql stable security definer set search_path = public as $$
  select g.puzzle_no, g.price_date, g.answer, g.attempts
  from progress g join players p on p.id = g.player_id
  where p.secret = p_secret
    and g.puzzle_no >= (current_date - date '2026-11-15')
    and not exists (select 1 from results r where r.player_id = g.player_id and r.puzzle_no = g.puzzle_no)
  order by g.puzzle_no
$$;

-- Save a finished daily round. Only the first finish of each puzzle counts, and it must
-- continue the attempts already saved for that round (no fresh start on another device).
create or replace function public.submit_result(p_secret uuid, p_puzzle int, p_price_date date, p_answer text[], p_attempts jsonb)
returns text
language plpgsql security definer set search_path = public as $$
declare
  pid uuid;
  cur int := (current_date - date '2026-11-15') + 1;
  n int;
  last_try jsonb;
  first_try jsonb;
  old jsonb;
  greens int := 0;
  i int;
  won boolean;
begin
  select id into pid from players where secret = p_secret;
  if pid is null then raise exception 'unknown_player'; end if;
  if p_puzzle is null or p_puzzle < cur - 1 or p_puzzle > cur + 1 then raise exception 'not_todays_puzzle'; end if;
  if not spotrank_valid_attempts(p_answer, p_attempts) then raise exception 'bad_attempts'; end if;
  n := jsonb_array_length(p_attempts);
  if exists (select 1 from results where player_id = pid and puzzle_no = p_puzzle) then return 'already_saved'; end if;
  select attempts into old from progress where player_id = pid and puzzle_no = p_puzzle;
  if old is not null and not spotrank_extends(p_attempts, old) then
    raise exception 'started_elsewhere' using hint = 'This round was continued on another device.';
  end if;
  first_try := p_attempts -> 0;
  last_try := p_attempts -> (n - 1);
  for i in 1 .. 5 loop
    if first_try ->> (i - 1) = p_answer[i] then greens := greens + 1; end if;
  end loop;
  won := (select bool_and(last_try ->> (k - 1) = p_answer[k]) from generate_series(1, 5) k);
  if not won and n < 4 then raise exception 'round_not_finished'; end if;
  insert into results (player_id, puzzle_no, price_date, solved, tries, first_greens, answer, attempts)
  values (pid, p_puzzle, p_price_date, won, n, greens, p_answer, p_attempts)
  on conflict (player_id, puzzle_no) do nothing;
  delete from progress where player_id = pid and puzzle_no = p_puzzle;
  return case when found then 'saved' else 'already_saved' end;
end $$;

-- Everyone's result for one daily puzzle (names and tries only, no answers).
create or replace function public.day_results(p_puzzle int)
returns table (name text, solved boolean, tries smallint, first_greens smallint, at timestamptz)
language sql stable security definer set search_path = public as $$
  select p.name, r.solved, r.tries, r.first_greens, r.created_at
  from results r join players p on p.id = r.player_id
  where r.puzzle_no = p_puzzle
  order by r.solved desc, r.tries, r.first_greens desc, r.created_at
$$;

-- Leaderboard for the daily puzzles played between two dates (inclusive).
-- Points: solved in 1 try = 4, 2 = 3, 3 = 2, 4 = 1, missed = 0.
create or replace function public.leaderboard(p_from date, p_to date)
returns table (name text, played int, wins int, points int, avg_tries numeric, first_greens numeric, streak int)
language sql stable security definer set search_path = public as $$
  with cur as (select (current_date - date '2026-11-15') + 1 as no),
  r as (
    select * from results
    where spotrank_play_date(puzzle_no) between p_from and p_to
  ),
  runs as (   -- current winning streak, over all time, ending today or yesterday
    select player_id, count(*) as len, max(puzzle_no) as last_no
    from (select player_id, puzzle_no, puzzle_no - row_number() over (partition by player_id order by puzzle_no) as grp
          from results where solved) s
    group by player_id, grp
  )
  select p.name,
         count(*)::int,
         count(*) filter (where r.solved)::int,
         sum(case when r.solved then 5 - r.tries else 0 end)::int,
         round(avg(r.tries) filter (where r.solved), 2),
         round(avg(r.first_greens), 2),
         coalesce((select max(len) from runs, cur where runs.player_id = p.id and runs.last_no >= cur.no - 1), 0)::int
  from r join players p on p.id = r.player_id
  group by p.id, p.name
  order by 4 desc, 3 desc, 5 nulls last, 1
$$;

-- First attempts of everyone (anonymous), for "you vs everyone" by region.
create or replace function public.first_attempts(p_from date, p_to date)
returns table (answer text[], first_try jsonb)
language sql stable security definer set search_path = public as $$
  select r.answer, r.attempts -> 0 from results r
  where spotrank_play_date(r.puzzle_no) between p_from and p_to
$$;

-- A player's own saved rounds (by secret code), so another device can show them.
create or replace function public.my_results(p_secret uuid)
returns table (puzzle_no int, price_date date, solved boolean, tries smallint, first_greens smallint, answer text[], attempts jsonb, at timestamptz)
language sql stable security definer set search_path = public as $$
  select r.puzzle_no, r.price_date, r.solved, r.tries, r.first_greens, r.answer, r.attempts, r.created_at
  from results r join players p on p.id = r.player_id
  where p.secret = p_secret
  order by r.puzzle_no
$$;

-- Tiny call the nightly GitHub job makes, so the free project is never idle for a week.
create or replace function public.ping() returns int
language sql stable security definer set search_path = public as $$ select count(*)::int from players $$;

revoke all on function public.register_player(text), public.whoami(uuid),
  public.submit_result(uuid, int, date, text[], jsonb), public.day_results(int),
  public.leaderboard(date, date), public.first_attempts(date, date), public.ping(), public.my_results(uuid) from public;
grant execute on function public.register_player(text), public.whoami(uuid),
  public.submit_result(uuid, int, date, text[], jsonb), public.day_results(int),
  public.leaderboard(date, date), public.first_attempts(date, date), public.ping(), public.my_results(uuid) to anon, authenticated;
revoke all on function public.spotrank_valid_attempts(text[], jsonb), public.spotrank_extends(jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.save_attempt(uuid, int, date, text[], jsonb), public.my_progress(uuid), public.my_results(uuid),
  public.submit_result(uuid, int, date, text[], jsonb) from public;
grant execute on function public.save_attempt(uuid, int, date, text[], jsonb), public.my_progress(uuid), public.my_results(uuid),
  public.submit_result(uuid, int, date, text[], jsonb) to anon, authenticated;
