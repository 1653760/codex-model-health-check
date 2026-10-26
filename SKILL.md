---
name: codex-model-health-check
description: 对 Codex 配置的模型 API 做两步体检：第一步单 token 分布指纹认证（内置 32 模型参考库，判定声明模型与实际基座是否一致），第二步自动评分的能力体检（推理、算术、代码理解、结构化输出、上下文检索）。用户要求模型体检、验证套壳/换壳、检查模型能力或怀疑降智时使用。不虚构真实度百分比，无基线不报降智百分比。
compatibility: Codex or another agent with a local terminal; Python 3.10+; API-key authentication. Node.js is needed only for skills CLI installation.
metadata:
  version: "1.1.0"
---

# Codex model health check (two-step)

Run the bundled script, rather than answering test questions yourself. Work relative to this installed skill directory; do not assume the user's current directory contains the scripts. Never modify the user's Codex configuration.

## 1. Prepare

Use the local terminal. Install dependencies only if needed:

```text
python -m pip install -r <SKILL_DIR>/requirements.txt
python <SKILL_DIR>/scripts/codex_check.py --self-test
python <SKILL_DIR>/scripts/codex_check.py --inspect
```

Replace `<SKILL_DIR>` with this skill's actual installed directory and quote paths containing spaces. Inspection makes no model requests. Do not read or print config/auth files into agent context: let the script inspect locally and output only redacted fields.

It supports `CODEX_HOME` or `~/.codex/config.toml`, provider `env_key`, optional API-key auth.json, and `chat`/`responses` wire APIs. Use `--profile` or `--model` when the user confirms a session override. If this session cannot be mapped to that configuration, describe the target as the configured API endpoint, not necessarily the current conversational model instance.

If only OAuth subscription login is present or custom authentication headers are needed, stop and explain the unsupported setup. Never extract OAuth tokens or ask the user to paste a secret into chat. Let the user set credentials locally in their terminal.

## 2. Obtain request-budget consent

Explain the default two-step run makes about **144 model requests** (step 1 fingerprint: 20 cells × 6 reps = 120 single-token probes; step 2 capability: 24 tasks), may incur API fees, and is a small diagnostic rather than a full benchmark. Follow a budget already explicitly authorized by the user; otherwise ask before the paid run. Do not repeatedly rerun after failures without approval.

Cheaper option if offered by the user: `--reps 3` (60 fingerprint requests), or `--skip-identity` for capability-only (24 requests).

## 3. Execute

```text
python <SKILL_DIR>/scripts/codex_check.py --run --output <USER_WORKSPACE>/model-health-results
```

Use a writable workspace output directory, not the installation directory.

- **Step 1 — fingerprint identity check**: the script sends temperature-1.0 single-token probes to the endpoint, aggregates answer distributions locally, and compares against the bundled 32-model reference library (qwen / gpt / claude / gemini / glm / grok families) built from the project's own relay-station collections. Verdicts: 一致 / 不一致 / 灰区 / 库外 (declared model not in library → family attribution only; 库外 ≠ fake model). When identity is 一致/灰区, a behavioral deviation test (per-model calibrated p-value) also runs — report it as an exploratory shift signal, never as proven capability loss.
- **Step 2 — capability health check**: 24 randomly generated tasks with automatic ground truth across arithmetic, deduction, exact/JSON instructions, code tracing, multi-step state tracking, and short-context retrieval.

Task instances are randomly generated; a fixed `--seed` reproduces tasks. `--repeats 2/3` increases capability requests and must fit the approved budget. Do not claim coverage of advanced coding, deep research, tools, or long-context windows. Test prompts (and nothing else) are sent directly to the configured endpoint.

## 4. Read and explain results

Read `<USER_WORKSPACE>/model-health-results/体检报告.md` and `体检结果.json`.

Reply briefly in the user's language:

1. Requested model and sanitized provider/interface; mention session routing ambiguity.
2. **Step 1**: identity verdict, nearest reference model with family and distance, thresholds; behavioral deviation verdict if present. The reference library uses relay-station labels, not officially certified weights — matching is evidence, not authentication. Do not report any "authenticity percentage".
3. **Step 2**: total pass rate, six category scores, measurement coverage and API-error count.
4. **Degradation: not estimable without a trustworthy baseline.** Never turn `100 - score` into a degradation percentage, and never report distribution drift as proven capability loss.
5. Output paths and caveats. Low coverage, HTTP errors (e.g. 401/403 model-access denial), truncation and protocol incompatibility must be reported separately from ordinary incorrect answers.

No keys, full base URLs, auth.json contents or user conversation data belong in reports. All analysis runs locally; no telemetry or third-party grading service is used by the script. The skills installer itself has its own telemetry policy.
