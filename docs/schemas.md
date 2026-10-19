# Data Schemas — v1.0

Agents, UI and overlays depend on these formats. **Change = bump `schema_version`** (`SCHEMA_VERSION` in `stats_engine.py`).

## Match file (`data/synthetic/match_XXX.json`)
```json
{
  "schema_version": "1.0",
  "match":  { "match_id", "synthetic": true, "competition", "date", "venue": {...}, "referee", "weather": {...},
              "pitch": { "length_m": 105, "width_m": 68, "coordinates" }, "periods": [...], "final_score": {"RIV": 4, "NGR": 1} },
  "teams":  { "home": { "team_id", "name", "short_name", "formation", "colors", "coach", "side",
                        "players": [ { "player_id", "name", "short_name", "number", "position", "nationality",
                                       "team_id", "starter", "attributes": {...} } ] },
              "away": { ... } },
  "events": [ Event, ... ],
  "generator": { "seed", "momentum_spells": [ { "start_elapsed_s", "end_elapsed_s", "team" } ] }
}
```

## Event
| Field | Type | Notes |
|---|---|---|
| `event_id` | string | unique, e.g. `match_001-e0087` |
| `type` | string | see list below |
| `period` | int | 1 or 2 |
| `clock` | string | broadcast clock, `"35:29"` or `"45+1:12"` |
| `minute`, `added_minute` | int | football minute; added-time minute (0 if none) |
| `elapsed_s` | number | match time since kick-off incl. stoppage; never decreases |
| `team` | string \| null | team_id |
| `player_id`, `player_name` | string \| null | |
| `x`, `y`, `end_x`, `end_y` | number \| null | absolute metres |
| `outcome` | string \| null | e.g. `complete`/`incomplete`, `goal`/`saved`/`off_target`/`blocked`/`post`, `won`/`lost` |
| `details` | object | type-specific (below) |

| Type | Key `details` |
|---|---|
| `pass` | `subtype` (short/medium/long/through_ball/switch/back/cross), `height`, `distance_m`, `difficulty`, `ball_speed_kmh`, `pressured`, `recipient_id` |
| `shot` | `subtype`, `xg`, `big_chance`, `shot_speed_kmh`, `distance_m`, `body_part` |
| `goal` | `score`, `assist_id`, `xg`, `shot_speed_kmh` |
| `pressure` | `target_player_id`, `distance_m`, `intensity` |
| `tackle` | `target_player_id` |
| `possession_change` | `reason`, `from_team`, `previous_spell_s`, `previous_spell_passes` |
| `sprint` | `top_speed_kmh`, `distance_m`, `duration_s` |
| `physical_update` | `players: [{player_id, team, minutes, distance_km, top_speed_kmh, sprints}]` |
| `set_piece` | `subtype` (corner/throw_in/goal_kick/free_kick) |
| `card` | `card`, `reason` · `foul`: `fouled_player_id` · `substitution`: `player_in_id`, `player_in_name`, `role` |
| others | `interception`, `clearance`, `save`, `ball_out`, `kick_off`, `period_start`, `period_end`, `match_end` |

## Stream messages (`ws://host:8500/ws`)
Server → client: `meta`, `catchup` (`reset`, `events`), `event`, `status`, `end`.
Client → server: `play`, `pause`, `speed` (`value`), `seek` (`minute`), `restart`. See `stream_server.py`.

## Snapshot (`MatchState.snapshot()`)
```json
{
  "schema_version": "1.0", "period": 2, "clock": "71:15", "elapsed_s": 4302.1,
  "score": { "RIV": 4, "NGR": 1 },
  "teams": { "RIV": { "name", "goals", "possession_pct", "shots", "shots_on_target", "xg", "big_chances",
                      "passes", "pass_accuracy_pct", "avg_pass_distance_m", "avg_pass_difficulty",
                      "hard_passes_completed", "final_third_passes", "tackles_won", "interceptions", "pressures",
                      "fouls", "yellow_cards", "red_cards", "corners", "sprints", "turnovers",
                      "fastest_shot_kmh", "avg_ball_speed_kmh" }, "NGR": { ... } },
  "momentum": { "window_s": 300, "threat_share_pct": { "RIV": 72.0, "NGR": 28.0 }, "on_top": "RIV" },
  "rhythm":   { "state": "control|balanced|chaos", "turnovers_per_min", "avg_spell_s", "possession_share": {...} },
  "players":  { "RIV27": { "name", "team", "touches", "passes", "passes_complete", "pass_accuracy_pct",
                           "pass_distance_m", "avg_pass_difficulty", "shots", "goals", "assists", "xg",
                           "tackles_won", "interceptions", "pressures", "sprints", "top_speed_kmh",
                           "distance_km", "yellow_cards" } },
  "alerts_total": 35,
  "rejected_events": { "duplicate": 0, "malformed": 0, "out_of_order": 0, "unknown_team": 0, "handler_error": 0 }
}
```

## Alert (returned by `MatchState.update()`)
```json
{ "alert_id": "a012", "type": "momentum", "priority": "high|medium|low",
  "elapsed_s": 3540.0, "clock": "59:00", "period": 2, "team": "RIV",
  "player_id": null, "player_name": null,
  "text": "Momentum shift: Riverside on top - 94% of the threat in the last 5 minutes",
  "data": { "share": 94.0 } }
```
Alert types: `goal`, `card`, `substitution`, `big_chance`, `momentum`, `sprint_record`, `shot_speed_record`, `distance_milestone`, `possession_streak`, `pressure_spell`, `rhythm`.

## Viewer profile
```json
{ "profile_id": "fan_es_ngr", "mode": "analyst|fan", "language": "en|es|fr|pt|hi|ar",
  "club": "NGR", "player": "RIV27", "metric": "xg|possession|pass_accuracy|shots|pressures|top_speed|distance|pass_accuracy_player" }
```
All fields except `profile_id` are optional (defaults: analyst, English, no club / player / metric).

## Overlay (orchestrator output, one per viewer)
```json
{ "overlay_id": "ov-m-a001", "version": 1, "kind": "moment|recap|player_tracker|metric_ticker",
  "source": "template|llm|fallback", "fallback_reason": null,
  "profile_id": "analyst_en", "mode": "analyst", "language": "en", "pending_translation": false,
  "moment_id": "m-a001", "event_id": "match_001-e0087", "alert_type": "goal", "category": "goal",
  "tags": ["turning_point"], "importance": 100, "priority": "high",
  "clock": "02:26", "elapsed_s": 146.7, "period": 1, "team": "RIV", "player_id": "RIV27", "player_name": "Arribas",
  "headline": "Arribas scores for Riverside", "text": "02:26 - Arribas scores for Riverside! Riverside lead 1-0.",
  "why": "Riverside lead 1-0. Riverside have 2 shots and 0.7 xG to 0 and 0.0 for Northgate.",
  "evidence": [ { "label": "Riverside xG", "value": 0.7 } ], "tag": "turning_point",
  "highlight": { "your_club": false, "your_player": false },
  "metric": { "label": "Possession %", "values": { "Riverside": 71.0, "Northgate": 29.0 } },
  "display": { "slot": "full_banner|lower_third|side_panel|full_screen", "duration_s": 10, "show_at_elapsed_s": 146.7 } }
```
- **Version 1** arrives in milliseconds (template); **version 2** (same `overlay_id`) replaces it when the LLM agents finish.
- Recaps add `recap_type` (`half_time|full_time`), `key_moments` (list of strings), `player_of_the_period` (`{name, reason}`).
- Renderers should show the overlay at `display.show_at_elapsed_s` for `display.duration_s` in `display.slot`.
