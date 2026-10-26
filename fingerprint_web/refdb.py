"""
参考指纹库：从项目已有数据构建 11 个模型的参考指纹 + 阈值校准。

数据源:
  1) 自采数据/data/normalized.jsonl        原 10 任务 x 4 语言 x 30 次 (T=1)
  2) 探针发现/data/phaseC_responses.jsonl  20 任务(原10+新10) x 英中 x 30 次 (T=1)

电池:
  new  = Phase C 的新 10 任务 x en/zh = 20 单元（判别力最强）
  orig = Phase C 的原 10 任务 x en/zh = 20 单元
  full = 上述 40 + 自采数据的原任务 x 4 语言 40 = 80 单元

校准（对给定审计次数 N）:
  genuine  = 各参考模型"前 N 次 vs 其余"的距离（与审计同样本量的自体距离）
  impostor = 各参考模型"前 N 次" vs 其他模型全量 30 次的距离
  τ_gen = genuine 95 分位, τ_imp = impostor 5 分位
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np

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
from normalize import normalize_raw, REFUSAL_PAT   # noqa: E402
from battery_c import BATTERY_C                     # noqa: E402

CFG = json.load(open(os.path.join(OWN_DIR, "config.json"),
                     encoding="utf-8"))
FAMILIES = {m: f for f, ms in CFG["families"].items() for m in ms}


def _classify_valid(raw, finish_reason):
    s = normalize_raw(raw)
    if not s:
        return ""
    if REFUSAL_PAT.search(raw or ""):
        return ""
    if finish_reason == "length":
        return ""
    return s


def _load_own_orig():
    """自采数据: 原 10 任务 x 4 语言（已归一化好）。"""
    cells = defaultdict(list)
    path = os.path.join(OWN_DIR, "data", "normalized.jsonl")
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["temperature"] != 1 or r["answer_class"] != "valid":
                continue
            cells[(r["model"], r["task_id"], r["lang"])].append(
                (r["rep"], r["normalized"]))
    return {(k[0], k[1], k[2]): [a for _, a in sorted(v)]
            for k, v in cells.items()}


def _load_phase_c():
    """Phase C: 20 任务 x en/zh（现场归一化）。"""
    cells = defaultdict(list)
    path = os.path.join(PROBE_DIR, "data", "phaseC_responses.jsonl")
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            s = _classify_valid(r["raw"], r["finish_reason"])
            if s:
                cells[(r["model"], r["task_id"], r["lang"])].append(
                    (r["rep"], s))
    return {(k[0], k[1], k[2]): [a for _, a in sorted(v)]
            for k, v in cells.items()}


class RefDB:
    def __init__(self):
        own = _load_own_orig()
        pc = _load_phase_c()
        self.cells = {}
        self.cells.update(own)
        self.cells.update(pc)                    # Phase C 覆盖同键（更新版）
        self.models = sorted({m for (m, _, _) in self.cells})
        # 电池定义
        self.batteries = {
            "new": sorted({(t, l) for (m, t, l) in self.cells
                           if t in BATTERY_C and BATTERY_C[t]["group"] == "new"
                           and l in ("en", "zh")}),
            "orig": sorted({(t, l) for (m, t, l) in self.cells
                            if t in BATTERY_C and BATTERY_C[t]["group"] == "orig"
                            and l in ("en", "zh")}),
        }
        self.batteries["full"] = sorted({(t, l) for (m, t, l) in self.cells})
        self._calib_cache = {}

    # ---------------- 距离 ----------------
    @staticmethod
    def _emp(answers):
        n = len(answers)
        d = {}
        for a in answers:
            d[a] = d.get(a, 0) + 1
        return {k: v / n for k, v in d.items()}

    @classmethod
    def _jsd(cls, p, q):
        cats = sorted(set(p) | set(q))
        pv = np.array([p.get(c, 0.0) for c in cats])
        qv = np.array([q.get(c, 0.0) for c in cats])
        m = 0.5 * (pv + qv)
        with np.errstate(divide="ignore", invalid="ignore"):
            kl_p = np.where(pv > 0, pv * np.log2(pv / m), 0.0).sum()
            kl_q = np.where(qv > 0, qv * np.log2(qv / m), 0.0).sum()
        return float(0.5 * kl_p + 0.5 * kl_q)

    def battery_distance(self, obs_cells, ref_model, battery,
                         min_obs=3, min_ref=10):
        """观测单元 -> 参考模型的电池平均 JSD。返回 (dist, n_cells)。"""
        ds = []
        for cell in self.batteries[battery]:
            obs = obs_cells.get(cell)
            ref = self.cells.get((ref_model,) + cell)
            if not obs or len(obs) < min_obs or not ref or len(ref) < min_ref:
                continue
            ds.append(self._jsd(self._emp(obs), self._emp(ref)))
        if not ds:
            return None, 0
        return float(np.mean(ds)), len(ds)

    # ---------------- 校准 ----------------
    def calibrate(self, battery, n_obs):
        """匹配审计样本量的阈值校准（带缓存）。"""
        key = (battery, n_obs)
        if key in self._calib_cache:
            return self._calib_cache[key]
        gen, imp = [], []
        for m in self.models:
            # genuine: 前 N 次 vs 其余次
            gen.extend(self._pseudo_distance(m, n_obs, battery))
            # impostor: 前 N 次 vs 其他模型全量
            for other in self.models:
                if other == m:
                    continue
                d, nc = self._pseudo_vs(m, other, n_obs, battery)
                if d is not None:
                    imp.append(d)
        cal = {"tau_gen": float(np.percentile(gen, 95)),
               "tau_imp": float(np.percentile(imp, 5)),
               "gen_median": float(np.median(gen)),
               "imp_median": float(np.median(imp)),
               "n_gen": len(gen), "n_imp": len(imp)}
        self._calib_cache[key] = cal
        return cal

    def _pseudo_distance(self, model, n_obs, battery):
        """模型自身: 前 n_obs 次 vs 其余次的电池距离（单值列表）。"""
        ds = []
        for cell in self.batteries[battery]:
            ref = self.cells.get((model,) + cell)
            if not ref or len(ref) < n_obs + 5:
                continue
            head, tail = ref[:n_obs], ref[n_obs:]
            ds.append(self._jsd(self._emp(head), self._emp(tail)))
        return [float(np.mean(ds))] if ds else []

    def _pseudo_vs(self, model, other, n_obs, battery):
        """模型前 n_obs 次 vs other 全量的电池距离。"""
        cells_head = {}
        for cell in self.batteries[battery]:
            ref = self.cells.get((model,) + cell)
            if ref and len(ref) >= n_obs:
                cells_head[cell] = ref[:n_obs]
        fake_obs = cells_head
        return self.battery_distance(fake_obs, other, battery,
                                      min_obs=max(3, n_obs - 2))

    # ---------------- 降智/质量检验 ----------------
    def quality_test(self, obs_cells, declared, battery, n_obs, n_boot=150):
        """H0: 被测端点行为 = 声明模型本尊 (E1 实验支持的降智检验)。

        零分布 = 声明模型自身「随机 head-N vs 其余次」的电池 JSD 重采样 n_boot 次,
        与 calibrate 的 genuine 口径一致 (随机版)。p 值 = 零分布中 >= 观测距离的占比。
        观测侧 = obs_cells(N 次采样) vs 参考全量; 零分布侧 = head-N vs 其余次——
        参考侧略短于全量, 该不对称使 p 值略偏保守 (高估), 可接受。

        返回 dict(p_value/effect/verdict/deviating_cells/...) 或数据不足时 None。
        """
        rng = np.random.default_rng(20261018)
        ref_cells = {}
        for cell in self.batteries[battery]:
            ref = self.cells.get((declared,) + cell)
            obs = obs_cells.get(cell)
            if not ref or not obs:
                continue
            if len(ref) < n_obs + 5 or len(obs) < 3:
                continue
            ref_cells[cell] = (ref, obs)
        if len(ref_cells) < 5:
            return None

        obs_jsds = {c: self._jsd(self._emp(o), self._emp(r))
                    for c, (r, o) in ref_cells.items()}
        obs_mean = float(np.mean(list(obs_jsds.values())))

        cell_nulls = {c: [] for c in ref_cells}
        null = []
        for _ in range(n_boot):
            ds = []
            for cell, (ref, _) in ref_cells.items():
                order = rng.permutation(len(ref))
                head = [ref[i] for i in order[:n_obs]]
                tail = [ref[i] for i in order[n_obs:]]
                if len(tail) < 5:
                    continue
                d = self._jsd(self._emp(head), self._emp(tail))
                ds.append(d)
                cell_nulls[cell].append(d)
            if ds:
                null.append(float(np.mean(ds)))
        if not null:
            return None
        null_arr = np.array(null)
        p = float((1 + (null_arr >= obs_mean).sum()) / (len(null_arr) + 1))

        dev = []
        for cell, nulls in cell_nulls.items():
            if len(nulls) < 20:
                continue
            excess = obs_jsds[cell] - float(np.percentile(nulls, 95))
            dev.append({"cell": f"{cell[0]} [{cell[1]}]",
                        "jsd_obs": round(obs_jsds[cell], 3),
                        "jsd_null_p95": round(float(np.percentile(nulls, 95)), 3),
                        "excess": round(excess, 3)})
        dev.sort(key=lambda x: -x["excess"])

        verdict = ("nominal" if p >= 0.05 else
                   "deviated" if p >= 0.01 else "strongly_deviated")
        zh = {"nominal": "质量正常",
              "deviated": "疑似偏离（可能降智）",
              "strongly_deviated": "显著偏离（高度疑似降智）"}
        return {
            "p_value": round(p, 4),
            "obs_distance": round(obs_mean, 4),
            "null_median": round(float(np.median(null_arr)), 4),
            "null_p95": round(float(np.percentile(null_arr, 95)), 4),
            "effect": round(obs_mean / max(float(np.median(null_arr)), 1e-9), 2),
            "verdict": verdict, "verdict_zh": zh[verdict],
            "n_boot": len(null), "n_cells": len(ref_cells),
            "deviating_cells": dev[:4],
        }

    # ---------------- 证据单元 ----------------
    def cell_distributions(self, model, cell, top_k=8):
        ref = self.cells.get((model,) + cell, [])
        p = self._emp(ref) if ref else {}
        top = sorted(p.items(), key=lambda kv: -kv[1])[:top_k]
        return top


if __name__ == "__main__":
    db = RefDB()
    print("models:", len(db.models))
    for b in ("new", "orig", "full"):
        print(f"battery {b}: {len(db.batteries[b])} cells")
    cal = db.calibrate("new", 6)
    print("calibrate(new, 6):", json.dumps(cal, indent=1))
