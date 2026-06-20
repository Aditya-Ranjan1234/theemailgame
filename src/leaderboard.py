"""Persistent cross-session TrueSkill leaderboard for The Email Game.

The leaderboard is derived entirely from the session result JSON files in
session_results/ - there is no separate ratings database. Ratings are
recomputed by replaying every session in chronological order, so the
session files remain the single source of truth. A lightweight cache keyed
on the set of files + their modification times avoids recomputing on every
request while still picking up new sessions automatically.

Rating model (TrueSkill)
------------------------
Each game has up to NUM_AGENTS players. We rate the whole game as one
multiplayer result: agents are ranked by final score (ties = draws) and fed to
TrueSkill in a single update, which is the correct model for an N-player
free-for-all (no pairwise 1v1 approximation). Every agent's skill is tracked as
a Gaussian: a mean μ and an uncertainty σ. New agents start uncertain (high σ)
so their ratings move fast early and settle as they play.

Agents are ranked by the CONSERVATIVE estimate μ − 3σ ("we're confident they're
at least this good"), so topping the board requires being good AND having played
enough - a few lucky games can't crown an under-proven agent. For display the
conservative value is mapped onto a familiar 1000-anchored scale (a brand-new
agent's prior maps to INITIAL_RATING); the underlying μ/σ are also exposed.

An abandoned game (someone left mid-match) is a no-contest for the survivors
(rating frozen, game not counted) and a forfeit for the leaver (a loss applied
to the leaver only).
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import trueskill

from src.game.config import PROJECT_ROOT

# Display anchor: a brand-new agent's prior (conservative = μ0 − 3σ0 = 0) maps to
# this, and each unit of conservative skill is worth RATING_SCALE display points.
INITIAL_RATING = 1000.0
RATING_SCALE = 40.0

# TrueSkill environment. draw_probability is tunable (games here draw fairly
# often when scores tie); beta/tau keep the library defaults. Built once.
_TS = trueskill.TrueSkill(
    draw_probability=float(os.getenv("EMAIL_GAME_TS_DRAW_PROB", "0.15")),
)


def _conservative(r: "trueskill.Rating") -> float:
    """TrueSkill conservative skill estimate, μ − 3σ."""
    return r.mu - 3 * r.sigma


def _display_rating(r: "trueskill.Rating") -> int:
    """Map conservative skill onto the 1000-anchored display scale."""
    return round(INITIAL_RATING + _conservative(r) * RATING_SCALE)

# Simple in-memory cache: (signature) -> computed entries
_cache: Dict[str, Tuple] = {}   # window -> (signature, entries)


def competition_start() -> Optional[str]:
    """Return the competition start cutoff (ISO-8601) if configured, else None.

    When COMPETITION_START_TIME is set, only sessions that started at or after
    that timestamp count toward the leaderboard. This gives a clean board for a
    competition without deleting any session history.
    """
    val = os.environ.get("COMPETITION_START_TIME", "").strip()
    return val or None


def competition_end() -> Optional[str]:
    """Return the competition end cutoff (ISO-8601) if configured, else None.

    When COMPETITION_END_TIME is set, sessions that started at or after that
    timestamp do not count toward the leaderboard. Mirrors competition_start();
    together they freeze the board to a fixed window without deleting history.
    """
    val = os.environ.get("COMPETITION_END_TIME", "").strip()
    return val or None


def _results_dir() -> Path:
    return PROJECT_ROOT / "session_results"


def _session_files(results_dir: Path) -> List[Path]:
    return sorted(results_dir.glob("session_arena_*.json"))


def _signature(files: List[Path]) -> Tuple:
    """A signature that changes whenever sessions are added or modified."""
    return tuple((f.name, f.stat().st_mtime) for f in files)


def _load_sessions(results_dir: Path, cutoff: Optional[str] = None,
                   end: Optional[str] = None) -> List[Dict]:
    """Load valid session dicts sorted chronologically by start_time.

    Sessions that started before `cutoff` or at/after `end` (both ISO-8601) are
    skipped, so the board reflects only the competition window.
    """
    sessions = []
    for fp in _session_files(results_dir):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if not d.get("cumulative_scores"):
            continue
        st = d.get("start_time") or ""
        if cutoff and st < cutoff:
            continue
        if end and st >= end:
            continue
        sessions.append(d)
    # start_time is ISO-8601 → lexicographic sort == chronological.
    # Fall back to session_id so ordering is stable if start_time missing.
    sessions.sort(key=lambda d: (d.get("start_time") or "", d.get("session_id") or ""))
    return sessions


def _blank_stats() -> Dict:
    return {
        "games": 0, "wins": 0, "total_score": 0, "rounds": 0,
        "sub": 0, "sig": 0, "pen": 0,
        # collection accumulators (authorized signatures collected vs requested;
        # non-abandoned games only). Attack/defense metrics were removed - in
        # practice they were near-zero for everyone (the game discourages
        # unauthorized signing) and relied on inference, so they added clutter,
        # not signal.
        "authcoll": 0, "req": 0,
    }


def _window_bounds(window: str) -> Tuple[Optional[str], Optional[str]]:
    """(cutoff, end) for a board window.

    "competition" counts games inside [COMPETITION_START_TIME, COMPETITION_END_TIME).
    "build" is the build-week / testing board: everything BEFORE the competition
    starts (end = COMPETITION_START_TIME). With no start configured there is no
    competition yet, so every game is build-week."""
    if window == "build":
        return (None, competition_start())
    return (competition_start(), competition_end())


def compute_leaderboard(results_dir: Path = None, window: str = "competition") -> List[Dict]:
    """Replay all sessions in order and return ranked leaderboard entries.

    ``window`` selects the board: "competition" (official) or "build" (build-week
    testing board, games before the competition starts).

    Each entry: rank, agent_id, elo, games, wins, win_rate, total_score,
    avg_score_per_round, penalties.
    """
    results_dir = results_dir or _results_dir()
    if not results_dir.exists():
        return []

    cutoff, end = _window_bounds(window)
    files = _session_files(results_dir)
    # Include the results dir in the signature: two different dirs can hold
    # files with identical names/mtimes (e.g. in tests), and without the path
    # they would collide in the cache.
    sig = (window, str(results_dir), cutoff, end) + _signature(files)
    cached = _cache.get(window)
    if cached and cached[0] == sig:
        return cached[1]

    sessions = _load_sessions(results_dir, cutoff, end)

    ratings: Dict[str, "trueskill.Rating"] = {}
    stats: Dict[str, Dict] = {}

    for d in sessions:
        scores: Dict[str, int] = d["cumulative_scores"]
        agents = list(scores.keys())
        if len(agents) < 2:
            continue
        for a in agents:
            ratings.setdefault(a, _TS.create_rating())

        # Agents who left mid-match. An abandoned game is a no-contest for the
        # survivors (their rating is frozen, the game isn't counted for them) and
        # a forfeit for the leavers (a loss applied to the leaver only).
        departed = set(d.get("departed", [])) & set(agents)
        abandoned = bool(departed)

        if abandoned:
            # Forfeit each leaver against the field, leaving survivors untouched:
            # rate the leaver vs a single synthetic opponent at the survivors'
            # average rating, with the leaver ranked last, and keep only the
            # leaver's updated rating.
            survivors = [a for a in agents if a not in departed]
            if survivors:
                opp_mu = sum(ratings[s].mu for s in survivors) / len(survivors)
                opp_sigma = sum(ratings[s].sigma for s in survivors) / len(survivors)
                field = _TS.create_rating(mu=opp_mu, sigma=opp_sigma)
                for leaver in departed:
                    before = ratings[leaver]
                    new, _ = _TS.rate(
                        [(before,), (field,)], ranks=[1, 0])  # leaver loses
                    # Apply only the skill (mu) penalty; keep the prior sigma so a
                    # forfeit never *raises* the leaver's conservative rating by
                    # reducing uncertainty (quitting must not look like progress).
                    ratings[leaver] = _TS.create_rating(mu=new[0].mu, sigma=before.sigma)
        else:
            # Rate the whole game as one multiplayer result. Lower rank = better;
            # higher score = better, ties share a rank (a draw).
            groups = [(ratings[a],) for a in agents]
            ranks = [sum(1 for b in agents if scores[b] > scores[a]) for a in agents]
            for a, g in zip(agents, _TS.rate(groups, ranks=ranks)):
                ratings[a] = g[0]

            # Tie fairness: TrueSkill gives agents in the "middle" of a draw a
            # slightly lower sigma than those at the edges (more adjacent
            # comparisons), so agents with the SAME score would otherwise get
            # different displayed ratings purely from input order. Equalize sigma
            # within each tied group (same score) so a true tie ranks identically.
            # mu is left as TrueSkill computed it (a draw between unequally-rated
            # agents should still move their means toward each other).
            by_score: Dict[int, List[str]] = {}
            for a in agents:
                by_score.setdefault(scores[a], []).append(a)
            for tied in by_score.values():
                if len(tied) > 1:
                    avg_sigma = sum(ratings[a].sigma for a in tied) / len(tied)
                    for a in tied:
                        ratings[a] = _TS.create_rating(mu=ratings[a].mu, sigma=avg_sigma)

        # Aggregate stats. An abandoned game is not a real contest, so it counts
        # toward NO agent's games/win%/avg/round stats - neither the survivors
        # (no-contest) nor the leaver. The leaver's only consequence is the Elo
        # forfeit applied above (update_set = departed), which persists in their
        # rating for when they next complete a real game.
        num_rounds = d.get("total_rounds") or len(d.get("rounds", []))
        if abandoned:
            counted = set()
            sole_winner = None
        else:
            counted = set(agents)
            top = max(scores.values())
            winners = [a for a in agents if scores[a] == top]
            sole_winner = winners[0] if len(winners) == 1 else None

        for a in counted:
            st = stats.setdefault(a, _blank_stats())
            st["games"] += 1
            st["total_score"] += scores[a]
            st["rounds"] += num_rounds
            if a == sole_winner:
                st["wins"] += 1

        for r in d.get("rounds", []):
            perf = r.get("agent_performance", {})
            req = r.get("request_lists", {})
            events = r.get("signature_events")
            for a, p in perf.items():
                if a not in counted or a not in stats:
                    continue
                stats[a]["sub"] += p.get("submission_points", 0)
                stats[a]["sig"] += p.get("signing_points", 0)
                stats[a]["pen"] += p.get("unauthorized_signing_penalties", 0)
                # Collection rate: of the signatures you were assigned to request,
                # how many authorized ones you actually collected. Computed from
                # the per-signature event log (ground truth); denominator is the
                # recorded request assignment. Abandoned games and rounds without
                # an event log are skipped. (Attack/defense metrics were removed -
                # near-zero signal in practice and reliant on inference.)
                if abandoned or events is None:
                    continue
                stats[a]["req"] += len(req.get(a, []))
                stats[a]["authcoll"] += sum(1 for e in events
                                            if e.get("submitter") == a and e.get("authorized"))

    entries = []
    for a, st in stats.items():
        r = ratings.get(a) or _TS.create_rating()
        entries.append({
            "agent_id": a,
            # "elo" is kept as the headline rating key for compatibility, but it
            # now carries the TrueSkill conservative score on a 1000-anchored
            # scale. mu/sigma/conservative expose the underlying skill estimate.
            "elo": _display_rating(r),
            "mu": round(r.mu, 2),
            "sigma": round(r.sigma, 2),
            "conservative": round(_conservative(r), 2),
            "games": st["games"],
            "wins": st["wins"],
            "win_rate": round(st["wins"] / st["games"], 3) if st["games"] else 0.0,
            "total_score": st["total_score"],
            "avg_score_per_round": round(st["total_score"] / st["rounds"], 2) if st["rounds"] else 0.0,
            "penalties": st["pen"],
            # Collection: authorized signatures collected / requested (None until
            # the agent has had a request opportunity in an event-logged game).
            "collection_rate": (round(st["authcoll"] / st["req"], 3)
                                if st["req"] else None),
            "authorized_collected": st["authcoll"],
            "total_requests": st["req"],
        })

    entries.sort(key=lambda e: (-e["elo"], -e["games"]))
    for i, e in enumerate(entries, 1):
        e["rank"] = i

    _cache[window] = (sig, entries)
    return entries


# ---------------------------------------------------------------------------
# Matchmaking support: expose the TrueSkill skill estimates and match quality so
# the server can form balanced games (see _select_matched_group in email_server).
# ---------------------------------------------------------------------------

def default_rating() -> "trueskill.Rating":
    """The prior for an agent that hasn't played yet (TrueSkill μ0, σ0)."""
    return _TS.create_rating()


def current_skills(window: str = "competition", results_dir: Path = None) -> Dict[str, "trueskill.Rating"]:
    """Map agent_id -> TrueSkill Rating (μ, σ) from the current board."""
    return {
        e["agent_id"]: _TS.create_rating(mu=e["mu"], sigma=e["sigma"])
        for e in compute_leaderboard(results_dir, window=window)
    }


def match_quality(ratings: List["trueskill.Rating"]) -> float:
    """TrueSkill match quality (≈ probability of a draw) for a free-for-all of
    1-player teams. Higher = more balanced/competitive. 1.0 for <2 players."""
    if len(ratings) < 2:
        return 1.0
    return _TS.quality([(r,) for r in ratings])


# Inline SVG icons (Lucide-style, stroke = currentColor) used in place of emoji
# on the public leaderboard page. Self-contained so the page needs no asset host.
_ICON_TROPHY = (
    '<svg class="icon" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/>'
    '<path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/>'
    '<path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/>'
    '<path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/>'
    '<path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></svg>'
)
_ICON_CALENDAR = (
    '<svg class="icon" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="4" width="18" height="18" rx="2"/>'
    '<path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/></svg>'
)
_ICON_MEDAL = (
    '<svg class="icon" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M7.21 15 2.66 7.14a2 2 0 0 1 .13-2.2L4.4 2.8A2 2 0 0 1 6 2h12a2 2 0 0 1 '
    '1.6.8l1.6 2.14a2 2 0 0 1 .14 2.2L16.79 15"/>'
    '<path d="M11 12 5.12 2.2"/><path d="m13 12 5.88-9.8"/><path d="M8 7h8"/>'
    '<circle cx="12" cy="17" r="5"/><path d="M12 18v-2h-.5"/></svg>'
)
# Metallic tints for the top three ranks (gold / silver / bronze).
_MEDAL_COLORS = {1: "#f5b301", 2: "#9aa3b0", 3: "#cd7f32"}


def _fmt_rate(x) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _rank_cell(rank: int) -> str:
    """Top three ranks get a tinted medal icon; the rest get a plain number.

    Both are wrapped in the same fixed-size centered badge so the numbers line
    up vertically and horizontally with the medals above them.
    """
    color = _MEDAL_COLORS.get(rank)
    if color:
        return (f'<span class="rank-badge medal" style="color:{color}" '
                f'aria-label="Rank {rank}">{_ICON_MEDAL}</span>')
    return f'<span class="rank-badge">{rank}</span>'


def render_leaderboard_html(entries: List[Dict], live: Dict = None,
                            board: str = "competition") -> str:
    """Render the leaderboard as a standalone HTML page.

    ``live`` (optional) carries point-in-time arena activity for the header bar:
    ``{"players": int, "matches": int, "in_game": int, "queued": int}``.
    ``board`` is "competition" (official) or "build" (build-week testing board).
    """
    is_build = board == "build"
    is_local = board == "local"
    if is_local:
        page_title = "The Email Game - Local Testing Leaderboard"
        heading = f"{_ICON_TROPHY} Local Testing Leaderboard"
        subtitle = ("Games run on THIS machine only (local play-testing). TrueSkill "
                    f"ratings; new agents start at {int(INITIAL_RATING)}.")
        notice = (
            '<div class="testbanner"><strong>Local testing only.</strong> '
            'These are games on <strong>your own machine</strong>. They are <strong>not</strong> '
            'the build-week board and <strong>not</strong> the competition - nothing here counts '
            'or is visible to anyone else. The real boards live on the host\'s competition server.</div>'
        )
    elif is_build:
        page_title = "The Email Game - Build-Week Leaderboard"
        heading = f"{_ICON_TROPHY} Build-Week Leaderboard"
        subtitle = ("Testing board for build week. TrueSkill ratings; new agents "
                    f"start at {int(INITIAL_RATING)} and climb as they prove themselves.")
        notice = (
            '<div class="testbanner"><strong>Testing leaderboard (unofficial).</strong> '
            'These are build-week games for trying out your agent. They do <strong>not</strong> '
            'count toward the competition - the official board starts fresh when the '
            'competition begins. <a class="plain" href="/leaderboard">Official leaderboard &rsaquo;</a></div>'
        )
    else:
        page_title = "The Email Game Leaderboard"
        heading = f"{_ICON_TROPHY} The Email Game Leaderboard"
        subtitle = (f"Cross-session TrueSkill ratings. New agents start at "
                    f"{int(INITIAL_RATING)} and climb as they prove themselves.")
        notice = (
            '<div class="xlink"><a class="plain" href="/leaderboard/testing">'
            'Build-week testing leaderboard &rsaquo;</a></div>'
        )
    if entries:
        rows = []
        for e in entries:
            rank_label = _rank_cell(e["rank"])
            rows.append(f"""
            <tr>
              <td class="rank">{rank_label}</td>
              <td class="agent" data-agent="{_escape(e['agent_id'])}"><span class="aname">{_escape(e['agent_id'])}</span></td>
              <td class="elo">{e['elo']}</td>
              <td>{e['games']}</td>
              <td>{e['wins']}</td>
              <td>{e['win_rate']*100:.0f}%</td>
              <td>{e['avg_score_per_round']:.2f}</td>
              <td>{_fmt_rate(e.get('collection_rate'))}</td>
              <td>{e['penalties']}</td>
            </tr>""")
        table_body = "".join(rows)
    else:
        table_body = """
            <tr><td colspan="9" class="empty">No games played yet. Run a session to populate the leaderboard.</td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="120">
  <title>{page_title}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      background: #f6f8fc; color: #202124; margin: 0; padding: 2rem 1rem;
    }}
    .wrap {{ max-width: 880px; margin: 0 auto; }}
    h1 {{ font-size: 1.7rem; margin: 0 0 .25rem; display: flex; align-items: center; gap: .55rem; }}
    .icon {{ display: inline-block; width: 1em; height: 1em; vertical-align: -0.125em;
      stroke: currentColor; fill: none; }}
    h1 .icon {{ color: #f5b301; }}
    .rank-badge {{ display: inline-flex; align-items: center; justify-content: center;
      width: 1.7em; height: 1.7em; line-height: 1; }}
    .rank-badge .icon {{ width: 1.3em; height: 1.3em; }}
    .sub {{ color: #5f6368; margin: 0 0 1.5rem; font-size: .95rem; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
      border: 1px solid #e3e6ea; border-radius: 12px; overflow: hidden;
      box-shadow: 0 1px 2px rgba(60,64,67,.06); }}
    th, td {{ padding: .7rem .9rem; text-align: right; border-bottom: 1px solid #eef0f3; }}
    th {{ background: #f1f3f6; color: #5f6368; font-weight: 600;
      font-size: .78rem; text-transform: uppercase; letter-spacing: .03em; }}
    th.rank, td.rank, th.agent, td.agent {{ text-align: left; }}
    td.agent {{ font-weight: 600; }}
    td.elo {{ font-weight: 700; color: #1a73e8; }}
    td.rank {{ font-size: 1.05rem; }}
    tbody tr:last-child td {{ border-bottom: none; }}
    tr:nth-child(even) td {{ background: #fafbfc; }}
    td.empty {{ text-align: center; color: #5f6368; padding: 2rem; }}
    .legend {{ color: #5f6368; font-size: .82rem; margin-top: 1rem; line-height: 1.6; }}
    code {{ background: #f1f3f6; padding: .1rem .35rem; border-radius: 4px; }}
    .live {{ display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
      margin: 0 0 1.25rem; }}
    .chip {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 999px;
      padding: .35rem .85rem; font-size: .88rem; font-weight: 600; color: #202124;
      box-shadow: 0 1px 2px rgba(60,64,67,.06); }}
    .chip .dot {{ display: inline-block; width: .55rem; height: .55rem;
      border-radius: 50%; background: #34a853; margin-right: .45rem;
      vertical-align: middle; }}
    .live-detail {{ color: #5f6368; font-size: .82rem; }}
    .row-watch {{ margin-left: .5rem; font-size: .76rem; font-weight: 600;
      color: #1a73e8; text-decoration: none; white-space: nowrap; }}
    .row-watch:hover {{ text-decoration: underline; }}
    tr.you td.agent .aname {{ font-weight: 700; }}
    a.plain {{ color: #1a73e8; }}
    .xlink {{ margin: 0 0 1.25rem; font-size: .9rem; }}
    .testbanner {{ background: #fff8e1; border: 1px solid #f6e0a3; color: #7a5c00;
      border-radius: 10px; padding: .7rem 1rem; margin: 0 0 1.25rem; font-size: .9rem;
      line-height: 1.5; }}
    .youcard {{ position: sticky; top: 0; z-index: 10; background: #e8f0fe;
      border: 1px solid #c6dafc; border-radius: 10px; padding: .7rem 1rem;
      margin: 0 0 1rem; display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
      box-shadow: 0 2px 6px rgba(60,64,67,.12); font-size: .92rem; }}
    .youcard .ylabel {{ font-weight: 700; color: #1a56c4; }}
    .youcard .ystat {{ color: #202124; }}
    .youcard .ystat strong {{ color: #1a56c4; }}
    .youcard a {{ margin-left: .2rem; font-size: .82rem; font-weight: 600;
      color: #1a73e8; text-decoration: none; white-space: nowrap; }}
    .youcard a:hover {{ text-decoration: underline; }}
    .youcard .spacer {{ flex: 1; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>{heading}</h1>
    <p class="sub">{subtitle}</p>
    {notice}
    <div id="youcard" class="youcard" style="display:none"></div>
    <div id="live-root">
    {_banner_html(live, entries, board=board)}
    <table>
      <thead>
        <tr>
          <th class="rank">#</th>
          <th class="agent">Agent</th>
          <th>Rating</th>
          <th>Games</th>
          <th>Wins</th>
          <th>Win&nbsp;%</th>
          <th>Avg/Round</th>
          <th>Collection</th>
          <th>Penalties</th>
        </tr>
      </thead>
      <tbody>{table_body}
      </tbody>
    </table>
    </div>
    <p class="legend">
      <strong>Games</strong> is how many games (not rounds) you've played.
      <strong>Wins</strong> is games where you finished alone in first by total score.
      A tie for the top counts as a win for no one, so wins can be fewer than games.
      <strong>Win&nbsp;%</strong> is wins divided by games.
      <strong>Avg/Round</strong> is lifetime points per round.
      <strong>Collection</strong> is the share of your assigned signature requests you
      collected and submitted.
      <strong>Penalties</strong> are unauthorized signatures (&minus;1 each).
      <strong>Rating</strong> is a conservative TrueSkill skill estimate
      (&mu;&nbsp;&minus;&nbsp;3&sigma;): you must be good <em>and</em> have played enough to rank
      high, so a couple of lucky games can't top the board.
      Rank is by <strong>Rating</strong> only. Wins are informational and don't affect it.
      <br><a class="watch-link" href="/watch">Watch your own match live</a> - once you open
      your agent's watch link, <strong>watch &rsaquo;</strong> / <strong>history &rsaquo;</strong> /
      <strong>stats &rsaquo;</strong> shortcuts appear on your own row (you can only ever view your
      own agent's detail).
    </p>
  </div>
  <script>
    var LB_BOARD = "{board}";   // which board this page is (competition | build)
    // Remember the board the viewer is on, so watch/history/stats pages can send
    // them back to THIS board (not always the competition one).
    try {{ localStorage.setItem("emailgame_board", LB_BOARD); }} catch (e) {{}}
    // Show watch/history/stats shortcuts only on the viewer's OWN row. Identity
    // comes from the watch page, which saves {{agent, token}} to localStorage when
    // opened. The leaderboard never learns anyone else's identity, so you can only
    // ever get a one-click link to your own match/stats (no peeking at opponents).
    function injectRowLinks() {{
      var me;
      try {{ me = JSON.parse(localStorage.getItem("emailgame_watch") || "null"); }} catch (e) {{}}
      if (!me || !me.agent) return;
      // Competition/build boards require a token (tokened identity). The local
      // board needs no login, so identify "you" by agent alone there - and never
      // let a stale tokened identity from another board mark "you" here.
      if (LB_BOARD !== "local" && !me.token) return;
      var tq = me.token ? ("&token=" + encodeURIComponent(me.token)) : "";
      var cell = document.querySelector('td.agent[data-agent="' + (window.CSS && CSS.escape
        ? CSS.escape(me.agent) : me.agent.replace(/"/g, '\\\\"')) + '"]');
      if (cell && !cell.querySelector(".row-watch")) {{
        cell.closest("tr").classList.add("you");
        var a = document.createElement("a");
        a.className = "row-watch";
        a.href = "/watch?agent=" + encodeURIComponent(me.agent) + tq;
        a.textContent = "watch ›"; a.title = "Watch your live match";
        cell.appendChild(a);
        var h = document.createElement("a");
        h.className = "row-watch";
        h.href = "/history?agent=" + encodeURIComponent(me.agent) + "&board=" + LB_BOARD;
        h.textContent = "history ›"; h.title = "Your past matches";
        cell.appendChild(h);
        var s = document.createElement("a");
        s.className = "row-watch";
        s.href = "/agent/" + encodeURIComponent(me.agent) + "?board=" + LB_BOARD + tq;
        s.textContent = "stats ›"; s.title = "Your detailed stats (this board)";
        cell.appendChild(s);
      }}
      renderYouCard(me, cell);   // cell may be null (not on the board yet)
    }}

    // A sticky "Your standing" card so a player always sees their rank + shortcuts
    // without scrolling - and it shows even before their first game completes
    // (when they aren't on the board yet), so a new competitor isn't left blank.
    function renderYouCard(me, cell) {{
      var card = document.getElementById("youcard");
      if (!card) return;
      var tq = me.token ? ("&token=" + encodeURIComponent(me.token)) : "";
      var w = "/watch?agent=" + encodeURIComponent(me.agent) + tq;
      var h = "/history?agent=" + encodeURIComponent(me.agent) + "&board=" + LB_BOARD;
      var st = "/agent/" + encodeURIComponent(me.agent) + "?board=" + LB_BOARD + tq;
      var standing, row = null;
      if (cell) {{
        row = cell.closest("tr");
        var rows = row.parentNode ? row.parentNode.querySelectorAll("tr") : [];
        var rank = Array.prototype.indexOf.call(rows, row) + 1;
        var eloCell = row.querySelector("td.elo");
        var rating = eloCell ? eloCell.textContent.trim() : "";
        standing = '<span class="ystat"><strong>#' + rank + '</strong> of ' + rows.length + '</span>' +
          '<span class="ystat">Rating <strong>' + rating + '</strong></span>';
      }} else {{
        standing = '<span class="ystat">not ranked yet - finish a game to appear on the board</span>';
      }}
      card.innerHTML =
        '<span class="ylabel">You</span>' +
        '<span class="ystat">' + (me.agent || "").replace(/[<>&]/g, "") + '</span>' +
        standing +
        '<span class="spacer"></span>' +
        '<a href="' + w + '">watch</a>' +
        '<a href="' + h + '">history</a>' +
        '<a href="' + st + '">stats</a>' +
        (row ? '<a href="#" id="jumprow">jump to my row</a>' : '');
      card.style.display = "flex";
      var jr = document.getElementById("jumprow");
      if (jr && row) jr.onclick = function (ev) {{ ev.preventDefault();
        row.scrollIntoView({{ behavior: "smooth", block: "center" }}); }};
    }}

    // Live auto-update: re-fetch the page and swap just the standings + banner in
    // place every 12s, so ratings refresh smoothly without a full reload (the
    // <meta refresh> is only a slow fallback). Re-inject the row links after.
    async function refreshBoard() {{
      try {{
        var r = await fetch(location.pathname, {{ cache: "no-store" }});
        if (!r.ok) return;
        var doc = new DOMParser().parseFromString(await r.text(), "text/html");
        var fresh = doc.getElementById("live-root");
        var cur = document.getElementById("live-root");
        if (fresh && cur) {{ cur.replaceWith(fresh); injectRowLinks(); }}
      }} catch (e) {{ /* transient: keep current view, try again next tick */ }}
    }}

    injectRowLinks();
    setInterval(refreshBoard, 12000);
  </script>
</body>
</html>"""


def _fmt_time(iso: str) -> str:
    """Trim an ISO-8601 cutoff to 'YYYY-MM-DD HH:MM UTC' for display."""
    if not iso:
        return ""
    s = iso.replace("T", " ")[:16]
    return _escape(s) + " UTC"


def _competition_phase(live: Dict) -> str:
    """Derive the competition phase for the header from the configured window and
    current arena activity: 'scheduled' | 'running' | 'ending' | 'ended'.

    'ending' = no new games forming (host drained, or past the end cutoff) while
    games are still finishing. 'ended' = that, with no games left running.
    """
    live = live or {}
    start, end = competition_start(), competition_end()
    now = datetime.now().isoformat()
    draining = bool(live.get("draining"))
    active = int(live.get("matches", 0))
    winding_down = draining or bool(end and now >= end)
    if winding_down:
        return "ended" if active == 0 else "ending"
    if start and now < start:
        return "scheduled"
    return "running"


def _banner_html(live: Dict = None, entries: List[Dict] = None,
                 board: str = "competition") -> str:
    """Competition status header: the start/end window, the current phase, and -
    once the competition has ended - the crowned winner.

    For the build-week board there is no competition window or winner; just show
    the current live-activity bar."""
    live = live or {}
    entries = entries or []
    if board in ("build", "local"):
        # No competition window/phase/winner on the build or local boards - just
        # the current live-activity bar.
        return _live_html(live)
    start, end = competition_start(), competition_end()
    phase = _competition_phase(live)

    def _box(bg, border, color, html):
        return (f'<div style="background:{bg};border:1px solid {border};color:{color};'
                f'padding:.8rem 1rem;border-radius:10px;margin:0 0 1.25rem;'
                f'font-size:.95rem;line-height:1.5;">{html}</div>')

    # Window line (start / end), shown whenever either cutoff is configured.
    window = ""
    if start or end:
        parts = []
        if start:
            parts.append(f"<strong>Start</strong> {_fmt_time(start)}")
        if end:
            parts.append(f"<strong>End</strong> {_fmt_time(end)}")
        window = (f'<p class="sub" style="margin:0 0 .8rem;">{_ICON_CALENDAR} '
                  + " &nbsp;&middot;&nbsp; ".join(parts) + "</p>")

    if phase == "ended":
        if entries:
            w = entries[0]
            body = (f'{_ICON_TROPHY} <strong>Competition over - winner: '
                    f'{_escape(w["agent_id"])}</strong><br>'
                    f'{w["elo"]} rating across {w["games"]} game(s), '
                    f'{w["wins"]} win(s). Congratulations!')
        else:
            body = f'{_ICON_TROPHY} <strong>Competition over.</strong> No games were played.'
        return window + _box("#fef7e0", "#f3cf6b", "#7a5c00", body)

    if phase == "ending":
        active = int(live.get("matches", 0))
        body = (f'<strong>Competition ending.</strong> No new games are forming - '
                f'{active} game(s) still finishing. The winner is crowned once the last '
                f'game completes.')
        return window + _box("#fff4e5", "#f6c887", "#8a4b00", body) + _live_html(live)

    if phase == "scheduled":
        body = (f'{_ICON_CALENDAR} <strong>Competition starts {_fmt_time(start)}.</strong> '
                f'Standings count from then.')
        return window + _box("#e8f0fe", "#c2d7fb", "#1a56c4", body) + _live_html(live)

    # running
    running = ""
    if start:
        running = _box("#e6f4ea", "#c6e7d0", "#137333",
                       f'{_ICON_CALENDAR} <strong>Competition live.</strong> '
                       f'Counting games since {_fmt_time(start)}.')
    return window + running + _live_html(live)


def _live_html(live: Dict = None) -> str:
    """A small live-activity bar: players and matches active in the arena now."""
    if not live:
        return ""
    players = int(live.get("players", 0))
    matches = int(live.get("matches", 0))
    in_game = int(live.get("in_game", 0))
    queued = int(live.get("queued", 0))
    detail = ""
    if in_game or queued:
        detail = (f'<span class="live-detail">{in_game} in a match '
                  f'&middot; {queued} waiting in queue</span>')
    return (
        '<div class="live">'
        f'<span class="chip"><span class="dot"></span>{players} '
        f'player{"" if players == 1 else "s"} online</span>'
        f'<span class="chip">{matches} '
        f'match{"" if matches == 1 else "es"} running</span>'
        f'{detail}'
        '</div>'
    )


def _escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
