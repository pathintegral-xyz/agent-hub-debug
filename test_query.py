import sys
sys.path.insert(0, "src")

from mcp_server.server import _query_logs

entries = _query_logs(
    env="stage",
    query="fields @timestamp, @message | filter @message like /f0a06119-f06c-4f4e-a6dc-e9bb1a215182/ and @message like /stream self/ | sort @timestamp asc | limit 10",
    hours=24,
    save=True,
    chat_only=False,
)

for e in entries:
    print(f"[{e.get('timestamp','')[:19]}] {e.get('level',''):8} | {e.get('message','')[:80]}")
