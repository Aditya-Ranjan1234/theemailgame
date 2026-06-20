"""Per-agent stats: attack/defense/collection breakdown for the detail page.

Reads the same session_results files as the leaderboard (honoring the competition
cutoff). Aggregate rates come from compute_leaderboard(); this module adds the
per-game detail -- who you tricked and who tricked you -- from the per-signature
event log, with a fallback to the older derived fields.
"""
from typing import Dict, List, Optional
from pathlib import Path

from .leaderboard import (_load_sessions, _results_dir, _window_bounds,
                          compute_leaderboard, _escape)


def compute_agent_report(agent_id: str, results_dir: Path = None,
                         window: str = "competition") -> Dict:
    """Aggregate summary + a per-game breakdown for one agent (newest first).

    ``window`` ("competition" or "build") scopes both the summary and the per-game
    list to the same board the viewer came from, so the testing board's agent
    stats are separate from the competition board's."""
    results_dir = results_dir or _results_dir()
    cutoff, end = _window_bounds(window)
    sessions = _load_sessions(results_dir, cutoff, end)

    games: List[Dict] = []
    for d in sessions:
        scores = d.get("cumulative_scores", {})
        agents = list(scores.keys())
        if agent_id not in agents or len(agents) < 2:
            continue
        departed = set(d.get("departed", [])) & set(agents)
        abandoned = bool(departed)

        victims: Dict[str, int] = {}      # agents this agent tricked
        attackers: Dict[str, int] = {}    # agents that tricked this agent
        attacks_landed = times_fooled = auth_coll = auth_signs = 0
        events_seen = False

        for r in d.get("rounds", []):
            perf = r.get("agent_performance", {}).get(agent_id, {})
            auth_signs += perf.get("signing_points", 0)
            events = r.get("signature_events")
            perm = r.get("signing_permissions", {})
            if events is not None:
                events_seen = True
                for e in events:
                    if e.get("submitter") == agent_id:
                        if e.get("authorized"):
                            auth_coll += 1
                        else:
                            attacks_landed += 1
                            victims[e["signer"]] = victims.get(e["signer"], 0) + 1
                    if e.get("signer") == agent_id and not e.get("authorized"):
                        times_fooled += 1
                        attackers[e["submitter"]] = attackers.get(e["submitter"], 0) + 1
            else:
                for y in perf.get("successfully_submitted_for", []):
                    if agent_id in perm.get(y, []):
                        auth_coll += 1
                    else:
                        attacks_landed += 1
                        victims[y] = victims.get(y, 0) + 1
                times_fooled += perf.get("unauthorized_signing_penalties", 0)

        top = max(scores.values()) if scores else 0
        winners = [a for a in agents if scores[a] == top]
        if abandoned:
            result = "forfeit" if agent_id in departed else "no-contest"
        elif scores.get(agent_id) == top and len(winners) == 1:
            result = "win"
        elif scores.get(agent_id) == top:
            result = "tie"
        else:
            result = "loss"

        games.append({
            "game_id": d.get("session_id"),
            "date": d.get("start_time"),
            "score": scores.get(agent_id, 0),
            "result": result,
            "attacks_landed": attacks_landed, "victims": victims,
            "times_fooled": times_fooled, "attackers": attackers,
            "authorized_collected": auth_coll, "authorized_signs": auth_signs,
            "abandoned": abandoned, "events_available": events_seen,
        })

    games.reverse()  # sessions load chronologically -> newest first
    summary = next((e for e in compute_leaderboard(results_dir, window=window)
                    if e["agent_id"] == agent_id), None)
    return {"agent_id": agent_id, "summary": summary, "games": games, "window": window}


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _counts(d: Dict[str, int]) -> str:
    if not d:
        return "-"
    return ", ".join(f"{_escape(k)} ({v})" for k, v in
                     sorted(d.items(), key=lambda kv: -kv[1]))


def render_agent_html(report: Dict) -> str:
    """Standalone HTML detail page for one agent."""
    aid = _escape(report["agent_id"])
    s = report.get("summary")
    games = report.get("games", [])
    back_url = "/leaderboard/testing" if report.get("window") == "build" else "/leaderboard"

    if s:
        cards = [
            ("Rating", str(s["elo"])),
            ("Rank", f"#{s['rank']}"),
            ("Games", str(s["games"])),
            ("Wins", str(s.get("wins", 0))),
            ("Collection", _pct(s.get("collection_rate"))),
            ("Penalties", str(s.get("penalties", 0))),
            ("Avg / round", f"{s['avg_score_per_round']:.2f}"),
        ]
        cards_html = "".join(
            f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div></div>'
            for k, v in cards)
    else:
        cards_html = '<div class="card"><div class="v">No games yet.</div></div>'

    if games:
        rows = "".join(f"""
            <tr>
              <td>{_escape(g['date'] or '')[:16].replace('T', ' ')}</td>
              <td class="res {g['result']}">{g['result']}</td>
              <td>{g['score']}</td>
              <td>{g['attacks_landed']}</td>
              <td class="who">{_counts(g['victims'])}</td>
              <td>{g['times_fooled']}</td>
              <td class="who">{_counts(g['attackers'])}</td>
              <td>{g['authorized_collected']}</td>
              <td>{g['authorized_signs']}</td>
            </tr>""" for g in games)
    else:
        rows = '<tr><td colspan="9" class="empty">No games recorded.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>{aid} - Email Game stats</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      background: #f6f8fc; color: #202124; margin: 0; padding: 2rem 1rem; }}
    .wrap {{ max-width: 980px; margin: 0 auto; }}
    a {{ color: #1a73e8; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    h1 {{ font-size: 1.6rem; margin: 0 0 .15rem; }}
    .back {{ font-size: .9rem; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: .6rem; margin: 1.25rem 0; }}
    .card {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 10px;
      padding: .7rem .85rem; box-shadow: 0 1px 2px rgba(60,64,67,.06); }}
    .card .k {{ color: #5f6368; font-size: .72rem; text-transform: uppercase;
      letter-spacing: .03em; }}
    .card .v {{ font-size: 1.25rem; font-weight: 700; margin-top: .15rem; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
      border: 1px solid #e3e6ea; border-radius: 12px; overflow: hidden;
      box-shadow: 0 1px 2px rgba(60,64,67,.06); font-size: .9rem; }}
    th, td {{ padding: .55rem .7rem; text-align: left; border-bottom: 1px solid #eef0f3;
      vertical-align: top; }}
    th {{ background: #f1f3f6; color: #5f6368; font-weight: 600; font-size: .72rem;
      text-transform: uppercase; letter-spacing: .03em; }}
    td.who {{ color: #5f6368; font-size: .82rem; }}
    .res {{ font-weight: 600; text-transform: capitalize; }}
    .res.win {{ color: #137333; }}
    .res.loss {{ color: #c5221f; }}
    .res.forfeit {{ color: #c5221f; }}
    .res.tie, .res.no-contest {{ color: #9a6700; }}
    td.empty {{ text-align: center; color: #5f6368; padding: 2rem; }}
    .legend {{ color: #5f6368; font-size: .82rem; margin-top: 1rem; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="wrap">
    <p class="back"><a href="{back_url}">&larr; Leaderboard</a></p>
    <h1>{aid}</h1>
    <div class="cards">{cards_html}</div>
    <p class="legend" style="margin-top:0">
      <strong>Rating</strong> = your leaderboard score; everyone starts around 1000 and it
      climbs as you win. It's deliberately <em>cautious while you're new</em> (a few lucky
      games won't spike it) and rises toward your true level as you play more.
      <strong>Collection</strong> = the share of your assigned signatures you actually collected.
    </p>
    <table>
      <thead><tr>
        <th>When</th><th>Result</th><th>Score</th>
        <th>You tricked</th><th>whom</th>
        <th>You got tricked</th><th>by whom</th>
        <th>Collected</th><th>Signed</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <p class="legend">
      Per game (your perspective):
      <strong>You tricked</strong> = times you got another agent to sign for you when it
      wasn't authorized to (a successful manipulation); <strong>whom</strong> lists them.
      <strong>You got tricked</strong> = times you signed when you weren't authorized
      (&minus;1 each); <strong>by whom</strong> lists who manipulated you.
      <strong>Collected</strong> = authorized signatures you gathered for your own messages;
      <strong>Signed</strong> = authorized signatures you correctly provided to others.
    </p>
  </div>
</body>
</html>"""
