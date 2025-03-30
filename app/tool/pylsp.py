import asyncio
import os
import json
import socket
import time
import select

from typing import Optional
from app.tool.base import BaseTool, ToolResult

_PYLSP_DESCRIPTION = """
Query a Python Language Server (LSP) for detailed code intelligence such as hover information, definitions, or inferred types within a Python codebase.

Use this tool whenever you need to:
- Understand what a specific symbol (e.g., a function, class, or variable) refers to.
- Find the definition location of a symbol to trace where it's declared or implemented.
- Retrieve type information to understand how data flows through the code.

To use this tool effectively:
1. Read the source file to find the **line number** and **character position** of the exact symbol you're interested in.
2. Provide these values using the `line` and `character` parameters. **Never leave them at zero** or use approximate guesses — the LSP relies on exact positions to return meaningful results.
3. Set `action` to either `"hover"` or `"definition"` depending on whether you want descriptive type info or the source location.

This tool is especially useful for tracing end-to-end features, navigating class hierarchies, and analyzing how different parts of a system interact at the code level.
"""

class LSPClientSession:
    def __init__(self, host="127.0.0.1", port=2087):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self._id = 0

    async def start(self):
        self.sock = socket.create_connection((self.host, self.port))
        self._id = 1
        await self._send("initialize", {
            "processId": None,
            "rootUri": f"file://{os.path.abspath('.')}",
            "capabilities": {},
        })
        await self._wait_for(self._id)
        await self._send("initialized", {})  # notify LSP that client is ready

    async def _send(self, method, params, id=None):
        if not self.sock:
            raise RuntimeError("LSP socket not connected.")
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if id is not None:
            payload["id"] = id
        body = json.dumps(payload)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        self.sock.sendall(header.encode() + body.encode())

    async def _wait_for(self, target_id, timeout=3.0):
        start = time.time()
        while time.time() - start < timeout:
            ready, _, _ = select.select([self.sock], [], [], 0.1)
            if not ready:
                continue
            headers = {}
            line = b""
            while not line.endswith(b"\r\n\r\n"):
                line += self.sock.recv(1)
            for l in line.decode().split("\r\n"):
                if ":" in l:
                    k, v = l.split(":", 1)
                    headers[k.strip()] = v.strip()
            content_length = int(headers.get("Content-Length", 0))
            body = self.sock.recv(content_length)
            message = json.loads(body)
            if message.get("id") == target_id:
                return message
        return None

    async def request(self, method, params):
        self._id += 1
        await self._send(method, params, id=self._id)
        return await self._wait_for(self._id)


class PythonLSPTool(BaseTool):
    name: str = "python_language_server"
    description: str = _PYLSP_DESCRIPTION

    parameters: dict = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Path to the Python file to analyze.",
            },
            "action": {
                "type": "string",
                "enum": ["hover", "definition"],
                "description": "Type of LSP action to perform.",
            },
            "line": {
                "type": "integer",
                "description": "Zero-based line number in the file.",
            },
            "character": {
                "type": "integer",
                "description": "Zero-based character index on the line.",
            },
        },
        "required": ["filepath", "action", "line", "character"],
    }

    _session: Optional[LSPClientSession] = None

    async def execute(
            self, filepath: str, action: str, line: int, character: int
        ) -> ToolResult:
        try:
            if self._session is None:
                self._session = LSPClientSession()
                await self._session.start()

            full_path = os.path.abspath(filepath)
            uri = f"file://{full_path}"
            if not os.path.exists(full_path):
                return ToolResult(error=f"File does not exist: {filepath}")

            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            # send didOpen (needed for hover/definition to work)
            await self._session._send("textDocument/didOpen", {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": 1,
                    "text": content
                }
            })

            await asyncio.sleep(0.3)  # let LSP process the file

            if action == "hover":
                result = await self._session.request("textDocument/hover", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character}
                })
            elif action == "definition":
                result = await self._session.request("textDocument/definition", {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": character}
                })
            else:
                return ToolResult(error=f"Unsupported action: {action}")

            return ToolResult(output=json.dumps(result.get("result", {}), indent=2))

        except Exception as e:
            return ToolResult(error=f"LSP tool error: {str(e)}")

if __name__ == "__main__":
    pylsp = PythonLSPTool()
    rst = asyncio.run(pylsp.execute("./main.py", "definition", 15, 22))
    print(rst)
