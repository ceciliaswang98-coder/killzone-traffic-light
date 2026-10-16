# -*- coding: utf-8 -*-
"""Kill Zone 红绿灯 —— 桌面右下角三个灯，可拖到任意角落，可收进托盘。

基准时区：北京时间（UTC+8）。冬夏令时全自动，无需手动干预。
数据来源：ICT kill zone 由纽约本地时间 (ET) 定义，本程序用 IANA tzdata 换算。

操作：
    左键拖动        移动位置（松手自动吸附到最近的角）
    滚轮            调整不透明度
    双击            收进托盘（图标在任务栏右下角）
    右键            菜单
    托盘图标        左键单击 = 显示 / 隐藏挂件；右键 = 同一个菜单
    托盘图标变绿    说明当前有 Kill Zone 正在进行

命令行 --at "2026-07-15 22:00"  按指定北京时间预览（调试 / 截图用）

零第三方依赖：只用标准库 tkinter + zoneinfo + ctypes。
"""

from __future__ import annotations

import ctypes
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont

from killzones import BEIJING, KILLZONES, KZ, KillZone
from tray import TrayIcon

HERE = Path(__file__).parent
CONFIG_PATH = Path(__file__).with_name("config.json")

BG = "#14171C"
BORDER = "#2E333D"
LAMP_OFF = "#1E2229"
LAMP_OFF_BORDER = "#363C48"
LABEL_ON = "#DCE1E8"
LABEL_OFF = "#8B929E"
CARD_BG = "#1D2027"      # 设置窗口的标题栏 / 按钮底色
ACCENT = "#3B82F6"
CARD_R = 9           # 卡片圆角半径（逻辑像素）

# ---- 逻辑像素（96 DPI 下的设计尺寸） ----
# 注意 pad_top / pad_bottom 不是相等的：横排的文字用 anchor="n" 绘制，
# 字形下方还有一段 descent 空白（本机字体实测 5 物理像素），
# 所以底部要多留一点补偿，否则内容会看着偏上。
V = dict(win_w=118, pad_top=9, pad_bottom=9, row_h=24, lamp=17,
         lamp_x=11, text_x=38, f_label=9)
H = dict(cell_w=36, cell_gap=6, pad_x=10, pad_top=9, pad_bottom=7, lamp=19,
         label_gap=3, label_h=11, f_label=8)
MARGIN = 18

DEFAULT_CFG = {
    "zones": ["london", "nyam", "nypm"],
    "layout": "h",
    "alpha": 0.92,
    "topmost": True,
    "visible": True,
    "notify_on_open": True,
    "x": None,
    "y": None,
    "pre_alert_min": 10,
    "tick_ms": 400,
}
LAYOUT_NAMES = {"h": "横排（简写）", "v": "竖排（全称）"}


# --------------------------------------------------------------------------- #
# 平台工具
# --------------------------------------------------------------------------- #
class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


def set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def dpi_scale() -> float:
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except Exception:
        return 1.0


def work_area() -> tuple[int, int, int, int]:
    try:
        r = _RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, 1920, 1080


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_cfg() -> dict:
    """读配置。任何异常都不能让你的设置凭空消失 —— 逐键容错，不认识/坏掉的键才回退默认。"""
    cfg = dict(DEFAULT_CFG)
    if not CONFIG_PATH.exists():
        return cfg
    try:
        # utf-8-sig：容忍别人（编辑器 / PowerShell Set-Content）写进去的 BOM。
        # 用 utf-8 读带 BOM 的文件会让 json.loads 直接抛异常，进而静默丢设置。
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(f"[warn] config.json 解析失败，本次使用默认值（文件未改动）：{exc}", file=sys.stderr)
        return cfg
    if not isinstance(data, dict):
        print("[warn] config.json 不是对象，本次使用默认值", file=sys.stderr)
        return cfg
    for key, val in data.items():
        if key in DEFAULT_CFG:
            cfg[key] = val
    return cfg


def save_cfg(cfg: dict) -> None:
    """写配置。只写认识且可序列化的键；先写临时文件再替换，避免写一半把配置弄坏。"""
    data = {k: v for k, v in cfg.items() if k in DEFAULT_CFG}
    try:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)
    except Exception as exc:
        print(f"[warn] 配置保存失败：{exc}", file=sys.stderr)


def active_zones(cfg: dict) -> tuple[KillZone, ...]:
    keys = [k for k in cfg.get("zones", []) if k in KZ]
    return tuple(KZ[k] for k in keys) or (KZ["london"], KZ["nyam"], KZ["nypm"])


# --------------------------------------------------------------------------- #
# 挂件
# --------------------------------------------------------------------------- #
class KillZoneLight:
    def __init__(self, offset: timedelta | None = None, overrides: dict | None = None) -> None:
        self.cfg = load_cfg()
        if overrides:
            self.cfg.update(overrides)
        self.offset = offset or timedelta(0)
        self.zones = active_zones(self.cfg)
        self.s = dpi_scale()
        self.visible = True
        self._prev_phase: dict[str, str] = {}
        self.settings: tk.Toplevel | None = None
        self._render_key = None
        self._alpha_syncing = False

        self.root = tk.Tk()
        self.root.title("Kill Zone 红绿灯")
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", float(self.cfg["alpha"]))
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self.canvas = tk.Canvas(
            self.root, width=self.win_w, height=self.win_h,
            bg=BG, highlightthickness=0, bd=0,
        )
        self.canvas.pack()

        fam = "Microsoft YaHei UI"
        # size 用负数 = 像素单位，避免 Tk 按 DPI 再缩放一次
        self.f_label_h = tkfont.Font(family=fam, size=-self.px(H["f_label"]), weight="bold")
        self.f_label_v = tkfont.Font(family=fam, size=-self.px(V["f_label"]), weight="bold")
        self.f_set = tkfont.Font(family=fam, size=-self.px(9))
        self.f_set_b = tkfont.Font(family=fam, size=-self.px(9), weight="bold")
        self.f_title = tkfont.Font(family=fam, size=-self.px(10), weight="bold")

        self._moved = False
        self._bind_events()
        self._place_initial()

        # 托盘
        self.tray = TrayIcon(
            str(Path(__file__).with_name("icon_idle.ico")),
            str(Path(__file__).with_name("icon_active.ico")),
            "Kill Zone 红绿灯",
        )
        self.tray.start()

        if not self.cfg.get("visible", True):
            self.set_visible(False)

        self.tick()

    # -------- 尺寸 --------
    def px(self, v: float) -> int:
        return max(1, int(round(v * self.s)))

    @property
    def layout(self) -> str:
        lay = self.cfg.get("layout", "h")
        return lay if lay in LAYOUT_NAMES else "h"

    @property
    def win_w(self) -> int:
        n = len(self.zones)
        if self.layout == "h":
            w = H["pad_x"] * 2 + H["cell_w"] * n + H["cell_gap"] * (n - 1)
        else:
            w = V["win_w"]
        return self.px(w)

    @property
    def win_h(self) -> int:
        n = len(self.zones)
        if self.layout == "h":
            h = H["pad_top"] + H["lamp"] + H["label_gap"] + H["label_h"] + H["pad_bottom"]
        else:
            h = V["pad_top"] + V["row_h"] * n + V["pad_bottom"]
        return self.px(h)

    def now(self) -> datetime:
        return datetime.now(BEIJING) + self.offset

    # -------- 事件 --------
    def _bind_events(self) -> None:
        c = self.canvas
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Button-3>", self._on_menu)
        c.bind("<Double-Button-1>", lambda e: self.set_visible(False))
        c.bind("<MouseWheel>", self._on_wheel)

    def _on_press(self, ev) -> None:
        self._drag = (ev.x_root, ev.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, ev) -> None:
        if not getattr(self, "_drag", None):
            return
        px, py, wx, wy = self._drag
        self.root.geometry(f"+{wx + (ev.x_root - px)}+{wy + (ev.y_root - py)}")
        self._moved = True

    def _on_release(self, ev) -> None:
        if self._moved:
            self._moved = False
            self._snap_and_save()

    def _on_wheel(self, ev) -> None:
        step = 0.05 if ev.delta > 0 else -0.05
        self._set_alpha(min(1.0, max(0.25, float(self.cfg["alpha"]) + step)))

    def _on_menu(self, ev) -> None:
        self.open_settings((ev.x_root, ev.y_root))

    # -------- 显示 / 隐藏 --------
    def set_visible(self, v: bool) -> None:
        if v == self.visible:
            return
        self.visible = v
        self.cfg["visible"] = v
        save_cfg(self.cfg)
        if v:
            self.root.deiconify()
            self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        else:
            self.root.withdraw()

    def toggle_visible(self) -> None:
        self.set_visible(not self.visible)

    # -------- 位置 --------
    def _default_pos(self) -> tuple[int, int]:
        left, top, right, bottom = work_area()
        return right - self.win_w - self.px(MARGIN), bottom - self.win_h - self.px(MARGIN)

    def _place_initial(self) -> None:
        dx, dy = self._default_pos()
        x, y = self.cfg.get("x"), self.cfg.get("y")
        left, top, right, bottom = work_area()
        # 保存的位置若已跑出屏幕（换显示器 / 改分辨率 / 换布局），回到默认位置
        if x is None or y is None or not (left - 50 <= x <= right - 40 and top - 50 <= y <= bottom - 40):
            x, y = dx, dy
        self.root.geometry(f"{self.win_w}x{self.win_h}+{int(x)}+{int(y)}")

    def _snap_and_save(self) -> None:
        left, top, right, bottom = work_area()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        m = self.px(12)
        cx, cy = x + self.win_w / 2, y + self.win_h / 2
        sx = left + m if cx < (left + right) / 2 else right - self.win_w - m
        sy = top + m if cy < (top + bottom) / 2 else bottom - self.win_h - m
        self.root.geometry(f"+{int(sx)}+{int(sy)}")
        self.cfg["x"], self.cfg["y"] = int(sx), int(sy)
        save_cfg(self.cfg)

    def reset_position(self) -> None:
        self.cfg["x"] = self.cfg["y"] = None
        self._place_initial()
        save_cfg(self.cfg)

    def _relayout(self) -> None:
        self.canvas.configure(width=self.win_w, height=self.win_h)
        self.reset_position()
        self.redraw(self.now())

    # -------- 设置窗口 --------
    # 为什么不用 tk.Menu：在「无边框 + 置顶 + 半透明」的窗口上弹出原生菜单不可靠
    # （出现过只剩一个空白灰框、菜单项不绘制）。改成独立的设置窗口，用标准控件。
    def _panel_chk(self, parent, text, var, cmd, fg=None):
        return tk.Checkbutton(
            parent, text=text, variable=var, command=cmd, anchor="w",
            bg=BG, fg=fg or LABEL_OFF, activebackground=BG, activeforeground=LABEL_ON,
            selectcolor=CARD_BG, font=self.f_set, highlightthickness=0, bd=0,
            padx=0, pady=self.px(1),
        )

    def _panel_btn(self, parent, text, cmd):
        return tk.Button(
            parent, text=text, command=cmd, font=self.f_set,
            bg=CARD_BG, fg=LABEL_ON, activebackground=LAMP_OFF_BORDER, activeforeground=LABEL_ON,
            relief="flat", bd=0, highlightthickness=0,
            padx=self.px(10), pady=self.px(3), cursor="hand2",
        )

    def open_settings(self, at: tuple[int, int] | None = None) -> None:
        if self.settings is not None and self.settings.winfo_exists():
            self.settings.lift()
            self.settings.focus_force()
            if at:
                self._place_settings(at)
            return

        P = self.px
        top = tk.Toplevel(self.root)
        self.settings = top
        top.title("Kill Zone 设置")     # 别和挂件同名，否则按标题找窗口会找错
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", max(0.95, min(1.0, float(self.cfg["alpha"]) + 0.20)))
        top.configure(bg=BG)

        # 标题栏：可拖动 + 关闭
        head = tk.Frame(top, bg=CARD_BG, height=P(30))
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="Kill Zone 设置", bg=CARD_BG, fg=LABEL_ON,
                 font=self.f_set_b).pack(side="left", padx=P(10))
        close = tk.Label(head, text="✕", bg=CARD_BG, fg=LABEL_OFF, font=self.f_set_b, cursor="hand2")
        close.pack(side="right", padx=P(10))
        close.bind("<Button-1>", lambda e: self.close_settings())
        head.bind("<Button-1>", self._set_drag_start)
        head.bind("<B1-Motion>", self._set_drag)

        body = tk.Frame(top, bg=BG, padx=P(12), pady=P(10))
        body.pack(fill="both", expand=True)

        # 布局
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(0, P(5)))
        tk.Label(row, text="布局", bg=BG, fg=LABEL_OFF, font=self.f_set,
                 width=5, anchor="w").pack(side="left")
        self._layout_var = tk.StringVar(value=self.layout)
        for key, name in LAYOUT_NAMES.items():
            tk.Radiobutton(
                row, text=name, value=key, variable=self._layout_var, command=self._apply_layout,
                bg=BG, fg=LABEL_OFF, activebackground=BG, activeforeground=LABEL_ON,
                selectcolor=CARD_BG, font=self.f_set, highlightthickness=0, bd=0,
            ).pack(side="left", padx=(0, P(8)))

        # 显示的时段
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(0, P(3)))
        tk.Label(row, text="时段", bg=BG, fg=LABEL_OFF, font=self.f_set,
                 width=5, anchor="w").pack(side="left")
        self._zone_vars: dict[str, tk.BooleanVar] = {}
        for k in KILLZONES:
            var = tk.BooleanVar(value=k.key in self.cfg["zones"])
            self._zone_vars[k.key] = var
            self._panel_chk(row, k.short, var, self._apply_zones, fg=k.color).pack(
                side="left", padx=(0, P(4)))

        # 开关
        self._top_var = tk.BooleanVar(value=bool(self.cfg["topmost"]))
        self._panel_chk(body, "总在最前", self._top_var, self._toggle_top).pack(anchor="w")
        self._notify_var = tk.BooleanVar(value=bool(self.cfg.get("notify_on_open", True)))
        self._panel_chk(body, "开盘时托盘气泡提醒", self._notify_var,
                        self._toggle_notify).pack(anchor="w")

        # 不透明度
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(P(5), 0))
        tk.Label(row, text="不透明度", bg=BG, fg=LABEL_OFF, font=self.f_set,
                 width=5, anchor="w").pack(side="left")
        self._alpha_lbl = tk.Label(row, text=f"{int(float(self.cfg['alpha']) * 100)}%",
                                   bg=BG, fg=LABEL_ON, font=self.f_set_b, width=3, anchor="e")
        self._alpha_lbl.pack(side="right")
        self._alpha_var = tk.DoubleVar(value=float(self.cfg["alpha"]))
        # 注意：command 必须等初值设好之后再挂。
        # 否则 tk.Scale 创建时会用它的默认值触发一次回调，把配置里的不透明度改掉（踩过）。
        sc = tk.Scale(
            row, from_=0.30, to=1.0, resolution=0.05, orient="horizontal",
            variable=self._alpha_var, showvalue=False,
            bg=BG, fg=LABEL_ON, troughcolor=CARD_BG, activebackground=ACCENT,
            highlightthickness=0, bd=0, sliderrelief="flat", length=P(90),
        )
        sc.set(float(self.cfg["alpha"]))
        sc.configure(command=self._on_alpha_scale)
        sc.pack(side="left", fill="x", expand=True, padx=(0, P(6)))

        # 底部按钮
        foot = tk.Frame(top, bg=CARD_BG, padx=P(12), pady=P(8))
        foot.pack(fill="x")
        self._panel_btn(foot, "隐藏到托盘" if self.visible else "显示挂件",
                        self.toggle_visible).pack(side="left")
        self._panel_btn(foot, "重置位置", self.reset_position).pack(side="left", padx=P(6))
        self._panel_btn(foot, "退出", self.quit).pack(side="right")

        top.bind("<Escape>", lambda e: self.close_settings())
        self._place_settings(at)
        top.focus_force()

    def close_settings(self) -> None:
        if self.settings is not None and self.settings.winfo_exists():
            self.settings.destroy()
        self.settings = None

    def _place_settings(self, at: tuple[int, int] | None = None) -> None:
        """托盘点击 → 贴着鼠标；右键挂件 → 贴在挂件旁边（优先左侧，别盖住挂件）。"""
        top = self.settings
        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        left, top_y, right, bottom = work_area()
        pad = self.px(8)

        if at is not None:
            x, y = at
        else:
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
            if wx - w - pad >= left:              # 挂件左边放得下
                x, y = wx - w - pad, wy + self.win_h - h
            elif wx + self.win_w + pad + w <= right:
                x, y = wx + self.win_w + pad, wy + self.win_h - h
            else:
                x, y = right - w - pad, top_y + pad

        x = min(max(x, left + pad), max(left + pad, right - w - pad))
        y = min(max(y, top_y + pad), max(top_y + pad, bottom - h - pad))
        top.geometry(f"+{int(x)}+{int(y)}")

    def _set_drag_start(self, ev) -> None:
        self._set_drag_pos = (ev.x_root, ev.y_root,
                              self.settings.winfo_x(), self.settings.winfo_y())

    def _set_drag(self, ev) -> None:
        if not getattr(self, "_set_drag_pos", None):
            return
        px, py, wx, wy = self._set_drag_pos
        self.settings.geometry(f"+{wx + (ev.x_root - px)}+{wy + (ev.y_root - py)}")

    def _on_alpha_scale(self, val) -> None:
        if getattr(self, "_alpha_syncing", False):
            return
        try:
            a = float(val)
        except (TypeError, ValueError):
            return
        self._alpha_lbl.configure(text=f"{int(a * 100)}%")
        self._set_alpha(a)

    def _apply_layout(self) -> None:
        self.cfg["layout"] = self._layout_var.get()
        save_cfg(self.cfg)
        self._relayout()

    def _toggle_top(self) -> None:
        self.cfg["topmost"] = bool(self._top_var.get())
        self.root.attributes("-topmost", self.cfg["topmost"])
        save_cfg(self.cfg)

    def _toggle_notify(self) -> None:
        self.cfg["notify_on_open"] = bool(self._notify_var.get())
        save_cfg(self.cfg)

    def _set_alpha(self, a: float) -> None:
        self.cfg["alpha"] = round(a, 2)
        self.root.attributes("-alpha", self.cfg["alpha"])
        # 设置窗口开着的话，让滑杆跟着走（加锁防止来回触发）
        if self.settings is not None and self.settings.winfo_exists():
            try:
                self._alpha_syncing = True
                self._alpha_var.set(self.cfg["alpha"])
                self._alpha_lbl.configure(text=f"{int(self.cfg['alpha'] * 100)}%")
            except Exception:
                pass
            finally:
                self._alpha_syncing = False
        save_cfg(self.cfg)

    def _apply_zones(self) -> None:
        keys = [k for k, v in self._zone_vars.items() if v.get()]
        if not keys:  # 至少留一个，否则挂件会变成空壳
            first = self.zones[0].key
            self._zone_vars[first].set(True)
            keys = [first]
        self.cfg["zones"] = keys
        save_cfg(self.cfg)
        self.zones = active_zones(self.cfg)
        self._relayout()

    # -------- 绘制 --------
    def _state(self, k: KillZone, now: datetime) -> dict:
        """灯的当前状态。两段式（见 killzones.KillZone.lamp）：
        off 灭 / kz 纯窗口填充 / pre 朝市场开盘涨 / market 市场已开（满）。"""
        fill, phase = k.lamp(now)
        nxt = k.next_on(now)
        secs = (nxt - now).total_seconds() if nxt else 0.0
        soon = phase == "off" and nxt is not None and 0 <= secs <= self.cfg["pre_alert_min"] * 60
        return {
            "fill": fill,
            "phase": phase,
            "on": phase != "off",
            "soon": soon,
            "next": nxt,
            "next_secs": secs,
            "end": k.end_of_on(now),
        }

    # ---- 卡片圆角：tkinter 没有原生圆角矩形，用「角点加密 + 平滑样条」画 ----
    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        pts = [
            x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _draw_lamp(self, lx, ly, ls, k, st) -> None:
        """方块灯。填充比例 = 现在离「市场开盘」还有多近：
        没满 = 还在 Kill Zone 预热，满了 = 市场已经开盘。"""
        cv, P = self.canvas, self.px
        if st["on"]:
            prog = st["fill"]
            # 满灯时整块同色；未满时下半段填充、上半段留暗
            filled = ls if prog >= 0.999 else int(round(ls * prog))
            if filled < ls:
                cv.create_rectangle(lx, ly, lx + ls, ly + (ls - filled),
                                    fill=LAMP_OFF, outline="")
            if filled > 0:
                cv.create_rectangle(lx, ly + (ls - filled), lx + ls, ly + ls,
                                    fill=k.color, outline="")
            cv.create_rectangle(lx - P(1), ly - P(1), lx + ls + P(1), ly + ls + P(1),
                                outline=k.color, width=1)
            cv.create_rectangle(lx - P(3), ly - P(3), lx + ls + P(3), ly + ls + P(3),
                                outline=k.color, width=1, stipple="gray12")
        else:
            cv.create_rectangle(lx, ly, lx + ls, ly + ls, fill=LAMP_OFF, outline=LAMP_OFF_BORDER)
            if st["soon"]:
                cv.create_rectangle(lx, ly, lx + ls, ly + ls, outline=k.color, width=1,
                                    stipple="gray50")

    def redraw(self, now: datetime) -> None:
        cv, P = self.canvas, self.px
        cv.delete("all")
        self._round_rect(0, 0, self.win_w, self.win_h, P(CARD_R), fill=BG, outline=BORDER)
        if self.layout == "h":
            self._redraw_h(now)
        else:
            self._redraw_v(now)

    def _redraw_h(self, now: datetime) -> None:
        cv, P = self.canvas, self.px
        cw, gap, ls = P(H["cell_w"]), P(H["cell_gap"]), P(H["lamp"])
        y_lamp = P(H["pad_top"])
        y_label = y_lamp + ls + P(H["label_gap"])
        for i, k in enumerate(self.zones):
            cx = P(H["pad_x"]) + cw // 2 + i * (cw + gap)
            st = self._state(k, now)
            self._draw_lamp(cx - ls // 2, y_lamp, ls, k, st)
            col = LABEL_ON if st["on"] else (k.color if st["soon"] else LABEL_OFF)
            cv.create_text(cx, y_label, text=k.short, anchor="n",
                           font=self.f_label_h, fill=col)

    def _redraw_v(self, now: datetime) -> None:
        cv, P = self.canvas, self.px
        ls = P(V["lamp"])
        for i, k in enumerate(self.zones):
            y = P(V["pad_top"]) + i * P(V["row_h"])
            st = self._state(k, now)
            self._draw_lamp(P(V["lamp_x"]), y + P(3), ls, k, st)
            col = LABEL_ON if st["on"] else (k.color if st["soon"] else LABEL_OFF)
            cv.create_text(P(V["text_x"]), y + P(V["row_h"]) // 2, text=k.label, anchor="w",
                           font=self.f_label_v, fill=col)

    # -------- 托盘 --------
    def _status_text(self, now: datetime) -> str:
        """托盘悬停提示。满灯 = 市场已开；只是亮着 = 预热中。"""
        on, nxt = [], None
        for k in self.zones:
            st = self._state(k, now)
            if st["phase"] == "market":
                on.append(f"{k.market_label or k.short} 已开盘")
            elif st["phase"] in ("pre", "kz"):
                end = st["end"]
                on.append(f"{k.short} 预热" + (f" 至{end:%H:%M}" if end else ""))
            elif st["next"] is not None and (nxt is None or st["next"] < nxt[0]):
                nxt = (st["next"], k, st["next_secs"])
        if on:
            return " · ".join(on)
        if nxt:
            m = int(nxt[2] // 60)
            return f"下一个 {nxt[1].short} {nxt[0]:%H:%M}（{m // 60}:{m % 60:02d} 后）"
        return ""

    def _pump_tray(self, now: datetime) -> None:
        # 1) 处理托盘点击（托盘在自己的线程里，事件通过队列过来）
        try:
            while True:
                what, x, y = self.tray.queue.get_nowait()
                if what == "toggle":
                    self.toggle_visible()
                elif what == "menu":
                    self.open_settings((x, y))
        except Exception:
            pass

        # 2) 更新托盘提示 + 图标颜色
        active = any(self._state(k, now)["on"] for k in self.zones)
        self.tray.update(f"Kill Zone · {self._status_text(now)}", active)

        # 3) 阶段变化气泡提醒：预热开始 / 市场开盘 各提醒一次
        if self.cfg.get("notify_on_open", True):
            for k in self.zones:
                st = self._state(k, now)
                was = self._prev_phase.get(k.key)
                if st["phase"] != was:
                    if st["phase"] == "market":
                        end = st["end"]
                        head = f"{k.market_label or k.label} 开盘"
                        body = f"满灯中" + (f"，北京时间至 {end:%H:%M}" if end else "")
                    elif st["phase"] in ("pre", "kz"):
                        nxt = st["next"]
                        head = f"{k.label} 开始预热"
                        body = f"灯亮起，还没满" + (f"；{k.market_label} 开盘 {nxt:%H:%M}" if k.market_label and nxt else "")
                    else:
                        head = ""
                    if head and was is not None:
                        self.tray.notify(head, body)
                self._prev_phase[k.key] = st["phase"]

    # -------- 主循环 --------
    def _key(self, now: datetime) -> tuple:
        """渲染指纹：只有真的变了才重绘，避免每 400ms 空转（这是之前'卡'的主因）。"""
        parts: list = [self.layout, self.win_w, self.win_h]
        for k in self.zones:
            st = self._state(k, now)
            parts.append((k.key, st["phase"], round(st["fill"] * 24), st["soon"]))
        return tuple(parts)

    def tick(self) -> None:
        now = self.now()
        key = self._key(now)
        if key != self._render_key:
            self._render_key = key
            self.redraw(now)
        self._pump_tray(now)
        self.root.after(int(self.cfg.get("tick_ms", 400)), self.tick)

    def quit(self) -> None:
        try:
            self.close_settings()
        except Exception:
            pass
        try:
            self.tray.stop()
            self.tray.join(0.8)   # 等托盘图标摘掉，避免留幽灵图标
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def parse_at(argv: list[str]) -> timedelta | None:
    """--at "2026-07-15 22:00" ：按指定北京时间预览（调试 / 截图用）。"""
    if "--at" not in argv:
        return None
    raw = argv[argv.index("--at") + 1]
    fmt = "%Y-%m-%d %H:%M" if " " in raw else "%Y-%m-%dT%H:%M"
    target = datetime.strptime(raw, fmt).replace(tzinfo=BEIJING)
    return target - datetime.now(BEIJING)


def parse_overrides(argv: list[str]) -> dict:
    """--alpha 0.8   --layout-h / --layout-v   仅用于预览 / 截图，不写进 config.json。"""
    out: dict = {}
    if "--alpha" in argv:
        try:
            out["alpha"] = min(1.0, max(0.2, float(argv[argv.index("--alpha") + 1])))
        except ValueError:
            pass
    for key in ("h", "v"):
        if f"--layout-{key}" in argv:
            out["layout"] = key
    return out


_MUTEX = None   # 持有句柄，不能让 GC 回收


def claim_single_instance() -> bool:
    """同一登录会话只允许一个实例，避免重复启动出现两个挂件。"""
    global _MUTEX
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _MUTEX = k32.CreateMutexW(None, False, r"Local\KillZoneLight_SingleInstance")
        return k32.GetLastError() != 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def main() -> None:
    set_dpi_aware()
    if not claim_single_instance():
        print("已有实例在运行，本次退出。", file=sys.stderr)
        return
    args = sys.argv
    app = KillZoneLight(parse_at(args), parse_overrides(args))
    if "--settings" in args:          # 启动后直接打开设置窗口（预览/调试用）
        app.root.after(800, app.open_settings)
    app.run()


if __name__ == "__main__":
    main()
