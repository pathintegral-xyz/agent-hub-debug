# Agent Hub Debug

## 运行方式

**不要使用 MCP 工具**。直接用 `uv run` 或 `.venv/bin/python` 调用底层函数：

```bash
source .env && .venv/bin/python -c "
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

AWS 凭证从 `.env` 读取，用 `source .env` 加载。
