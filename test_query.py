import sys
sys.path.insert(0, "src")

from mcp_server.server import _query_logs

entries = _query_logs(
    env="stage",
    query="fields @timestamp, @message | sort @timestamp asc | limit 50",
    minutes=10,
    save=True,
    chat_only=False,
)

for e in entries:
    print(f"[{e.get('timestamp','')[:19]}] {e.get('level',''):8} | {e.get('message','')[:80]}")
