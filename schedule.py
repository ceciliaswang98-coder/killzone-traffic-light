# -*- coding: utf-8 -*-
"""生成三个灯的开/关时段表，并渲染成一张周视图。

    python schedule.py     ->  打印表格 + schedule.png
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

from killzones import BEIJING, ET, KILLZONES, KZ, trading_week

FONT_DIR = r"C:\Windows\Fonts"
WEEK_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
PHASE_CN = {"off": "灭", "kz": "亮", "pre": "预热", "market": "满"}


def font(name, size):
    try:
        return ImageFont.truetype(f"{FONT_DIR}\\{name}", size)
    except OSError:
        return ImageFont.truetype(f"{FONT_DIR}\\msyh.ttc", size)


def probe() -> None:
    """打印每个灯的时间段（夏令时 / 冬令时）。"""
    for season, month, day in (("夏令时 EDT", 7, 15), ("冬令时 EST", 1, 15)):
        print(f"\n{'=' * 76}\n{season}（美国）\n{'=' * 76}")
        sample = datetime(2026, month, day, 12, 0, tzinfo=BEIJING)
        w_open, w_close = trading_week(sample.astimezone(ET))
        print(
            f"交易周：北京 "
            f"{w_open.astimezone(BEIJING):%a %H:%M} 开盘 → "
            f"{w_close.astimezone(BEIJING):%a %H:%M} 收盘"
        )
        for k in KILLZONES:
            et_date = sample.astimezone(ET).date()
            kz_s, kz_e = k.et_window(et_date)
            mk = k.et_market(et_date)
            print(f"\n  [{k.short}] {k.label}")
            if mk is None:
                print(
                    f"      亮   {kz_s.astimezone(BEIJING):%H:%M} – "
                    f"{kz_e.astimezone(BEIJING):%H:%M}"
                    + ("（次日）" if kz_e.astimezone(BEIJING).date()
                       != kz_s.astimezone(BEIJING).date() else "")
                    + "    窗口内匀速填充"
                )
            else:
                m_s, m_e = mk
                print(
                    f"      预热 {kz_s.astimezone(BEIJING):%H:%M} – "
                    f"{m_s.astimezone(BEIJING):%H:%M}    灯亮但没满"
                )
                print(
                    f"      满灯 {m_s.astimezone(BEIJING):%H:%M} – "
                    f"{m_e.astimezone(BEIJING):%H:%M}     {k.market_label}开盘"
                    + ("（次日）" if m_e.astimezone(BEIJING).date()
                       != m_s.astimezone(BEIJING).date() else "")
                )
            print(f"      灭   其余时间（周末与周五收盘后不亮）")


# --------------------------------------------------------------------------- #
# 周视图
# --------------------------------------------------------------------------- #
CELL_W, ROW_H, LEFT = 34, 30, 74
HEAD = 34


def render() -> None:
    seasons = [("夏令时 EDT", 2026, 7, 20), ("冬令时 EST", 2026, 1, 19)]  # 都是周一
    lamps = list(KILLZONES)
    panel_h = HEAD + ROW_H * 7 + 12
    W = 40 + (LEFT + 24 * CELL_W + 40) * len(seasons)
    H = 92 + panel_h * len(lamps) + 20
    img = Image.new("RGB", (W, H), (17, 19, 24))
    d = ImageDraw.Draw(img)

    d.text((40, 22), "Kill Zone 红绿灯 · 一周开关时段（北京时间）", font=font("msyhbd.ttc", 22),
           fill=(232, 234, 237))
    d.text((40, 54), "横轴 = 北京时间 0–24 时；颜色越实表示越接近/已到市场开盘。周末整段不亮。",
           font=font("msyh.ttc", 13), fill=(139, 146, 158))

    for li, k in enumerate(lamps):
        top = 92 + li * panel_h
        d.text((40, top), f"[{k.short}] {k.label}" + (f" · 满灯 = {k.market_label}开盘" if k.market_open else ""),
               font=font("msyhbd.ttc", 15), fill=(210, 215, 224))
        body = top + HEAD
        for si, (sname, y, mo, mday) in enumerate(seasons):
            x0 = 40 + si * (LEFT + 24 * CELL_W + 40)
            d.text((x0, body - 22), sname, font=font("msyhbd.ttc", 12), fill=(150, 157, 168))
            # 小时刻度
            for hh in range(0, 25, 3):
                gx = x0 + LEFT + hh * CELL_W
                d.line((gx, body - 4, gx, body + ROW_H * 7), fill=(40, 44, 52))
                if hh < 24:
                    d.text((gx + 2, body - 20), f"{hh:02d}", font=font("msyh.ttc", 9),
                           fill=(95, 102, 114))
            monday = datetime(y, mo, mday, 0, 0, tzinfo=BEIJING)
            for di in range(7):
                ry = body + di * ROW_H
                base = monday + timedelta(days=di)
                lab = f"{WEEK_CN[di]} {base:%m-%d}"
                d.text((x0 + LEFT - 8, ry + 8), lab, font=font("msyh.ttc", 11),
                       fill=(150, 157, 168), anchor="ra")
                d.rectangle((x0 + LEFT, ry + 3, x0 + LEFT + 24 * CELL_W, ry + ROW_H - 3),
                            fill=(24, 27, 33))
                # 逐分钟采样太慢，按 5 分钟步长扫
                prev = None
                run_start = None
                for mi in range(0, 24 * 60 + 5, 5):
                    t = base + timedelta(minutes=mi)
                    fillv, phase = k.lamp(t)
                    if phase != prev:
                        if prev is not None and prev != "off":
                            xa = x0 + LEFT + (run_start * CELL_W / 60)
                            xb = x0 + LEFT + (mi * CELL_W / 60)
                            col = (k.color if prev == "market" else
                                   _dim(k.color, 0.42 if prev == "pre" else 0.30))
                            d.rectangle((xa, ry + 5, max(xb, xa + 1), ry + ROW_H - 5), fill=col)
                        prev, run_start = phase, mi
                if prev is not None and prev != "off":
                    xa = x0 + LEFT + (run_start * CELL_W / 60)
                    xb = x0 + LEFT + 24 * CELL_W
                    col = (k.color if prev == "market" else
                           _dim(k.color, 0.42 if prev == "pre" else 0.30))
                    d.rectangle((xa, ry + 5, max(xb, xa + 1), ry + ROW_H - 5), fill=col)

    y = H - 16
    for i, (name, col, alpha) in enumerate(
        (("灭", (36, 40, 48), 1.0), ("亮（窗口内）", _dim(KZ["london"].color, 0.30), 1.0),
         ("预热（未满）", _dim(KZ["nyam"].color, 0.42), 1.0), ("满灯 = 市场开盘", KZ["nyam"].color, 1.0))
    ):
        x = 40 + i * 170
        d.rectangle((x, y, x + 22, y + 12), fill=col)
        d.text((x + 30, y - 2), name, font=font("msyh.ttc", 11), fill=(150, 157, 168))

    img.save("schedule.png", "PNG")
    print(f"\nOK -> schedule.png  {img.width}x{img.height}")


def _dim(hexcolor: str, factor: float) -> tuple[int, int, int]:
    h = hexcolor.lstrip("#")
    return tuple(int(int(h[i:i + 2], 16) * factor) for i in (0, 2, 4))


if __name__ == "__main__":
    probe()
    render()
