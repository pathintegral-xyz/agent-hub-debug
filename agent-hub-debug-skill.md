# Agent Hub Debug — Log Investigation SOP

You have access to these MCP tools (prefixed `mcp__agent_debug__`):

| Tool | What it returns |
|------|----------------|
| `query_cloudwatch_logs` | Fetches logs → stores in memory as `q1`, `q2`, … |
| `list_queries` | Lists all stored result sets with counts |
| `get_log_entries` | Paginated **summary** view of stored entries (safe to call freely) |
| `get_entry_detail` | Full JSON of one entry — **can be very large, see warnings below** |
| `get_session_trace` | Condensed chronological timeline for one session |

---

## Step-by-step SOP

### Step 1 — Query narrowly
Always filter in CloudWatch before fetching. Never fetch all logs.

```
query_cloudwatch_logs(
    env="stage",
    query="fields @timestamp, @message | filter @message like /SESSION-UUID/ | sort @timestamp asc | limit 50",
    hours=2
)
```

Returns: `"Stored 23 results as 'q1'."`

**Tips:**
- Start with `hours=1` or `hours=2`, widen only if results are empty
- Always filter by session_id or a keyword — never omit the `filter` clause
- `chat_only=True` (default) is almost always what you want

---

### Step 2 — Orient with summaries
Before reading any full entry, browse the summary view:

```
get_log_entries("q1", limit=20)
```

This returns lightweight rows: `index`, `timestamp`, `session_id`, `type`, and type-specific previews (`text_preview`, `token_info`, `tool_calls`, etc.). **Use this to find the interesting indices — do not skip straight to detail.**

Filter by type if needed:
```
get_log_entries("q1", entry_type="stream_response", limit=10)
get_log_entries("q1", entry_type="chat_payload", limit=10)
```

---

### Step 3 — Session timeline (preferred over detail)
If you have a session_id, prefer the trace over reading individual entries:

```
get_session_trace("q1", "your-session-uuid")
```

This gives a condensed view of the full request/response/tool cycle in one call — much cheaper than reading entries one by one.

---

### Step 4 — Drill into a specific entry (with caution)

Only call `get_entry_detail` after you've identified the exact index from Step 2.

```
get_entry_detail("q1", 7)
```

#### Context size warnings by entry type

| Entry type | Typical size | Risk |
|---|---|---|
| `chat_payload` | Small | Safe |
| `stream_response` | Small–medium | Safe |
| `stream_call` | **Very large** | **Dangerous** |

**`stream_call` entries contain the full LangChain message history** — system prompt, all prior turns, all tool results. A single entry can be 5,000–20,000+ tokens. Rules:

- Never call `get_entry_detail` on a `stream_call` entry unless you specifically need the `messages` array
- From `get_log_entries`, `stream_call` rows already expose `token_info` and `tool_names` — check those first
- If you only need to know token counts or which tools were available, the summary from `get_log_entries` is enough

---

## Decision tree

```
Got a session_id?
  └─ Yes → get_session_trace first → done in most cases
  └─ No  → get_log_entries(limit=20) to find the session_id, then trace

Need to see the AI response text?
  └─ get_log_entries(entry_type="stream_response") → text_preview is usually enough
  └─ Need full text → get_entry_detail on that stream_response index (safe)

Need to see token breakdown?
  └─ Already in get_log_entries summary for stream_call rows → no detail needed

Need to see the full conversation history / tool results?
  └─ get_entry_detail on a stream_call index → do this last, expect large output
```

---

## Common patterns

**Check what the user said and what the AI replied:**
```
get_log_entries("q1", entry_type="chat_payload")      # user queries
get_log_entries("q1", entry_type="stream_response")   # AI responses + tool calls
```

**Token cost per turn:**
```
get_log_entries("q1", entry_type="stream_call")
# token_info in each row: {system, tool_def, human, ai, ai_toolcall, tool}
```

**Did the model call any tools?**
```
get_log_entries("q1", entry_type="stream_response")
# check tool_calls field in each row
```

**Multiple sessions in one result set:**
```
get_log_entries("q1", limit=5)                        # spot distinct session_ids
get_session_trace("q1", "session-a-uuid")
get_session_trace("q1", "session-b-uuid")
```

**Paginate large result sets:**
```
get_log_entries("q1", offset=0,  limit=20)
get_log_entries("q1", offset=20, limit=20)
```
