---
name: recent-features
description: 当任务涉及创建/更新/维护近期功能实现文档、给功能模块或服务器资源建带代码指针的文档、改完代码同步架构文档、整理文档树分组时使用；不用于写普通说明文档或 AGENTS.md 导航
---

# 近期功能实现文档维护

## 1. 存储与共享

近期功能实现文档存放在 `.governance/logic/<domain>.json`，一个业务域一个文件，随 git 入库并由团队共用。条目指针包含 `file`、`symbol`、`line`、`end`、`role`：机械层由引擎自动同步，叙事层由人或 AI 复核。

命名约定：人读名称统一「近期功能实现」；`govern logic`、`.governance/logic/`、`LOGIC-*` 规则名与插件 tab 标识 `logic` 是机器标识，不改。

域级字段：`overview` 是字符串，写系统边界与组件总览、数据流向、进程/模块分工；`crosscut` 是 `{title, body}` 列表，写并发与锁、超时与预算、开关清单、降级链、观测与门控。条目级字段 `sections` 是 `{title, body}` 列表，写条目细节。

## 2. 树状划分

- 域分两类：**近期功能域**一个功能一个域（如 `kb-folder-upload`），日常最常查，`group` 用 `功能模块/<功能名>`，每域配一条「配额与配置总表」条目（全部配置项、默认值、环境变量覆盖前缀、调优建议）；**基座域**按业务域或运行形态组织（知识库、服务与资源），写长期稳定的底层机制。
- `domain` 是业务域。
- 条目的 `group` 是以斜杠分隔的分组路径，`kind` 是分类。
- `kind` 取值：`module`（功能模块）、`service`（服务与进程）、`resource`（服务器资源）、`api`（接口契约）、`data`（数据与存储）、`task`（任务与调度）。
- `group` 第一层固定使用中文分类名：`功能模块`、`服务与进程`、`服务器资源`、`接口契约`、`数据与存储`、`任务与调度`。
- 第二层起按模块或资源名细分，例如 `功能模块/知识库上传`、`服务器资源/ECS 生产`、`任务与调度/索引进程`。
- 空 `group` 的条目挂在域根下；条目超过 5 个必须分组。

## 3. 写多详细

架构文档要写到「照着能定位、能复现、能排障」的深度，不是逐行复述代码。概述与细节分层：概述留在 `summary`、`narrative` 与域级 `overview`，细节一律进 `sections` 与 `crosscut`。

### 3.1 条目 `sections`

- 至少 3 个小节。近期功能域条目优先用「功能逻辑 / 配置与限额 / 边界与异常 / 使用与验证」：功能逻辑写状态机、阶段与分支；配置与限额逐项列配置名、默认值与覆盖方式；边界与异常写上限、拒绝条件与恢复路径；使用与验证写触发方式与单测方法。基座域条目从「处理流程 / 核心机制 / 数据与契约 / 并发与事务 / 配置与限额 / 失败与降级 / 观测与测试 / 部署与资源」中按实际形态选取。不适用的不写。
- 每个小节 `body` 至少 3 句，且必须含可核查细节：机制名、关键分支条件、配置项与默认值、数据结构/表名/队列名、超时与重试次数、systemd 指令或端口。
- 禁止「负责处理 XX」式空话；没有对应代码的机制不写。
- `summary` 与 `narrative` 仍是概述，`narrative` 不写成大段墙。

### 3.2 条目 `pointers`

- 每条目 2–5 个指针，`role` 区分 `入口` / `核心` / `下游` / `配置` / `测试`。
- 每个指针对应真实存在的代码位置；找不到代码对应的机制就不要写。

### 3.3 域级 `overview` 与 `crosscut`

- `overview` 写系统边界与组件总览、数据流向、进程/模块分工，5–8 句。
- `crosscut` 按 `title`/`body` 分节写：并发与锁、超时与预算、开关清单、降级链、观测与门控。

### 3.4 表述纪律

- 只陈述当前状态，不写修订痕迹、改动历史或待办计划。
- 只写配置项名与引用方式，严禁密钥或凭证原文。

## 4. 服务器资源条目

- 指针指向 `deploy/systemd` unit、`docker-compose.yml`、边缘/nginx 配置、`.env.example` 等真实文件。
- `sections` 写进程角色、端口、资源限额、依赖与启停方式；`narrative` 只留概述与取舍。
- 严禁写密钥或凭证原文，只写配置项名与引用方式。

## 5. 创建流程

1. 初始化业务域：

   ```bash
   govern logic init <domain>
   ```

2. 逐条起草条目，机械字段由引擎自动定位：

   ```bash
   govern logic add <domain> <id> --file F [--symbol S] [--group 路径] [--kind 分类]
   ```

3. 补充 `summary`、`narrative`、`flow` 与 `sections`（按第 3 条的深度标准），写为什么这样做及其取舍，不复述代码。
4. 补充域级 `overview` 与 `crosscut`（按第 3.3 条）。
5. 逐条接受并检查：

   ```bash
   govern logic accept <domain> <id>
   govern logic check
   ```

## 6. 更新流程：改完代码的标准动作

1. 运行 `govern logic sync`。
2. 对 `changed` 条目复核叙事与 `sections`，确认仍符合代码后执行 `govern logic accept <domain> <id>`。
3. 只报告 `moved` 的条目无需复核。
4. 对 `broken` 条目先修指针：文件改名或符号重命名后更新 pointer，再运行 `govern logic sync`。
5. 新增模块在同一提交建条目；删除模块在同一提交删条目。

## 7. 维护流程

- 用 `govern logic list` 查看全量条目与状态。
- 用 `govern logic check [--strict]` 执行门控。
- `LOGIC-BROKEN`（error）立即修复；`LOGIC-STALE`（warning）复核叙事后 accept。引擎按实时内容判定，行号移动不会误报。
- 条目与代码改动进入同一个 git 提交。
- 合并冲突后运行 `govern logic sync` 和 `govern logic check`。

## 8. 验收清单

- [ ] `govern logic list` 无 broken。
- [ ] stale 条目全部复核。
- [ ] 指针可定位到真实代码行。
- [ ] JSON 可解析。
- [ ] 分组符合第 2 条。
- [ ] 每个条目 `sections` 至少 3 个小节，每小节至少 3 句且含可核查细节。
- [ ] 每个条目 `pointers` 2–5 个且 role 区分 入口/核心/下游/配置/测试。
- [ ] 域 `overview` 5–8 句，`crosscut` 覆盖并发与锁/超时与预算/开关清单/降级链/观测与门控。
- [ ] `summary`/`narrative` 是概述而非细节堆。
- [ ] 文档无密钥或凭证原文。

## 9. 反模式

- `narrative` 复述代码或写成大段墙。
- `sections` 少于 3 个小节，或用「负责处理 XX」式空话充数。
- 写没有代码对应的机制或凭空给出配置默认值。
- 指针写绝对路径，或 `pointers` 少于 2 个。
- 整域平铺、超过 5 个条目仍不分组。
- 把密钥或凭证原文写进文档。
- 未复核叙事就 accept。

## 10. 在 OpenCode 中查看

插件第 5 个 tab「近期功能实现」（键 `5`）可查看树与状态，并可点击指针跳转到代码。
