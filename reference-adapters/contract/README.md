# 契约门控（参考实现）

`contract_gate.py` 对比两份 OpenAPI JSON（old=基线、new=当次生成物），只输出确定性证据：破坏性变更计数与逐条 findings。

## 用法

```bash
python3 contract_gate.py --old openapi.baseline.json --new openapi.json --json
# {"findings":[...],"breaking":2,"non_breaking":1,"engine":"internal"}

# 有 oasdiff 时优先委托（本机无 oasdiff 则内置实现兜底）
which oasdiff && python3 contract_gate.py --old old.json --new new.json
```

退出码：`0` 无破坏性变更、`1` 有 breaking（或 oasdiff 报 breaking）、`2` 文件不可读/非法 JSON（UNABLE）。`--internal` 强制走内置实现，用于离线与自检。

## 为什么必须 `--fail-on ERR`

`oasdiff breaking` 默认只打印报告，**即使存在 ERR 级破坏性变更也以 0 退出**——等于门控永远不拦截。必须显式 `--fail-on ERR`，让 CI 拿到非零退出码。内置实现同样按「有 breaking 即退出 1」执行。

oasdiff 返回非零时本脚本原样透传；oasdiff 返回 0 但内置实现检出 breaking 时仍退出 1（不因委托而清零拦截结论）。两者结论不一致本身是信号：应核对 oasdiff 版本与规范版本（3.0/3.1）后再决定，不允许直接删掉内置判断。

## 覆盖的破坏性变更

- 删除 path、删除 method、删除响应状态码
- 删除 required 字段（含请求体、参数、响应）与疑似改名（同层「一删一增必填」）
- 字段类型收窄/变更（`type` 集合缩小、枚举值删除、`maximum/maxLength/maxItems` 收紧、`minimum/minLength/minItems` 放宽方向相反即收紧、`additionalProperties` 关闭、`nullable` 由 true 变非 true）
- 新增必填参数、参数由可选变必填、新增必填请求体
- 仅记录但不拦截：新增可选字段/参数/路径、类型放宽、format 变化、响应新增必填字段（严格客户端可能失败，交人工判断）

远端 `$ref` 不解析，记 `unresolved_ref` 非拦截提示；需要在 CI 前把外部 schema 内联或拉平。

## 生成物入库 + CI 重生成比对

契约文件是**生成物**：由代码注解/IDL 生成，生成后提交入库，作为 next 比较的基线。

```bash
# CI：重生成后必须与入库版本一致，否则说明代码改了契约却没提交生成物
python3 scripts/gen_openapi.py > openapi.json
git diff --exit-code openapi.json
contract_gate --old "$(git show HEAD:openapi.json)" --new openapi.json  # 或独立 baseline 文件
```

只比对、不重生成，会漏掉「代码已改但生成物未提交」的漂移；只重生成不入库，会失去可比基线。两者都要。

## 契约先行排序

1. 先改契约（`openapi.json`）→ 跑契约门控 → 人评审破坏性变更清单与迁移方案；
2. 再改服务端实现 → 契约测试；
3. 最后改客户端，按同一份契约生成/校对。

门控不通过时，禁止用「放宽判定/降 severity/删 findings」过关；确需破坏性变更，走版本化（v1/v2 并存）或显式豁免记录（豁免需人签字并写明退出日期）。

## 自检

```bash
python3 contract_gate.py --selftest
```

覆盖：删除 required 字段判 breaking、改名提示、同文档 0 breaking、类型收窄、删除 path、新增必填参数、CLI 退出码 1/0/2。退出码 0 表示自检全过。