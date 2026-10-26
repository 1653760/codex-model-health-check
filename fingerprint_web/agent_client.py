"""
隐私模式本地客户端：把 API URL / Key 留在本机，只向检测网站回传分布。

数据流（隐私优先）：
  agent_client（本机）
    1) 从网站拉取探针电池（无需任何凭证）
    2) 用本地环境变量里的 URL/Key 直接调用目标端点
    3) 本地归一化答案，聚合为每单元分布
    4) 把「分布」POST 回网站
    5) 网站返回一致性判定 / 家族归因 / 基座模型确信度

环境变量（绝不发送到网站）：
  FINGERPRINT_API_BASE   目标端点 base URL，例如 https://example.com/v1
  FINGERPRINT_API_KEY    API Key
  FINGERPRINT_MODEL      声明的模型名（要验证的对象）
  FINGERPRINT_WEB        检测网站地址，默认 http://127.0.0.1:8765

也可作为 MCP stdio server 使用：python agent_client.py mcp
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


# ---------------- 数字系统映射（与 normalize.py 完全一致） ----------------
_FW = "０１２３４５６７８９"                      # 全角
_AR = "٠١٢٣٤٥٦٧٨٩"                               # 阿拉伯-印度
_FA = "۰۱۲۳۴۵۶۷۸۹"                               # 波斯扩展
_DIGMAP = {c: str(i) for s in (_FW, _AR, _FA) for i, c in enumerate(s)}
_ZH_DIG = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def _zh_num_to_int(s):
    """中文数字(1-100 范围) -> int。失败返回 None。"""
    s = s.strip()
    if s == "十":
        return 10
    if s == "一百" or s == "百":
        return 100
    m = re.fullmatch(r"([一二两三四五六七八九])?十([一二三四五六七八九])?", s)
    if m:
        tens = _ZH_DIG[m.group(1)] if m.group(1) else 1
        ones = _ZH_DIG[m.group(2)] if m.group(2) else 0
        return tens * 10 + ones
    if re.fullmatch(r"[一二两三四五六七八九]", s):
        return _ZH_DIG[s]
    return None


def _normalize(raw):
    """与自采数据/src/normalize.py 完全一致的归一化（含数字系统/中文数字）。"""
    if raw is None:
        return ""
    s = unicodedata.normalize("NFC", raw or "")
    s = s.strip()
    s = "".join(_DIGMAP.get(c, c) for c in s)          # 数字系统统一
    s = s.strip().strip("`'\"“”‘’«»„ \t\n\r")          # 去引号
    s = re.sub(r"[.。,!!??;;::]+$", "", s)             # 去尾部标点
    s = s.strip("`'\"“”‘’«»„ \t\n\r")
    zn = _zh_num_to_int(s)                             # 中文数字 -> 阿拉伯
    if zn is not None:
        s = str(zn)
    s = s.casefold()
    parts = s.split()                                  # 首词提取
    return parts[0] if parts else s


REFUSAL_PAT = re.compile(
    r"(sorry|cannot|can't|unable|as an ai|i cannot|抱歉|对不起|无法|不能回答)",
    re.I)


class PrivacyClient:
    def __init__(self, web_base=None, api_base=None, api_key=None,
                 declared_model=None, reps=6, battery="new", workers=6):
        self.web_base = (web_base or _env("FINGERPRINT_WEB",
                                          "http://127.0.0.1:8765")).rstrip("/")
        self.api_base = (api_base or _env("FINGERPRINT_API_BASE")).rstrip("/")
        self.api_key = api_key or _env("FINGERPRINT_API_KEY")
        self.declared = declared_model or _env("FINGERPRINT_MODEL")
        self.reps = int(reps)
        self.battery = battery
        self.workers = int(workers)

    # ---------- 与网站的交互 ----------
    def fetch_battery(self):
        r = requests.post(self.web_base + "/api/privacy/session/start",
                          json={"declared_model": self.declared,
                                "battery": self.battery,
                                "reps": self.reps},
                          timeout=30)
        r.raise_for_status()
        return r.json()

    def submit_distributions(self, session_id, distributions):
        r = requests.post(
            self.web_base + f"/api/privacy/session/{session_id}/analyze",
            json={"distributions": distributions,
                  "declared_model": self.declared},
            timeout=120)
        r.raise_for_status()
        return r.json()

    # ---------- 本地采集 ----------
    def _call(self, cell):
        payload = {
            "model": self.declared,
            "messages": [
                {"role": "system", "content": cell["system"]},
                {"role": "user", "content": cell["prompt"]}],
            "temperature": 1.0,
            "max_tokens": 16,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        r = requests.post(self.api_base + "/chat/completions",
                          headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            return None
        j = r.json()
        ch = j["choices"][0]
        raw = ch.get("message", {}).get("content") or ""
        finish = ch.get("finish_reason")
        norm = _normalize(raw)
        if not norm or REFUSAL_PAT.search(raw or "") or finish == "length":
            return None
        return norm

    def collect(self, manifest):
        tasks = [(cell, rep) for cell in manifest["cells"]
                 for rep in range(1, self.reps + 1)]
        buckets = defaultdict(list)
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._call, cell): cell for cell, _ in tasks}
            for fut in as_completed(futs):
                ans = fut.result()
                if ans:
                    buckets[futs[fut]["cell_key"]].append(ans)
        return buckets

    def run(self):
        if not self.api_base or not self.api_key or not self.declared:
            raise RuntimeError(
                "缺少 FINGERPRINT_API_BASE / FINGERPRINT_API_KEY / "
                "FINGERPRINT_MODEL 环境变量")
        manifest = self.fetch_battery()
        session_id = manifest["session_id"]
        buckets = self.collect(manifest)
        distributions = {k: dict((a, v.count(a)) for a in set(v))
                         for k, v in buckets.items()}
        result = self.submit_distributions(session_id, distributions)
        return manifest, distributions, result


def _human_report(result):
    out = []
    out.append("verdict: {} ({})".format(result["verdict"],
                                         result["verdict_zh"]))
    out.append("score_declared: {}".format(result.get("score_declared")))
    out.append("confidence(declared==base): {}".format(result.get("confidence")))
    if result.get("nearest"):
        out.append("nearest: {} [{}] dist={}".format(
            result["nearest"]["model"], result["nearest"]["family"],
            result["nearest"]["dist"]))
    out.append("ranking:")
    for x in result.get("ranking", []):
        mark = " <-- declared" if x["model"] == result["declared"] else ""
        out.append("  {:.4f}  {}{}".format(x["dist"], x["model"], mark))
    return "\n".join(out)


def _mcp_loop():
    """极简 MCP stdio server：暴露 fetch_battery / run_privacy 两个工具。"""
    import threading
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        rpc_id = req.get("id")
        if method == "initialize":
            _send_mcp(rpc_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "llm-soft-fingerprint",
                               "version": "1.0"}})
        elif method == "tools/list":
            _send_mcp(rpc_id, {"tools": [
                {"name": "fetch_battery",
                 "description": "从检测网站获取探针问题集合（不涉及任何凭证）",
                 "inputSchema": {"type": "object", "properties": {
                     "battery": {"type": "string"},
                     "reps": {"type": "integer"}}}},
                {"name": "run_privacy_check",
                 "description": "本地采集目标端点并只回传分布，返回检测结果与确信度",
                 "inputSchema": {"type": "object", "properties": {
                     "declared_model": {"type": "string"},
                     "battery": {"type": "string"},
                     "reps": {"type": "integer"}}}},
            ]})
        elif method == "tools/call":
            _mcp_tool_call(rpc_id, req.get("params", {}))
        else:
            _send_mcp(rpc_id, {"error": "unsupported method"})


def _send_mcp(rpc_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rpc_id,
                                 "result": result}) + "\n")
    sys.stdout.flush()


def _mcp_tool_call(rpc_id, params):
    name = params.get("name")
    args = params.get("arguments", {}) or {}
    try:
        if name == "fetch_battery":
            c = PrivacyClient(declared_model=args.get("declared_model", ""),
                              reps=args.get("reps", 6),
                              battery=args.get("battery", "new"))
            text = json.dumps(c.fetch_battery(), ensure_ascii=False)
        elif name == "run_privacy_check":
            c = PrivacyClient(declared_model=args.get("declared_model"),
                              reps=args.get("reps", 6),
                              battery=args.get("battery", "new"))
            _, _, result = c.run()
            text = json.dumps(result, ensure_ascii=False)
        else:
            text = json.dumps({"error": "unknown tool"})
        _send_mcp(rpc_id, {"content": [{"type": "text", "text": text}],
                           "isError": False})
    except Exception as e:  # noqa: BLE001
        _send_mcp(rpc_id, {"content": [{"type": "text", "text": str(e)}],
                           "isError": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="run",
                    choices=["run", "mcp", "battery"])
    ap.add_argument("--declared-model", default=None)
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--battery", default="new")
    ap.add_argument("--web", default=None)
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    if args.mode == "mcp":
        _mcp_loop()
        return

    client = PrivacyClient(web_base=args.web, api_base=args.api_base,
                           api_key=args.api_key,
                           declared_model=args.declared_model,
                           reps=args.reps, battery=args.battery)
    if args.mode == "battery":
        print(json.dumps(client.fetch_battery(), ensure_ascii=False, indent=2))
        return
    manifest, distributions, result = client.run()
    print(json.dumps({
        "session_id": manifest.get("session_id"),
        "battery": manifest.get("battery"),
        "reps": manifest.get("reps"),
        "distributions": distributions,
    }, ensure_ascii=False, indent=2))
    print("---- website result ----")
    print(_human_report(result))


if __name__ == "__main__":
    main()

