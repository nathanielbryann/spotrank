-- Spotrank Forecast challenge. Paste all of this into Supabase: SQL Editor > New query > Run.
-- Safe to run again. It adds three tables and a few functions; nothing that exists is changed.
--
-- Players rank tomorrow's daily baseload prices for the five zones of today's daily puzzle, once,
-- before 11:30 Brussels time on the day before delivery (the SDAC auction closes at 12:00).
--
-- How it is protected:
--  * The deadline is checked on the database's own clock, so a wrong clock on a phone changes nothing.
--  * Forecasts can't be read by anyone (no table access for the web page). The functions only return
--    names, times and points, and points only exist once the auction is over.
--  * Scores come from day_prices. Only the nightly GitHub job can write it, with the Supabase secret key
--    kept as a GitHub secret. The web page's publishable key can't write it.

create table if not exists public.day_prices (
  price_date date primary key,
  prices     jsonb not null,               -- {"FI": 89.45, "SE3": 149.77, ...}: daily baseload, €/MWh
  updated_at timestamptz not null default now()
);

create table if not exists public.forecast_days (
  delivery_date date primary key,
  zones         text[] not null,           -- the day's five zones, in alphabetical order
  created_at    timestamptz not null default now()
);

create table if not exists public.forecasts (
  player_id     uuid not null references public.players (id) on delete cascade,
  delivery_date date not null,
  ranking       text[] not null,           -- the player's forecast, cheapest to dearest
  submitted_at  timestamptz not null default now(),
  primary key (player_id, delivery_date)
);
create index if not exists forecasts_day on public.forecasts (delivery_date);

alter table public.day_prices enable row level security;
alter table public.forecast_days enable row level security;
alter table public.forecasts enable row level security;
revoke all on public.day_prices, public.forecast_days, public.forecasts from anon, authenticated;
grant select, insert, update on public.day_prices to service_role;   -- the nightly job's secret key

-- The clock every check uses.
create or replace function public.spotrank_now() returns timestamptz
language sql stable as $$ select now() $$;

-- 'early' before midnight Brussels time on the day before delivery, 'open' until 11:30, then 'closed'.
create or replace function public.spotrank_fc_state(p_delivery date, p_at timestamptz) returns text
language sql stable as $$
  select case
    when p_at < ((p_delivery - 1)::timestamp at time zone 'Europe/Brussels') then 'early'
    when p_at < (((p_delivery - 1) + time '11:30') at time zone 'Europe/Brussels') then 'open'
    else 'closed' end
$$;

-- Zones that can be in the game.
create or replace function public.spotrank_game_zones() returns text[]
language sql immutable as $$
  select array['NO1','NO2','NO3','NO4','NO5','SE1','SE2','SE3','SE4','FI','DK1','DK2','EE','LV','LT','PL',
               'DE-LU','NL','BE','FR','AT','CZ','SK','HU','SI','HR','RO','BG','GR','IT','ES','PT','CH']
$$;

create or replace function public.spotrank_sorted(a text[]) returns text[]
language sql immutable as $$ select array(select x from unnest(a) x order by x collate "C") $$;

-- Points for a forecast (0-5): a zone is in the right place when its price is within €1 of the
-- price that belongs in that place, so zones less than €1 apart count either way round.
-- Null while any of the five prices is missing.
create or replace function public.spotrank_fc_points(p_ranking text[], p_prices jsonb) returns int
language sql immutable as $$
  with p as (select i, (p_prices ->> z)::numeric as v from unnest(p_ranking) with ordinality u(z, i)),
       s as (select row_number() over (order by v) as i, v from p)
  select case
    when p_prices is null or coalesce(array_length(p_ranking, 1), 0) <> 5 or (select count(v) from p) <> 5 then null
    else (select count(*) from p join s on s.i = p.i where abs(p.v - s.v) <= 1)::int end
$$;

-- The naive forecast: the five zones in the order of their prices the day before delivery
-- (ties alphabetical). Null while any of those prices is missing.
create or replace function public.spotrank_fc_naive(p_zones text[], p_prev jsonb) returns text[]
language sql immutable as $$
  select case
    when p_prev is null or (select count(*) from unnest(p_zones) z where jsonb_typeof(p_prev -> z) = 'number')
                           <> coalesce(array_length(p_zones, 1), 0) then null
    else array(select z from unnest(p_zones) z order by (p_prev ->> z)::numeric, z collate "C") end
$$;

-- Save a player's forecast for one delivery day. One forecast per player and day; it can't be changed.
-- The first forecast of a day fixes its five zones, and later ones must use the same five.
create or replace function public.submit_forecast(p_secret uuid, p_delivery date, p_ranking text[])
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  pid uuid;
  st text;
  day_zones text[];
  old record;
begin
  select id into pid from players where secret = p_secret;
  if pid is null then raise exception 'unknown_player'; end if;
  st := spotrank_fc_state(p_delivery, spotrank_now());
  if st = 'early' then raise exception 'forecast_not_open' using hint = 'This forecast isn''t open yet.'; end if;
  if st = 'closed' then raise exception 'forecast_closed' using hint = 'Forecasts for this day closed at 11:30 CET.'; end if;
  if p_ranking is null or coalesce(array_length(p_ranking, 1), 0) <> 5 or array_position(p_ranking, null) is not null
     or (select count(distinct x) from unnest(p_ranking) x) <> 5 or not (p_ranking <@ spotrank_game_zones()) then
    raise exception 'bad_ranking' using hint = 'A forecast is five different game zones.';
  end if;
  select f.ranking, f.submitted_at into old from forecasts f where f.player_id = pid and f.delivery_date = p_delivery;
  if found then
    return jsonb_build_object('status', 'already_saved', 'ranking', to_jsonb(old.ranking), 'submitted_at', old.submitted_at);
  end if;
  insert into forecast_days (delivery_date, zones) values (p_delivery, spotrank_sorted(p_ranking))
  on conflict (delivery_date) do nothing;
  select d.zones into day_zones from forecast_days d where d.delivery_date = p_delivery;
  if day_zones <> spotrank_sorted(p_ranking) then
    raise exception 'other_zones' using hint = array_to_string(day_zones, ',');
  end if;
  insert into forecasts (player_id, delivery_date, ranking) values (pid, p_delivery, p_ranking)
  on conflict (player_id, delivery_date) do nothing;
  select f.ranking, f.submitted_at into old from forecasts f where f.player_id = pid and f.delivery_date = p_delivery;
  return jsonb_build_object('status', 'saved', 'ranking', to_jsonb(old.ranking), 'submitted_at', old.submitted_at);
end $$;

-- A player's own forecasts with their points (null until the prices are in), for any device.
create or replace function public.my_forecasts(p_secret uuid)
returns table (delivery_date date, ranking text[], submitted_at timestamptz, points int, naive_points int)
language sql stable security definer set search_path = public as $$
  select f.delivery_date, f.ranking, f.submitted_at,
         spotrank_fc_points(f.ranking, a.prices),
         spotrank_fc_points(spotrank_fc_naive(f.ranking, b.prices), a.prices)
  from forecasts f
  join players p on p.id = f.player_id
  left join day_prices a on a.price_date = f.delivery_date
  left join day_prices b on b.price_date = f.delivery_date - 1
  where p.secret = p_secret
  order by f.delivery_date
$$;

-- Everyone's forecast for one delivery day: names and times, and points once the prices are in.
-- Rankings are never returned.
create or replace function public.fc_day_results(p_delivery date)
returns table (name text, submitted_at timestamptz, points int, naive_points int)
language sql stable security definer set search_path = public as $$
  select p.name, f.submitted_at,
         case when spotrank_fc_state(p_delivery, spotrank_now()) = 'closed' then spotrank_fc_points(f.ranking, a.prices) end,
         case when spotrank_fc_state(p_delivery, spotrank_now()) = 'closed'
              then spotrank_fc_points(spotrank_fc_naive(f.ranking, b.prices), a.prices) end
  from forecasts f
  join players p on p.id = f.player_id
  left join day_prices a on a.price_date = f.delivery_date
  left join day_prices b on b.price_date = f.delivery_date - 1
  where f.delivery_date = p_delivery
  order by 3 desc nulls last, 2
$$;

-- Forecast leaderboard for delivery days between two dates (inclusive), scored forecasts only.
create or replace function public.fc_leaderboard(p_from date, p_to date)
returns table (name text, played int, points int, avg_points numeric, beat_naive int, perfect int)
language sql stable security definer set search_path = public as $$
  with s as (
    select f.player_id,
           spotrank_fc_points(f.ranking, a.prices) as pts,
           spotrank_fc_points(spotrank_fc_naive(f.ranking, b.prices), a.prices) as nv
    from forecasts f
    join day_prices a on a.price_date = f.delivery_date
    left join day_prices b on b.price_date = f.delivery_date - 1
    where f.delivery_date between p_from and p_to
  )
  select p.name, count(*)::int, sum(s.pts)::int, round(avg(s.pts), 2),
         count(*) filter (where s.pts > s.nv)::int, count(*) filter (where s.pts = 5)::int
  from s join players p on p.id = s.player_id
  where s.pts is not null
  group by p.id, p.name
  order by 3 desc, 4 desc, 1
$$;

revoke all on function public.spotrank_now(), public.spotrank_fc_state(date, timestamptz), public.spotrank_game_zones(),
  public.spotrank_sorted(text[]), public.spotrank_fc_points(text[], jsonb), public.spotrank_fc_naive(text[], jsonb)
  from public, anon, authenticated;
revoke all on function public.submit_forecast(uuid, date, text[]), public.my_forecasts(uuid),
  public.fc_day_results(date), public.fc_leaderboard(date, date) from public;
grant execute on function public.submit_forecast(uuid, date, text[]), public.my_forecasts(uuid),
  public.fc_day_results(date), public.fc_leaderboard(date, date) to anon, authenticated;
