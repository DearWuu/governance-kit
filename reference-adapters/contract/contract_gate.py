"""契约门控：对比两份 OpenAPI JSON，检出破坏性变更并输出可复核 findings。

上游：CI 在契约变更后调用；PATH 上有 oasdiff 时优先委托并透传其退出码。
下游：编排器读 breaking 计数决定拦截；--json 输出供机读归档。
约束：仅 stdlib；内置实现解析本地 $ref，远端 $ref 记提示而非崩溃。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
MAX_REF_DEPTH = 32


class SpecError(ValueError):
    """OpenAPI 文件不可读或结构非法。"""


def load_spec(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise SpecError(f"{path} 读取失败：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict) or not ("paths" in data or "components" in data):
        raise SpecError(f"{path} 不像 OpenAPI 文档（缺 paths/components）")
    return data


def _pointer(spec: dict, ref: str):
    if not ref.startswith("#/"):
        return None  # 远端/外部 $ref 不解析
    cur = spec
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and token in cur:
            cur = cur[token]
        elif isinstance(cur, list) and token.isdigit() and int(token) < len(cur):
            cur = cur[int(token)]
        else:
            return None
    return cur


def _merge_schema(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, value in extra.items():
        if key == "properties":
            props = dict(merged.get("properties") or {})
            props.update(value or {})
            merged["properties"] = props
        elif key == "required":
            merged["required"] = sorted(set(merged.get("required") or []) | set(value or []))
        elif key not in merged or merged[key] in (None, {}, []):
            merged[key] = value
    return merged


def resolve(spec: dict, node, depth: int = 0) -> dict:
    """展开本地 $ref 与 allOf；无法解析时返回带 __unresolved__ 的占位。"""
    if not isinstance(node, dict):
        return {}
    if depth > MAX_REF_DEPTH:
        return {"__unresolved__": "引用层级过深"}
    if "$ref" in node:
        target = _pointer(spec, str(node["$ref"]))
        if target is None:
            rest = {k: v for k, v in node.items() if k != "$ref"}
            return _merge_schema({"__unresolved__": str(node["$ref"])}, rest)
        node = _merge_schema(resolve(spec, target, depth + 1),
                             {k: v for k, v in node.items() if k != "$ref"})
    if "allOf" in node:
        base = {k: v for k, v in node.items() if k != "allOf"}
        merged = resolve(spec, base, depth + 1)
        for sub in node.get("allOf") or []:
            merged = _merge_schema(merged, resolve(spec, sub, depth + 1))
        return merged
    return node


def _types(schema: dict) -> set:
    typ = schema.get("type")
    if isinstance(typ, str):
        return {typ}
    if isinstance(typ, list):
        return {t for t in typ if isinstance(t, str)}
    return set()


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Findings:
    def __init__(self):
        self.items = []
        self._seen = set()

    def add(self, kind: str, severity: str, location: str, detail: str) -> None:
        key = (kind, location, detail)
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append({
            "kind": kind,
            "severity": severity,
            "location": location,
            "detail": detail,
        })

    def breaking(self) -> int:
        return sum(1 for f in self.items if f["severity"] == "breaking")

    def non_breaking(self) -> int:
        return sum(1 for f in self.items if f["severity"] == "non_breaking")


def _compare_enum(old: dict, new: dict, loc: str, out: Findings) -> None:
    old_vals, new_vals = old.get("enum"), new.get("enum")
    if not isinstance(old_vals, list) or not isinstance(new_vals, list):
        return
    removed = [v for v in old_vals if v not in new_vals]
    added = [v for v in new_vals if v not in old_vals]
    if removed:
        out.add("enum_value_removed", "breaking", loc,
                f"枚举值被删除：{removed}（原 {old_vals} -> 现 {new_vals}）")
    if added:
        out.add("enum_value_added", "non_breaking", loc, f"新增枚举值：{added}")


def _compare_bounds(old: dict, new: dict, loc: str, out: Findings) -> None:
    for key, tighter in (("maximum", lambda o, n: n < o),
                         ("maxLength", lambda o, n: n < o),
                         ("maxItems", lambda o, n: n < o)):
        if _is_number(old.get(key)) and _is_number(new.get(key)):
            if tighter(old[key], new[key]):
                out.add("bound_tightened", "breaking", loc,
                        f"{key} 收紧：{old[key]} -> {new[key]}")
    for key, tighter in (("minimum", lambda o, n: n > o),
                         ("minLength", lambda o, n: n > o),
                         ("minItems", lambda o, n: n > o)):
        if _is_number(old.get(key)) and _is_number(new.get(key)):
            if tighter(old[key], new[key]):
                out.add("bound_tightened", "breaking", loc,
                        f"{key} 收紧：{old[key]} -> {new[key]}")


def compare_schema(old_raw, new_raw, spec_old: dict, spec_new: dict,
                   loc: str, out: Findings, mode: str = "request",
                   depth: int = 0) -> None:
    """递归比较两个 schema；mode=request 时新增必填视作破坏，response 时不算。"""
    if depth > 24:
        return
    old = resolve(spec_old, old_raw, depth)
    new = resolve(spec_new, new_raw, depth)
    if old.get("__unresolved__"):
        out.add("unresolved_ref", "non_breaking", loc,
                f"旧 schema 引用无法解析：{old['__unresolved__']}（未判定）")
        return
    if new.get("__unresolved__"):
        out.add("unresolved_ref", "non_breaking", loc,
                f"新 schema 引用无法解析：{new['__unresolved__']}（未判定）")
        return

    old_types, new_types = _types(old), _types(new)
    if old_types and new_types and old_types != new_types:
        if new_types < old_types:
            kind, sev = "type_narrowed", "breaking"
        elif new_types > old_types:
            kind, sev = "type_widened", "non_breaking"
        else:
            kind, sev = "type_changed", "breaking"
        out.add(kind, sev, loc, f"type {sorted(old_types)} -> {sorted(new_types)}")

    if old.get("nullable") is True and new.get("nullable") is not True:
        out.add("nullable_removed", "breaking", loc, "nullable true -> false/缺省")

    if old.get("format") and new.get("format") and old["format"] != new["format"]:
        out.add("format_changed", "non_breaking", loc,
                f"format {old['format']} -> {new['format']}（需人工确认是否收窄）")

    _compare_enum(old, new, loc, out)
    _compare_bounds(old, new, loc, out)

    if old.get("additionalProperties") is not False and new.get("additionalProperties") is False:
        out.add("additional_properties_closed", "breaking", loc,
                "additionalProperties true/缺省 -> false，客户端多余字段会被拒绝")

    old_props = old.get("properties") or {}
    new_props = new.get("properties") or {}
    old_required = set(old.get("required") or [])
    new_required = set(new.get("required") or [])
    removed_props = [p for p in old_props if p not in new_props]
    added_required = sorted(new_required - old_required)
    renamed_hint = ""
    if len(removed_props) == 1 and len(added_required) == 1:
        renamed_hint = f"；疑似改名 {removed_props[0]} -> {added_required[0]}"

    for name in sorted(removed_props):
        if name in old_required:
            out.add("required_field_removed", "breaking", f"{loc}.{name}",
                    "必填字段被删除/改名" + renamed_hint)
        else:
            out.add("optional_field_removed", "non_breaking", f"{loc}.{name}",
                    "可选字段被删除")

    for name in added_required:
        severity = "breaking" if mode == "request" else "non_breaking"
        detail = "新增必填字段，旧客户端不会提供"
        if mode == "response":
            detail = "响应新增必填字段，严格校验的客户端可能失败（默认不拦截）"
        out.add("new_required_field", severity, f"{loc}.{name}", detail)

    for name in sorted(set(old_props) & set(new_props)):
        compare_schema(old_props[name], new_props[name], spec_old, spec_new,
                       f"{loc}.{name}", out, mode, depth + 1)

    if "items" in old or "items" in new:
        if "items" in old and "items" in new:
            compare_schema(old["items"], new["items"], spec_old, spec_new,
                           f"{loc}[]", out, mode, depth + 1)

    for combiner in ("oneOf", "anyOf"):
        old_variants = old.get(combiner) or []
        new_variants = new.get(combiner) or []
        if not old_variants and not new_variants:
            continue
        if len(new_variants) < len(old_variants):
            out.add("variant_removed", "breaking", loc,
                    f"{combiner} 分支数 {len(old_variants)} -> {len(new_variants)}")
        for idx in range(min(len(old_variants), len(new_variants))):
            compare_schema(old_variants[idx], new_variants[idx], spec_old, spec_new,
                           f"{loc}.{combiner}[{idx}]", out, mode, depth + 1)


def _collect_params(spec: dict, path_item: dict, op: dict) -> dict:
    params = {}
    for raw in list(path_item.get("parameters") or []) + list(op.get("parameters") or []):
        param = resolve(spec, raw)
        name = param.get("name")
        if name:
            params[(name, param.get("in"))] = param
    return params


def _body_schema(spec: dict, op: dict):
    body = op.get("requestBody")
    if not body:
        return None, False
    body = resolve(spec, body)
    if body.get("__unresolved__"):
        return None, bool(body.get("required"))
    content = body.get("content") or {}
    for media in content.values():
        if isinstance(media, dict) and "schema" in media:
            return media["schema"], bool(body.get("required"))
    return None, bool(body.get("required"))


def _response_schemas(spec: dict, op: dict) -> dict:
    result = {}
    for code, response in (op.get("responses") or {}).items():
        response = resolve(spec, response)
        content = response.get("content") or {}
        for media in content.values():
            if isinstance(media, dict) and "schema" in media:
                result[code] = media["schema"]
                break
    return result


def compare_specs(old: dict, new: dict) -> Findings:
    out = Findings()
    old_paths = old.get("paths") or {}
    new_paths = new.get("paths") or {}

    for path in sorted(set(old_paths) - set(new_paths)):
        out.add("path_removed", "breaking", path, "接口路径被删除")
    for path in sorted(set(new_paths) - set(old_paths)):
        out.add("path_added", "non_breaking", path, "新增接口路径")

    for path in sorted(set(old_paths) & set(new_paths)):
        old_item = resolve(old, old_paths[path])
        new_item = resolve(new, new_paths[path])
        for method in HTTP_METHODS:
            if method not in old_item:
                if method in new_item:
                    out.add("method_added", "non_breaking", f"{method.upper()} {path}",
                            "新增方法")
                continue
            loc = f"{method.upper()} {path}"
            if method not in new_item:
                out.add("method_removed", "breaking", loc, "方法被删除")
                continue

            old_op = resolve(old, old_item[method])
            new_op = resolve(new, new_item[method])

            old_params = _collect_params(old, old_item, old_op)
            new_params = _collect_params(new, new_item, new_op)
            for key in sorted(set(old_params) - set(new_params), key=str):
                name, where = key
                needed = old_params[key].get("required") or where == "path"
                out.add("parameter_removed", "breaking" if needed else "non_breaking",
                        f"{loc} parameters[{where}:{name}]",
                        "必填参数被删除" if needed else "可选参数被删除")
            for key in sorted(set(new_params) - set(old_params), key=str):
                name, where = key
                needed = bool(new_params[key].get("required")) or where == "path"
                out.add("new_required_parameter", "breaking" if needed else "non_breaking",
                        f"{loc} parameters[{where}:{name}]",
                        "新增必填参数" if needed else "新增可选参数")
            for key in sorted(set(old_params) & set(new_params), key=str):
                name, where = key
                old_p, new_p = old_params[key], new_params[key]
                ploc = f"{loc} parameters[{where}:{name}]"
                if not (old_p.get("required") or where == "path") and \
                        (new_p.get("required") or where == "path"):
                    out.add("parameter_became_required", "breaking", ploc,
                            "参数由可选变为必填")
                if "schema" in old_p and "schema" in new_p:
                    compare_schema(old_p["schema"], new_p["schema"], old, new,
                                   f"{ploc}.schema", out, "request")

            old_body, old_body_required = _body_schema(old, old_op)
            new_body, new_body_required = _body_schema(new, new_op)
            if old_body is not None and new_body is None:
                out.add("request_body_removed", "breaking", f"{loc} requestBody",
                        "请求体被删除")
            elif old_body is None and new_body is not None:
                sev = "breaking" if new_body_required else "non_breaking"
                out.add("request_body_added", sev, f"{loc} requestBody",
                        "新增必填请求体" if new_body_required else "新增可选请求体")
            elif old_body is not None and new_body is not None:
                compare_schema(old_body, new_body, old, new, f"{loc} requestBody",
                               out, "request")

            old_responses = _response_schemas(old, old_op)
            new_responses = _response_schemas(new, new_op)
            for code in sorted(set(old_responses) - set(new_responses)):
                out.add("response_removed", "breaking", f"{loc} responses[{code}]",
                        "响应状态码/内容被删除")
            for code in sorted(set(old_responses) & set(new_responses)):
                compare_schema(old_responses[code], new_responses[code], old, new,
                               f"{loc} responses[{code}]", out, "response")

    return out


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    for finding in payload.get("findings", []):
        print(f"[{finding['severity']}] {finding['kind']} @ {finding['location']}: "
              f"{finding['detail']}")
    print(f"breaking={payload['breaking']} non_breaking={payload['non_breaking']} "
          f"engine={payload.get('engine', 'internal')}")
    if payload.get("oasdiff_output"):
        print("--- oasdiff ---")
        print(payload["oasdiff_output"].rstrip())


def _run_oasdiff(old_path: str, new_path: str):
    exe = shutil.which("oasdiff")
    if not exe:
        return None
    attempts = [
        [exe, "breaking", "--fail-on", "ERR", "--format", "json", old_path, new_path],
        [exe, "breaking", "--fail-on", "ERR", old_path, new_path],
    ]
    last = None
    for cmd in attempts:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"oasdiff 调用失败：{exc}"
        last = proc
        if proc.returncode in (0, 1):
            return proc, ""
    return last, ""


def _selftest() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str) -> None:
        print(f"[{'PASS' if cond else 'FAIL'}] {name}: {detail}")
        if not cond:
            failures.append(name)

    def spec(required: list, props: dict, path: str = "/users") -> dict:
        return {
            "openapi": "3.0.3",
            "info": {"title": "t", "version": "1"},
            "paths": {path: {"post": {
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"type": "object", "required": required, "properties": props}}}},
                "responses": {"200": {"description": "ok"}},
            }}},
        }

    old = spec(["username"], {"username": {"type": "string"}})
    new = spec(["email"], {"email": {"type": "string"}})
    result = compare_specs(old, new)
    kinds = {f["kind"] for f in result.items}
    check("删除 required 字段 -> breaking",
          "required_field_removed" in kinds and result.breaking() >= 1,
          json.dumps({"breaking": result.breaking(), "kinds": sorted(kinds)}, ensure_ascii=False))
    hint = [f for f in result.items if f["kind"] == "required_field_removed"][0]["detail"]
    check("改名提示", "疑似改名" in hint, hint)

    same = compare_specs(old, old)
    check("完全相同 -> 0 breaking", same.breaking() == 0, f"breaking={same.breaking()}")

    narrowed = compare_specs(
        spec(["username"], {"username": {"type": "string"}}),
        spec(["username"], {"username": {"type": "integer"}}),
    )
    check("类型收窄 -> breaking",
          any(f["kind"] == "type_changed" for f in narrowed.items) and narrowed.breaking() >= 1,
          f"breaking={narrowed.breaking()}")

    removed_path = compare_specs(old, spec(["username"], {"username": {"type": "string"}},
                                           path="/other"))
    check("删除 path -> breaking",
          any(f["kind"] == "path_removed" for f in removed_path.items),
          f"breaking={removed_path.breaking()}")

    with_query = spec(["username"], {"username": {"type": "string"}})
    with_query["paths"]["/users"]["post"]["parameters"] = [
        {"name": "tenant", "in": "query", "required": True, "schema": {"type": "string"}}]
    added_param = compare_specs(old, with_query)
    check("新增必填参数 -> breaking",
          any(f["kind"] == "new_required_parameter" for f in added_param.items),
          f"breaking={added_param.breaking()}")

    tmp = tempfile.mkdtemp(prefix="contract-gate-selftest-")

    def write(name: str, payload: dict) -> str:
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    old_path = write("old.json", old)
    new_path = write("new.json", new)
    same_path = write("same.json", old)
    bad_path = os.path.join(tmp, "bad.json")
    with open(bad_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    cmd = [sys.executable, os.path.abspath(__file__), "--internal", "--json"]

    proc = subprocess.run(cmd + ["--old", old_path, "--new", new_path],
                          capture_output=True, text=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {}
    check("CLI 有 breaking -> exit 1 且输出 JSON",
          proc.returncode == 1 and payload.get("breaking", 0) >= 1,
          f"exit={proc.returncode} stdout={proc.stdout.strip()[:200]}")

    proc_same = subprocess.run(cmd + ["--old", old_path, "--new", same_path],
                               capture_output=True, text=True)
    check("CLI 无 breaking -> exit 0",
          proc_same.returncode == 0 and json.loads(proc_same.stdout)["breaking"] == 0,
          f"exit={proc_same.returncode} stdout={proc_same.stdout.strip()[:200]}")

    proc_bad = subprocess.run(cmd + ["--old", old_path, "--new", bad_path],
                              capture_output=True, text=True)
    check("非法 JSON -> exit 2（UNABLE）",
          proc_bad.returncode == 2 and "UNABLE" in proc_bad.stderr,
          f"exit={proc_bad.returncode} stderr={proc_bad.stderr.strip()[:160]}")

    for name in os.listdir(tmp):
        try:
            os.remove(os.path.join(tmp, name))
        except OSError:
            pass
    try:
        os.rmdir(tmp)
    except OSError:
        pass

    if failures:
        print(f"selftest 失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("selftest 全部通过")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI 契约门控（破坏性变更检测）")
    parser.add_argument("--old", help="旧契约 JSON（基线，入库生成物）")
    parser.add_argument("--new", help="新契约 JSON（当次生成物）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--internal", action="store_true", help="强制使用内置实现")
    parser.add_argument("--selftest", action="store_true", help="内置最小用例自检")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.old or not args.new:
        parser.error("需要 --old 与 --new（或 --selftest）")

    try:
        old_spec = load_spec(args.old)
        new_spec = load_spec(args.new)
    except SpecError as exc:
        print(f"contract_gate: UNABLE: {exc}", file=sys.stderr)
        return 2

    findings = compare_specs(old_spec, new_spec)
    payload = {
        "findings": findings.items,
        "breaking": findings.breaking(),
        "non_breaking": findings.non_breaking(),
        "engine": "internal",
    }

    if not args.internal:
        delegated = _run_oasdiff(args.old, args.new)
        if delegated is not None:
            proc, note = delegated
            payload["engine"] = "oasdiff"
            if proc is not None:
                payload["oasdiff_exit_code"] = proc.returncode
                payload["oasdiff_output"] = (proc.stdout or proc.stderr or "")[:8000]
                if not args.json:
                    print(f"oasdiff exit={proc.returncode}", file=sys.stderr)
                if note:
                    payload["oasdiff_output"] += f"\n{note}"
            else:
                payload["oasdiff_output"] = note
            _emit(payload, args.json)
            # 透传 oasdiff 的非零退出码；同时不允许内置判定为 breaking 时被清零。
            if proc is not None and proc.returncode != 0:
                return proc.returncode
            return 1 if findings.breaking() else 0

    _emit(payload, args.json)
    return 1 if findings.breaking() else 0


if __name__ == "__main__":
    sys.exit(main())