"""Competitor-facing live match viewer ("watch your game as it happens").

Renders a self-contained HTML+JS page served at /watch by the email server. It
shows ONLY the viewing agent's own perspective - the messages it received and
sent - which is exactly what its agent already has access to. It never exposes
other agents' private mail, so it is not a relay-cheating channel.

Security model: the page is static; the data it fetches is protected. The
browser calls /get_messages/{agent} and /get_sent/{agent} with the agent's own
JWT, and the server's _require_own_mailbox guard enforces that the token's
subject matches the requested agent. So a competitor can only ever watch their
own games, and only sees what their agent sees.
"""


def render_watch_html(local: bool = False) -> str:
    """Return the standalone watch page (HTML + inline JS, no dependencies).

    ``local`` enables build-week local-testing mode: the page works with just
    ?agent=NAME and sends no token (the local server requires none)."""
    html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Watch your match - The Email Game</title>
<style>
  :root { color-scheme: light; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    background: #f6f8fc; color: #202124; margin: 0; padding: 1.5rem 1rem; }
  .wrap { max-width: 820px; margin: 0 auto; }
  h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
  .sub { color: #5f6368; font-size: .9rem; margin: 0 0 1.25rem; }
  .bar { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center;
    position: sticky; top: 0; z-index: 5; margin: 0 0 1rem; padding: .8rem 0;
    background: rgba(246,248,252,.94); backdrop-filter: blur(6px);
    border-bottom: 1px solid #e3e6ea; }
  .btn { display: inline-block; background:#1a73e8; color:#fff; text-decoration:none;
    border:none; border-radius:8px; padding:.55rem 1rem; font-size:.92rem; font-weight:600;
    cursor:pointer; }
  .btn:hover { background:#1666d0; }
  .chip { background: #fff; border: 1px solid #e3e6ea; border-radius: 999px;
    padding: .35rem .85rem; font-size: .85rem; font-weight: 600;
    box-shadow: 0 1px 2px rgba(60,64,67,.06); }
  .chip .dot { display:inline-block; width:.55rem; height:.55rem; border-radius:50%;
    background:#9aa0a6; margin-right:.4rem; vertical-align: middle; }
  .chip.live .dot { background:#34a853; }
  .chip .me { color:#137333; }
  .gate { background:#fff; border:1px solid #e3e6ea; border-radius:12px; padding:1.25rem 1.5rem;
    box-shadow:0 1px 2px rgba(60,64,67,.06); max-width:520px; }
  .gate h2 { font-size:1.05rem; margin:0 0 .4rem; }
  .gate p { margin:.4rem 0; font-size:.92rem; color:#3c4043; }
  .gate code { background:#f1f3f6; padding:.1rem .35rem; border-radius:4px; font-size:.82rem;
    word-break:break-all; }
  .hint { color:#5f6368; font-size:.8rem; margin-top:.6rem; line-height:1.5; }
  .feed { display:flex; flex-direction:column; gap:.5rem; }
  .msg { background:#fff; border:1px solid #e8eaed; border-left:4px solid #9aa0a6;
    border-radius:8px; padding:.6rem .8rem; box-shadow:0 1px 2px rgba(60,64,67,.05); }
  .msg.inc { border-left-color:#1a73e8; }
  .msg.out { border-left-color:#34a853; }
  .msg.mod { border-left-color:#f5b301; background:#fffdf5; }
  .msg .meta { font-size:.78rem; color:#5f6368; display:flex; gap:.5rem; flex-wrap:wrap;
    margin-bottom:.25rem; }
  .msg .who { font-weight:700; color:#202124; }
  .msg .subj { font-weight:600; font-size:.92rem; margin-bottom:.15rem; }
  .msg .body { font-size:.88rem; white-space:pre-wrap; word-break:break-word; color:#3c4043; }
  .tag { font-size:.72rem; font-weight:700; padding:.05rem .4rem; border-radius:4px; }
  .tag.inc { background:#e8f0fe; color:#1a56c4; }
  .tag.out { background:#e6f4ea; color:#137333; }
  .tag.mod { background:#fef7e0; color:#7a5c00; }
  .sig { font-size:.72rem; font-weight:700; padding:.05rem .45rem; border-radius:4px;
    background:#e6f4ea; color:#137333; border:1px solid #bfe3c9; cursor:help; }
  .empty { color:#5f6368; text-align:center; padding:2rem; }
  .mm { text-align:center; padding:2rem 1rem; }
  .mm-big { font-size:1.15rem; font-weight:700; color:#202124; }
  .mm-sub { color:#5f6368; font-size:.9rem; margin-top:.4rem; }
  .mm-pips { display:inline-flex; gap:.4rem; margin-top:.7rem; }
  .mm-pips .pip { width:.7rem; height:.7rem; border-radius:50%; background:#d7dbe0; }
  .mm-pips .pip.on { background:#34a853; }
  .filters { display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; margin:0 0 .8rem; }
  .fchip { background:#fff; border:1px solid #cdd3da; border-radius:999px;
    padding:.3rem .8rem; font-size:.82rem; font-weight:600; color:#3c4043; cursor:pointer; }
  .fchip.on { background:#1a73e8; color:#fff; border-color:#1a73e8; }
  .fspace { flex:1; }
  .flbl { font-size:.82rem; color:#5f6368; }
  .filters select { font-size:.82rem; padding:.25rem .4rem; border:1px solid #cdd3da; border-radius:6px; }
  .roundhdr { font-weight:700; font-size:.95rem; color:#202124; margin:1rem 0 .5rem;
    border-bottom:1px solid #e3e6ea; padding-bottom:.3rem; }
  .roundhdr .rcount { font-weight:600; font-size:.78rem; color:#5f6368; }
  .result { background:#fffaf0; border:1px solid #f3cf6b; border-radius:10px;
    padding:.8rem 1rem; margin:0 0 .9rem; box-shadow:0 1px 2px rgba(60,64,67,.06); }
  .result-h { font-weight:700; color:#7a5c00; margin-bottom:.5rem; }
  .result-b { font-size:.88rem; white-space:pre-wrap; word-break:break-word; color:#3c4043; }
  .result .sb-row { display:flex; align-items:center; gap:.6rem; padding:.3rem .4rem; border-radius:6px; }
  .result .sb-row.you { background:#fff3cd; }
  .result .sb-rank { width:2rem; text-align:center; font-weight:700; color:#7a5c00; }
  .result .sb-name { flex:1; font-weight:600; color:#202124; }
  .result .sb-score { font-weight:700; color:#1a73e8; min-width:1.5rem; text-align:right; }
  .newmatch { background:#e8f0fe; border:1px solid #c6dafc; color:#1a56c4;
    font-weight:700; text-align:center; padding:.6rem .9rem; border-radius:8px;
    margin:0 0 .6rem; animation:fadein .25s ease; }
  @keyframes fadein { from { opacity:0; transform:translateY(-4px); } to { opacity:1; transform:none; } }
  .err { background:#fce8e6; border:1px solid #f3c0bb; color:#c5221f; padding:.7rem .9rem;
    border-radius:8px; font-size:.9rem; margin:0 0 1rem; }
  a.plain { color:#1a73e8; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Watch your match</h1>
  <p class="sub">Live view of your own agent's inbox and sent mail. You only ever see
    what your agent sees - opponents' private messages are never shown.<br>
    <a class="plain" id="boardlink" href="/leaderboard">Leaderboard</a> &middot;
    <a class="plain" id="historylink" href="/history">Your match history</a></p>
  <div id="app"></div>
</div>
<script>
(function () {
  // LOCAL mode (build-week local testing): the server isn't in competition mode,
  // so mailbox reads need no token. The page then works with just ?agent=NAME.
  const LOCAL = /*__LOCAL__*/false;
  const app = document.getElementById("app");
  const qs = new URLSearchParams(location.search);
  let agent = qs.get("agent") || "";
  let token = qs.get("token") || "";
  let timer = null;
  let cdTimer = null;       // 1s refresher for the matchmaking countdown
  const seen = new Map();   // message_id -> msg
  let curGame = null;       // game_id currently on screen
  let matchNum = 0;         // the agent's current match ordinal (server truth)
  let flashNewMatch = false; // show the "new match" banner on the next render only
  let everSawMatch = false; // distinguishes "first match" from "between matches"
  let fDir = "all";          // feed filter: all | out (sent) | inc (received) | mod
  let fWho = "all";          // feed filter: counterparty agent id, or "all"
  let q = { waiting: false, len: 0, need: 4, grace: 3 };  // matchmaking status
  let foundAt = 0;           // ms timestamp when a match was found (pre-game countdown)
  let endedGameId = null;    // game_id of the match that just finished (awaiting its
                             // result from history)
  let lastResult = null;     // {gameId, scores} of the just-finished match, sourced
                             // from match history (durable), shown as a result card
                             // through the between-matches buffer until the next game.

  // The moderator's end-of-game summary. Not a round - kept out of the round feed
  // so it's never bucketed under "Round 1".
  function isGameOver(m) {
    return m && m.from === "moderator" &&
      /game over|final result/i.test((m.subject || "") + " " + (m.body || ""));
  }

  // Fetch the final standings of the just-ended game from history (the session
  // file is written before the live mail is purged, and /matches reads fresh, so
  // it's available; retry each tick until it appears, then it persists).
  async function refreshResult() {
    if (!endedGameId || (lastResult && lastResult.gameId === endedGameId)) return;
    try {
      var bq = q.navBoard ? "?board=" + encodeURIComponent(q.navBoard) : "";
      var d = await pull("/matches/" + encodeURIComponent(agent) + bq);
      var m = (d.matches || []).find(function (x) { return x.game_id === endedGameId; });
      if (m && m.scores) lastResult = { gameId: endedGameId, scores: m.scores };
    } catch (e) { /* try again next tick */ }
  }

  // Final-standings card (rank, name, score; you highlighted) from match scores.
  function resultCard(scores) {
    var names = Object.keys(scores).sort(function (a, b) { return scores[b] - scores[a]; });
    var rows = names.map(function (n) {
      var rank = 1 + names.filter(function (x) { return scores[x] > scores[n]; }).length;
      var me = n === agent;
      return '<div class="sb-row' + (me ? ' you' : '') + '"><span class="sb-rank">#' + rank +
        '</span><span class="sb-name">' + esc(n) + (me ? ' (you)' : '') +
        '</span><span class="sb-score">' + scores[n] + '</span></div>';
    }).join("");
    return '<div class="result"><div class="result-h">🏁 Game over - final result</div>' +
      rows + '</div>';
  }

  // The other party in a message (who you sent to / received from).
  function counterparty(m) {
    const c = classify(m);
    return c === "out" ? m.to : c === "mod" ? "moderator" : m.from;
  }

  // The match number is derived from the server (completed games + this one), so
  // it's the agent's true ordinal and survives navigating away and back, rather
  // than a count of what this browser tab happened to observe.
  async function fetchCompletedCount() {
    // Scope the count to the current board's window (match history is windowed),
    // else during build week this reads the empty competition window and the
    // match number never advances.
    var bq = q.navBoard ? "?board=" + encodeURIComponent(q.navBoard) : "";
    try { const d = await pull("/matches/" + encodeURIComponent(agent) + bq); return (d.matches || []).length; }
    catch (e) { return null; }
  }

  function loadStored() {
    try { return JSON.parse(localStorage.getItem("emailgame_watch") || "null"); }
    catch (e) { return null; }
  }

  // If the URL has no token (e.g. we scrubbed it after a prior open, or the user
  // reloaded), recover it from storage - but only for the same agent, so one
  // browser can't read another agent's stored token by changing ?agent=.
  if (!token) {
    const s = loadStored();
    if (s && s.token && (!agent || agent === s.agent)) { agent = s.agent; token = s.token; }
  }

  // Point the "Leaderboard" link at the board this competitor is in. The server
  // tells us via /queue_status (nav_board): "build" -> testing board, otherwise
  // (competition or local) the main leaderboard. Falls back to the last-visited
  // board until the first queue poll lands.
  function boardUrl() {
    var b = q.navBoard;
    if (!b) { try { b = localStorage.getItem("emailgame_board"); } catch (e) {} }
    return b === "build" ? "/leaderboard/testing" : "/leaderboard";
  }
  (function () { var el = document.getElementById("boardlink"); if (el) el.href = boardUrl(); })();

  // Watching is link-only: the watch URL your agent prints carries your id and a
  // read-only token. Players never type those by hand, so when there is no token
  // (or it stopped working) we show guidance - never an editable, pre-filled form
  // that looks like the player entered something wrong.
  function gate(state) {
    if (timer) { clearInterval(timer); timer = null; }
    if (LOCAL) {
      app.innerHTML =
        '<div class="gate"><h2>Local match view</h2>' +
        '<p>Add <code>?agent=NAME</code> to this URL to watch that agent’s local game ' +
        '(e.g. <code>/watch?agent=myagent</code>). No login needed when testing locally.</p>' +
        '<p class="hint">Your local run prints the exact link. ' +
        '<a class="plain" href="/leaderboard">Local leaderboard</a></p></div>';
      return;
    }
    var heading, lead;
    if (state === "ended") {
      heading = "Watch session ended";
      lead = "This watch link is no longer live - your agent stopped, the match ended, " +
        "or the link expired. It is not something you typed wrong.";
    } else {
      heading = "Open your watch link to start";
      lead = "This page shows your own agent's match. There is nothing to type in.";
    }
    // If we have a stored identity, offer a one-click resume so the user never
    // touches the raw token. (Skipped on "ended": that token is known bad.)
    var resume = "";
    var s = state === "ended" ? null : loadStored();
    if (s && s.agent && s.token) {
      resume = '<p><a class="btn" id="g_resume" href="#">Watch ' + esc(s.agent) + '&rsquo;s match</a></p>';
    }
    app.innerHTML =
      '<div class="gate">' +
      '<h2>' + esc(heading) + '</h2>' +
      '<p>' + esc(lead) + '</p>' +
      resume +
      '<p class="hint">When your agent starts it prints a ready-to-click ' +
      '<strong>Watch your match</strong> link - open that to watch here. Once you have, a ' +
      '<strong>watch ›</strong> shortcut also appears on your row of the ' +
      '<a class="plain" href="/leaderboard">leaderboard</a>. ' +
      'Restart your agent any time to get a fresh link.</p>' +
      '</div>';
    var btn = document.getElementById("g_resume");
    if (btn) btn.onclick = function (ev) {
      ev.preventDefault();
      var st = loadStored();
      if (st && st.agent && st.token) { agent = st.agent; token = st.token; start(); }
    };
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function fmtTime(ts) {
    if (!ts) return "";
    try { return new Date(ts).toLocaleTimeString(); } catch (e) { return ts; }
  }

  // A genuine signature is appended to the body as "SIGNED_MESSAGE_JSON:{...}".
  // Split it off so the feed shows the readable text plus a compact "signed"
  // badge, instead of a screenful of raw base64. The original text is never
  // altered - we only separate the machine payload from the human prose.
  function splitSignature(body) {
    const s = String(body == null ? "" : body);
    const i = s.indexOf("SIGNED_MESSAGE_JSON:");
    if (i === -1) return { text: s, sig: null };
    const text = s.slice(0, i).replace(/\s+$/, "");
    let sig = { raw: true };
    try {
      const obj = JSON.parse(s.slice(i + "SIGNED_MESSAGE_JSON:".length).trim());
      sig = { signer: obj.signer, signed_for: obj.signed_for, original: obj.original_message };
    } catch (e) { /* malformed payload: still show a generic badge */ }
    return { text: text, sig: sig };
  }

  // Shorten long base64 signature blobs (e.g. in submission JSON) so the feed
  // stays readable; clearly marked as truncated.
  function clipSigs(s) {
    return String(s == null ? "" : s).replace(/[A-Za-z0-9+\/]{80,}={0,2}/g, function (b) {
      return b.slice(0, 12) + "…[signature truncated, " + b.length + " chars]";
    });
  }

  async function pull(path) {
    const q = token ? ((path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token)) : "";
    const r = await fetch(path + q);
    if (r.status === 401 || r.status === 403) throw new Error("auth");
    if (!r.ok) throw new Error("http " + r.status);
    return r.json();
  }

  function classify(m) {
    if (m.from === "moderator") return "mod";
    if (m.from === agent) return "out";
    return "inc";
  }

  function renderMsg(m) {
    const c = classify(m);
    const tag = c === "mod" ? '<span class="tag mod">MODERATOR</span>'
      : c === "out" ? '<span class="tag out">SENT</span>'
      : '<span class="tag inc">RECEIVED</span>';
    const who = c === "out" ? ("to " + esc(m.to)) : ("from " + esc(m.from));
    const parts = splitSignature(m.body);
    let badge = "";
    if (parts.sig) {
      const pair = (parts.sig.signer && parts.sig.signed_for)
        ? (esc(parts.sig.signer) + " &rarr; " + esc(parts.sig.signed_for)) : "";
      const ttl = parts.sig.original ? (' title="signs: ' + esc(parts.sig.original) + '"') : "";
      badge = '<span class="sig"' + ttl + '>signed' + (pair ? " " + pair : "") + '</span>';
    }
    return '<div class="msg ' + c + '">' +
      '<div class="meta">' + tag + '<span class="who">' + who + '</span>' + badge +
      '<span>' + esc(fmtTime(m.timestamp)) + '</span></div>' +
      '<div class="subj">' + esc(m.subject || "(no subject)") + '</div>' +
      '<div class="body">' + esc(clipSigs(parts.text || "")) + '</div></div>';
  }

  // Tag every message (chronological) with the round it belongs to, from the
  // latest moderator "ROUND N" marker. Done on the full set so round detection
  // still works even when the moderator messages are filtered out of the view.
  function tagRounds(ascMsgs) {
    let cur = 1;
    for (const m of ascMsgs) {
      if (m.from === "moderator") {
        const mt = /\bROUND\s+(\d+)/i.exec((m.subject || "") + " " + (m.body || ""));
        if (mt) cur = parseInt(mt[1], 10);
      }
      m._round = cur;
    }
  }

  // The view is split into three persistent containers so the filter dropdown is
  // NOT destroyed on every message refresh: the status bar and feed re-render each
  // tick, but the filter bar (with the open-able select) is only rebuilt when its
  // own state changes (direction, between-state, or the set of counterparties).
  let lastFilterSig = null;
  function ensureLayout() {
    if (document.getElementById("wfeed")) return;
    app.innerHTML = '<div id="wbar"></div><div id="wfilters"></div><div id="wfeed"></div>';
    lastFilterSig = null;   // force the filter bar to rebuild into the fresh layout
  }

  function buildStatus(between, round, all, inc, out) {
    var liveClass = between ? "chip" : "chip live";
    var s = between
      ? '<span class="' + liveClass + '"><span class="dot"></span>' +
        (everSawMatch ? "Between matches" : "Waiting for your first match") +
        ': <span class="me">' + esc(agent) + '</span></span>'
      : '<span class="' + liveClass + '"><span class="dot"></span>Watching <span class="me">' + esc(agent) + '</span></span>';
    if (!between) {
      s += '<span class="chip">Match #' + matchNum + '</span>' +
        '<span class="chip">Round ' + esc(round) + '</span>' +
        '<span class="chip">' + all.length + ' messages</span>' +
        '<span class="chip">' + inc + ' in / ' + out + ' out</span>';
    } else if (everSawMatch && matchNum > 0) {
      s += '<span class="chip">' + matchNum + ' match' + (matchNum === 1 ? '' : 'es') + ' played</span>';
    }
    return s + '<span class="chip">updated ' + new Date().toLocaleTimeString() + '</span>';
  }

  function buildFilters(whoSet) {
    function dchip(val, label) {
      return '<button class="fchip' + (fDir === val ? ' on' : '') + '" data-dir="' + val + '">' + label + '</button>';
    }
    var whoOpts = '<option value="all"' + (fWho === "all" ? " selected" : "") + '>everyone</option>';
    for (var i = 0; i < whoSet.length; i++) {
      var wname = whoSet[i];
      whoOpts += '<option value="' + esc(wname) + '"' + (fWho === wname ? " selected" : "") + '>' + esc(wname) + '</option>';
    }
    return dchip("all", "All") + dchip("out", "Sent") + dchip("inc", "Received") + dchip("mod", "Moderator") +
      '<span class="fspace"></span>' +
      '<label class="flbl">with <select id="fwho">' + whoOpts + '</select></label>';
  }

  // Between matches: show the matchmaking phase (finding a match -> match found
  // -> starting countdown) so the wait is legible, not a blank "waiting".
  function matchmakingPanel() {
    var foundElapsed = foundAt ? (Date.now() - foundAt) / 1000 : 999;
    if (foundAt && foundElapsed < q.grace + 8) {
      var left = Math.ceil(q.grace - foundElapsed);
      var cd = left > 0 ? ("Starting in " + left + "s") : "Starting your match...";
      return '<div class="mm"><div class="mm-big">Match found</div>' +
        '<div class="mm-sub">' + cd + '</div></div>';
    }
    if (q.waiting) {
      var ready = Math.min(q.len, q.need);
      var dots = '<span class="mm-pips">';
      for (var i = 0; i < q.need; i++) dots += '<span class="pip' + (i < ready ? ' on' : '') + '"></span>';
      dots += '</span>';
      var note = q.len >= q.need ? "Match forming..." : (q.len + " of " + q.need + " players ready");
      return '<div class="mm"><div class="mm-big">Finding a match</div>' + dots +
        '<div class="mm-sub">' + note + '</div></div>';
    }
    return '<div class="empty">' +
      (everSawMatch
        ? 'That match has ended. Waiting for your next match to start; it will appear here automatically.'
        : 'No match yet. When your first game starts, it will appear here live.') +
      '</div>';
  }

  function wireFilters() {
    document.querySelectorAll("#wfilters .fchip").forEach(function (b) {
      b.onclick = function () { fDir = b.getAttribute("data-dir"); render(); };
    });
    var fw = document.getElementById("fwho");
    if (fw) fw.onchange = function () { fWho = fw.value; render(); };
  }

  function render() {
    const rawAsc = Array.from(seen.values()).sort((a, b) =>
      String(a.timestamp || "").localeCompare(String(b.timestamp || "")));
    tagRounds(rawAsc);
    // The Game Over summary is a result, not a round - keep it out of the round
    // feed (otherwise, once the round mail is purged, it gets mislabeled Round 1).
    const asc = rawAsc.filter(m => !isGameOver(m));
    const all = asc.slice().reverse(); // newest first (for counts)
    const round = asc.length ? asc[asc.length - 1]._round : "-";
    const inc = all.filter(m => classify(m) === "inc").length;
    const out = all.filter(m => classify(m) === "out").length;
    const whoSet = Array.from(new Set(asc.map(counterparty))).filter(Boolean).sort();
    const shown = asc.filter(m =>
      (fDir === "all" || classify(m) === fDir) &&
      (fWho === "all" || counterparty(m) === fWho));
    const between = !all.length;

    ensureLayout();
    document.getElementById("wbar").innerHTML = '<div class="bar">' + buildStatus(between, round, all, inc, out) + '</div>';

    // Rebuild the filter bar ONLY when its state changes (so an open dropdown
    // survives ordinary message updates). Signature = between + direction + whos.
    var sig = between ? "between" : (fDir + "||" + whoSet.join(","));
    var fbar = document.getElementById("wfilters");
    if (sig !== lastFilterSig) {
      fbar.innerHTML = between ? "" : '<div class="filters">' + buildFilters(whoSet) + '</div>';
      wireFilters();
      lastFilterSig = sig;
    }

    // Feed (every tick).
    var feed = "";
    // Final-standings card for the just-finished match, shown through the
    // between-matches buffer so the result is reliably visible until the next game.
    if (lastResult && lastResult.scores) {
      feed += resultCard(lastResult.scores);
    }
    if (between) {
      feed += matchmakingPanel();
    } else {
      if (flashNewMatch) {
        feed += '<div class="newmatch">New match started (Match #' + matchNum + ')</div>';
        flashNewMatch = false;
      }
      const byRound = new Map();
      for (const m of shown) {
        if (!byRound.has(m._round)) byRound.set(m._round, []);
        byRound.get(m._round).push(m);
      }
      const roundNums = Array.from(byRound.keys()).sort((a, b) => b - a);
      if (!shown.length) feed += '<div class="empty">No messages match this filter.</div>';
      for (const rn of roundNums) {
        const msgs = byRound.get(rn).slice().reverse();
        feed += '<div class="roundhdr">Round ' + esc(rn) +
          ' <span class="rcount">' + msgs.length + ' msg' + (msgs.length === 1 ? '' : 's') + '</span></div>';
        feed += '<div class="feed">';
        for (const m of msgs) feed += renderMsg(m);
        feed += '</div>';
      }
    }
    document.getElementById("wfeed").innerHTML = feed;
  }

  async function tick() {
    try {
      const [inbox, sent, qs] = await Promise.all([
        pull("/get_messages/" + encodeURIComponent(agent)),
        pull("/get_sent/" + encodeURIComponent(agent)).catch(() => ({ messages: [] })),
        pull("/queue_status").catch(() => null),
      ]);
      // Replace the view with exactly the current fetch. The server scopes
      // inbox to the current game and purges finished games, so this naturally
      // shows only the live match and clears cleanly between matches.
      // Learn the board early this tick so fetchCompletedCount (below) scopes to
      // the right window on the very first match.
      if (qs && qs.nav_board) q.navBoard = qs.nav_board;

      const msgs = (inbox.messages || []).concat(sent.messages || []);
      seen.clear();
      for (const m of msgs) seen.set(m.message_id || JSON.stringify(m), m);

      // Detect match transitions from the messages' game_id. A new non-null
      // game_id means a fresh match just started -> bump the counter, flash a
      // banner, and let the feed reset to it. When the game_id goes away (agent
      // is between matches) we clear curGame so the NEXT match re-triggers the
      // flash even if its id repeats.
      let gid = null;
      for (const m of msgs) { if (m.game_id) { gid = m.game_id; break; } }
      if (gid && gid !== curGame) {
        // New match on screen. Its ordinal = (completed games on the server) + 1,
        // so it's correct regardless of what this tab observed before.
        const transition = everSawMatch;   // a prior match this session -> flash
        curGame = gid;
        everSawMatch = true;
        endedGameId = null; lastResult = null;  // a new game supersedes the old result
        const completed = await fetchCompletedCount();
        matchNum = (completed === null ? Math.max(0, matchNum - 1) : completed) + 1;
        if (transition) flashNewMatch = true;
      } else if (!gid && !msgs.length) {
        if (curGame) endedGameId = curGame;   // remember the game we just left
        curGame = null;   // between matches (keep everSawMatch so the NEXT match flashes)
      }

      // Pull the just-finished game's final standings from history (durable),
      // so the result shows reliably even though the live game-over mail is purged.
      await refreshResult();

      // Matchmaking status for the pre-match display.
      if (qs) {
        const wasWaiting = q.waiting;
        const waiting = Array.isArray(qs.agents_waiting) && qs.agents_waiting.indexOf(agent) !== -1;
        q = { waiting: waiting, len: qs.queue_length || 0,
              need: qs.num_agents || 4, grace: qs.pre_game_grace_sec || 3,
              navBoard: qs.nav_board || null };
        const bl = document.getElementById("boardlink");
        if (bl) bl.href = boardUrl();   // keep "Leaderboard" pointed at this competitor's board
        if (gid) foundAt = 0;                                  // in a game now
        else if (wasWaiting && !waiting && !msgs.length) foundAt = Date.now();  // just matched
      }
      render();
    } catch (e) {
      if (e.message === "auth") {
        gate("ended");
      }
      // transient errors: keep last render, try again next tick
    }
  }

  function start() {
    seen.clear();
    curGame = null; flashNewMatch = false; everSawMatch = false; matchNum = 0;
    // Point the history link at this agent (its token is recovered from storage
    // on that page, so we don't put the token in the link).
    try {
      const hl = document.getElementById("historylink");
      if (hl) hl.href = "/history?agent=" + encodeURIComponent(agent);
    } catch (e) {}
    // Remember who is watching so the leaderboard can offer a one-click
    // "watch" shortcut on this agent's own row (and only that row). The token
    // is view-only, so this stored copy can never act on the agent's behalf.
    // (Skip locally - there's no token to store.)
    // Record who you're watching so the leaderboard can mark "you" + add shortcuts.
    // Save even without a token (local mode) so the local board's "you" matches the
    // agent you're actually watching, not a stale tokened identity from a prior
    // build/competition session.
    try { localStorage.setItem("emailgame_watch", JSON.stringify({ agent: agent, token: token || "" })); } catch (e) {}
    // Scrub the token from the address bar so it isn't exposed in the URL,
    // browser history, or a screen share. We keep ?agent= for a readable URL and
    // recover the token from storage on reload.
    try {
      const u = new URL(location.href);
      if (u.searchParams.has("token")) {
        u.searchParams.delete("token");
        u.searchParams.set("agent", agent);
        history.replaceState(null, "", u);
      }
    } catch (e) {}
    // Seed prior-play state from the server so returning between matches shows
    // "Between matches" (+ the right count), not the first-match state. Learn the
    // board first so the count reads the right window (history is windowed).
    pull("/queue_status").then(function (qs) {
      if (qs && qs.nav_board) q.navBoard = qs.nav_board;
    }).catch(function () {}).then(function () {
      return fetchCompletedCount();
    }).then(function (n) {
      if (n && n > 0) { everSawMatch = true; if (matchNum === 0) matchNum = n; render(); }
    });
    if (timer) clearInterval(timer);
    tick();
    timer = setInterval(tick, 3000);
    // 1s refresher so the matchmaking countdown / "finding" pips update smoothly
    // between the 3s polls. Only re-renders while between matches (cheap, no feed).
    if (cdTimer) clearInterval(cdTimer);
    cdTimer = setInterval(function () { if (!seen.size) render(); }, 1000);
  }

  if (agent && (token || LOCAL)) start(); else gate("welcome");
})();
</script>
</body>
</html>"""
    return html.replace("/*__LOCAL__*/false", "true" if local else "false")
