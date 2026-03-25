"""
Agent Hub Debug MCP Server

Provides CloudWatch log querying tools for the txyz backend.
AWS credentials are read from environment variables.
"""
import csv
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    host="127.0.0.1", port="8081",
    # `host` and `port` will not work for stdio transport
)

LOG_GROUPS: Dict[str, str] = {
    "stage": "/copilot/txyz-stage-anyon-api",
    "prod": "/copilot/txyz-prod-anyon-api",
}


def get_aws_credentials() -> Dict[str, str]:
    required = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"]
    credentials = {}
    for key in required:
        value = os.getenv(key)
        if not value:
            raise ValueError(
                f"Missing required AWS credential: {key}. "
                f"Please set environment variables: {', '.join(required)}"
            )
        credentials[key] = value
    session_token = os.getenv("AWS_SESSION_TOKEN")
    if session_token:
        credentials["AWS_SESSION_TOKEN"] = session_token
    return credentials


def get_log_group(env: str) -> str:
    if env not in LOG_GROUPS:
        raise ValueError(f"Invalid environment: {env}. Must be one of: {', '.join(LOG_GROUPS.keys())}")
    return LOG_GROUPS[env]


def _parse_chat_payload(message: str) -> dict:
    """Parse 'Chat json payload: {...}' via ast.literal_eval."""
    import ast
    try:
        return ast.literal_eval(message[len("Chat json payload: "):])
    except Exception as e:
        return {"parse_error": str(e)}


def _parse_stream_call(message: str) -> dict:
    """Parse 'stream self.provider=X self.model=Y [messages] ...kwargs'"""
    import ast, re
    m = re.match(r"stream self\.provider=(\S+)\s+self\.model=(\S+)\s+(.*)", message, re.DOTALL)
    if not m:
        return {}
    provider, model, rest = m.groups()
    result: dict = {"provider": provider, "model": model}

    # Split off the messages list from the trailing kwargs
    msgs_str = re.split(r"\s+tool_schemas=", rest, maxsplit=1)[0]
    try:
        result["messages"] = ast.literal_eval(msgs_str)
    except Exception:
        result["messages_raw"] = msgs_str  # store as-is if truncated

    # Trailing kwargs: token_info, temperature, tool_choice, tool_names
    ti = re.search(r"token_info=(\{[^}]+\})", rest)
    if ti:
        try:
            result["token_info"] = ast.literal_eval(ti.group(1))
        except Exception:
            pass

    for key, pat in [("temperature", r"temperature=([\d.]+)"), ("tool_choice", r"tool_choice='([^']*)'")]:
        km = re.search(pat, rest)
        if km:
            result[key] = km.group(1)

    ts = re.search(r"tool_schemas=(\[.*?\])\s+tool_choice", rest, re.DOTALL)
    if ts:
        try:
            schemas = ast.literal_eval(ts.group(1))
            result["tool_names"] = [s.get("function", {}).get("name") for s in schemas if "function" in s]
        except Exception:
            pass

    return result


def _parse_stream_response(message: str) -> dict:
    """Parse 'stream response {...} token_info={...}' - a LangChain AI message dict."""
    import ast, re
    rest = message[len("stream response "):]

    # Split off trailing token_info kwarg
    ti_match = re.search(r"\s+token_info=(\{[^}]+\})$", rest)
    token_info = None
    if ti_match:
        try:
            token_info = ast.literal_eval(ti_match.group(1))
        except Exception:
            pass
        rest = rest[:ti_match.start()]

    try:
        result = ast.literal_eval(rest)
        if token_info:
            result["token_info"] = token_info
        return result
    except Exception as e:
        return {"parse_error": str(e)}


def _parse_log_entry(raw_message, timestamp: str) -> dict:
    """Parse a raw loguru JSON @message into a flat, readable dict."""
    try:
        msg = json.loads(raw_message) if isinstance(raw_message, str) else raw_message
        record = msg.get("record", {})
        extra = record.get("extra", {})
        exc = record.get("exception")
        message = record.get("message", "")

        entry = {
            "timestamp": timestamp,
            "level": record.get("level", {}).get("name", ""),
            "message": message,
            "module": record.get("name", ""),
            "session_id": extra.get("session_id", ""),
            "user_id": extra.get("user_id", ""),
            "context_id": extra.get("context_id", ""),
            "request_path": extra.get("request_path", ""),
            "status_code": extra.get("status_code", ""),
            "process_time_ms": extra.get("process_time", ""),
            "exception": str(exc) if exc else "",
        }

        if message.startswith("Chat json payload:"):
            entry["chat_payload"] = _parse_chat_payload(message)
        elif message.startswith("stream self.provider="):
            entry["stream_call"] = _parse_stream_call(message)
        elif message.startswith("stream response "):
            entry["stream_response"] = _parse_stream_response(message)

        return entry
    except (json.JSONDecodeError, AttributeError):
        return {"timestamp": timestamp, "message": raw_message}


def _query_logs(
    env: str,
    query: str,
    minutes: Optional[int] = None,
    hours: Optional[int] = None,
    days: Optional[int] = None,
    weeks: Optional[int] = None,
    save: bool = False,
    chat_only: bool = True,
) -> List[dict]:
    """
    Core CloudWatch query helper. Returns a list of parsed log entry dicts.

    Args:
        env: 'stage' or 'prod'
        query: CloudWatch Logs Insights query string
        minutes/hours/days/weeks: time range (default: 1 day)
        save: if True, saves raw + parsed results to a timestamped JSON file

    Returns:
        List of parsed log entry dicts, or a list with a single error dict.
    """
    try:
        import boto3
    except ImportError:
        return [{"error": "boto3 not installed. Install with: uv add boto3"}]

    try:
        credentials = get_aws_credentials()
    except ValueError as e:
        return [{"error": str(e)}]

    for key, value in credentials.items():
        if not os.getenv(key):
            os.environ[key] = value

    os.environ.pop("AWS_PROFILE", None)
    os.environ.pop("AWS_DEFAULT_PROFILE", None)

    try:
        log_group = get_log_group(env)
    except ValueError as e:
        return [{"error": str(e)}]

    if minutes:
        start_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    elif hours:
        start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    elif days:
        start_time = datetime.now(timezone.utc) - timedelta(days=days)
    elif weeks:
        start_time = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    else:
        start_time = datetime.now(timezone.utc) - timedelta(days=1)

    end_time = datetime.now(timezone.utc)

    try:
        client = boto3.client('logs')
        response = client.start_query(
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query,
        )
    except Exception as e:
        return [{"error": f"Failed to start query: {e}"}]

    query_id = response['queryId']

    for _ in range(30):
        time.sleep(2)
        try:
            result = client.get_query_results(queryId=query_id)
        except Exception as e:
            return [{"error": f"Failed to get results: {e}"}]

        if result['status'] == 'Complete':
            raw_entries = []
            for row in result['results']:
                entry = {f['field']: f['value'] for f in row if f['field'] != '@ptr'}
                if entry and '@message' in entry:
                    try:
                        entry['@message'] = json.loads(entry['@message'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if entry:
                    raw_entries.append(entry)

            parsed = [
                _parse_log_entry(e.get("@message", ""), e.get("@timestamp", ""))
                for e in raw_entries
            ]

            if chat_only:
                parsed = [
                    e for e in parsed
                    if e.get("message", "").startswith("Chat json payload:")
                    or e.get("message", "").startswith("stream self.provider=")
                    or e.get("message", "").startswith("stream response ")
                ]

            if save:
                filename = f"logs_{env}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump({"query": query, "entries": parsed}, f, indent=2, ensure_ascii=False)
                logger.info(f"Saved to {filename}")

            return parsed

        elif result['status'] == 'Failed':
            return [{"error": "CloudWatch query failed"}]

    return [{"error": f"Query timed out. Query ID: {query_id}"}]


@mcp.tool()
def generate_uuid(count: int = 1) -> str:
    """Generate UUID strings.

    Args:
        count: Number of UUIDs to generate (default: 1)
    """
    return "\n".join(str(uuid.uuid4()) for _ in range(max(count, 0)))


@mcp.tool()
def query_cloudwatch_logs(
    env: str,
    query: str,
    minutes: Optional[int] = None,
    hours: Optional[int] = None,
    days: Optional[int] = None,
    weeks: Optional[int] = None,
    chat_only: bool = True,
) -> str:
    """Query CloudWatch Logs and return parsed results as CSV.

    AWS credentials must be set: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    AWS_DEFAULT_REGION (and AWS_SESSION_TOKEN for temporary credentials).

    Args:
        env: Environment — 'stage' or 'prod'
        query: CloudWatch Logs Insights query string
        minutes: Query logs from last N minutes
        hours: Query logs from last N hours
        days: Query logs from last N days (default if no range given)
        weeks: Query logs from last N weeks

    Returns:
        CSV of parsed log fields: timestamp, level, message, session_id,
        request_path, status_code, process_time_ms, exception
    """
    entries = _query_logs(env, query, minutes=minutes, hours=hours, days=days, weeks=weeks, chat_only=chat_only)

    if not entries:
        return "Query returned no results."
    if "error" in entries[0]:
        return f"Error: {entries[0]['error']}"

    output = StringIO()
    fields = list(entries[0].keys())
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(entries)
    return f"Found {len(entries)} results.\n\n{output.getvalue()}"


def main():
    logger.info('Starting agent-hub-debug MCP server')
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
