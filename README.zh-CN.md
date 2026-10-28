# continue-hook

**别让你的 agent 每一轮都以一个它自己就能回答的问题收尾。**

> "Do you want me to continue?" · "Want me to run the tests?" · "要我顺便把这个也做了吗？" (Chinese)

你的 agent 把活干完了——然后还要问一句，能不能顺手做下一件显而易见的事。它把一个
自己本来就能做的决定丢给你，你却要花一整个 turn 回一个词。忙起来的一天，这就是几十次
白白浪费的往返。

continue-hook 会抓住这种结尾，让 agent 重新自查一遍，然后放手让它把活干完。

**别再问了，直接把活干完。**

```text
Claude Code · Codex CLI · Qwen Code · Cursor · GitHub Copilot CLI ·
Gemini CLI · OpenCode · Pi · DeepSeek Harness · Hermes Agent
```

它从不替 agent 做决定：检测层只负责指出*这条回复看起来是以一句提议收尾的*；这个
问句该不该问，最终还是 agent 自己说了算。

> **这跟权限确认框不是一回事。** Claude Code 的 `⏵ Allow Bash(…)` 对话框问的是
> *工具权限*，这个插件完全不碰它。这里拦的是 agent 反过来问*你*要不要继续干。

[![ci](https://github.com/ttxs69/hermes-continue-hook/actions/workflows/ci.yml/badge.svg)](https://github.com/ttxs69/hermes-continue-hook/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/@ttxs69/hermes-continue-hook)](https://www.npmjs.com/package/@ttxs69/hermes-continue-hook)
[![license: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English](README.md)

## 为什么要做这个

agent 经常用一个它自己就能回答的问题来收尾：

> “要不要我把这个写进 skill？”

用户只好再回一句“是”，去批准一件 agent 本来就有权做的事。每一轮都是一次白白浪费的
往返——忙起来的一天，就是几十次。

现成的“继续跑”类工具（`keep-going`、`infinite-claude-code-session`）解决的是另一个
问题：**无脑续跑**——永不停手，把 token 一直烧下去。这个插件做的是**有条件续跑**：
只在那些求许可的结尾才触发，而*“这个问题该不该问”*的判断权仍然留在 agent 手里。

| | 无脑续跑（`keep-going`） | continue-hook |
|---|---|---|
| 触发时机 | 每次停止 | 只在求许可的结尾 |
| 判断 | 没有——一直续 | 由 agent 复查边界 |
| 何时会停 | （永不停） | 真正只能由用户拍板的边界 |
| 成本 | 循环时持续烧 token | 多一个 turn，而且只在真的触发时 |

## ⚠️ 选模式之前先读这段

插件本身改不了 Hermes core，所以在 **plugin mode** 下它会*晚一个 turn*才生效：

```
You:    Tighten the README install steps
Bot:    Want me to also tighten the examples?   ← turn 1 ends; the question slips out
Bot:    [auto-continue] Done: README install steps tightened + examples tightened.
        Verification: lint passed.             ← turn 2, seconds later
```

这条 nudge 会以一条新的 user 角色消息注入**同一个 session**（完整对话历史都还在），
从而开启一个后续的 turn，让 agent 自己把活干完。功能上等价——但你会看到**两条消息
而不是一条**，而且那句多余的问句会留在聊天记录里。

**能不能投递取决于 host，其中一种情况目前还不支持。**

| Session | 能否投递？ |
|---|---|
| CLI / TUI / desktop | 能——nudge 会进本地输入队列 |
| 消息网关（Telegram、QQ、Discord …） | **不能。** `on_session_end` 拿到的是 `session_id`、`task_id`、`turn_id`、`completed`、`failed`、`interrupted`、`turn_exit_reason`、`model` 和 `platform`——既没有持久化的复合 `session_key`，也没有网关注入所需的 `origin` 映射。硬造一个 key 只会指向一个并不存在的 session。 |

在网关上 hook 依然会评估、会记录日志，并写下 `inject_unsupported_gateway`，这样这个
缺口在 `/ch-stats` 里看得见，而不是变成一次悄悄消失、无处可去的 nudge。**想在消息
网关上做到同一个 turn 内续跑，请用 [patch mode](patch-mode/README.md)**——它在 turn
内部运行，不需要任何注入。

注入是有特权门槛的，所以 Hermes 也会按插件逐个放行；自建的网关部署如果确实能传
`session_key`，需要：

```yaml
plugins:
  entries:
    continue-hook:
      allow_gateway_injection: true
```

想要那种无缝体验（闸门在 agent 循环*内部*触发，那个问题根本不会被投递出来，你只会
看到做完的结果），请用 **patch mode**——见下面的[模式](#modes)。

## 安装

```bash
# every host on this machine
npx @ttxs69/hermes-continue-hook install --host all

# one host
npx @ttxs69/hermes-continue-hook install --host claude-code
```

如果装到 Hermes Agent 自己：

```bash
hermes plugins install ttxs69/hermes-continue-hook
hermes plugins enable continue-hook
```

然后配置（可选——默认值直接能用）：

```bash
cat > ~/.hermes/continue_hook.json <<'JSON'
{
  "enabled": true,
  "dry_run": false,
  "max_nudges_per_turn": 2,
  "max_nudges_per_session": 20
}
JSON
```

配置改动会热重载——**不需要重启**。文件格式写坏了会退回默认值（它不可能把网关搞崩）。

## 快速开始（30 秒）

```bash
npx @ttxs69/hermes-continue-hook install --host all
# then ask an agent for something trivial:
#   "Tighten the README install steps"
# and watch whether it asks permission for things it could just do.
```

看看它都看见了什么：

```bash
cat ~/.hermes/continue-hook-events.jsonl | tail -5
```

在 Hermes 里，同样的数字可以用 `/ch-stats` 查看，而 `/ch-test Want me to continue?`
能对任意一句话跑一次检测器的 dry-run。

## 不等真实对话，先验证它确实能用

“丢给 agent 一句话然后祈祷”不算测试。一条命令就能复现整条链路：同一个检测器、同一个
决策服务、host 收到的同一份 JSON——其中一条消息*必须*被 nudge，另一条*必须不*被 nudge。

发布的包里就带着 adapter，所以**在你装任何东西之前**就能跑：

```bash
npm pack @ttxs69/hermes-continue-hook --silent   # → ttxs69-hermes-continue-hook-<version>.tgz
tar xzf ttxs69-hermes-continue-hook-*.tgz

# 1. the trigger fires on a permission-seeking ending
printf '%s' '{"last_assistant_message":"好的，已经改完了。\n\n要不要我顺便把示例也补上？"}' \
  | python3 package/adapters/claude-code/stop_hook.py

# 2. a clean delivery is left alone
printf '%s' '{"last_assistant_message":"已完成 README 优化。验证：lint 通过。"}' \
  | python3 package/adapters/claude-code/stop_hook.py
```

预期输出——用例 1 打印一个 JSON 对象，要求 agent 重新自查一遍；用例 2 什么都不打印：

```json
{"decision": "block", "reason": "【continue-hook】Your last reply ended by asking the user a question.\nSelf-check once, per standing discipline:\n1. Does what you asked touch a user-only boundary …"}
```

如果你已经装过了，同一套检查可以对着已安装的副本跑：

```bash
printf '%s' '{"last_assistant_message":"要不要我继续？"}' \
  | python3 ~/.hermes/continue-hook/claude-code/stop_hook.py
```

hook 只是*让 agent 再看一眼*。那个问题该不该问，依然由 agent 定夺——这正是整个设计。

如果用例 1 什么都没打印，说明这个 host 上没装 hook。跑一下
`npx @ttxs69/hermes-continue-hook check` 就知道哪些 host 已经接好了。

## 实际跑起来是什么样

在真实机器上跑满 60 次评估的实测数据（来自插件自己的事件日志
`~/.hermes/continue-hook-events.jsonl`）：

| | |
|---|---|
| 评估次数 | 60 |
| nudge 次数 | 32（占评估次数的 53%） |
| 最常见的触发 | `trigger:offer`——32 次 nudge 里有 31 次 |
| 跳过 | `no_trigger` 14、`cooldown` 9、`stop_hook_active` 3、`boundary` 2 |

53% 的命中率说明 agent 确实差不多每隔一轮就会用一个求许可的问句收尾——这正是这个
工具要解决的问题。真正重要的是那些 `boundary` 跳过：那些是该问的时候问了。

## 它是怎么工作的

```
agent turn ends with "…Want me to continue?"
   ↓ detect (configurable: offer / hand-off / confirm-seek / bare ？)
   ↓ exclude: money, identity, publishing, destructive actions, real blockers
   ↓ nudge the agent to self-check:
        touches a user-only boundary?  → re-ask in ONE short line, then stop
        missing info only the user has? → re-ask in ONE short line, then stop
        otherwise                       → DO IT, then report result + verification + risks
```

检测只是一个**传输层**，没有任何判断力：它只决定*要不要考虑 nudge*。被拦下的那个
问题该不该问，是在 nudge 里面由 agent 决定的。匹配规则就是普通的可配置正则——不调
模型，没有延迟。

## 配置

`~/.hermes/continue_hook.json`（所有键都是可选的；缺失的键回落到默认值）：

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `dry_run` | `false` | 只记日志的试运行：记录*本来会*触发什么，什么都不注入 |
| `max_nudges_per_turn` | `2` | 一个 turn 内允许的 nudge 次数（turn 干净结束时会重置） |
| `max_nudges_per_session` | `20` | 给那些没有 host 侧上限的 host 兜底（DeepSeek Harness、Cursor） |
| `cooldown_seconds` | `0`（关闭） | 可选：同一个 session 内两次 nudge 之间的最小间隔 |
| `tail_chars` | `240` | 只检查结尾的 N 个字符 |
| `triggers.offer` | — | 第一人称提议：`要不要我`, `需要我…做吗`, `should i`, `want me to` |
| `triggers.hand_off` | — | 决策移交：`你定`, `听你的`, `your call` |
| `triggers.confirm_seek` | — | 求确认：`这样可以吗`, `does this work` |
| `triggers.question_mark` | `true` | 结尾部分出现任意 `？/?` |
| `exclusions.boundary` | — | 只有用户能拍板的边界：金钱、身份、凭据、发布、破坏性操作 |
| `exclusions.user_must` | — | 第二人称请求：只有用户能提供或决定的事 |

正则想加就加——就是普通的 Python 正则字符串。触发匹配之前会先剥掉被引号包住的片段
（在报告里引用一个模式算是*提到*，不是*发问*），而排除规则仍然扫描原文——这是更安全
的方向。

## 支持的 agent

分三层，因为各家 agent 允许你做的事确实不一样：

| Agent | 层级 | 机制 | 续跑信号 |
|---|---|---|---|
| **Claude Code** | enforce | `Stop` hook，`~/.claude/settings.json` | `{"decision":"block","reason":…}` |
| **Codex CLI** | enforce | `Stop` hook，`~/.codex/hooks.json` | 同上 |
| **Qwen Code** | enforce | `Stop` hook，`~/.qwen/settings.json`（兼容 Claude） | 同上 |
| **Cursor** | enforce | `stop` hook，`~/.cursor/hooks.json` | `{"followup_message":…}` |
| **GitHub Copilot CLI** | enforce | `agentStop` hook，`~/.copilot/hooks/` | `{"decision":"block","reason":…}` |
| **Gemini CLI** | enforce | `AfterAgent` hook，`~/.gemini/settings.json` | `{"decision":"deny","reason":…}` |
| **OpenCode** | enforce | JS plugin，`session.idle` 事件 | `client.session.prompt(...)` |
| **Pi** | enforce | TS extension，`agent_before_settle` | `return { continue: true }` |
| **DeepSeek Harness (dsh)** | enforce | 原生 Cordis plugin，`agent/turn-stopping` | `agent.steer(createUserMessage(...))` |
| **Hermes Agent** | enforce | 原生 plugin（本仓库的起点） | `inject_message` |
| Kimi Code, Antigravity, Devin CLI, Factory Droid, Grok Build, Muse, Windsurf, Aider, Continue, Cline, Roo, goose, Zed | behavior | `SKILL.md`（Agent Skills 标准）+ `AGENTS.md` | ——（是规则，不是强制续跑） |

**enforce** = host 可以拦下 turn 的结束，强制再发一次模型请求。
**behavior** = agent 读到这条规则后，自己就不再问了。

所有 enforce 层的 agent 共用同一个检测引擎（`detector.py`）、同一份配置
（`~/.hermes/continue_hook.json`）和同一份事件日志，所以它们的提问率可以直接横向比较。

### 安装

```bash
npx @ttxs69/hermes-continue-hook install --host all      # every host on this machine
npx @ttxs69/hermes-continue-hook install --host claude-code
npx hermes-continue-hook check                    # what is installed
npx hermes-continue-hook uninstall --host all     # remove (keeps your other hooks)
```

或者从 clone 装：

```bash
node bin/continue-hook.js install --host all
```

安装器是幂等的（装两次也不会产生重复的 hook），而且不破坏现场（你其他的 hook 和配置项
在安装、卸载之后都还在）。`--dry-run` 只展示会改动什么，不会真的动任何文件。

按 host 分开看：

| Host | 安装 | 额外步骤 |
|---|---|---|
| Claude Code | `--host claude-code` | —— |
| Codex CLI | `--host codex` | 在 CLI 里跑一次 `/hooks`，信任这个 hook |
| Qwen Code | `--host qwen` | —— |
| Cursor | `--host cursor` | —— |
| Copilot CLI | `--host copilot` | —— |
| Gemini CLI | `--host gemini` | —— |
| OpenCode | `--host opencode` | plugin 装到 `~/.config/opencode/plugin/` |
| Pi | `--host pi` | extension 装到 `~/.pi/agent/extensions/` |
| DeepSeek Harness | `--host dsh` | `npm install && npm run build`，然后 `dsh plugin add <dir>` |

有 manifest 格式的那些 host，其 plugin marketplace 直接读本仓库里的文件。已有：Claude Code
（`.claude-plugin/`）、Codex（`.codex-plugin/`）、Cursor（`.cursor-plugin/`）、Kimi
（`.kimi-plugin/`）、Antigravity（`.antigravity-plugin/`）、Devin（`.devin-plugin/`）、
Muse（`.muse-plugin/`）、Gemini（`gemini-extension.json`），以及跨 host 的
`.agents/plugins/marketplace.json`。Copilot CLI、Grok Build、Factory Droid 和 Windsurf
没有 manifest 格式——用 `--host copilot`（或者可移植的 skill 层）装，这才是真正能用的
做法：`.claude-plugin/`、`.codex-plugin/`、`.cursor-plugin/`、`.kimi-plugin/`、
`.antigravity-plugin/`、`.devin-plugin/`、`.muse-plugin/`、
`.agents/plugins/marketplace.json`、`gemini-extension.json`。

### 验证某个 host 真的会触发

```bash
printf '%s' '{"session_id":"t","stop_hook_active":false,"last_assistant_message":"要不要我顺便也把示例补上？"}' \
  | python3 ~/.hermes/continue-hook/claude-code/stop_hook.py
# → {"decision": "block", "reason": "【continue-hook】…"}
```

同样的 payload 结构对 Codex 和 Qwen 也适用（`last_assistant_message`）；Cursor 要换成
`transcript_path`；Gemini 要 `prompt_response`。

### Patch mode 锁版本——用之前先读这段

Patch mode 会改动 **Hermes core 的源码树**。这让它成为最难维护的一种模式：patch 是
照着某一个 Hermes 版本写的，目标对不上时它会**拒绝安装**（`git apply --check` 失败，
或者版本守卫找不到那条 stop-gate 链）。拒绝安装是预期行为，不是 bug——改用 plugin mode。

它放进去的那个文件（`patch-mode/continue_hook.py`）由 `scripts/build-patch-mode.py` 从
共享引擎**生成**，所以永远不会变成第二份、逐渐走样的规则实现。文件一过期，构建就会失败。

## 文档

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —— 各层是怎么拼起来的，以及为什么策略要集中在一处
* [docs/CONFIGURATION.md](docs/CONFIGURATION.md) —— 每个可调项及其默认值
* [docs/DESIGN.md](docs/DESIGN.md) —— 为 Hermes plugin 评估过的十二条路线，以及每一条被否掉的原因
* [docs/PUBLISHING.md](docs/PUBLISHING.md) —— 一次发布如何到达 npm（trusted publishing / OIDC，以及首次发布时的分阶段发布路径）

## 测试

```bash
python3 -m unittest discover -s tests   # 191 tests: detector, plugin, 8 adapters, dsh/pi contracts
npx vitest run                          # 68 tests: OpenCode plugin, installer, lifecycle invariants, dsh
bash adapters/dsh/typecheck.sh          # compiles the dsh plugin against @deepseek-ai/dsh-*
```

dsh 那套测试需要访问 npm registry（它会真的装 dsh 包）；其余都能离线跑。

两套测试也都能通过 `npm test` 跑，而整套闸门——linter、类型检查、两套测试、打包——
则通过 `./scripts/check.sh` 跑。


<a id="modes"></a>

## 模式

| 模式 | 同一 turn 内无缝 | 不改 core | 安装 |
|---|---|---|---|
| **Plugin（默认）** | ✗（后续一个 turn，上下文完整） | ✅ | `hermes plugins install` |
| **Patch mode** —— 一道 core 内的 stop gate；锁版本，见下面的注意事项 | ✅ | ✗ | `patch-mode/install.sh <hermes-agent-path>` |
| **Preventive layer** —— 追加到用户消息末尾的常驻提醒（可选 `hooks.pre_llm_call`） | 预防 | ✅ | 把 `agent-hooks/ch_preventive.sh` 复制到 `~/.hermes/agent-hooks/` 并注册 |
| 上游 PR（让 `pre_verify` 在所有文本停止时都触发） | ✅ | 合并后 ✅ | 路线图 |

Patch mode 会动 **四个 core 文件**（三个改、一个新增），并锁定在 core commit `d923a5e`。
`patch-mode/install.sh --check <path>` 会告诉你你的代码树是不是它能打 patch 的；不是的话
安装器直接拒绝，而不是把一个过期的 diff 半吊子地打上去。

针对这张表评估过十二种方案；被否掉的那些以及逐条核实过的理由，都写在
[`docs/DESIGN.md`](docs/DESIGN.md) 里。

## 安全性

- 每个 turn 的 nudge 上限（默认 2）和每个 session 的上限（默认 20）。cooldown
  **默认关闭**——一旦设成非零，第一次 nudge 之后 hook 会悄无声息地停上好几分钟。
- **每个 turn 的上限是天花板，不是承诺。** Claude Code、Codex、Qwen、Cursor、Copilot
  和 Gemini 都会回报“这次 stop 你已经续过了”，hook 在同一个 stop 周期里不会拦第二次——
  所以在这六个 host 上，不管上限设多高，一个 turn 也只会得到**一次** nudge。那些在进程
  内跑插件的 host（Hermes），或者走自己扩展 API 的（OpenCode、Pi、dsh），才能达到配置的
  数量。
- 边界排除会在任何触发之前短路：金钱 / 身份 / 凭据 / 发布 / 破坏性操作一律重新问你，
  绝不自动执行
- 急停开关：配置 `enabled: false`，或者环境变量 `HERMES_CONTINUE_HOOK=0`
- 失败即安全：插件绝不会把异常抛进网关——配置坏了或者 hook 挂了，退化成“不 nudge”，而不是
  搞坏一个 session

## 可观测性

每一次评估——包括跳过的，这样分母才存在——都会追加到
`~/.hermes/continue-hook-events.jsonl`：

```json
{"ts":"2026-10-28 01:03:31","session":"s1","platform":"cli","decision":"skip","reason":"no_trigger","attempt":0,"snippet":"…"}
```

`decision` 的取值是 `nudge` | `skip` | `nudge_dryrun` | `skip_dryrun` | `error`（两个
`_dryrun` 取值出现在配置了 `dry_run: true` 时）；`reason` 说明命中的是哪条规则
（`trigger:offer`、`boundary`、`user_must`、`cooldown`、`max_nudges`、`chain_closed`……）。

**隐私：** 每条记录都会带上回复最后约 160 个字符作为 `snippet`，这样你能看到为什么做出
这个判断。那是 agent 的输出，不是用户输入，但它仍然是对话内容——日志文件以 `0600`
写入，目录在本工具创建时以 `0700` 创建。把 `"log_snippet": false` 设为只记录决策不记录
文本，或者把 `"events_path"` 指向一个你自己控制的目录。

## 开发

```bash
npm install               # dev tooling: eslint, prettier, tsc, vitest, typescript-eslint
./scripts/check.sh        # everything CI runs, one command
```

`npm install` 还会装上两个 git hook（husky）：`pre-commit` 对你暂存的文件跑快速检查
（约 2 秒），`pre-push` 跑整套闸门（约 60 秒）——这样和 CI 对不上时是在 push 之前而不是
之后暴露。参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

用 `.nvmrc` 里那个**精确的** node 版本（`nvm use`）。闸门和 hooks 比的是完整版本号，
对不上就拒绝运行。大版本前缀其实是个范围，而同一个范围在不同地方解析出来的结果不一样：
`.nvmrc` 里写的是 `24`，CI 解析成了 24.21.0，而这台机器跑的是 24.22.1。跑在不同 node
上的检查，预测不了 CI 的结果。

### 质量闸门

每一行是一项工作，以及本仓库为它选用的工具。Python 和 TypeScript 两个生态用不同的
二进制解决同样的四个问题；这张表的意义在于没有哪一行是缺的。

| 项目 | Python | TypeScript / JS | 配置文件 | 运行时机 |
|---|---|---|---|---|
| **Lint** | ruff `check` | **eslint**（flat config、typescript-eslint） | `pyproject.toml`、`eslint.config.mjs` | 每次 push |
| **复杂度** | ruff `C901`（上限 **10**） | eslint `complexity`（上限 **10**） | `pyproject.toml`、`eslint.config.mjs` | 每次 push |
| **格式化** | ruff `format` | **prettier** | `pyproject.toml`、`.prettierrc.json` | 每次 push |
| **类型** | **mypy** | **tsc**——TS 源码用 `strict`，JS 源码用 `checkJs` | `pyproject.toml`、`tsconfig.json`、`tsconfig.checkjs.json` | 每次 push |
| **测试** | **pytest** | **vitest** | `pyproject.toml`、`vitest.config.mjs` | 每次 push |
| **覆盖率下限** | pytest-cov（`fail_under = 80`） | vitest v8（`lines 80`） | 同样的文件 | 每次 push |
| **零依赖运行** | 标准库 `unittest` | —— | —— | 3.10–3.13 |

**复杂度上限不是摆设。** 在这道闸门出现之前，发布代码里最复杂的函数是 47（安装器的
`main`），Python 那边最复杂的是 17（插件入口点）——每一个都是决策表、解析逻辑，或者
一串分支写成的按类型分发。修法是“函数式内核、命令式外壳”的拆分：*决策*被挪进一个只吃
数据只吐数据的纯函数，*外壳*只管 I/O。`parseArgs` 现在抛 `UsageError`，而不是调用
`process.exit`；`configShapeProblem` 现在是回答一个问题，而不是就地做校验；安装器的
分类型工作变成三个小函数加一个分发，而不是一个 47 分支的函数。现在剩下的最高复杂度是 9。

两道上限各有一个专门的测试（`tests/test_complexity_gate.py`）：它断言配置的阈值、证明
一个故意写得太复杂的函数*确实*会被报出来，再拿真实文件跑一遍真实的检查——一个不可能
失败的闸门不算闸门。

Python 那边 `ruff` 一个二进制身兼两职（lint 和 format）；JS 那边这是两个工具，eslint
和 prettier。`mypy` 没有一个不靠类型标注就能用的 JS 等价物，所以
`tsconfig.checkjs.json` 故意用 `noImplicitAny: false` 运行：它抓的是真正流出去过的那些
bug——一个并不存在的属性、一个被当 `Error` 读的 `unknown` 错误——同时不会要求给两千行
纯 ESM 补 JSDoc。TS 源码（`adapters/pi`、`adapters/dsh`）则在完整的 `strict` 下检查。

**覆盖率数字覆盖了什么、没覆盖什么。** Python 那套测试会 import 自己的模块，所以它的
79% 是发布代码的真实语句覆盖率（`hookctl` 94%、`detector` 84%、`__init__` 76%、
`_common` 69%、patch mode 71%）。JS 侧的覆盖率下限只作用于 OpenCode 插件（语句覆盖
82%），因为它的测试会 import 它。安装器**不在**这个数字里：它的测试是在一个一次性的
`HOME` 里以子进程方式驱动真实 CLI——对于一个全部工作就是改别人文件的程序，这是唯一
忠实的测法——而测试进程里的 V8 覆盖率看不见子进程。它的证据是行为上的：33 个安装器测试
和 15 条生命周期不变量，全部对着真实二进制跑。把它算成 0% 只是朝另一个方向的谎，所以
它被排除，而不是被平均进去。

`ruff format` 会跳过 `patch-mode/continue_hook.py`——它由
`scripts/build-patch-mode.py` 生成，所以空格排布跟的是拼装结果，不是格式化器。它照样
会被 lint。`prettier` 出于同样的原因跳过 Markdown：文档是手写、靠肉眼审的，重排表格只会
制造无谓的改动，不是真正的整洁。

```bash
python -m unittest discover -s tests     # 191 tests, no dependencies
python -m pytest --cov                   # same suite + coverage floor
npx vitest run --coverage                # 68 tests + coverage floor
npx eslint . && npx prettier --check '**/*.{js,mjs,ts,json,yml}'
npx tsc -p tsconfig.json && npx tsc -p tsconfig.checkjs.json
```


## 文档

- [`docs/DESIGN.md`](docs/DESIGN.md) —— 架构调研、评估过的 12 种方案、自己用下来的结论
- [`CHANGELOG.md`](CHANGELOG.md) —— 更新日志
- [`CONTRIBUTING.md`](CONTRIBUTING.md) —— 如何新增模式与测试

## 许可证

MIT——见 [LICENSE](LICENSE)。
