# MCP stdio 适配器（参考实现）

`server.py` 把 governance-kit 的只读治理能力暴露成 MCP 工具。它**不实现规则**，只做 `tools/call` → `<kit>/engine/govern` 的转发与协议层校验。

## 传输与协议

- stdio，JSON-RPC 2.0，**一行一条消息**；响应按请求 `id` 回写。
- `stdout` 只输出协议消息；启动日志、异常摘要一律走 `stderr`。客户端若把 stderr 混入 stdout，会直接破坏会话，挂载时不要重定向。
- 实现方法：`initialize`、`tools/list`、`tools/call`、`ping`、`shutdown`；`notifications/*` 不响应。
- 错误码：`-32700` 解析失败、`-32600` 非法请求、`-32601` 未知方法、`-32602` 参数错误、`-32603` 内部错误。

## 暴露的工具（只读）

| 工具 | 子命令 | 入参 |
| --- | --- | --- |
| `governance_scan` | `scan` | `repo`（必填，仓库根）、`path`（可选） |
| `governance_board` | `board` | 同上 |
| `governance_map` | `map` | 同上 |
| `governance_health` | `health` | 同上 |

调用形如：

```bash
<kit>/engine/govern --repo /abs/repo --json health
```

`path` 仅在当前引擎版本支持 `--path` 时转发（启动时探测子命令 `--help` 并缓存）；不支持则忽略，避免不同引擎版本导致调用失败。

**没有写工具**：不提供改文件、执行任意命令、提 PR 的能力。治理结论只读，落盘与提交由人或上层流程完成。

## 挂载

### Codex（`~/.codex/config.toml`）

```toml
[mcp_servers.governance]
command = "python3"
args = ["/root/.config/opencode/plugins/governance-kit/adapters/mcp/server.py"]
```

### Trae（`mcp.json` / 设置面板「添加 MCP Server」）

```json
{
  "mcpServers": {
    "governance": {
      "command": "python3",
      "args": ["/root/.config/opencode/plugins/governance-kit/adapters/mcp/server.py"]
    }
  }
}
```

### OpenCode（`opencode.json` / `~/.config/opencode/opencode.json`）

```json
{
  "mcp": {
    "governance": {
      "type": "local",
      "command": ["python3", "/root/.config/opencode/plugins/governance-kit/adapters/mcp/server.py"],
      "enabled": true
    }
  }
}
```

三者的字段名与文件位置随客户端版本演进，以各自当前文档为准；共同点是「无参数启动、靠 stdio 通信」，因此必须写**绝对路径**，且 `python3` 能在客户端进程的 PATH 中解析。容器化客户端需把套件目录挂进容器，否则 `engine/govern` 不存在，工具会返回 `isError=true` 并提示缺引擎，而不会崩溃。

## 自检

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python3 adapters/mcp/server.py

python3 adapters/mcp/server.py --selftest
```

自检覆盖：initialize 的 `protocolVersion`/`capabilities.tools`、tools/list 的 4 个工具名、ping、未知方法 `-32601`、参数缺 `repo` 的 `-32602`、tools/call 的结构化返回（引擎缺失时 `isError=true`）。

## 后续扩展：elicitation 承载人工确认

治理动作里有一类必须人批：提升监管等级、批准新基线、放行破坏性契约变更。MCP 的 elicitation 正是这个通道：

1. 客户端在 `initialize` 的 `capabilities` 里声明 `elicitation`；
2. 服务端以 `elicitation/create` 发一个请求（携带 schema：批准/拒绝 + 理由 + 过期时间），用请求 id 与业务动作关联；
3. 收到 `accept` 才执行动作，`decline`/`cancel` 一律按拒绝处理并记录审计。

**能力缺失时不得自行放行**：未声明 elicitation 的客户端，服务端应返回「需人工确认」的结构化结果（`isError=true` 或明确 `status=NEEDS_APPROVAL`），由人另走审批，而不是把「无法询问」当成「默认同意」。审计记录需包含：动作、仓库、请求摘要、批准人、时间、过期时间。