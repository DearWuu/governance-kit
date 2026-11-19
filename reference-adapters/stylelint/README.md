# stylelint 样式硬门控（参考实现）

本目录是**参考配置**，不是开箱即用配置：`governance-kit` 只提供门控口径，样式规则由各项目在本仓库内实例化。配置生效依赖项目自行安装 `stylelint`；未安装时该门控应判为 UNABLE，不得视为通过。

## 门控口径

| 规则 | 目的 |
| --- | --- |
| `declaration-property-value-disallowed-list` | color / spacing / font-size / radius / shadow 只允许 `var(--*)`、`var(--obg-*)` 或 `0`、`auto`、`inherit`、`none`、`transparent`、`currentColor`、`calc()/min()/max()/clamp()/color-mix()` 等不含字面量的值 |
| `declaration-no-important` | 禁止用 `!important` 绕过级联与 token |
| `selector-class-pattern` | 类名必须落在 `obg-` / `app-` / `ui-` 前缀域内，小写 kebab |
| `selector-max-specificity` | 单条规则选择器特异性不超过 `0,3,0` |
| `no-descending-specificity` | 禁止后出现的低特异性选择器覆盖高特异性声明 |

需要 `--obg-*` token 体系时，在项目主题里先定义 `--obg-*`，再让业务样式只引用 token；本配置不负责 token 定义。

## 安装与运行

```bash
# 项目根目录执行；锁版本并提交 lockfile，避免门控随上游漂移
npm i -D stylelint stylelint-config-standard

# 单次门控：退出码非 0 即失败
npx stylelint "src/**/*.{css,scss,vue}" \
  --config reference-adapters/stylelint/.stylelintrc.json \
  --max-warnings=0

# CI 机读：输出 JSON 交给编排器归一化
npx stylelint "src/**/*.{css,scss}" \
  --config reference-adapters/stylelint/.stylelintrc.json \
  --formatter json > stylelint-report.json
```

`--max-warnings=0` 必须带：stylelint 默认 warnings 不影响退出码，等于不拦截。规则里的 `defaultSeverity` 已是 `error`，两者同时保留是为了防止项目级覆盖把 severity 降级。

## 可选：强制 token 的插件写法

若希望「值必须以 `var(--*)` 开头」表达得更直接（含对 shorthand、`!important` 的独立处理），可安装 `stylelint-declaration-strict-value`，用下列片段替换 `declaration-property-value-disallowed-list`：

```bash
npm i -D stylelint-declaration-strict-value
```

```json
{
  "plugins": ["stylelint-declaration-strict-value"],
  "rules": {
    "scale-unlimited/declaration-strict-value": [
      ["color", "background-color", "border-color", "font-size", "margin", "padding", "gap", "border-radius", "box-shadow"],
      { "ignoreValues": ["0", "auto", "inherit", "initial", "unset", "none", "transparent", "currentColor"], "ignoreFunctions": ["var", "calc", "min", "max", "clamp", "color-mix"] }
    ]
  }
}
```

插件缺失时 stylelint 会直接报错退出非 0，这属于 UNABLE 而非 PASS，编排器需按「工具不可用」记录。

## 治理边界

- 本配置属于 harness 资产，由人审批后入库；agent **不得**为让门控通过而修改规则、下调 `defaultSeverity`、加 `stylelint-disable` 或改写 `ignoreFiles`。
- 规则变更是门控口径变更：必须单独提交、说明放宽理由，并同步评测基线；不得夹带在业务改动里。
- 门控只给「有没有违规」的确定性证据，不评价视觉好坏；复杂样式取舍由人评审。