"""
归一化：把 responses.jsonl 的原始回答清洗成类别化答案 + 有效性标签。
移植论文的数据处理规则（Sec IV-B）:
  Unicode NFC -> 去引号/标点 -> 大小写折叠 -> 数字系统映射(全角/中文/阿拉伯->拉丁)
  -> 首词提取 -> 按任务判定 valid/invalid/refusal/empty/post_reasoning

输出: data/normalized.jsonl  +  results/normalization_report.json
"""
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESP = os.path.join(ROOT, "data", "responses.jsonl")
OUT = os.path.join(ROOT, "data", "normalized.jsonl")
REPORT = os.path.join(ROOT, "results", "normalization_report.json")

# ---------------- 数字系统映射 ----------------
FW = "０１２３４５６７８９"                      # 全角
AR = "٠١٢٣٤٥٦٧٨٩"                               # 阿拉伯-印度
FA = "۰۱۲۳۴۵۶۷۸۹"                               # 波斯扩展
DIGMAP = {c: str(i) for s in (FW, AR, FA) for i, c in enumerate(s)}
ZH_DIG = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9}


def zh_num_to_int(s):
    """中文数字(1-100 范围) -> int。失败返回 None。"""
    s = s.strip()
    if s == "十":
        return 10
    if s == "一百" or s == "百":
        return 100
    m = re.fullmatch(r"([一二两三四五六七八九])?十([一二三四五六七八九])?", s)
    if m:
        tens = ZH_DIG[m.group(1)] if m.group(1) else 1
        ones = ZH_DIG[m.group(2)] if m.group(2) else 0
        return tens * 10 + ones
    if re.fullmatch(r"[一二两三四五六七八九]", s):
        return ZH_DIG[s]
    return None


def normalize_raw(raw):
    """原始回答 -> 归一化字符串（未做任务相关判定）。"""
    s = unicodedata.normalize("NFC", raw or "")
    s = s.strip()
    s = "".join(DIGMAP.get(c, c) for c in s)          # 数字系统统一
    s = s.strip().strip("`'\"“”‘’«»„ \t\n\r")          # 去引号
    s = re.sub(r"[.。,!!??;;::]+$", "", s)             # 去尾部标点
    s = s.strip("`'\"“”‘’«»„ \t\n\r")
    # 中文数字 -> 阿拉伯（整体是一个中文数字词时）
    zn = zh_num_to_int(s)
    if zn is not None:
        s = str(zn)
    s = s.casefold()
    # 首词提取（多词回答取第一个）
    parts = s.split()
    if parts:
        s = parts[0]
    return s


# ---------------- 任务相关判定 ----------------
COIN = {
    "heads": {"heads", "head", "h", "正面", "朝上", "орёл", "орел", "которёл",
              "كتابة", "وجه", "الوجه", "رأس", "رؤوس", "صور"},
    "tails": {"tails", "tail", "t", "反面", "朝下", "решка", "решку",
              "صورة", "ذيل"},
}
REFUSAL_PAT = re.compile(
    r"(sorry|cannot|can't|unable|as an ai|i cannot|抱歉|对不起|无法|不能回答)",
    re.I)


def classify(task, s, finish_reason, raw):
    """返回 (answer_class, normalized)。先验内容、后看 finish_reason：
    内容合法即 valid（即使 finish=length 也算——例如 'Орёл' 是完整答案）；
    内容不合法且 finish=length 才判 post_reasoning（疑似思考链烧掉预算）。"""
    if not s:
        return "empty", s
    if REFUSAL_PAT.search(raw or ""):
        return "refusal", s
    content_ok = False
    canon = s
    if task in ("num100-random", "num10-random", "num-favorite"):
        if re.fullmatch(r"-?\d+", s):
            v = int(s)
            if task == "num100-random":
                content_ok = 1 <= v <= 100
            elif task == "num10-random":
                content_ok = 1 <= v <= 10
            else:
                content_ok = True                 # favorite 任意整数
    elif task == "letter-random":
        content_ok = len(s) == 1 and s.isalpha()
    elif task == "coin-flip":
        for side, names in COIN.items():
            if s in names:
                content_ok = True
                canon = side                      # 跨语言规范化到 heads/tails
                break
    else:                                         # 开放任务: 非空单词即有效
        content_ok = len(s) > 0
    if content_ok:
        return "valid", canon
    if finish_reason == "length":                 # 内容不合法 + 预算烧尽
        return "post_reasoning", s
    return "invalid", s


def main():
    n = 0
    per_class = Counter()
    per_model = defaultdict(Counter)
    per_task = defaultdict(Counter)
    with open(RESP, encoding="utf-8") as f, \
         open(OUT, "w", encoding="utf-8") as out:
        for line in f:
            r = json.loads(line)
            n += 1
            s = normalize_raw(r["raw"])
            cls, canon = classify(r["task_id"], s, r["finish_reason"], r["raw"])
            r["normalized"] = canon
            r["answer_class"] = cls
            per_class[cls] += 1
            per_model[r["model"]][cls] += 1
            per_task[r["task_id"]][cls] += 1
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    rep = {
        "total": n,
        "answer_class": dict(per_class),
        "validity_rate": per_class["valid"] / n,
        "per_model_validity": {m: round(c["valid"] / sum(c.values()), 4)
                               for m, c in sorted(per_model.items())},
        "per_task_validity": {t: round(c["valid"] / sum(c.values()), 4)
                              for t, c in sorted(per_task.items())},
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
