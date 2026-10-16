# -*- coding: utf-8 -*-
"""画 Kill Zone 红绿灯的图标（可爱简约）。

    python make_logo.py

产出：
    logo.ico          桌面 / 开始菜单快捷方式用（三灯全亮）
    icon_active.ico   托盘用，三灯亮   = 当前有 Kill Zone 进行中
    icon_idle.ico     托盘用，三灯暗   = 当前没有
    logo.png          预览图

设计：竖直红绿灯外形，三颗圆灯
      上 = LDN 伦敦KZ（蓝）  中 = NY 纽约AM（橙）  下 = PM 纽约PM（紫）
      圆角放大、加一点高光，16px 下也能认出是"红绿灯"。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

S = 8                      # 超采样
BASE = 256

BODY = (35, 40, 51, 255)       # #232833
BODY_EDGE = (66, 74, 90, 255)
GLASS = (22, 25, 32, 255)
LIGHTS = ((0x3B, 0x82, 0xF6), (0xF9, 0x73, 0x16), (0xA8, 0x55, 0xF7))  # LDN / NY / PM
SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
CENTER_Y = (70, 128, 186)

# 快捷方式图标必须放在这里。
# 原因：资源管理器提取 .lnk 图标是在一个受限的低完整性进程里做的，
#       它读不到某些目录（例如工作目录带特殊 ACL 时），图标就会退化成"白纸"。
#       放到用户目录里就正常。
USER_ICON_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "KillZoneLight"
USER_ICON = USER_ICON_DIR / "logo.ico"


def rr(d, box, r, **kw):
    d.rounded_rectangle([v * S for v in box], radius=r * S, **kw)


def circle(d, cx, cy, r, **kw):
    d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], **kw)


def _dim(rgb, k: float):
    return tuple(int(c * k) for c in rgb)


def build(active: bool) -> Image.Image:
    """active=True 三灯亮（有 KZ）；False 三灯暗（无 KZ）。"""
    img = Image.new("RGBA", (BASE * S, BASE * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 亮灯时给灯身描边加一点同色辉光，暗的时候用普通描边
    if active:
        edge = (96, 106, 126, 255)
    else:
        edge = (58, 64, 76, 255)

    # 灯身：矮胖的竖直胶囊，圆角大一点更可爱
    rr(d, (70, 16, 186, 240), 52, fill=BODY, outline=edge, width=3 * S)
    rr(d, (88, 30, 168, 96), 34, fill=(255, 255, 255, 16 if active else 8))

    for rgb, cy in zip(LIGHTS, CENTER_Y):
        circle(d, 128, cy, 29, fill=GLASS)
        if active:
            circle(d, 128, cy, 24, fill=rgb + (255,))
            circle(d, 120, cy - 9, 7, fill=(255, 255, 255, 105))
        else:
            # 暗灯：保留一点本色，看得出是"三颗灯"，但明显是灭的
            circle(d, 128, cy, 24, fill=_dim(rgb, 0.34) + (255,))
            circle(d, 128, cy, 24, outline=_dim(rgb, 0.5) + (255,), width=2 * S)

    return img.resize((BASE, BASE), Image.LANCZOS)


def preview(on: Image.Image, off: Image.Image) -> None:
    from PIL import ImageFont

    pad = 26
    ladder = [(64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    W = pad * 2 + 132 + 46 + 132 + 46 + sum(s for s, _ in ladder) + 24 * (len(ladder) - 1) + 10
    H = 262
    out = Image.new("RGB", (W, H), (17, 19, 24))
    d = ImageDraw.Draw(out)
    try:
        big = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 21)
        small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 12)
    except OSError:
        big = small = None

    d.text((pad, pad - 4), "Kill Zone 红绿灯 · 图标", font=big, fill=(232, 234, 237))
    d.text((pad, pad + 26), "上蓝 LDN · 中橙 NY · 下紫 PM", font=small, fill=(139, 146, 158))

    x = pad
    for im, cap in ((on, "桌面 / 有 KZ"), (off, "无 KZ")):
        t = im.resize((132, 132), Image.LANCZOS)
        out.paste(t, (x, pad + 56), t)
        d.text((x + 34, pad + 194), cap, font=small, fill=(150, 157, 168))
        x += 132 + 46

    d.text((x, 112), "实际尺寸（托盘 16px 起）", font=small, fill=(139, 146, 158))
    for s, _ in ladder:
        t = on.resize((s, s), Image.LANCZOS)
        out.paste(t, (x, 190 - s), t)
        d.text((x, 200), f"{s}", font=small, fill=(107, 114, 126))
        x += s + 24

    out.save("logo.png", "PNG")
    print(f"OK -> logo.png  {out.width}x{out.height}")


if __name__ == "__main__":
    on, off = build(True), build(False)

    on.save("logo.ico", sizes=SIZES)
    print("OK -> logo.ico（桌面快捷方式用）")
    on.save("icon_active.ico", sizes=SIZES)
    print("OK -> icon_active.ico（托盘：有 KZ）")
    off.save("icon_idle.ico", sizes=SIZES)
    print("OK -> icon_idle.ico（托盘：无 KZ）")

    USER_ICON_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile("logo.ico", USER_ICON)
    print(f"OK -> {USER_ICON}")
    print("     图标放这里的原因见 README：D:\\Workspace 里的图标 shell 读不到")

    preview(on, off)
