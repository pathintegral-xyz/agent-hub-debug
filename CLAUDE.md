# Agent Hub Debug — Local Usage Guide

This tool queries CloudWatch logs for the txyz anyon-api backend and parses them into structured Python objects. Use it locally via Python to investigate sessions, LLM calls, and AI responses.

## Setup

```bash
source .env          # loads AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_DEFAULT_REGION
uv run python test_query.py
```

AWS credentials are temporary (SSO). Re-run `aws sso login` and update `.env` when they expire.

## Core Function

```python
import sys; sys.path.insert(0, "src")
from mcp_server.server import _query_logs

entries = _query_logs(
    env="stage",           # "stage" or "prod"
    query="fields @timestamp, @message | filter @message like /PATTERN/ | sort @timestamp asc | limit 50",
    minutes=30,            # or hours=, days=, weeks=
    save=True,             # saves to logs_stage_YYYYMMDD_HHMMSS.json
    chat_only=True,        # default True — filters to the 3 meaningful log types
)
```

**`chat_only=True`** keeps only 3 entry types (see below). Set to `False` to see all logs (HTTP requests, health checks, etc.).

**Query tip:** always filter in CloudWatch before fetching — `filter @message like /session-uuid/` or `filter @message like /你的关键词/`. Do not fetch all logs and filter locally.

## Log Entry Types

Each entry in `entries` is a dict with base fields plus a type-specific parsed sub-dict.

### Base fields (all entries)

| Field | Description |
|-------|-------------|
| `timestamp` | CloudWatch timestamp string |
| `level` | `INFO`, `SUCCESS`, `ERROR`, etc. |
| `message` | Raw log message string |
| `module` | Logger name (e.g. `app.api.v1`, `twin.common.llm`) |
| `session_id` | Session UUID (may be empty for some log lines) |
| `context_id` | Short request context ID (e.g. `TLP3RQL574`) |
| `exception` | Exception string if present |

---

### Type 1: `chat_payload` — Incoming user request

Triggered when: `message.startswith("Chat json payload:")`

```python
entry["chat_payload"] = {
    "query": "你起床了吗？",       # user's input text
    "type": "query",               # always "query" so far
    "file_ids": [],                # attached file UUIDs (often empty)
    "client_tool": {
        "functions": [             # tools available to the agent from the client side
            {
                "name": "send_self_qrcode",
                "description": "...",
                "parameters": {
                    "type": "object",
                    "properties": { "message": { "type": "string", "description": "..." } }
                    # "required": [...] — present only when params are required
                }
            }
        ]
    }
}
```

**What to look for:**
- `query` — what the user actually said
- `client_tool.functions[*].name` — which client-side tools were available for this request

---

### Type 2: `stream_call` — LLM invocation (outgoing request to model)

Triggered when: `message.startswith("stream self.provider=")`

```python
entry["stream_call"] = {
    "provider": "GCP",
    "model": "GEMINI_3_FLASH",
    "temperature": "0.0",
    "tool_choice": "auto",
    "tool_names": ["canvas_modify", "knowledge_read", "txyz_search_web", ...],

    "token_info": {          # token breakdown of the prompt by role
        "system": 16600,     # system prompt tokens
        "tool_def": 2116,    # tool schema tokens
        "human": 398,        # human message tokens
        "ai": 329,           # prior AI turn tokens
        "ai_toolcall": 545,  # prior tool call tokens
        "tool": 9404         # tool result tokens
    },

    # Full LangChain conversation history (when not truncated by CloudWatch):
    "messages": [
        {
            "type": "system",
            "content": [{"type": "text", "text": "...system prompt..."}],
            "additional_kwargs": {}, "response_metadata": {}, "name": null, "id": null
        },
        {
            "type": "human",
            "content": [
                {"type": "text", "text": "====2026-03-24T18:48:43+08:00====\n"},
                {"type": "media", "mime_type": "audio/mpeg", "data": "[15744 bytes of audio/mpeg]"},
                {"type": "text", "text": ""}
            ],
            "additional_kwargs": {
                "audio_file_ids": ["d65ae712-..."],
                "content_times": {"0": "==2026-03-24T18:48:43+08:00=="}
            },
            "id": "4673f7c2-..."
        },
        {
            "type": "ai",
            "content": [
                {"type": "thinking", "thinking": "...", "index": 0},
                {"type": "text", "text": "...", "index": 1, "extras": {"signature": "CiQ..."}}
            ],
            "tool_calls": [
                {"name": "knowledge_read", "args": {"mode": "RAG", "payload": {"query": "..."}},
                 "id": "3de42377-...", "type": "tool_call"}
            ],
            "usage_metadata": {
                "input_tokens": 13310, "output_tokens": 185, "total_tokens": 13495,
                "input_token_details": {"cache_read": 0},
                "output_token_details": {"reasoning": 169}
            },
            "response_metadata": {
                "finish_reason": "STOP", "model_name": "gemini-3-flash-preview",
                "model_provider": "google_genai", "safety_ratings": []
            },
            "additional_kwargs": {
                "agent_name": "guomai-agent",
                "content_times": {"0": "==...=="}
            }
        },
        {
            "type": "tool",
            "content": [
                {"type": "text", "text": "<file_name=已解决问题.csv,chunk_id=10/>"},
                {"type": "text", "text": "| row data... |"}
            ],
            "tool_call_id": "3de42377-...",   # matches the ai message tool_call id
            "status": "success",
            "artifact": null
        }
    ],

    # When system prompt is too large, CloudWatch truncates the log.
    # In that case, messages is absent and messages_raw contains the raw string:
    # "messages_raw": "[{'content': [{'type': 'text', 'text': 'If there is...total 20731 characters...'}...]"
}
```

**What to look for:**
- `token_info` — where tokens are being spent (tool results dominate? system prompt huge?)
- `messages` — full conversation history including tool calls and results
- `tool_names` — what tools the agent had access to
- `messages_raw` present → system prompt was too large, CloudWatch truncated the log

**Message role sequence** typically looks like:
`system → human → ai → tool → ai → tool → ai → human → ai → ...`

---

### Type 3: `stream_response` — LLM response (model output)

Triggered when: `message.startswith("stream response ")`

```python
entry["stream_response"] = {
    "type": "ai",
    "content": [
        {
            "type": "thinking",
            "thinking": "**Evaluating Context**\n\nI'm currently processing...",
            "index": 0
        },
        {
            "type": "text",
            "text": "<text>早起来了</text>\n<text>都下午两点多了，刚开完会</text>",
            "index": 1,
            "extras": {"signature": "CiQBjz1rX0FJb..."}
        }
    ],
    "tool_calls": [],           # non-empty when model invokes a tool
    "invalid_tool_calls": [],

    "usage_metadata": {         # token cost of THIS turn
        "input_tokens": 23825,
        "output_tokens": 468,
        "total_tokens": 24293,
        "input_token_details": {"cache_read": 0},
        "output_token_details": {"reasoning": 443}  # reasoning tokens within output
    },

    "token_info": {             # full conversation token breakdown (same as stream_call)
        "system": 16600, "tool_def": 2116, "human": 398,
        "ai": 329, "ai_toolcall": 545, "tool": 9404
    },

    "response_metadata": {
        "finish_reason": "STOP",           # or "LENGTH", "CONTENT_FILTER"
        "model_name": "gemini-3-flash-preview",
        "model_provider": "google_genai",
        "safety_ratings": []
    },
    "additional_kwargs": {},
    "name": null,
    "id": null
}
```

**What to look for:**
- `content` — the actual AI response text (in `<text>` tags) and thinking process
- `usage_metadata.output_token_details.reasoning` — how many tokens went to thinking vs actual output
- `tool_calls` — if non-empty, the model is invoking a tool (next entry will be `stream_call` with tool result)
- `response_metadata.finish_reason` — anything other than `STOP` indicates a problem

---

## Common Queries

```python
# Find all activity for a session
_query_logs("stage", "fields @timestamp, @message | filter @message like /SESSION-UUID/ | sort @timestamp asc | limit 50", weeks=1, save=True)

# Find errors
_query_logs("stage", "fields @timestamp, @message | filter @message like /ERROR/ | sort @timestamp desc | limit 50", hours=24, chat_only=False)

# Find a specific user message
_query_logs("stage", "fields @timestamp, @message | filter @message like /用户的关键词/ | sort @timestamp asc | limit 20", hours=1)
```

## Working with Saved Files

```python
import json

d = json.load(open("logs_stage_20260325_142604.json", encoding="utf-8"))
entries = d["entries"]

# Get all LLM calls
calls = [e for e in entries if "stream_call" in e]

# Get token breakdown per turn
for e in calls:
    print(e["timestamp"], e["stream_call"]["token_info"])

# Get all AI responses with their text
responses = [e for e in entries if "stream_response" in e]
for e in responses:
    texts = [b["text"] for b in e["stream_response"]["content"] if b["type"] == "text"]
    print(e["timestamp"], texts)

# Check for tool usage
for e in responses:
    tc = e["stream_response"].get("tool_calls", [])
    if tc:
        print(e["timestamp"], "called:", [t["name"] for t in tc])
```
