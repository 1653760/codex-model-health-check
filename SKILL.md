---
name: codex-model-health-check
description: 对 Codex 配置的模型 API 做本地能力体检，测试推理、算术、代码理解、结构化输出与上下文检索，生成报告。用户要求模型体检、检查模型能力或怀疑降智时使用。不用于证明模型真实身份或在无基线时计算降智百分比。
compatibility: Codex or another agent with a local terminal; Python 3.10+; API-key authentication. Node.js is needed only for skills CLI installation.
metadata:
  version: "1.0.0"
---

# Codex model health check

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

Explain that a quick run makes **24 model requests** (no automatic retries), may incur API fees, and is a small diagnostic rather than a full benchmark. Follow a budget already explicitly authorized by the user; otherwise ask before the paid run. Do not repeatedly rerun after failures without approval.

## 3. Execute

```text
python <SKILL_DIR>/scripts/codex_check.py --run --skip-identity --output <USER_WORKSPACE>/model-health-results
```

Use a writable workspace output directory, not the installation directory. Task instances are randomly generated with automatic ground truth. A fixed `--seed` reproduces tasks; `--repeats 2` or `3` increases requests and must fit the approved budget.

Tests cover six small task groups: arithmetic, deduction, exact/JSON instructions, code tracing, multi-step state tracking, and retrieval within a short supplied context. Do not claim coverage of advanced coding, deep research, tools, or long-context windows.

The public skill does **not** contain the private project reference database. Its identity status is `not_evaluated`: report the configured model as a request declaration only. Do not download the author's private project, ask for its secrets, or claim a real-identity probability.

## 4. Read and explain results

Read `<USER_WORKSPACE>/model-health-results/体检报告.md` and `体检结果.json`.

Reply briefly in the user's language:

1. Requested model and sanitized provider/interface; mention session routing ambiguity.
2. Total task pass rate, six category scores, measurement coverage and API-error count.
3. Weak task groups and example failures if present, without overgeneralization.
4. **Authenticity: not evaluated without an authenticated reference. Degradation: not estimable without a trustworthy baseline.** Never turn `100 - score` into a degradation percentage, and never report distribution drift as proven capability loss.
5. Output paths and caveats. Low coverage, HTTP errors, truncation and protocol incompatibility must be reported separately from ordinary incorrect answers.

No keys, full base URLs, auth.json contents or user conversation data belong in reports. Generated test prompts only are sent directly to the configured endpoint. No telemetry or third-party grading service is used by the script. The skills installer itself has its own telemetry policy.
