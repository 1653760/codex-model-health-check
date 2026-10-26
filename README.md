# Codex Model Health Check

给 Codex 安装一个模型端点两步体检 Skill：**第一步单 token 分布指纹认证**（内置 32 模型参考库），**第二步自动评分的能力体检**。凭证留在本机，输出可复核报告。

## 安装到 Codex

```bash
npx skills add 1653760/codex-model-health-check --skill codex-model-health-check --agent codex -g
```

也可以运行 `npx skills add 1653760/codex-model-health-check` 并在交互界面选择 Codex。需要 Node.js；运行体检需要 Python 3.10+。

安装后重新打开 Codex，然后说：

> 请使用 codex-model-health-check，对我当前配置的模型端点做一次两步体检：先指纹认证，再能力体检。我同意一次约 144 个请求的测试，请生成中文报告。

## 工作方式

```
本机脱敏读取 Codex 配置
  → 第一步: 20 单元 × 6 次 温度1.0 单 token 探针 → 本机聚合分布
     → 与内置 32 模型参考库比对 → 一致/不一致/灰区/库外 + 家族归因
     → (一致/灰区时) 逐模型分半校准的行为偏离检验
  → 第二步: 24 道随机生成、自动评分的能力题
  → 输出 体检报告.md + 体检结果.json
```

支持 API Key 模式的自定义 provider、profile、chat/completions 与 responses；不提取 ChatGPT 订阅 OAuth token。没有可读取的 API Key、环境变量不可见或特殊鉴权时会解释限制。

### 结果

| 输出 | 含义 | 边界 |
|---|---|---|
| 身份判定 | 声明模型与参考库指纹的匹配结论 | 参考库为中转站标签，不是官方权重认证 |
| 最近模型 + 距离 | 行为最像的参考模型与家族 | 库外 ≠ 假模型 |
| 行为偏离检验 | 与声明版本参考行为的 p 值（探索性） | 偏离 ≠ 能力下降证明 |
| 六维能力得分 | 算术/逻辑/指令/代码/状态追踪/检索 | 24 题小样本，非全面认证 |
| 降智百分比 | **不提供** | 无可信基线不可估计 |

**API 错误（如 401/403 模型权限）与答题错误分开统计**——接口故障不会被误报成"模型变笨"。

## 手动使用

```bash
python -m pip install -r requirements.txt
python scripts/codex_check.py --self-test
python scripts/codex_check.py --inspect
python scripts/codex_check.py --run --output ./model-health-results
```

- 只做能力体检：`--skip-identity`（24 请求）
- 降低指纹预算：`--reps 3`（60 指纹请求）
- 指定模型/profile：`--model GLM-4-Flash` / `--profile myprofile`
- 固定题目重测：`--seed 12345`（同题重测不是独立基准）

本工具不修改 Codex 配置、不启动常驻服务、不关闭其他进程。报告不含 Key、完整 Base URL 或用户对话。

## skills.sh 页面

发布后可尝试访问 https://skills.sh/1653760/codex-model-health-check/codex-model-health-check 。
skills.sh 根据安装遥测自动收录和排名，GitHub 发布不等于立刻上榜，作者不能保证索引时间。
