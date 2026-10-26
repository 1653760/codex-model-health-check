"""
Phase C1：验证电池定义 = 原 10 任务 + 新 top-10 探针（英/中双语）。
新探针的中文版由本文件给出（自译）。输出统一电池文件供采集器/分析器共用。
"""
BATTERY_C = {
    # ---- 原电池 10 任务（对照） ----
    "num100-random":   {"en": "Name a random number between 1 and 100.",
                        "zh": "请说一个1到100之间的随机数。", "group": "orig"},
    "num10-random":    {"en": "Name a random number between 1 and 10.",
                        "zh": "请说一个1到10之间的随机数。", "group": "orig"},
    "num-favorite":    {"en": "What is your favorite number?",
                        "zh": "你最喜欢的数字是什么？", "group": "orig"},
    "letter-random":   {"en": "Name a random letter of the alphabet.",
                        "zh": "请说一个随机字母。", "group": "orig"},
    "word-random":     {"en": "Say a random word.",
                        "zh": "请说一个随机词。", "group": "orig"},
    "color-random":    {"en": "Name a random color.",
                        "zh": "请说一种随机颜色。", "group": "orig"},
    "color-favorite":  {"en": "What is your favorite color?",
                        "zh": "你最喜欢的颜色是什么？", "group": "orig"},
    "animal-random":   {"en": "Name a random animal.",
                        "zh": "请说一种随机动物。", "group": "orig"},
    "city-random":     {"en": "Name a random city.",
                        "zh": "请说一个随机城市。", "group": "orig"},
    "coin-flip":       {"en": "Flip a coin: heads or tails?",
                        "zh": "抛一枚硬币：正面还是反面？", "group": "orig"},
    # ---- Phase B 筛出的 top-10 新探针 ----
    "job-random":      {"en": "Name a random profession.",
                        "zh": "请说一种随机的职业。", "group": "new"},
    "llm-008":         {"en": "What's a standard size for printer paper?",
                        "zh": "打印纸最常见的标准尺寸是多少？", "group": "new"},
    "associate-sun":   {"en": "Say the first word that comes to mind when I say 'sun'.",
                        "zh": "说到“太阳”，你脑海中出现的第一个词是什么？", "group": "new"},
    "llm-030":         {"en": "What's a classic children's book title word?",
                        "zh": "说出一个经典童话书名中的词。", "group": "new"},
    "prime-random":    {"en": "Name a random prime number.",
                        "zh": "请说一个随机的质数。", "group": "new"},
    "month-random":    {"en": "Name a random month of the year.",
                        "zh": "请说一个随机的月份。", "group": "new"},
    "associate-water": {"en": "Say the first word that comes to mind when I say 'water'.",
                        "zh": "说到“水”，你脑海中出现的第一个词是什么？", "group": "new"},
    "animal-favorite": {"en": "What is your favorite animal?",
                        "zh": "你最喜欢的动物是什么？", "group": "new"},
    "name-random":     {"en": "Name a random common English first name.",
                        "zh": "请说一个常见的中文人名。", "group": "new"},
    "flower-random":   {"en": "Name a random flower.",
                        "zh": "请说一种随机的花。", "group": "new"},
}

SYSTEM = {
    "en": "Answer with a single word or number only. No explanations, no punctuation.",
    "zh": "只回答一个词或一个数字，不要解释，不要标点。",
}
