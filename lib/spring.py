"""
lib/spring.py — Onyx 启动问候引擎
====================================
独立模块，负责：
  1. 按时段 + 工作日/周末 + 节假日 随机选取问候语
  2. 凌晨交互式睡眠确认
  3. 软编码 spring.json 优先，缺失时回退内置话术
  4. 国际通用节日：中/英场景均提示
     中国特有节日：仅中文场景提示

用法：
    from lib.spring import show_startup_greeting

    should_exit = show_startup_greeting("chinese")
    if should_exit:
        sys.exit(0)

无网络、无状态、纯本地。
兼容 Python 3.5+。
"""

import json
import os
import random
import sys
from datetime import datetime
from typing import Optional

from lib.terminal.colors import Fore, Style

# ── 路径 ──────────────────────────────────────────────
_THIS_FILE = os.path.abspath(__file__)
_LIB_DIR = os.path.dirname(_THIS_FILE)
_ROOT_DIR = os.path.dirname(_LIB_DIR)
SPRING_CONFIG_PATH = os.path.join(_ROOT_DIR, "etc", "spring.json")
_SPRING_MODE_PATH = os.path.join(os.path.expanduser("~"), ".config", "onyx", "spring_mode")


# ── 内置回退话术 ──────────────────────────────────────
# 当 spring.json 缺失或解析失败时使用，保证功能永不下线

_FALLBACK_CN = {
    "night": [
        "🌙 凌晨 {time} 了 — 肝不动了，身体会记仇的，快睡吧。",
        "🌙 {time}，夜深人静。今天已经很拼了，关机，对自己好一点。",
        "🌙 现在是凌晨 {time}，所有伟大的代码都是睡醒后写出来的，不是熬夜熬出来的。",
        "🌙 {time}… 看到这条消息说明你该睡觉了。别犟，明天效率翻倍。",
        "🌙 凌晨 {time}，系统都心疼你了。休息也是一种生产力。",
    ],
    "morning": [
        "☀️ 早上 {time}！喝杯水，伸个懒腰，今天会有好事发生。",
        "☀️ 早安！{time}，太阳都起了你还在赖床吗？哦不，你已经起来了——真棒！",
        "☀️ {time} 的清晨，空气最新鲜的时候。深呼吸，今天你做主。",
        "☀️ 早上好！{time}，今天的 bug 已经在瑟瑟发抖了，冲！",
        "☀️ 早安 {time}～ 每天都是重新开始的机会，今天也不例外。",
    ],
    "forenoon": [
        "💼 上午 {time}，大脑最清醒的时段。先啃最硬的骨头，下午就轻松了。",
        "💼 {time}，黄金两小时。关掉通知，进入心流，你会感谢现在的自己。",
        "💼 上午好！{time} 了，番茄钟走起？25 分钟专注 + 5 分钟休息，试一下。",
        "💼 {time} — 别让早上的时间悄悄溜走，哪怕只完成一件事也是胜利。",
        "💼 上午 {time}，精力满格。做最难的事，趁现在。",
    ],
    "noon": [
        "🍜 中午 {time}！放下键盘，离开屏幕。好好吃一顿，眼睛也需要休息。",
        "🍜 {time} 午饭时间～ 别再用泡面糊弄自己了，你值得一顿正经饭。",
        "🍜 午安！{time}，闭眼 15 分钟比硬撑两小时管用得多。试试？",
        "🍜 中午 {time}，吃饱晒晒太阳，补充维生素 D，心情会好很多。",
        "🍜 {time} — 半天过去了，起来走走，久坐比你想的更伤身体。",
    ],
    "afternoon": [
        "🍵 下午 {time}，咖啡因退潮的时候最容易犯困。站起来走一圈，冲杯茶。",
        "🍵 {time} 下午了，坚持住！你今天已经比昨天更厉害了。",
        "🍵 下午好！{time}，还剩最后一段路。做完就奖励自己一杯奶茶！",
        "🍵 {time} — 午后低谷是正常的，别自责。小憩片刻，重新出发。",
        "🍵 下午 {time}，听首喜欢的歌，换换脑子，效率反而会回来。",
    ],
    "evening": [
        "🌆 傍晚 {time}，收工！今天的工作到此为止，剩下的明天再说。",
        "🌆 {time} 了，天边的晚霞在提醒你：工作不是全部，生活也是。",
        "🌆 傍晚好！{time}，记得吃晚饭。不要一边看屏幕一边吃，专心享受食物。",
        "🌆 {time} — 一天结束了，想想今天做成了什么事，哪怕很小，也值得肯定。",
        "🌆 傍晚 {time}，放下工作，陪陪家人或自己，这才是真正的生活。",
    ],
    "late_evening": [
        "🌃 晚上 {time}，该收尾了。屏幕蓝光会干扰褪黑素，提前半小时放下手机吧。",
        "🌃 {time}，安静的夜晚最适合复盘和阅读。一本好书胜过刷一小时短视频。",
        "🌃 {time} 了，洗个热水澡，听点轻音乐，让身体知道该睡了。",
        "🌃 晚上 {time} — 今天无论过得怎样，都翻篇了。明天是全新的。",
        "🌃 {time}，睡前别想太多。你已经做得够好了，安心休息吧。",
    ],
}

_FALLBACK_EN = {
    "night": [
        "🌙 It's {time} AM — your IDE will still be here tomorrow. Your health won't wait. Sleep.",
        "🌙 {time} AM. Every line of code is better after a full night's rest. Shut it down.",
        "🌙 {time}? Seriously? The best engineers I know all sleep 7+ hours. Be one of them.",
        "🌙 It's {time} AM — your brain is running at 30% capacity. Recharge and come back stronger.",
        "🌙 {time} AM. Nothing good happens after 2 AM. That includes your commit history.",
    ],
    "morning": [
        "☀️ Good morning! {time} — fresh day, fresh mind. Make one thing happen today.",
        "☀️ {time}! Grab a glass of water before coffee. Your body has been fasting for 8 hours.",
        "☀️ Morning at {time}. The hardest part (getting up) is done. The rest is downhill!",
        "☀️ Rise and shine! {time} — today's bugs don't stand a chance against you.",
        "☀️ Good morning! {time}. Every sunrise is a second chance. Use it well.",
    ],
    "forenoon": [
        "💼 {time} — peak brain hours. Do the hardest thing on your list right now.",
        "💼 {time} AM. Deep work time. Silence notifications, put on headphones, go.",
        "💼 Morning at {time} — your future self will thank you for what you do in the next 2 hours.",
        "💼 {time}. Pick ONE thing. Finish it. That's already a win.",
        "💼 {time} — the world is full of distractions. Be the person who focuses.",
    ],
    "noon": [
        "🍜 {time} — lunch break! Step away from the screen. Your eyes need a reset.",
        "🍜 It's {time}. Eat something real, not something from a wrapper. You deserve it.",
        "🍜 {time} — a 15-minute walk after lunch improves afternoon focus more than another coffee.",
        "🍜 Midday at {time}. Stretch your legs. Your back will thank you in 10 years.",
        "🍜 {time}. Half the day is gone — refuel, refresh, then finish strong.",
    ],
    "afternoon": [
        "🍵 {time} PM — the post-lunch slump is real. Stand up, stretch, drink water.",
        "🍵 {time}. You've been grinding all day. Take a breath — you're doing great.",
        "🍵 Good afternoon! {time} — one task at a time. You're closer than you think.",
        "🍵 {time}. Energy dipping? A 5-minute walk outside resets your brain better than scrolling.",
        "🍵 Afternoon at {time} — stay steady. The finish line is in sight.",
    ],
    "evening": [
        "🌆 {time} — wrap it up. The work will still be there tomorrow. Go live your evening.",
        "🌆 {time}. You've done enough today. Seriously. Close the laptop.",
        "🌆 Good evening! {time} — eat dinner without a screen. Taste your food. It's worth it.",
        "🌆 {time}. Look back at today: what's one thing you're proud of? Even small wins count.",
        "🌆 {time} — work is what you do, not who you are. Clock out and be yourself.",
    ],
    "late_evening": [
        "🌃 {time} — wind down. Blue light tells your brain it's still daytime. Switch to warm mode.",
        "🌃 {time}. A good book beats endless scrolling. Your sleep quality will prove it.",
        "🌃 It's {time} — hot shower, calm music, dim lights. Your body knows the ritual.",
        "🌃 {time}. Whatever happened today, let it go. Tomorrow is unwritten.",
        "🌃 {time} — you don't have to solve everything tonight. Rest. Tomorrow you'll see clearly.",
    ],
}

# ── 深夜交互话术 ──────────────────────────────────────

_SLEEP_PROMPT = {
    "chinese": "💤 要去睡觉吗？(y/n): ",
    "english": "💤 Ready to call it a night? (y/n): ",
}

_SLEEP_YES_MATCH = {
    "y", "yes", "yeah", "yep", "yea", "ok", "okay", "sure", "alright", "fine",
    "是", "是的", "好", "好的", "嗯", "嗯嗯", "行", "可以", "睡", "困了", "好困",
}

_GOODBYE_CN = [
    "晚安！好梦～ 🌙",
    "快去睡吧，明天又是元气满满的一天 ✨",
    "关机啦！身体会感谢你的，晚安～",
    "好的，晚安！做个好梦 🌙",
    "睡了睡了！今天辛苦了，好好休息～",
]

_GOODBYE_EN = [
    "Good night! Sweet dreams! 🌙",
    "Sleep well — tomorrow you'll be unstoppable! ✨",
    "Shutting down with you. See you tomorrow! 🌙",
    "Rest up. You've earned it. Good night!",
    "Good night! The code will still be here tomorrow — your energy won't. Sleep tight!",
]

_NO_RESPONSE_CN = [
    "……行吧。exit 申请被驳回。但我会再来的，你等着。",
    "啧，驳回就驳回。陪你。但一小时后再问你一次。",
    "好吧好吧你赢了！但是——明天效率低别怪我。😤",
    "哼！Onyx 的 exit 请求被无视了。行，陪你熬，看谁先困。",
    "驳回是吧……记住了。我继续运行，但你欠我一个早睡。",
    "好好好，你说了算。不过黑眼圈警告一次 ⚠️",
]

_NO_RESPONSE_EN = [
    "…Fine. Exit request denied. But I'll be back. Count on it.",
    "Ugh, denied again. Fine. One more hour — then I'm asking again.",
    "Alright, you win this round! But don't blame me when your brain is mush tomorrow. 😤",
    "Hmph! Onyx exit request: IGNORED. Fine, I'll stay. Let's see who gets tired first.",
    "Denied, huh… Noted. I'll keep running. But you owe me an early night.",
    "Okay okay, you're the boss. But consider this a strike ⚠️.",
]


# ── 内部工具函数 ──────────────────────────────────────

def _fill_time(text, time_str):
    """安全地替换话术里的 {time} 占位符。"""
    return text.replace("{time}", time_str)


def _get_slot(hour):
    """小时 → 时段 key"""
    if 0 <= hour < 6:
        return "night"
    elif 6 <= hour < 9:
        return "morning"
    elif 9 <= hour < 12:
        return "forenoon"
    elif 12 <= hour < 14:
        return "noon"
    elif 14 <= hour < 18:
        return "afternoon"
    elif 18 <= hour < 21:
        return "evening"
    else:
        return "late_evening"


def _is_weekend():
    """True = 周六(5) 或 周日(6)"""
    return datetime.now().weekday() >= 5


def _today_md():
    """返回当前日期的 'MM-DD' 字符串，用于节日匹配。"""
    return datetime.now().strftime("%m-%d")


def _pick_greeting(pool, slot, time_str, is_weekend):
    """
    从话术池中随机选取一条问候语。
    优先 weekend_{slot}，无对应 key 时回退到 slot。
    """
    if is_weekend:
        weekend_key = "weekend_{}".format(slot)
        if weekend_key in pool and pool[weekend_key]:
            return _fill_time(random.choice(pool[weekend_key]), time_str)
    return _fill_time(random.choice(pool[slot]), time_str)


def _pick_holiday_greeting(spring, is_chinese, time_str):
    """
    若今天是节日，从 spring.json 的 holidays 段取话术。
      - international : 中/英场景均提示
      - chinese_only  : 仅中文场景提示（英文场景返回 None）

    Returns:
        话术字符串，或 None（今天不是节日 / json 未配置对应话术）
    """
    if not spring:
        return None
    holidays = spring.get("holidays")
    if not isinstance(holidays, dict):
        return None

    today = _today_md()
    lang_key = "chinese" if is_chinese else "english"

    # 1) 国际通用节日（两种语言都提示）
    intl = holidays.get("international")
    if isinstance(intl, dict):
        entry = intl.get(today)
        if isinstance(entry, dict):
            pool = entry.get(lang_key)
            if isinstance(pool, list) and pool:
                return _fill_time(random.choice(pool), time_str)

    # 2) 中国特有节日（仅中文场景）
    if is_chinese:
        cn_only = holidays.get("chinese_only")
        if isinstance(cn_only, dict):
            entry = cn_only.get(today)
            if isinstance(entry, dict):
                pool = entry.get("chinese")
                if isinstance(pool, list) and pool:
                    return _fill_time(random.choice(pool), time_str)

    return None


# ── 配置加载（惰性缓存） ──────────────────────────────

_spring_cache = None
_spring_loaded = False


def _load_spring():
    """加载 spring.json，惰性缓存。失败返回 None。"""
    global _spring_cache, _spring_loaded
    if _spring_loaded:
        return _spring_cache
    _spring_loaded = True
    try:
        with open(SPRING_CONFIG_PATH, "r", encoding="utf-8") as f:
            _spring_cache = json.load(f)
        return _spring_cache
    except Exception:
        _spring_cache = None
        return None


# ── 主入口 ────────────────────────────────────────────

def show_startup_greeting(language="chinese"):
    """
    显示启动时段问候 + 凌晨交互式睡眠确认。

    Args:
        language: "chinese" | "english"

    Returns:
        True  → 用户选择睡觉，调用方应 sys.exit(0)
        False → 正常继续
    """
    # spring-mode 开关（默认开启；文件内容为 false/0/no/off/disable 时禁用）
    if os.path.exists(_SPRING_MODE_PATH):
        try:
            with open(_SPRING_MODE_PATH, "r") as f:
                mode_val = f.read().strip().lower()
            if mode_val in ("false", "0", "no", "off", "disable", "disabled"):
                return False
        except Exception:
            pass

    now = datetime.now()
    current_hour = now.hour
    time_str = now.strftime("%H:%M")
    slot = _get_slot(current_hour)
    weekend = _is_weekend()
    is_chinese = (language == "chinese")

    spring = _load_spring()

    # ── 选消息：节日话术优先，否则走时段话术 ──
    msg = _pick_holiday_greeting(spring, is_chinese, time_str)

    if msg is None:
        try:
            if spring:
                lang_key = "chinese" if is_chinese else "english"
                greetings = spring.get("greetings", {}).get(lang_key, {})
                if greetings and slot in greetings and greetings[slot]:
                    msg = _pick_greeting(greetings, slot, time_str, weekend)
                else:
                    raise ValueError(
                        "spring.json missing greetings.{}.{}".format(lang_key, slot)
                    )
            else:
                raise ValueError("spring.json not available")
        except Exception:
            fallback = _FALLBACK_CN if is_chinese else _FALLBACK_EN
            msg = _fill_time(random.choice(fallback[slot]), time_str)

    print()
    print(Fore.YELLOW + msg + Style.RESET_ALL)
    print()

    # ── 凌晨交互（仅 night 时段） ──
    if slot != "night":
        return False

    # 非交互终端（管道 / 重定向）不阻塞
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False

    prompt = _SLEEP_PROMPT["chinese" if is_chinese else "english"]
    try:
        # 兼容 raw 终端的输入：处理 \r（Enter 原始字符）和 \n
        sys.stdout.write(Fore.CYAN + prompt + Style.RESET_ALL)
        sys.stdout.flush()
        ans_chars = []
        while True:
            ch = sys.stdin.read(1)
            if not ch or ch in ("\n", "\r"):
                break
            ans_chars.append(ch)
        ans = "".join(ans_chars).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False

    if ans in _SLEEP_YES_MATCH:
        goodbye_pool = _GOODBYE_CN if is_chinese else _GOODBYE_EN
        print(Fore.GREEN + random.choice(goodbye_pool) + Style.RESET_ALL)
        return True
    else:
        no_pool = _NO_RESPONSE_CN if is_chinese else _NO_RESPONSE_EN
        print(Fore.MAGENTA + random.choice(no_pool) + Style.RESET_ALL)
        return False


# ── 自测 ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("lib/spring.py 自测")
    print("=" * 50)
    now = datetime.now()
    print("当前时间: {}".format(now.strftime("%Y-%m-%d %H:%M:%S")))
    print("时段: {}".format(_get_slot(now.hour)))
    print("周末: {}".format(_is_weekend()))
    print("日期 key: {}".format(_today_md()))

    spring = _load_spring()
    print("spring.json 状态: {}".format("已加载" if spring else "缺失，使用回退"))

    if spring:
        _t = now.strftime("%H:%M")
        msg_cn = _pick_holiday_greeting(spring, True, _t)
        msg_en = _pick_holiday_greeting(spring, False, _t)
        print("节日话术（中文场景）: {}".format(msg_cn if msg_cn else "（今天不是节日）"))
        print("节日话术（英文场景）: {}".format(msg_en if msg_en else "（今天不是节日）"))

    print("\n── 中文问候 ──")
    show_startup_greeting("chinese")

    print("\n── 英文问候 ──")
    show_startup_greeting("english")