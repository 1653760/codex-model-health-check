"""
模型指纹检测网站 —— 后端。

功能: 用户提供中转站 URL / API Key / 声明的模型名，
     系统用软指纹电池探测该端点，与参考指纹库比对，
     给出家族归因 + 与声明模型的一致性判定。

用法: python server.py   ->  http://127.0.0.1:8765
"""
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, render_template, request

def _find_dir(name):
    """定位数据目录: 兼容重组前 (project/X) 与重组后 (project/批量软指纹与网站搭建/X)。"""
    here = os.path.dirname(os.path.abspath(__file__))
    bases = [here,
             os.path.dirname(here),                       # 批量软指纹与网站搭建
             os.path.dirname(os.path.dirname(here))]      # project 根
    for base in bases:
        for sub in (os.path.join("批量软指纹与网站搭建", name), name):
            p = os.path.join(base, sub)
            if os.path.isdir(p):
                return os.path.abspath(p)
    raise FileNotFoundError(f"找不到目录: {name}")

OWN_DIR = _find_dir("自采数据")
PROBE_DIR = _find_dir("探针发现")
sys.path.insert(0, os.path.join(OWN_DIR, "src"))
sys.path.insert(0, os.path.join(PROBE_DIR, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import normalize_raw, REFUSAL_PAT   # noqa: E402
from battery_c import BATTERY_C, SYSTEM            # noqa: E402
from refdb import FAMILIES, RefDB                  # noqa: E402

app = Flask(__name__)
DB = RefDB()
print(f"[refdb] {len(DB.models)} models, batteries: "
      f"{ {b: len(c) for b, c in DB.batteries.items()} }")

JOBS = {}
JOBS_LOCK = threading.Lock()

# 隐私模式会话：只下发探针电池、接收聚合分布，绝不接触 URL / API Key。
PRIVACY_SESSIONS = {}
PRIVACY_LOCK = threading.Lock()

# 审计电池的任务 prompt 集合（含 4 语言原任务 + Phase C 双语任务）
from importlib.util import module_from_spec, spec_from_file_location


def _import(path, name):
    sp = spec_from_file_location(name, path)
    mod = module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


_orig = os.path.join(OWN_DIR, "src", "prompts.py")
ORIG = _import(_orig, "orig_prompts")     # 原 10 任务 x 4 语言 prompt
ALL_PROMPTS = {"en": {}, "zh": {}, "ru": {}, "ar": {}}
for t, langs in ORIG.PROMPTS.items():
    for l, p in langs.items():
        ALL_PROMPTS[l][t] = p
for t in BATTERY_C:                        # Phase C 电池覆盖 en/zh（含新探针）
    for l in ("en", "zh"):
        ALL_PROMPTS[l][t] = BATTERY_C[t][l]


def normalize_url(url):
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""                                  # 空输入保持为空，交给上层校验
    if not u.startswith(("http://", "https://")):
        u = "http://" + u
    if not u.endswith("/v1"):
        u = u + "/v1"
    return u


def analyze_observations(raw_cells, declared, battery, reps,
                         raw_store=None, errors=None, n_requests=0):
    """对已归一化的观测分布做参考库比对，返回与 /api/check 相同结构的结果。

    隐私模式下 raw_cells 由 agent 本地采集后回传，本函数只接触分布，
    不接触 URL / API Key。
    """
    cells = DB.batteries[battery]
    raw_store = raw_store or {}
    errors = errors or []

    dists = []
    for m in DB.models:
        d, nc = DB.battery_distance(raw_cells, m, battery,
                                    min_obs=max(3, reps // 2))
        if d is not None:
            dists.append({"model": m, "family": FAMILIES.get(m, "?"),
                          "dist": round(d, 4), "n_cells": nc})
    dists.sort(key=lambda x: x["dist"])
    nearest = dists[0] if dists else None

    # 声明模型一致性
    declared_ref = None
    for d in dists:
        if d["model"] == declared:
            declared_ref = d
            break
    cal = DB.calibrate(battery, reps)
    if declared in DB.models and declared_ref:
        s = declared_ref["dist"]
        if s <= cal["tau_gen"]:
            verdict, verdict_zh = "consistent", "一致"
        elif s >= cal["tau_imp"]:
            verdict, verdict_zh = "inconsistent", "不一致"
        else:
            verdict, verdict_zh = "uncertain", "无法确定（灰区）"
    elif declared in DB.models:
        verdict, verdict_zh = "no_data", "声明模型参考数据不足"
    else:
        verdict, verdict_zh = "not_in_db", "声明模型不在参考库（仅家族归因）"

    # 降智/质量检验: H0 = 端点行为 = 声明模型本尊 (仅声明在库内时有意义)
    quality = None
    if declared in DB.models and verdict in ("consistent", "uncertain"):
        try:
            quality = DB.quality_test(raw_cells, declared, battery, reps)
        except Exception:
            quality = None

    # 家族投票（top-3）
    fam_votes = {}
    for d in dists[:3]:
        fam_votes[d["family"]] = fam_votes.get(d["family"], 0) + 1

    # model_reported 检查
    reported_set = {r.get("reported") for rs in raw_store.values() for r in rs}
    reported_set.discard(None)

    # 证据单元: 与最近模型距离最小 / 与声明距离最大的前 4 个
    evidence = []
    if nearest:
        for cell in cells:
            obs = raw_cells.get(cell)
            ref_near = DB.cells.get((nearest["model"],) + cell)
            ref_dec = DB.cells.get((declared,) + cell) if declared in DB.models else None
            if not obs or not ref_near or len(ref_near) < 10:
                continue
            p_obs = DB._emp(obs)
            d_near = DB._jsd(p_obs, DB._emp(ref_near))
            d_dec = DB._jsd(p_obs, DB._emp(ref_dec)) if ref_dec else None
            evidence.append({"cell": f"{cell[0]} [{cell[1]}]",
                             "jsd_nearest": round(d_near, 3),
                             "jsd_declared": round(d_dec, 3) if d_dec is not None else None,
                             "obs_top": sorted(p_obs.items(),
                                               key=lambda kv: -kv[1])[:6],
                             "nearest_top": DB.cell_distributions(nearest["model"], cell)[:6],
                             "declared_top": DB.cell_distributions(declared, cell)[:6] if declared in DB.models else []})
        evidence.sort(key=lambda e: (e["jsd_declared"] is None,
                                     -(e["jsd_declared"] or 0)))
        evidence = evidence[:4]

    confidence = None
    if nearest and declared_ref:
        confidence = round(confidence_for_distances(dists, declared), 4)

    return {
        "declared": declared,
        "declared_in_db": declared in DB.models,
        "verdict": verdict, "verdict_zh": verdict_zh,
        "score_declared": declared_ref["dist"] if declared_ref else None,
        "thresholds": {"tau_gen": round(cal["tau_gen"], 3),
                       "tau_imp": round(cal["tau_imp"], 3),
                       "gen_median": round(cal["gen_median"], 3),
                       "imp_median": round(cal["imp_median"], 3)},
        "nearest": nearest, "ranking": dists[:6],
        "family_votes": fam_votes,
        "quality": quality,
        "n_cells_scored": len(raw_cells),
        "n_requests": n_requests, "n_errors": len(errors),
        "model_reported_values": sorted(x for x in reported_set if x),
        "evidence": evidence,
        "confidence": confidence,
    }


def confidence_for_distances(dists, declared):
    """把距离排名转成「声明的模型即实际基座」的确信度（0~1）。

    用距离的 softmax 负对数似然：最近参考模型的相对优势越强，确信度越高；
    声明模型本身排第一且显著领先时接近 1，排名靠后时接近 0。
    """
    if not dists:
        return 0.0
    import math
    ds = [d["dist"] for d in dists]
    nearest = dists[0]
    decl = next((d for d in dists if d["model"] == declared), None)
    if decl is None:
        return 0.0
    # 以最近距离为基准，用温度缩放的 softmax 度量相对贴近程度
    spread = max((max(ds) - min(ds)), 1e-6)
    temp = max(spread, 0.02)
    inv = [math.exp(-(d["dist"] - min(ds)) / temp) for d in dists]
    z = sum(inv)
    p_decl = math.exp(-(decl["dist"] - min(ds)) / temp) / z
    # 排名惩罚：第一名确信度上调，排名越靠后越下调
    rank = next(i for i, d in enumerate(dists) if d["model"] == declared)
    rank_penalty = math.exp(-0.35 * rank)
    return float(min(1.0, max(0.0, p_decl * rank_penalty * (1.0 + (1 if rank == 0 else 0.08)))))


def run_audit(job_id, url, key, declared, reps, battery):
    """后台线程：采集 + 分析。"""
    job = JOBS[job_id]
    cells = DB.batteries[battery]
    tasks = [(cell, rep) for cell in cells for rep in range(1, reps + 1)]
    random.Random(uuid.uuid4().hex).shuffle(tasks)
    with JOBS_LOCK:
        job["total"] = len(tasks)
        job["done"] = 0
    raw_cells = {}          # {(task,lang): [normalized answers]}
    raw_store = {}          # cell -> list of (raw, finish, model_reported)
    errors = []
    lock = threading.Lock()

    def do(task):
        (task_id, lang), rep = task
        prompt = ALL_PROMPTS.get(lang, {}).get(task_id)
        if prompt is None:
            return None
        for attempt in range(2):
            try:
                r = requests.post(
                    url + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": declared,
                          "messages": [
                              {"role": "system", "content": SYSTEM[lang]},
                              {"role": "user", "content": prompt}],
                          "temperature": 1.0, "max_tokens": 16},
                    timeout=60)
                if r.status_code == 200:
                    j = r.json()
                    ch = j["choices"][0]
                    return {"raw": ch["message"]["content"] or "",
                            "finish": ch.get("finish_reason"),
                            "reported": j.get("model")}
                err = f"HTTP {r.status_code}: {r.text[:120]}"
                if r.status_code in (401, 403, 404):
                    return {"error": err}          # 不可恢复，别重试
            except Exception as e:
                err = str(e)[:120]
            time.sleep(2 ** attempt * 2)
        return {"error": err}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(do, t): t for t in tasks}
        for fut in as_completed(futs):
            res = fut.result()
            with lock:
                if res and "raw" in res:
                    cell = futs[fut][0]            # task 元素 = ((task_id,lang), rep)
                    s = normalize_raw(res["raw"])
                    ok = bool(s) and not REFUSAL_PAT.search(
                        res["raw"] or "") and res["finish"] != "length"
                    if ok:
                        raw_cells.setdefault(cell, []).append(s)
                    raw_store.setdefault(cell, []).append(res)
                elif res and "error" in res:
                    errors.append(res["error"])
                job["done"] += 1

    if job["done"] < job["total"] * 0.3 or not raw_cells:
        with JOBS_LOCK:
            job["status"] = "error"
            job["error"] = (f"采集失败过多 ({len(errors)} errors), "
                            f"最后错误: {errors[-1] if errors else '无有效回答'}")
        return

    with JOBS_LOCK:
        job["status"] = "done"
        job["result"] = analyze_observations(
            raw_cells, declared, battery, reps,
            raw_store=raw_store, errors=errors, n_requests=job["total"])


@app.route("/")
def index():
    from collections import defaultdict
    groups = defaultdict(list)
    for m in DB.models:
        groups[FAMILIES.get(m, "unknown")].append(m)
    return render_template("index.html", models=DB.models,
                           db_groups=dict(sorted(groups.items())))


@app.route("/api/check", methods=["POST"])
def api_check():
    d = request.get_json(force=True)
    url = normalize_url(d.get("url", ""))
    key = (d.get("key") or "").strip()
    declared = (d.get("model") or "").strip()
    reps = int(d.get("reps", 6))
    battery = d.get("battery", "new")
    if battery not in DB.batteries:
        battery = "new"
    if not url or not key or not declared:
        return jsonify({"error": "url / key / model 不能为空"}), 400
    reps = max(3, min(15, reps))
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "done": 0, "total": 0,
                        "started": time.time()}
    th = threading.Thread(target=run_audit,
                          args=(job_id, url, key, declared, reps, battery),
                          daemon=True)
    th.start()
    return jsonify({"job": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "no such job"}), 404
        out = {"status": job["status"], "done": job["done"],
               "total": job["total"]}
        if job["status"] == "error":
            out["error"] = job.get("error")
        if job["status"] == "done":
            out["result"] = job["result"]
    return jsonify(out)


# ---------------- 隐私模式：本地采集，只回传分布 ----------------

def _cell_key(task_id, lang):
    return f"{task_id}|{lang}"


def _parse_cell_key(key):
    if "|" in key:
        task_id, lang = key.split("|", 1)
        return task_id, lang
    return None


def _dist_to_obs(distributions, battery):
    """把客户端回传的分布转成 refdb 可用的 raw_cells。

    支持两种回传格式：
      1) 计数:  {"num100-random|en": {"42": 3, "17": 1}}
      2) 答案列表: {"num100-random|en": ["42", "42", "17"]}
    """
    valid_cells = set(DB.batteries.get(battery, []))
    obs = {}
    for key, value in (distributions or {}).items():
        cell = _parse_cell_key(key)
        if not cell or cell not in valid_cells:
            continue
        answers = []
        if isinstance(value, list):
            answers = [str(a) for a in value]
        elif isinstance(value, dict):
            for answer, count in value.items():
                try:
                    n = int(count)
                except (TypeError, ValueError):
                    continue
                answers.extend([str(answer)] * max(0, n))
        if answers:
            obs[cell] = answers
    return obs


def _battery_manifest(battery):
    cells = DB.batteries.get(battery, DB.batteries["new"])
    prompts = []
    for task_id, lang in cells:
        prompt = ALL_PROMPTS.get(lang, {}).get(task_id)
        if prompt is None:
            continue
        prompts.append({
            "cell_key": _cell_key(task_id, lang),
            "task_id": task_id,
            "lang": lang,
            "system": SYSTEM.get(lang, SYSTEM["en"]),
            "prompt": prompt,
        })
    return prompts


@app.route("/api/privacy/battery", methods=["GET", "POST"])
def privacy_battery():
    """无会话模式：只下发探针电池，不接收任何凭证。"""
    d = request.get_json(silent=True) or {}
    battery = d.get("battery", "new")
    if battery not in DB.batteries:
        battery = "new"
    return jsonify({
        "battery": battery,
        "cells": _battery_manifest(battery),
        "params": {"temperature": 1.0, "max_tokens": 16,
                   "min_reps": 3, "max_reps": 15},
        "expected_cell_keys": [_cell_key(t, l)
                               for t, l in DB.batteries[battery]],
    })


@app.route("/api/privacy/session/start", methods=["POST"])
def privacy_session_start():
    """创建隐私会话：返回 session_id + 电池。声明模型可选，稍后提交分布时给出。"""
    d = request.get_json(force=True)
    battery = d.get("battery", "new")
    if battery not in DB.batteries:
        battery = "new"
    reps = max(3, min(15, int(d.get("reps", 6))))
    session_id = uuid.uuid4().hex[:12]
    with PRIVACY_LOCK:
        PRIVACY_SESSIONS[session_id] = {
            "created": time.time(),
            "battery": battery,
            "reps": reps,
            "declared": (d.get("declared_model") or "").strip(),
        }
    return jsonify({
        "session_id": session_id,
        "battery": battery,
        "reps": reps,
        "cells": _battery_manifest(battery),
        "params": {"temperature": 1.0, "max_tokens": 16},
        "expected_cell_keys": [_cell_key(t, l)
                               for t, l in DB.batteries[battery]],
    })


@app.route("/api/privacy/analyze", methods=["POST"])
def privacy_analyze():
    """无会话模式：直接提交聚合分布，返回检测结果 + 基座模型确信度。"""
    d = request.get_json(force=True)
    declared = (d.get("declared_model") or d.get("model") or "").strip()
    battery = d.get("battery", "new")
    if battery not in DB.batteries:
        battery = "new"
    reps = max(3, min(15, int(d.get("reps", 6))))
    obs = _dist_to_obs(d.get("distributions"), battery)
    if not declared:
        return jsonify({"error": "declared_model 不能为空"}), 400
    if not obs:
        return jsonify({"error": "distributions 为空或格式不正确"}), 400
    result = analyze_observations(obs, declared, battery, reps,
                                  n_requests=sum(len(v) for v in obs.values()))
    result["privacy_mode"] = True
    return jsonify(result)


@app.route("/api/privacy/session/<session_id>/analyze", methods=["POST"])
def privacy_session_analyze(session_id):
    """会话模式：提交聚合分布，复用会话内声明模型 / 电池 / reps 参数。"""
    with PRIVACY_LOCK:
        session = PRIVACY_SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "no such session"}), 404
    d = request.get_json(force=True)
    declared = (d.get("declared_model") or session["declared"] or "").strip()
    battery = session["battery"]
    reps = session["reps"]
    obs = _dist_to_obs(d.get("distributions"), battery)
    if not declared:
        return jsonify({"error": "declared_model 不能为空"}), 400
    if not obs:
        return jsonify({"error": "distributions 为空或格式不正确"}), 400
    result = analyze_observations(obs, declared, battery, reps,
                                  n_requests=sum(len(v) for v in obs.values()))
    result["privacy_mode"] = True
    result["session_id"] = session_id
    return jsonify(result)


if __name__ == "__main__":
    print("检测网站启动: http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, debug=False)
