# Codex Model Health Check

给 Codex 安装一个模型端点能力体检 Skill。凭证留在本机，自动生成 24 道有标准答案的题目，输出六维度得分和可复核报告。

## 安装到 Codex

```bash
npx skills add 1653760/codex-model-health-check --skill codex-model-health-check --agent codex -g
```

也可以运行 `npx skills add 1653760/codex-model-health-check` 并在交互界面选择 Codex。需要 Node.js；运行体检需要 Python 3.10+。

安装后重新打开 Codex，然后说：

> 请使用 codex-model-health-check，对我当前配置的模型端点进行一次能力体检。允许一次24请求测试，生成报告。

## 工作方式

本机脱敏检查Codex配置 → 用户授权请求预算 → 本机调用模型 → 精确自动评分 → 输出 Markdown/JSON。

支持API Key模式的自定义provider、profile、chat/completions与responses；不提取ChatGPT订阅OAuth token。没有可读取的API Key、环境变量不可见或特殊鉴权时会解释限制。

### 结果

- 算术、逻辑、指令/JSON、代码理解、状态追踪、短上下文检索六类任务得分；
- 接口错误、有效覆盖率、截断状态；
- 随机种子、标准答案、回答、提示哈希；
- 不宣称已认证模型真实身份，不将能力得分解释为降智百分比。

**无基线体检 ≠ 降智检测**。24个简单题是可操作的教学诊断，不是标准化全面能力认证。默认配置端点不一定就是当前会话路由。公开版不包含私有项目参考数据或密钥，身份状态明确为未核验。

## 手动使用

```bash
python -m pip install -r requirements.txt
python scripts/codex_check.py --self-test
python scripts/codex_check.py --inspect
python scripts/codex_check.py --run --skip-identity --output ./model-health-results
```

本工具不修改Codex配置、不启动常驻服务、不关闭其他进程。

## skills.sh 页面

发布后可尝试访问 https://skills.sh/1653760/codex-model-health-check/codex-model-health-check 。
skills.sh 根据安装遥测自动收录和排名，GitHub发布不等于立刻上榜，作者不能保证索引时间。
