-- Spotrank update, 16 Nov 2026: lets a player's rounds follow them to another device.
-- Paste into Supabase: SQL Editor > New query > Run. Safe to run again.

-- A player's own saved rounds (by secret code), so another device can show them.
create or replace function public.my_results(p_secret uuid)
returns table (puzzle_no int, price_date date, solved boolean, tries smallint, first_greens smallint, answer text[], attempts jsonb, at timestamptz)
language sql stable security definer set search_path = public as $$
  select r.puzzle_no, r.price_date, r.solved, r.tries, r.first_greens, r.answer, r.attempts, r.created_at
  from results r join players p on p.id = r.player_id
  where p.secret = p_secret
  order by r.puzzle_no
$$;

revoke all on function public.my_results(uuid) from public;
grant execute on function public.my_results(uuid) to anon, authenticated;
