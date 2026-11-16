-- Spotrank update, 16 Nov 2026: the Forecast challenge closes at 11:00 Brussels time instead of 11:30.
-- (Switzerland's own auction closes at 11:00 and publishes from 11:10, so at 11:30 its price could be looked up.)
-- Paste into Supabase: SQL Editor > New query > Run. Safe to run again. Nothing else changes.

-- 'early' before midnight Brussels time on the day before delivery, 'open' until 11:00, then 'closed'.
create or replace function public.spotrank_fc_state(p_delivery date, p_at timestamptz) returns text
language sql stable as $$
  select case
    when p_at < ((p_delivery - 1)::timestamp at time zone 'Europe/Brussels') then 'early'
    when p_at < (((p_delivery - 1) + time '11:00') at time zone 'Europe/Brussels') then 'open'
    else 'closed' end
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
  if st = 'closed' then raise exception 'forecast_closed' using hint = 'Forecasts for this day closed at 11:00 CET.'; end if;
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

revoke all on function public.spotrank_fc_state(date, timestamptz) from public, anon, authenticated;
revoke all on function public.submit_forecast(uuid, date, text[]) from public;
grant execute on function public.submit_forecast(uuid, date, text[]) to anon, authenticated;
