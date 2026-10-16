# -*- coding: utf-8 -*-
"""ICT Kill Zone 时间模型。

权威口径：kill zone 由 Michael Huddleston 用【纽约本地时间 ET】定义。
本项目只用其中三个（不做亚洲盘）：

    伦敦     02:00–05:00 ET   （外汇最强时段，EUR/GBP 主战场）
    纽约 AM  08:30–11:00 ET   （黄金 / 纳指，两段式：预热 → 满灯）
    纽约 PM  13:30–16:00 ET   （尾盘调仓，顺势为主）

    （亚洲盘 20:00–00:00 ET 以及 Silver Bullet 03/10/14 ET 保留在常量里备查，未启用。）

重要：ET 才是定义基准，UTC 不是。
    网上大量表格把「UTC 01:00-05:00 / 07:00-10:00」当成固定值，那其实是
    【冬令时 EST】的换算结果。到了夏令时，同一批 UTC 数字对应的真实 kill zone
    会整体偏 1 小时。所以本模块只认 ET，其余全部由 tzdata 推导。

对你（UTC+8 北京）的影响：kill zone 在北京时间里随美国冬夏令时整体平移 1 小时。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
ET = ZoneInfo("America/New_York")

# 美国夏令时切换日（用于标注「下周灯会挪 1 小时」）
DST_NOTE = "美国夏令时切换后，下表北京时间整体平移 1 小时"

# 横排布局的地区简写
SHORT: dict[str, str] = {
    "london": "LDN",
    "nyam": "NY",
    "nypm": "PM",
}

# 外汇交易周：周日 17:00 ET 开盘 → 周五 17:00 ET 收盘。
# 周六、周日白天、以及周五收盘之后都不交易，灯一律不亮。
FOREX_OPEN_HM = (17, 0)   # 周日 17:00 ET
FOREX_DAYS = 5            # 周日 17:00 + 5 天 = 周五 17:00 ET


def trading_week(et_now: datetime) -> tuple[datetime, datetime]:
    """包含 et_now 的那个交易周：[周日 17:00 ET, 周五 17:00 ET)。"""
    days_since_sunday = (et_now.weekday() + 1) % 7
    sunday = et_now.date() - timedelta(days=days_since_sunday)
    open_dt = datetime.combine(sunday, time(*FOREX_OPEN_HM), tzinfo=ET)
    if et_now < open_dt:
        open_dt -= timedelta(days=7)
    return open_dt, open_dt + timedelta(days=FOREX_DAYS)


def in_trading_week(et_now: datetime) -> bool:
    o, c = trading_week(et_now)
    return o <= et_now < c


@dataclass(frozen=True)
class KillZone:
    key: str
    label: str
    color: str
    start: tuple[int, int]  # ET
    end: tuple[int, int]    # ET
    note: str = ""
    # 可选：关联的真实市场时段（ET）。给了之后灯就是两段式 ——
    #   从 KZ 起点(start)开始填充 → 市场开盘(market_open)时正好涨满 100%
    #   → 满灯维持到市场收盘(market_close)才灭
    # 这样「灯只是亮着」= 还在预热，不等于市场开盘；「灯满」才是市场开了。
    market_open: tuple[int, int] | None = None
    market_close: tuple[int, int] | None = None
    market_label: str = ""

    @property
    def short(self) -> str:
        """横排布局用的地区简写。"""
        return SHORT.get(self.key, self.label)

    # ---- ET 原始时刻 ----
    def et_window(self, et_date: date) -> tuple[datetime, datetime]:
        s = datetime.combine(et_date, time(*self.start), tzinfo=ET)
        e = datetime.combine(et_date, time(*self.end), tzinfo=ET)
        if e <= s:
            e += timedelta(days=1)
        return s, e

    def et_market(self, et_date: date) -> tuple[datetime, datetime] | None:
        if self.market_open is None or self.market_close is None:
            return None
        s = datetime.combine(et_date, time(*self.market_open), tzinfo=ET)
        e = datetime.combine(et_date, time(*self.market_close), tzinfo=ET)
        if e <= s:
            e += timedelta(days=1)
        return s, e

    def occurrence(self, et_date: date) -> tuple[datetime, datetime]:
        """返回该 ET 日期上的 kill zone，转成北京时间。"""
        s, e = self.et_window(et_date)
        return s.astimezone(BEIJING), e.astimezone(BEIJING)

    def interval_around(self, moment: datetime) -> tuple[datetime, datetime]:
        """覆盖 moment 的那个 kill zone 实例（含前后一天以便跨午夜判断）。"""
        et_date = moment.astimezone(ET).date()
        for off in (-1, 0, 1):
            s, e = self.occurrence(et_date + timedelta(days=off))
            if s <= moment < e:
                return s, e
        return self.next_after(moment)

    def is_active(self, moment: datetime) -> bool:
        s, e = self.interval_around(moment)
        return s <= moment < e

    def next_after(self, moment: datetime) -> tuple[datetime, datetime]:
        et_date = moment.astimezone(ET).date()
        cands = [self.occurrence(et_date + timedelta(days=o)) for o in (0, 1, 2)]
        future = [(s, e) for s, e in cands if s > moment]
        return min(future, key=lambda x: x[0]) if future else cands[0]

    def progress(self, moment: datetime) -> float | None:
        """当前处于该 kill zone 的进度 0.0–1.0；不在窗口内返回 None。"""
        s, e = self.interval_around(moment)
        if not (s <= moment < e):
            return None
        return (moment - s).total_seconds() / (e - s).total_seconds()

    # ---- 灯的状态（两段式） ----
    def lamp(self, moment: datetime) -> tuple[float, str]:
        """返回 (填充比例 0.0–1.0, 阶段)。

        阶段：
            "off"     灭
            "kz"      纯 kill zone 窗口内匀速填充（没关联市场时用）
            "pre"     预热中，正在朝市场开盘涨（还没满）
            "market"  市场已开盘，满灯
        """
        et_now = moment.astimezone(ET)
        if not in_trading_week(et_now):
            return 0.0, "off"

        if self.et_market(et_now.date()) is None:
            for off in (-1, 0, 1):
                kz_s, kz_e = self.et_window(et_now.date() + timedelta(days=off))
                if kz_s <= et_now < kz_e:
                    return (et_now - kz_s).total_seconds() / (kz_e - kz_s).total_seconds(), "kz"
            return 0.0, "off"

        for off in (-1, 0, 1):
            d = et_now.date() + timedelta(days=off)
            kz_s, _ = self.et_window(d)
            m_s, m_e = self.et_market(d)
            if kz_s <= et_now < m_e:
                if et_now >= m_s:
                    return 1.0, "market"
                return (et_now - kz_s).total_seconds() / (m_s - kz_s).total_seconds(), "pre"
        return 0.0, "off"

    def next_on(self, moment: datetime) -> datetime | None:
        """下一次开始亮灯的时刻（北京时间）—— 即下一个交易日的 KZ 起点。"""
        et_now = moment.astimezone(ET)
        for off in range(0, 12):
            kz_s, _ = self.et_window(et_now.date() + timedelta(days=off))
            if kz_s > et_now and in_trading_week(kz_s):
                return kz_s.astimezone(BEIJING)
        return None

    def end_of_on(self, moment: datetime) -> datetime | None:
        """当前这一轮亮灯何时结束（北京时间）。"""
        et_now = moment.astimezone(ET)
        if not in_trading_week(et_now):
            return None
        _, week_close = trading_week(et_now)
        rng = range(-1, 2)
        if self.et_market(et_now.date()) is None:
            for off in rng:
                kz_s, kz_e = self.et_window(et_now.date() + timedelta(days=off))
                if kz_s <= et_now < kz_e:
                    return min(kz_e, week_close).astimezone(BEIJING)
            return None
        for off in rng:
            d = et_now.date() + timedelta(days=off)
            kz_s, _ = self.et_window(d)
            _, m_e = self.et_market(d)
            if kz_s <= et_now < m_e:
                return min(m_e, week_close).astimezone(BEIJING)
        return None




KILLZONES: tuple[KillZone, ...] = (
    KillZone("london", "伦敦KZ", "#3B82F6", (2, 0), (5, 0), "外汇最强 · EUR/GBP 主战场"),
    KillZone("nyam", "纽约AM", "#F97316", (8, 30), (11, 0), "黄金 / 纳指 参考窗口",
             market_open=(9, 30), market_close=(16, 0), market_label="纳指"),
    KillZone("nypm", "纽约PM", "#A855F7", (13, 30), (16, 0), "尾盘调仓 · 顺势为主"),
)

SILVER_BULLETS: tuple[tuple[int, int], ...] = ((3, 0), (10, 0), (14, 0))  # ET，各 1 小时

KZ = {k.key: k for k in KILLZONES}


def active_now(moment: datetime) -> list[KillZone]:
    return [k for k in KILLZONES if k.is_active(moment)]


def next_event(moment: datetime) -> tuple[datetime, KillZone, bool] | None:
    """下一个 kill zone 开/关事件。返回 (时刻, killzone, 是否开盘)。"""
    cands: list[tuple[datetime, KillZone, bool]] = []
    for k in KILLZONES:
        s, e = k.interval_around(moment)
        if s > moment:
            cands.append((s, k, True))
        elif e > moment:
            cands.append((e, k, False))
        else:
            s2, _ = k.next_after(moment)
            cands.append((s2, k, True))
    return min(cands, key=lambda x: x[0]) if cands else None


def silver_bullets_bj(moment: datetime) -> list[str]:
    out = []
    for hm in SILVER_BULLETS:
        s = datetime.combine(moment.astimezone(ET).date(), time(*hm), tzinfo=ET)
        e = s + timedelta(hours=1)
        out.append(f"{s.astimezone(BEIJING):%H:%M}–{e.astimezone(BEIJING):%H:%M}")
    return out


def print_table() -> None:
    scenarios = [
        ("夏令时 EDT (UTC-4)", datetime(2026, 7, 15, 12, 0, tzinfo=BEIJING)),
        ("冬令时 EST (UTC-5)", datetime(2026, 1, 15, 12, 0, tzinfo=BEIJING)),
        ("错位周 3/20 (美国已夏令时)", datetime(2026, 3, 20, 12, 0, tzinfo=BEIJING)),
        ("错位周 10/28 (美国仍夏令时)", datetime(2026, 10, 28, 12, 0, tzinfo=BEIJING)),
    ]
    for label, moment in scenarios:
        et_now = moment.astimezone(ET)
        print(f"\n{'=' * 78}\n{label}   纽约 {et_now:%H:%M} {et_now.tzname()}\n{'=' * 78}")
        print(f"{'Kill Zone':<12}{'纽约时间(ET)':<18}{'北京时间':<20}{'时长':<8}{'说明'}")
        print("-" * 78)
        for k in KILLZONES:
            s, e = k.occurrence(et_now.date())
            dur = (e - s).total_seconds() / 3600
            et_txt = f"{k.start[0]:02d}:{k.start[1]:02d}–{k.end[0]:02d}:{k.end[1]:02d}"
            bj = f"{s:%H:%M}–{e:%H:%M}" + ("(+1)" if e.date() != s.date() else "")
            print(f"{k.label:<12}{et_txt:<18}{bj:<20}{dur:g}h{'':<4}{k.note}")
        print(f"{'Silver Bullet':<12}{'03/10/14 ET':<18}{' · '.join(silver_bullets_bj(moment))}")


def main() -> None:
    now = datetime.now(BEIJING)
    print(f"北京时间 {now:%Y-%m-%d %H:%M:%S}   |   纽约 {now.astimezone(ET):%H:%M} {now.astimezone(ET).tzname()}")
    act = active_now(now)
    if act:
        for k in act:
            s, e = k.interval_around(now)
            p = (k.progress(now) or 0) * 100
            print(f"  进行中：{k.label}  北京 {s:%H:%M}–{e:%H:%M}  进度 {p:.0f}%")
    else:
        nxt = next_event(now)
        if nxt:
            t, k, opening = nxt
            d = t - now
            print(f"  当前无 KZ。下一个：{k.label} {'开盘' if opening else '结束'} {t:%H:%M}（还有 {d}）")
    print_table()


if __name__ == "__main__":
    main()
