"""MCP stdio 适配器：把 governance-kit 的只读治理能力暴露为 MCP 工具。

上游：Codex / Trae / OpenCode 等 MCP 客户端通过 stdio 拉起本进程。
下游：<kit>/engine/govern 子命令；本进程只转发，不实现治理规则、不写仓库。
约束：仅 stdlib；JSON-RPC 2.0 换行分隔；stdout 只放协议消息，日志走 stderr。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "governance-kit-mcp", "version": "0.1.0"}
KIT_ROOT = Path(__file__).resolve().parents[2]
GOVERN = KIT_ROOT / "engine" / "govern"
TIMEOUT_SECONDS = 60

TOOL_SPECS = [
    {
        "name": "governance_scan",
        "command": "scan",
        "description": "扫描仓库治理规则，返回 findings 列表（只读）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库根目录（绝对路径或相对 cwd）"},
                "path": {"type": "string", "description": "可选：限定扫描子路径"},
            },
            "required": ["repo"],
            "additionalProperties": False,
        },
    },
    {
        "name": "governance_board",
        "command": "board",
        "description": "检查进度板/任务状态一致性，返回漂移项（只读）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库根目录（绝对路径或相对 cwd）"},
                "path": {"type": "string", "description": "可选：限定子路径"},
            },
            "required": ["repo"],
            "additionalProperties": False,
        },
    },
    {
        "name": "governance_map",
        "command": "map",
        "description": "校验导航地图与索引双射，返回缺失/多余条目（只读）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库根目录（绝对路径或相对 cwd）"},
                "path": {"type": "string", "description": "可选：限定子路径"},
            },
            "required": ["repo"],
            "additionalProperties": False,
        },
    },
    {
        "name": "governance_health",
        "command": "health",
        "description": "输出治理健康度汇总与门控结论（只读）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库根目录（绝对路径或相对 cwd）"},
                "path": {"type": "string", "description": "可选：限定子路径"},
            },
            "required": ["repo"],
            "additionalProperties": False,
        },
    },
]
TOOLS_BY_NAME = {spec["name"]: spec for spec in TOOL_SPECS}


def _log(message: str) -> None:
    print(f"[governance-mcp] {message}", file=sys.stderr, flush=True)


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(msg_id, result) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id, code: int, message: str, data=None) -> None:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _send({"jsonrpc": "2.0", "id": msg_id, "error": err})


def _tool_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def _govern_argv() -> list:
    """按可执行位/后缀决定调用方式；engine/govern 可能尚未落地。"""
    if not GOVERN.exists():
        raise FileNotFoundError(f"治理引擎不存在：{GOVERN}")
    if os.access(GOVERN, os.X_OK):
        return [str(GOVERN)]
    return [sys.executable, str(GOVERN)]


_PATH_SUPPORT_CACHE: dict = {}


def _engine_supports_path(command: str) -> bool:
    """探测子命令是否支持 --path；引擎版本差异不应让工具调用直接失败。"""
    if command in _PATH_SUPPORT_CACHE:
        return _PATH_SUPPORT_CACHE[command]
    supported = False
    try:
        proc = subprocess.run(
            _govern_argv() + [command, "--help"],
            capture_output=True, text=True, timeout=10, shell=False,
        )
        supported = "--path" in (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        supported = False
    _PATH_SUPPORT_CACHE[command] = supported
    return supported


def call_tool(name: str, arguments: dict) -> dict:
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"未知工具：{name}")
    if not isinstance(arguments, dict):
        raise ValueError("arguments 必须是对象")
    repo_raw = arguments.get("repo")
    if not isinstance(repo_raw, str) or not repo_raw.strip():
        raise ValueError("缺少必填参数 repo")

    repo = Path(repo_raw).expanduser()
    if not repo.is_absolute():
        repo = Path.cwd() / repo
    repo = repo.resolve()
    if not repo.is_dir():
        return _tool_result(f"repo 不是已存在的目录：{repo}", is_error=True)

    try:
        argv = _govern_argv()
    except FileNotFoundError as exc:
        return _tool_result(f"{exc}；请先安装/生成 engine/govern 后再挂载本适配器。", is_error=True)

    argv += ["--repo", str(repo), "--json", spec["command"]]
    path_arg = arguments.get("path")
    if isinstance(path_arg, str) and path_arg.strip() and _engine_supports_path(spec["command"]):
        argv += ["--path", path_arg]

    try:
        proc = subprocess.run(
            argv,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return _tool_result(f"治理引擎超时（>{TIMEOUT_SECONDS}s）：{' '.join(argv)}", is_error=True)
    except OSError as exc:
        return _tool_result(f"治理引擎启动失败：{exc}", is_error=True)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = stdout or stderr or "无输出"
        return _tool_result(
            f"治理引擎退出码 {proc.returncode}：{detail}", is_error=True)
    try:
        json.loads(stdout)
    except json.JSONDecodeError:
        return _tool_result(f"治理引擎未返回合法 JSON：{stdout[:2000]}", is_error=True)
    return _tool_result(stdout)


def handle_message(msg) -> bool:
    """处理一条 JSON-RPC 消息；返回 False 表示要求退出。"""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        _error(None, -32600, "Invalid Request")
        return True
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    if not isinstance(method, str):
        if not is_notification:
            _error(msg_id, -32600, "Invalid Request：缺少 method")
        return True
    if method.startswith("notifications/"):
        return True  # 通知一律不响应
    if is_notification:
        return True  # 其他无 id 消息按通知处理

    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion")
        _result(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (f"governance-kit 只读治理工具（客户端请求协议 {requested or '未声明'}）。"
                             "不接受写操作；需要人工确认的变更请走 elicitation/人工流程。"),
        })
        return True
    if method == "ping":
        _result(msg_id, {})
        return True
    if method == "tools/list":
        _result(msg_id, {"tools": [
            {"name": s["name"], "description": s["description"], "inputSchema": s["inputSchema"]}
            for s in TOOL_SPECS
        ]})
        return True
    if method == "tools/call":
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            _error(msg_id, -32602, "Invalid params")
            return True
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOLS_BY_NAME:
            _error(msg_id, -32602, f"未知工具：{name}")
            return True
        try:
            _result(msg_id, call_tool(name, params.get("arguments") or {}))
        except ValueError as exc:
            _error(msg_id, -32602, str(exc))
        except Exception as exc:  # 兜底：协议层不得因单次调用崩溃
            _log(f"tools/call 内部错误：{exc!r}")
            _error(msg_id, -32603, "Internal error")
        return True
    if method == "shutdown":
        _result(msg_id, {})
        return False
    _error(msg_id, -32601, f"Method not found: {method}")
    return True


def serve() -> int:
    _log(f"started; kit={KIT_ROOT} govern_exists={GOVERN.exists()}")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "Parse error")
            continue
        if isinstance(msg, list):
            for item in msg:
                if not handle_message(item):
                    return 0
            continue
        if not handle_message(msg):
            return 0
    return 0


def _selftest() -> int:
    import tempfile

    failures = []

    def check(name: str, cond: bool, detail: str) -> None:
        print(f"[{'PASS' if cond else 'FAIL'}] {name}: {detail}")
        if not cond:
            failures.append(name)

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
        {"jsonrpc": "2.0", "id": 4, "method": "nonexistent/method", "params": {}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "governance_health", "arguments": {"repo": tempfile.gettempdir()}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "governance_health", "arguments": {}}},
    ]
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        input=payload, capture_output=True, text=True, timeout=120,
    )
    print(f"--- server stderr ---\n{proc.stderr.strip()}")
    responses = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            check("stdout 只含协议消息", False, line[:200])
    by_id = {resp.get("id"): resp for resp in responses}
    print(json.dumps(responses, ensure_ascii=False, indent=2))

    init = by_id.get(1, {}).get("result", {})
    check("initialize 返回 protocolVersion/capabilities/tools",
          bool(init.get("protocolVersion")) and "tools" in (init.get("capabilities") or {}),
          f"protocolVersion={init.get('protocolVersion')}")
    names = sorted(t["name"] for t in by_id.get(2, {}).get("result", {}).get("tools", []))
    expected = sorted(s["name"] for s in TOOL_SPECS)
    check("tools/list 暴露 4 个只读工具", names == expected, str(names))
    check("ping 返回空对象", by_id.get(3, {}).get("result") == {}, str(by_id.get(3)))
    check("未知方法返回 -32601",
          (by_id.get(4, {}).get("error") or {}).get("code") == -32601,
          str(by_id.get(4)))
    call = by_id.get(5, {}).get("result", {})
    check("tools/call 返回结构化结果（引擎缺失时 isError=true）",
          isinstance(call.get("isError"), bool) and bool(call.get("content")),
          str(call)[:300])
    missing_repo = by_id.get(6, {}).get("error") or {}
    check("tools/call 缺 repo -> -32602", missing_repo.get("code") == -32602,
          str(missing_repo))

    if failures:
        print(f"selftest 失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("selftest 全部通过")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return _selftest()
    return serve()


if __name__ == "__main__":
    sys.exit(main())