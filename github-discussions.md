# Discussions 开启步骤 + 置顶 roadmap 帖文案

> 依据：`~/moneymaker/state/exposure/distribution/github-discoverability.md` P0-5
> （在 state 同名备稿基础上完善，入库随仓库走）。GitHub 官方文档：
> <https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/enabling-or-disabling-github-discussions-for-a-repository>
> 现状：Discussions 未开启（2026-11-03 仓库页实测，无 Discussions tab）。

## 1. 开启（零成本，网页操作，无 gh 命令）

Settings → General → Features → 勾选 **Discussions** → 使用默认分类模板
（Announcements / Ideas / Q&A / Show and tell）。

## 2. 首个置顶帖（Announcements，外发链接之前先贴）

发帖后在帖子右侧 `…` 菜单选 **Pin**（置顶最多 4 帖）。

**标题**：Roadmap & known limitations (v1.5.1) — read before opening issues

**正文**：

> What works: enforce-level stop hooks on Claude Code, Codex CLI, Qwen Code,
> Cursor, Copilot CLI, Gemini CLI, OpenCode, Pi, DeepSeek Harness, and Hermes
> Agent — one engine (`detector.py`), one config (`~/.hermes/continue_hook.json`),
> one event log, so the ask-rate is comparable across all of them. 363 tests,
> coverage floor 80%, complexity ceiling 10 — `./scripts/check.sh` runs
> everything CI runs.
>
> Known limitations (honest list):
>
> - Solo-maintained, zero external users so far; tested on one Linux box only
> - Regex detector misses non-English / unusual phrasings — PRs adding trigger
>   patterns are the single highest-value contribution
> - Occasionally blocks a legitimate question (costs one turn)
> - Hermes plugin mode lands the nudge one turn late (patch mode is seamless
>   but modifies the host)
>
> Ideas I'm considering (vote in Ideas):
>
> - [ ] Ship a small curated pack of community trigger/exclusion regexes per language
> - [ ] Windows / macOS validation
> - [ ] A `--report` command summarizing your own question-rate from the event log
>
> If you installed it: reply with your host + one ending the hook caught that it
> shouldn't have (or missed). Those two lists are how the detector gets better.

## 3. Q&A 预置一问一答（发到 Q&A 分类，自己自答）

**问**：How is this different from "keep-going" / infinite-session tools?

**答**：Those continue unconditionally on every stop and burn tokens in loops.
continue-hook triggers only on permission-seeking endings, and instead of
deciding itself, it injects a one-shot self-check so the agent re-examines
whether the question crosses a user-only boundary (money, identity,
publishing, irreversible deletes, user-only facts). Conditional continuation;
the judgment stays with the agent.

## 4. 目的与验证

- 外发流量（HN/Reddit）落地后第一屏即见「活跃维护」信号
- 把「regex 漏检」从差评转化为贡献入口（roadmap 帖明确 PR 方向）
- 验证：仓库页出现 Discussions tab；roadmap 帖带 📌 且为最新活动之一
