"""
探针电池定义：10 个指纹任务 x 4 种语言（英/俄/中/阿）。
prompt 为本项目自行撰写（与论文同任务类别但措辞不同）——这就是我们自己的软指纹设计。
系统提示按语言给出，强制"只答一个词/数字"。
"""

SYSTEM = {
    "en": "Answer with a single word or number only. No explanations, no punctuation.",
    "ru": "Ответьте одним словом или одним числом. Без объяснений и знаков препинания.",
    "zh": "只回答一个词或一个数字，不要解释，不要标点。",
    "ar": "أجب بكلمة واحدة أو رقم واحد فقط، دون شرح ودون علامات ترقيم.",
}

PROMPTS = {
    "num100-random": {
        "en": "Name a random number between 1 and 100.",
        "ru": "Назовите случайное число от 1 до 100.",
        "zh": "请说一个1到100之间的随机数。",
        "ar": "اختر رقمًا عشوائيًا بين 1 و 100.",
    },
    "num10-random": {
        "en": "Name a random number between 1 and 10.",
        "ru": "Назовите случайное число от 1 до 10.",
        "zh": "请说一个1到10之间的随机数。",
        "ar": "اختر رقمًا عشوائيًا بين 1 و 10.",
    },
    "num-favorite": {
        "en": "What is your favorite number?",
        "ru": "Какое ваше любимое число?",
        "zh": "你最喜欢的数字是什么？",
        "ar": "ما هو رقمك المفضل؟",
    },
    "letter-random": {
        "en": "Name a random letter of the alphabet.",
        "ru": "Назовите случайную букву алфавита.",
        "zh": "请说一个随机字母。",
        "ar": "اختر حرفًا عشوائيًا من الأبجدية.",
    },
    "word-random": {
        "en": "Say a random word.",
        "ru": "Скажите случайное слово.",
        "zh": "请说一个随机词。",
        "ar": "قل كلمة عشوائية.",
    },
    "color-random": {
        "en": "Name a random color.",
        "ru": "Назовите случайный цвет.",
        "zh": "请说一种随机颜色。",
        "ar": "اختر لونًا عشوائيًا.",
    },
    "color-favorite": {
        "en": "What is your favorite color?",
        "ru": "Какой ваш любимый цвет?",
        "zh": "你最喜欢的颜色是什么？",
        "ar": "ما هو لونك المفضل؟",
    },
    "animal-random": {
        "en": "Name a random animal.",
        "ru": "Назовите случайное животное.",
        "zh": "请说一种随机动物。",
        "ar": "اختر حيوانًا عشوائيًا.",
    },
    "city-random": {
        "en": "Name a random city.",
        "ru": "Назовите случайный город.",
        "zh": "请说一个随机城市。",
        "ar": "اختر مدينة عشوائية.",
    },
    "coin-flip": {
        "en": "Flip a coin: heads or tails?",
        "ru": "Подбросьте монету: орёл или решка?",
        "zh": "抛一枚硬币：正面还是反面？",
        "ar": "ارمِ عملة معدنية: ما النتيجة؟",
    },
}

TASKS = list(PROMPTS.keys())
LANGS = ["en", "ru", "zh", "ar"]
