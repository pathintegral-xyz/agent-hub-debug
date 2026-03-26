# Agent Hub Debug

## 运行方式

**不要使用 MCP 工具**。用 `uv run` 调用底层函数（boto3 在本 repo 的 venv 里，不在 agent-runtime venv 里）：

```bash
source .env && uv run python test_query.py
```

或内联：

```bash
source .env && uv run python -c "
import sys
sys.path.insert(0, 'src')
from mcp_server.server import _query_logs

results = _query_logs(
    env='stage',  # 或 'prod'
    query=\"fields @timestamp, @message | filter @message like /关键词/ | sort @timestamp asc | limit 50\",
    hours=24,
    chat_only=True,
    save=True,  # 保存为 logs_<env>_<timestamp>.json
)
"
```

## 注意事项

- **不要用** `source /home/rong/agent-runtime/.venv/bin/activate`，那个 venv 没有 boto3
- `.env` 必须包含 `AWS_DEFAULT_REGION`（不是 `AWS_REGION`），否则会报 missing credential 错误
- `source .env` 必须在 `uv run` 之前执行
- 查对话历史用 `stream self` filter（stream_call 条目含完整 messages 列表）；查模型输出用 `stream response` filter
- session_id / context_id 直接 filter 无效，要用 `@message like /xxx/` 匹配
