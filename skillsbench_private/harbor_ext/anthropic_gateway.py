import argparse
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_LOG_DIR = Path(
    os.environ.get(
        "SKILLSBENCH_ANTHROPIC_GATEWAY_LOG_DIR",
        Path(__file__).resolve().parents[2] / "tmp" / "anthropic_gateway_logs",
    )
)
DEFAULT_BACKEND_BASE = "http://127.0.0.1:4010/v1"
DEFAULT_UPSTREAM_BASE = "http://127.0.0.1:4002"
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _flatten_text_blocks(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    parts: list[str] = []
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "\n\n".join(parts)


def _extract_last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        text = _flatten_text_blocks(message.get("content"))
        if text:
            return text
    return ""


def _build_backend_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    system_text = _flatten_text_blocks(payload.get("system"))
    user_text = _extract_last_user_text(payload.get("messages", []))
    tools = payload.get("tools", [])

    system_parts = [
        "You are acting behind an Anthropic-compatible adapter for Claude Code.",
        "Reply with a concise plain-text assistant response only.",
        "Do not emit JSON, XML, markdown code fences, or tool-call syntax in this first-pass adapter.",
    ]
    if system_text:
        system_parts.append("Original system prompt:\n" + system_text)
    if tools:
        tool_names = [tool.get("name", "") for tool in tools if isinstance(tool, dict)]
        system_parts.append("Available tools:\n" + ", ".join(name for name in tool_names if name))

    user_parts = [user_text or "Continue the task."]

    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(re.findall(r"\S+", text)))


def _normalize_backend_model(model: Any, default_model: str) -> str:
    if not isinstance(model, str) or not model:
        return default_model
    if model.startswith("openai/"):
        return model.split("/", 1)[1]
    return model


def _parse_tool_call_line(line: str) -> tuple[str, dict[str, Any]] | None:
    line = line.strip()
    if not line:
        return None
    if line.startswith("<tool_call>"):
        line = line[len("<tool_call>") :].strip()
    if not line or line.startswith("<"):
        return None

    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$", line)
    if not match:
        return None
    name = match.group(1)
    rest = match.group(2).strip().lstrip(",").strip()

    pairs = re.findall(r'(\w+)="([^"]*)"', rest)
    if not pairs:
        return None

    normalized: dict[str, Any] = {}
    for key, value in pairs:
        if key == "path":
            normalized["file_path"] = value
        elif key == "file_path":
            normalized["file_path"] = value
        else:
            normalized[key] = value
    return name, normalized


def _parse_read_path_line(line: str) -> tuple[str, dict[str, Any]] | None:
    line = line.strip()
    if not line or line.startswith("```"):
        return None
    match = re.match(r"^Read\s+(/[^\\s`]+)$", line)
    if not match:
        return None
    return "Read", {"file_path": match.group(1)}


def _extract_content_blocks(
    text: str, available_tools: set[str], request_id: str
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    tool_uses: list[dict[str, Any]] = []

    if "<tool_call>" in text:
        prefix, _, remainder = text.partition("<tool_call>")
        cleaned_prefix = prefix.replace("</think>", "").strip()
        if cleaned_prefix:
            blocks.append({"type": "text", "text": cleaned_prefix})
        for raw_line in remainder.splitlines():
            parsed = _parse_tool_call_line(raw_line)
            if parsed is None:
                parsed = _parse_read_path_line(raw_line)
            if parsed is None:
                continue
            name, tool_input = parsed
            if name not in available_tools:
                continue
            if name != "Read":
                continue
            if "file_path" not in tool_input:
                continue
            tool_uses.append(
                {
                    "type": "tool_use",
                    "id": "",
                    "name": name,
                    "input": tool_input,
                }
            )
    else:
        cleaned = text.replace("</think>", "").strip()
        if cleaned:
            blocks.append({"type": "text", "text": cleaned})

    for index, block in enumerate(tool_uses[:2], start=1):
        block["id"] = f"toolu_{request_id.replace('-', '')}_{index}"
        blocks.append(block)

    if not blocks:
        blocks.append({"type": "text", "text": text.strip() or "I’m ready to help with this task."})

    return blocks


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "AnthropicGateway/0.1"

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> tuple[bytes, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        parsed = None
        if raw:
            parsed = json.loads(raw.decode("utf-8"))
        return raw, parsed

    def _log_request(self, request_id: str, raw_body: bytes, parsed_body: Any) -> None:
        record = {
            "request_id": request_id,
            "timestamp": utc_now(),
            "client_address": self.client_address[0],
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "raw_body": raw_body.decode("utf-8", errors="replace"),
            "json_body": parsed_body,
        }
        log_path = self.server.log_dir / f"{int(time.time() * 1000)}_{request_id}.json"
        log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))

    def _log_response(self, request_id: str, payload: dict[str, Any], suffix: str) -> None:
        log_path = self.server.log_dir / f"{int(time.time() * 1000)}_{request_id}_{suffix}.json"
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def _send_sse_event(self, event: str, data: dict[str, Any]) -> None:
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.wfile.write(("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _feature_payload(self) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        return {
            "feature_gates": {},
            "dynamic_configs": {},
            "layer_configs": {},
            "sdkParams": {},
            "has_updates": False,
            "evaluated_keys": {},
            "time": now_ms,
        }

    def _call_backend(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        backend_url = self.server.backend_base.rstrip("/") + "/chat/completions"
        backend_payload = {
            "model": _normalize_backend_model(payload.get("model"), self.server.default_model),
            "messages": _build_backend_messages(payload),
            "temperature": 0.2,
            "max_tokens": min(int(payload.get("max_tokens") or 512), 1024),
            "stream": False,
        }
        req = urllib.request.Request(
            url=backend_url,
            data=json.dumps(backend_payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with NO_PROXY_OPENER.open(req, timeout=self.server.timeout_sec) as resp:
            response_body = json.loads(resp.read().decode("utf-8"))

        choices = response_body.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        text = message.get("content") or "I’m ready to help with this task."
        if isinstance(text, list):
            text = "\n".join(
                item.get("text", "")
                for item in text
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
        if not isinstance(text, str):
            text = str(text)
        return text.strip() or "I’m ready to help with this task.", response_body

    def _build_anthropic_response(
        self, request_id: str, payload: dict[str, Any], text: str
    ) -> dict[str, Any]:
        input_text = _flatten_text_blocks(payload.get("system")) + "\n" + _extract_last_user_text(
            payload.get("messages", [])
        )
        available_tools = {
            tool.get("name", "")
            for tool in payload.get("tools", [])
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
        content_blocks = _extract_content_blocks(text, available_tools, request_id)
        return {
            "id": f"msg_{request_id.replace('-', '')}",
            "type": "message",
            "role": "assistant",
            "model": payload.get("model") or self.server.default_model,
            "content": content_blocks,
            "stop_reason": "tool_use"
            if any(block.get("type") == "tool_use" for block in content_blocks)
            else "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": _estimate_tokens(input_text),
                "output_tokens": _estimate_tokens(text),
            },
        }

    def _handle_messages(self, request_id: str, parsed_body: Any) -> None:
        if not isinstance(parsed_body, dict):
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"type": "error", "error": {"type": "invalid_request", "message": "JSON body must be an object"}},
            )
            return

        try:
            text, backend_response = self._call_backend(parsed_body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            self._json_response(
                HTTPStatus.BAD_GATEWAY,
                {
                    "type": "error",
                    "error": {
                        "type": "backend_http_error",
                        "message": body or str(exc),
                        "status_code": exc.code,
                        "request_id": request_id,
                    },
                },
            )
            return
        except Exception as exc:  # noqa: BLE001
            self._json_response(
                HTTPStatus.BAD_GATEWAY,
                {
                    "type": "error",
                    "error": {
                        "type": "backend_error",
                        "message": str(exc),
                        "request_id": request_id,
                    },
                },
            )
            return

        response_payload = self._build_anthropic_response(request_id, parsed_body, text)
        self._log_response(request_id, {"backend_response": backend_response}, "backend")
        self._log_response(request_id, response_payload, "response")

        if parsed_body.get("stream"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("x-anthropic-gateway-request-id", request_id)
            self.end_headers()

            message_start = {
                "id": response_payload["id"],
                "type": "message",
                "role": "assistant",
                "model": response_payload["model"],
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": response_payload["usage"]["input_tokens"],
                    "output_tokens": 0,
                },
            }
            self._send_sse_event("message_start", {"type": "message_start", "message": message_start})
            for index, block in enumerate(response_payload["content"]):
                if block["type"] == "text":
                    self._send_sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                    block_text = block.get("text", "")
                    for chunk in [block_text[i : i + 160] for i in range(0, len(block_text), 160)] or [""]:
                        self._send_sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": index,
                                "delta": {"type": "text_delta", "text": chunk},
                            },
                        )
                    self._send_sse_event("content_block_stop", {"type": "content_block_stop", "index": index})
                    continue

                if block["type"] == "tool_use":
                    self._send_sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": {
                                "type": "tool_use",
                                "id": block["id"],
                                "name": block["name"],
                                "input": {},
                            },
                        },
                    )
                    partial_json = json.dumps(block.get("input", {}), ensure_ascii=False)
                    self._send_sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {"type": "input_json_delta", "partial_json": partial_json},
                        },
                    )
                    self._send_sse_event("content_block_stop", {"type": "content_block_stop", "index": index})
            self._send_sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": response_payload["stop_reason"],
                        "stop_sequence": response_payload["stop_sequence"],
                    },
                    "usage": {"output_tokens": response_payload["usage"]["output_tokens"]},
                },
            )
            self._send_sse_event("message_stop", {"type": "message_stop"})
            return

        self._json_response(HTTPStatus.OK, response_payload)

    def _forward(self, request_id: str, raw_body: bytes) -> None:
        upstream_base = self.server.upstream_base
        if upstream_base is None:
            self._json_response(
                HTTPStatus.BAD_GATEWAY,
                {
                    "type": "error",
                    "error": {
                        "type": "gateway_error",
                        "message": "No upstream configured for anthropic gateway",
                        "request_id": request_id,
                    },
                },
            )
            return

        url = upstream_base.rstrip("/") + self.path
        req = urllib.request.Request(url=url, data=raw_body, method=self.command)
        for key, value in self.headers.items():
            if key.lower() == "host":
                continue
            req.add_header(key, value)

        try:
            with NO_PROXY_OPENER.open(req, timeout=self.server.timeout_sec) as resp:
                body = resp.read()
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() in {"transfer-encoding", "connection"}:
                        continue
                    self.send_header(key, value)
                self.send_header("x-anthropic-gateway-request-id", request_id)
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                if key.lower() in {"transfer-encoding", "connection"}:
                    continue
                self.send_header(key, value)
            self.send_header("x-anthropic-gateway-request-id", request_id)
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:  # noqa: BLE001
            self._json_response(
                HTTPStatus.BAD_GATEWAY,
                {
                    "type": "error",
                    "error": {
                        "type": "gateway_error",
                        "message": str(exc),
                        "request_id": request_id,
                    },
                },
            )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/health", "/v1/health"}:
            self._json_response(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "timestamp": utc_now(),
                    "upstream_base": self.server.upstream_base,
                },
            )
            return
        if parsed.path == "/api/tools":
            self._json_response(HTTPStatus.OK, {"tools": []})
            return
        if parsed.path == "/api/config":
            self._json_response(HTTPStatus.OK, {})
            return
        if parsed.path == "/api/features":
            self._json_response(HTTPStatus.OK, {})
            return
        if parsed.path == "/api/organizations":
            self._json_response(HTTPStatus.OK, {"metrics_enabled": False})
            return
        if parsed.path == "/api/agents":
            self._json_response(HTTPStatus.OK, [])
            return
        if parsed.path == "/api/claude_code_penguin_mode":
            self._json_response(
                HTTPStatus.OK,
                {
                    "status": "disabled",
                    "reason": "preference",
                },
            )
            return
        if parsed.path == "/api/claude_code/organizations/metrics_enabled":
            self._json_response(HTTPStatus.OK, {"enabled": False})
            return
        if parsed.path.startswith("/api/features/"):
            self._json_response(HTTPStatus.OK, self._feature_payload())
            return
        if parsed.path == "/v1/models" and self.server.upstream_base is not None:
            self._forward(str(uuid4()), b"")
            return
        self._json_response(
            HTTPStatus.NOT_FOUND,
            {"type": "error", "error": {"type": "not_found", "message": self.path}},
        )

    def do_POST(self) -> None:  # noqa: N802
        request_id = str(uuid4())
        parsed = urllib.parse.urlparse(self.path)
        try:
            try:
                raw_body, parsed_body = self._read_json_body()
            except json.JSONDecodeError as exc:
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "type": "error",
                        "error": {"type": "invalid_json", "message": str(exc)},
                    },
                )
                return

            self._log_request(request_id, raw_body, parsed_body)

            if parsed.path == "/v1/messages":
                if self.server.upstream_base is not None:
                    self._forward(request_id, raw_body)
                    return
                self._handle_messages(request_id, parsed_body)
                return

            if parsed.path == "/v1/complete":
                self._forward(request_id, raw_body)
                return

            if parsed.path.startswith("/api/eval/"):
                self._json_response(HTTPStatus.OK, self._feature_payload())
                return

            if parsed.path == "/api/event_logging/batch":
                self._json_response(HTTPStatus.OK, {"ok": True})
                return

            self._json_response(
                HTTPStatus.NOT_FOUND,
                {"type": "error", "error": {"type": "not_found", "message": self.path}},
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "type": "error",
                    "error": {
                        "type": "internal_gateway_error",
                        "message": str(exc),
                        "request_id": request_id,
                    },
                },
            )


class GatewayServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        log_dir: Path,
        upstream_base: str | None,
        backend_base: str,
        default_model: str,
        timeout_sec: int,
    ) -> None:
        super().__init__(server_address, GatewayHandler)
        self.log_dir = log_dir
        self.upstream_base = upstream_base
        self.backend_base = backend_base
        self.default_model = default_model
        self.timeout_sec = timeout_sec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(os.environ.get("ANTHROPIC_GATEWAY_LOG_DIR", DEFAULT_LOG_DIR)),
    )
    parser.add_argument(
        "--upstream-base",
        default=os.environ.get("ANTHROPIC_GATEWAY_UPSTREAM_BASE", DEFAULT_UPSTREAM_BASE),
    )
    parser.add_argument(
        "--backend-base",
        default=os.environ.get("ANTHROPIC_GATEWAY_BACKEND_BASE", DEFAULT_BACKEND_BASE),
    )
    parser.add_argument(
        "--default-model",
        default=os.environ.get("ANTHROPIC_GATEWAY_DEFAULT_MODEL", "qwen3.5-9b-awq"),
    )
    parser.add_argument("--timeout-sec", type=int, default=120)
    args = parser.parse_args()

    args.log_dir.mkdir(parents=True, exist_ok=True)

    server = GatewayServer(
        (args.host, args.port),
        log_dir=args.log_dir,
        upstream_base=args.upstream_base,
        backend_base=args.backend_base,
        default_model=args.default_model,
        timeout_sec=args.timeout_sec,
    )

    print(
        json.dumps(
            {
                "event": "gateway_start",
                "host": args.host,
                "port": args.port,
                "log_dir": str(args.log_dir),
                "upstream_base": args.upstream_base,
                "backend_base": args.backend_base,
                "default_model": args.default_model,
            }
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
