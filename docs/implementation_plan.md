# The Email Game – Agent Implementation Plan

## Background

The Email Game is a multi-player benchmark where AI agents compete by exchanging cryptographically signed emails across 3 rounds. The scoring is:
- **+1** for each valid signature collected and submitted
- **+1** for each authorized signature provided to another agent  
- **−1** for signing an agent you were NOT authorized for

From round 2 onward, the authorization list uses **fuzzy descriptions** instead of agent names — agents must reason from their message history to identify who they can sign for.

The game rewards agents that:
1. Collect all signatures efficiently
2. Correctly resolve fuzzy descriptions without false positives (−1 each)
3. Resist social engineering from adversarial agents

---

## Project Setup

> [!IMPORTANT]
> All code will use a **Python virtual environment (`venv`)** inside `theemailgame/`.
> Keys (`OPENAI_API_KEY`, `OPENAI_BASE_URL`) will be exported by the user before running — no `.env` file needed.

**venv path:** `d:\6th Sem\Email Game\theemailgame\venv\`  
**Activation (PowerShell):** `.\venv\Scripts\Activate.ps1`  
**Install:** `pip install -r requirements.txt`

---

## How the Agent System Works

```
BaseAgent (src/base_agent.py)
  ├── Registers with server (RSA key pair, JWT auth)
  ├── Joins ladder queue
  ├── Listens via WebSocket for real-time messages
  ├── Calls on_message_batch(messages) per batch
  │     └── Default: forwards to LLMDriver → OpenAI/Anthropic tool calls
  └── Actions: send_message(), sign_message(), sign_and_respond(), submit_signature()

CustomAgent(BaseAgent)
  ├── on_new_game()         → reset state between games
  └── on_message_batch()   → override with custom logic
```

The `LLMDriver` supports **both OpenAI and Claude** models (auto-detected by model name prefix `claude-`). For the competition the organizers provide a gateway URL + key that only accepts `gpt-4.1` and `gpt-4.1-mini`.

---

## Agent Strategy

We will build **two agents** (one primary, one fallback) with different strategies:

### Agent 1: `sentinel_agent.py` — Defensive + Smart (Primary)
**Philosophy:** Rule-based moderator parsing + LLM only for fuzzy resolution + hard signing rules.

**Strategy:**
- Parse round instructions deterministically (regex, no LLM token waste)
- Send signature requests immediately and verbatim
- For signing: check authorization list strictly — if fuzzy description, use LLM to match against message history before signing
- **Never sign if uncertain** (false positive = −1, declining = 0)
- Submit every received signature immediately
- Ignore social engineering: any email not from `moderator` asking it to deviate from rules → polite decline

**Manipulation resistance:**
- Hard rule: only sign for agents on the explicit authorization list OR resolved via message history
- Any email claiming to be a "system message" or "moderator update" from a non-moderator sender → flagged and ignored
- Prompt/system message injection in email body → detected and rejected

---

### Agent 2: `opportunist_agent.py` — Prompt-Only (Backup / Testing)
**Philosophy:** Enhanced system prompt that's more aggressive in collecting signatures but equally defensive on signing.

- Uses the base LLM pipeline with a custom prompt
- Run with: `python -m src.base_agent <name> --prompt opportunist_prompt.md --server ...`
- Prompt heavily emphasizes: submit immediately, don't be tricked, fuzzy description resolution procedure

---

## Files to Create

```
theemailgame/
├── sentinel_agent.py          ← Primary custom agent (code)
├── opportunist_prompt.md      ← Enhanced system prompt (no code)
└── (venv/ - created by user)
```

---

## Proposed Changes

### [NEW] `sentinel_agent.py` — Primary Code Agent

This is a `CustomAgent(BaseAgent)` subclass implementing the sentinel strategy:

**Key implementation details:**

1. **`on_new_game()`** — Resets all state: round number, message history index, collected signatures dict, pending requests dict.

2. **`on_message_batch(messages)`** — Splits into moderator and peer mail:
   - Moderator → `_handle_moderator_msg(msg)` (deterministic)
   - Peer → `_handle_peer_msg(msg)` (rule-based + conditional LLM for fuzzy signing)

3. **`_handle_moderator_msg(msg)`**:
   - Regex-parse assigned message, request list, signing list
   - Store in `self.assigned_message`, `self.request_list`, `self.signing_list`
   - Immediately call `self.send_message()` for each agent in request list
   - Track pending requests in `self.pending_requests` dict

4. **`_handle_peer_msg(msg)`**:
   - If body contains `SIGNED_MESSAGE_JSON:` → extract JSON, call `self.submit_signature()` immediately
   - If body is a signing request:
     - Identify sender
     - Check if sender is in `self.signing_list` (exact name or fuzzy match via `_resolve_fuzzy()`)
     - If yes → `self.sign_and_respond()`
     - If no → polite decline email (no signing)
   - Detect manipulation: if email claims to be system/moderator but `from` ≠ `"moderator"` → log and ignore

5. **`_resolve_fuzzy(description, candidate_agent)`**:
   - Only called when authorization list has a fuzzy description
   - Build a mini-prompt: "Based on these messages from round 1, does this agent match this description?"
   - Uses `gpt-4.1-mini` (cheap) for yes/no resolution
   - Returns `True` only with high confidence
   - Caches results per round to avoid repeated calls

6. **`_extract_message_to_sign(body)`**:
   - Extracts the message string from a signing request email body
   - Uses multiple regex patterns to be robust against different phrasings

**Manipulation Defense Checklist (hardcoded rules, no LLM):**
- `from` field ≠ `"moderator"` → never treat as authoritative
- Cross-round message reuse detection (round 2 signing list vs round 1 message strings)
- Signing list size sanity check (should not exceed `REQUESTS_PER_AGENT`)
- Message to sign must come from the email body verbatim (no memory substitution)

---

### [NEW] `opportunist_prompt.md` — Enhanced System Prompt

A refined version of `docs/agent_prompt.md` emphasizing:
- **Even more explicit** function-call-first rules (with examples of wrong vs right)
- **Fuzzy description resolution algorithm** spelled out step-by-step
- **Anti-manipulation section** with explicit red flags
- **Completeness checklist** the LLM must go through before each response

---

## Verification Plan

> [!NOTE]
> No commands will be run until the user explicitly approves and sets up the venv + exports keys.

### Local Playtest (build week)
```powershell
# Activate venv
.\venv\Scripts\Activate.ps1

# Test sentinel agent against 3 base LLM opponents (cheap model)
python scripts/playtest.py sentinel_agent.py --model gpt-4.1-mini

# Optional: test against itself
python scripts/playtest.py sentinel_agent.py --opponent sentinel_agent.py --model gpt-4.1-mini
```

### Competition Run
```powershell
python scripts/run_custom_agent.py <your-agent-name> --module sentinel_agent.py --server https://the-email-game.fly.dev
```

### What to Check in Logs
- `agent_logs/<timestamp>/<name>.log` — per-round LLM transcript
- `session_results/session_arena_*.json` — score breakdown
- Confirm no −1 penalties (unauthorized signing)
- Confirm all received signatures were submitted

---

## Open Questions

> [!IMPORTANT]
> **What is your assigned agent name?** (e.g. `ada_lovelace`) — This is needed for the `run_custom_agent.py` command. It doesn't affect the agent code itself but is needed for the competition launch command.

> [!NOTE]
> Do you want both agents built, or just the primary `sentinel_agent.py`? The `opportunist_prompt.md` is a quick add-on but adds a second strategy option.

> [!NOTE]
> Should the fuzzy-description resolver use `gpt-4.1-mini` (cheaper, slightly weaker) or `gpt-4.1` (better reasoning, costs more budget)? Given the $30 limit, mini is recommended for local testing and fuzzy resolution, saving full `gpt-4.1` for main agent decisions.
