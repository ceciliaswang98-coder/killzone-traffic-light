# -*- coding: utf-8 -*-
"""一键自检 —— 时间引擎 / 托盘图标 / 挂件本身。

    python selftest.py

三部分依次跑，最后汇总。任一部分失败，退出码为 1。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
TITLE = "Kill Zone 红绿灯"


def _pythonw() -> str:
    """启动挂件用 pythonw.exe（同目录找），找不到就退回当前解释器。"""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


SYS_PY = _pythonw()

FAIL: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAIL.append(f"{name}\n     期望 {want!r}\n     实际 {got!r}")
    else:
        print(f"  ok  {name}")


# --------------------------------------------------------------------------- #
# 1. 时间引擎
# --------------------------------------------------------------------------- #
def test_engine() -> None:
    from killzones import BEIJING, ET, KZ, KILLZONES, active_now, next_event

    def bj2win(kz, month: int):
        s, e = kz.occurrence(datetime(2026, month, 15).date())
        return f"{s:%H:%M}", f"{e:%H:%M}", e.date() != s.date()

    print("\n[1/3] 时间引擎：北京时间换算 / 冬夏令时 / 错位周")

    print("  -- 夏令时（美国 EDT, UTC-4）")
    check("伦敦KZ", bj2win(KZ["london"], 7), ("14:00", "17:00", False))
    check("纽约AM", bj2win(KZ["nyam"], 7), ("20:30", "23:00", False))
    # 纽约PM 13:30-16:00 ET(07-15) → 北京 07-16 01:30-04:00，两端同属一个北京日，不跨北京午夜
    check("纽约PM", bj2win(KZ["nypm"], 7), ("01:30", "04:00", False))

    print("  -- 冬令时（美国 EST, UTC-5）：整体后移 1 小时")
    check("伦敦KZ", bj2win(KZ["london"], 1), ("15:00", "18:00", False))
    check("纽约AM", bj2win(KZ["nyam"], 1), ("21:30", "00:00", True))
    check("纽约PM", bj2win(KZ["nypm"], 1), ("02:30", "05:00", False))

    print("  -- 归属日：北京凌晨的灯属于前一个纽约交易日")
    bj_2am = datetime(2026, 7, 16, 2, 0, tzinfo=BEIJING)
    s, e = KZ["nypm"].interval_around(bj_2am)
    check("北京 07-16 02:00 亮纽约PM", KZ["nypm"].is_active(bj_2am), True)
    check("窗口归属 ET 日期", f"{s.astimezone(ET):%m-%d}", "07-15")
    check("窗口北京区间", (f"{s:%m-%d %H:%M}", f"{e:%m-%d %H:%M}"), ("07-16 01:30", "07-16 04:00"))

    print("  -- 欧美错位周：必须跟【美国】走，不能跟欧洲")
    for mo, day, label in ((3, 20, "3/8–3/29 美国已夏令时"), (10, 28, "10/25–11/1 美国仍夏令时")):
        s, e = KZ["london"].occurrence(datetime(2026, mo, day).date())
        check(f"伦敦KZ {label}", (f"{s:%H:%M}", f"{e:%H:%M}"), ("14:00", "17:00"))

    print("  -- 2026 年美国夏令时切换日前后")
    check("3/8 前纽约AM 北京 21:30", f"{KZ['nyam'].occurrence(datetime(2026, 3, 6).date())[0]:%H:%M}", "21:30")
    check("3/8 后纽约AM 北京 20:30", f"{KZ['nyam'].occurrence(datetime(2026, 3, 16).date())[0]:%H:%M}", "20:30")
    check("11/1 前纽约AM 北京 20:30", f"{KZ['nyam'].occurrence(datetime(2026, 10, 30).date())[0]:%H:%M}", "20:30")
    check("11/1 后纽约AM 北京 21:30", f"{KZ['nyam'].occurrence(datetime(2026, 11, 6).date())[0]:%H:%M}", "21:30")

    print("  -- 亮灯边界（窗口内亮、窗口外灭）")
    kz = KZ["nyam"]
    s, e = kz.occurrence(datetime(2026, 7, 15).date())
    check("开始前 1 秒 → 灭", kz.is_active(s - timedelta(seconds=1)), False)
    check("开始瞬间 → 亮", kz.is_active(s), True)
    check("窗口正中 → 亮", kz.is_active(s + (e - s) / 2), True)
    check("结束前 1 秒 → 亮", kz.is_active(e - timedelta(seconds=1)), True)
    check("结束瞬间 → 灭", kz.is_active(e), False)

    print("  -- 进度填充 0→1")
    check("起点 0%", round((kz.progress(s) or 0) * 100), 0)
    check("中点 50%", round((kz.progress(s + (e - s) / 2) or 0) * 100), 50)
    check("窗口外为 None", kz.progress(e), None)

    print("  -- 北京时间基准不受本机时区影响")
    m = datetime(2026, 7, 15, 22, 0, tzinfo=BEIJING)   # 夏令时纽约AM 20:30-23:00
    check("北京 22:00 命中纽约AM", KZ["nyam"].is_active(m), True)
    check("同时刻伦敦KZ 未开", KZ["london"].is_active(m), False)

    print("  -- 三个 KZ 首尾相接不重叠，合计 8 小时")
    spans = sorted((*k.occurrence(datetime(2026, 7, 15).date()), k.key) for k in KILLZONES)
    total = sum((b - a).total_seconds() for a, b, _ in spans) / 3600
    check("合计时长", total, 8.0)
    check("无重叠", any(a[1] > b[0] for a, b in zip(spans, spans[1:])), False)

    print("  -- NY 灯两段式：预热未满、开盘满灯")
    d = datetime(2026, 7, 15).date()
    kz_s, _ = KZ["nyam"].et_window(d)
    m_s, m_e = KZ["nyam"].et_market(d)
    from datetime import timezone
    half = kz_s + (m_s - kz_s) / 2
    check("预热中 50%", round(KZ["nyam"].lamp(half.astimezone(timezone.utc))[0] * 100), 50)
    check("预热阶段标记", KZ["nyam"].lamp(half.astimezone(timezone.utc))[1], "pre")
    check("开盘瞬间满灯", KZ["nyam"].lamp(m_s.astimezone(timezone.utc)), (1.0, "market"))
    check("临近收盘仍满灯", KZ["nyam"].lamp((m_e - timedelta(minutes=1)).astimezone(timezone.utc)),
          (1.0, "market"))
    check("收盘即灭", KZ["nyam"].lamp(m_e.astimezone(timezone.utc)), (0.0, "off"))
    check("纯 KZ 灯无预热段", KZ["london"].lamp(
        (KZ["london"].et_window(d)[0] + timedelta(minutes=30)).astimezone(timezone.utc))[1], "kz")

    print("  -- 周末不亮（外汇周：周日17:00 ET → 周五17:00 ET）")
    cases = [
        ("周五15:59 ET 满", datetime(2026, 7, 24, 15, 59, tzinfo=ET), "market"),
        ("周五16:00 ET 收盘即灭", datetime(2026, 7, 24, 16, 0, tzinfo=ET), "off"),
        ("周五17:00 ET 灭", datetime(2026, 7, 24, 17, 0, tzinfo=ET), "off"),
        ("周六12:00 ET 灭", datetime(2026, 7, 25, 12, 0, tzinfo=ET), "off"),
        ("周日12:00 ET 灭", datetime(2026, 7, 26, 12, 0, tzinfo=ET), "off"),
        ("周日20:00 ET 灭(纳指周日不开)", datetime(2026, 7, 26, 20, 0, tzinfo=ET), "off"),
        ("周一10:00 ET 满", datetime(2026, 7, 27, 10, 0, tzinfo=ET), "market"),
    ]
    for name, t, want in cases:
        check(name, KZ["nyam"].lamp(t)[1], want)

    print("  -- active_now / next_event 一致")
    mid = KZ["nypm"].occurrence(datetime(2026, 1, 15).date())[0] + timedelta(minutes=30)
    check("冬季纽约PM 内活跃", [k.key for k in active_now(mid)], ["nypm"])
    ev = next_event(mid)
    check("下一个事件是结束", (ev[1].key, ev[2]), ("nypm", False))


# --------------------------------------------------------------------------- #
# 2. 托盘图标
# --------------------------------------------------------------------------- #
def test_tray() -> None:
    from tray import TrayIcon

    print("\n[2/3] 托盘图标：消息窗口 / 图标加载 / NIM_ADD / 换色 / 气泡 / NIM_DELETE")

    idle, active = HERE / "icon_idle.ico", HERE / "icon_active.ico"
    missing = [p.name for p in (idle, active) if not p.exists()]
    if missing:
        FAIL.append(f"缺少图标文件：{', '.join(missing)}")
        print(f"  !! 缺少 {', '.join(missing)}")
        return

    tray = TrayIcon(str(idle), str(active), "Kill Zone 自检")
    tray.start()
    time.sleep(2.0)

    check("消息窗口创建成功", tray._hwnd is not None, True)
    check("两个图标都加载成功", sorted(tray._icons), ["active", "idle"])
    check("Shell_NotifyIconW(NIM_ADD) 成功", tray.add_ok, True)

    if tray.add_ok:
        tray.update("Kill Zone 自检 · 绿灯态", True)
        time.sleep(1.0)
        check("切换到 active 图标未报错", tray._is_active, True)
        tray.notify("Kill Zone 自检", "气泡通知测试 —— 应该能看到这条通知")
        time.sleep(1.0)
        print("  （已发出气泡通知）")

    tray.stop()
    tray.join(1.5)
    time.sleep(0.3)
    print("  ok  已按 NIM_DELETE 摘除")


# --------------------------------------------------------------------------- #
# 3. 挂件本身
# --------------------------------------------------------------------------- #
def test_app() -> None:
    import killzone_light as K

    print("\n[3/3] 挂件：启动 / 窗口尺寸 / 右下角位置 / 优雅退出")

    if sys.platform != "win32":
        print("  -- 非 Windows，跳过")
        return

    u = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    u.FindWindowW.restype = ctypes.c_void_p
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]

    scale = u.GetDpiForSystem() / 96.0
    scale = max(1.0, scale)
    n = len(K.DEFAULT_CFG["zones"])
    exp_w = round((K.H["pad_x"] * 2 + K.H["cell_w"] * n + K.H["cell_gap"] * (n - 1)) * scale)
    exp_h = round((K.H["pad_top"] + K.H["lamp"] + K.H["label_gap"]
                   + K.H["label_h"] + K.H["pad_bottom"]) * scale)

    u.GetLayeredWindowAttributes.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_uint32),
    ]

    # 关键：备份用户配置，测完原样还回去。绝不能删！
    cfg = HERE / "config.json"
    backup = cfg.read_bytes() if cfg.exists() else None
    want_alpha = 0.70
    cfg.write_text(__import__("json").dumps(
        {"alpha": want_alpha, "layout": "h", "zones": ["london", "nyam", "nypm"]},
        ensure_ascii=False, indent=2), encoding="utf-8")

    log = open(HERE / "selftest_app.log", "wb")
    # 顺带把设置窗口一起拉起来测（--settings），确认打开设置不会改动配置
    proc = subprocess.Popen([SYS_PY, "-B", str(HERE / "killzone_light.py"), "--settings"],
                            cwd=str(HERE), stdout=log, stderr=log)
    try:
        time.sleep(5.0)
        if proc.poll() is not None:
            check("进程保持运行", False, True)
            print((HERE / "selftest_app.log").read_text(errors="replace"))
            return

        hwnd = u.FindWindowW(None, TITLE)
        check("找到挂件窗口", bool(hwnd), True)
        if not hwnd:
            return

        rect = (ctypes.c_long * 4)()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        l, t, rr, b = rect
        check("窗口宽度", rr - l, exp_w)
        check("窗口高度", b - t, exp_h)

        wa = (ctypes.c_long * 4)()
        u.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
        m = round(K.MARGIN * scale)
        check("右边距", wa[2] - rr, m)
        check("下边距", wa[3] - b, m)
        print(f"  （DPI 缩放 {scale:g}×，窗口 {rr - l}×{b - t} 物理像素）")

        # 不透明度：配置里写 0.70，窗口实际就应该是 0.70 —— 验证"关掉不会恢复默认"
        key, alpha, flags = ctypes.c_uint32(), ctypes.c_ubyte(), ctypes.c_uint32()
        got = u.GetLayeredWindowAttributes(hwnd, ctypes.byref(key),
                                           ctypes.byref(alpha), ctypes.byref(flags))
        check("窗口按配置应用了不透明度", round(alpha.value / 255, 2), want_alpha)
        print(f"  （配置 alpha={want_alpha} → 窗口实际 {alpha.value / 255:.2f}）")

        # 设置窗口：能打开，且打开它不能把配置里的不透明度改掉（曾经踩过这个坑）
        u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        sh = u.FindWindowW(None, "Kill Zone 设置")
        check("设置窗口能打开", bool(sh), True)
        after = __import__("json").loads(cfg.read_text(encoding="utf-8-sig"))
        check("打开设置没改动配置里的不透明度", after.get("alpha"), want_alpha)
        if sh:
            u.PostMessageW(sh, 0x0010, 0, 0)
    finally:
        hwnd = u.FindWindowW(None, TITLE)
        if hwnd:
            u.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
        try:
            proc.wait(timeout=3)
            check("优雅退出（退出码 0）", proc.returncode, 0)
        except Exception:
            proc.terminate()
            check("优雅退出（退出码 0）", "超时未退出", 0)
        log.close()

        # 还原用户配置（这是之前踩过的坑：自检不该动用户设置）
        if backup is not None:
            cfg.write_bytes(backup)
            check("用户配置已还原", cfg.read_bytes(), backup)
        elif cfg.exists():
            cfg.unlink()
        applog = HERE / "selftest_app.log"
        if applog.exists():
            applog.unlink()


def main() -> None:
    # 控制台编码：Windows 默认可能是 GBK，直接 print ✅ 会炸；CI 上也需要 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # --engine-only：只跑时间引擎。CI 的 runner 没有桌面会话，
    # 托盘（要 explorer.exe）和窗口测不了，所以分开。
    engine_only = "--engine-only" in sys.argv

    print("=" * 70)
    print("Kill Zone 红绿灯 · 一键自检" + ("（仅时间引擎）" if engine_only else ""))
    print("=" * 70)

    test_engine()
    if not engine_only:
        test_tray()
        test_app()

    print("\n" + "=" * 70)
    if FAIL:
        print(f"❌ {len(FAIL)} 项失败：")
        for f in FAIL:
            print(f"   - {f}")
        raise SystemExit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()
