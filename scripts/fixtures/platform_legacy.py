"""不依赖 SDK 的旧协议夹具，可在 discover 时主动关闭以验证新连接回退。"""

import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    if method == "server/discover":
        if os.environ.get("CLOSE_DISCOVER"):
            sys.exit(0)
        result = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32601, "message": "legacy"},
        }
        print(json.dumps(result), flush=True)
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "legacy", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "echo",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                    },
                }
            ]
        }
    elif method == "tools/call":
        value = {
            "message": request.get("params", {}).get("arguments", {}).get("message"),
            "pid": os.getpid(),
        }
        result = {
            "content": [{"type": "text", "text": json.dumps(value)}],
            "structuredContent": value,
        }
    else:
        result = {}
    print(
        json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
        flush=True,
    )
