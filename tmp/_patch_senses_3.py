# -*- coding: utf-8 -*-
"""补丁 3：挂载感官线程（启动 / 优雅关闭）。"""
import io
import sys


def patch(path, old, new):
    s = io.open(path, encoding="utf-8").read()
    if s.count(old) != 1:
        print("FAIL %s count=%d" % (path, s.count(old)))
        sys.exit(1)
    io.open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))
    print("OK %s" % path)


# ── 启动：与 man-scanner 并列 ──
patch(
    "Onyx.py",
    '''        threading.Thread(target=_deferred_start_man_scan, daemon=True, name="man-scanner-delayed").start()''',
    '''        threading.Thread(target=_deferred_start_man_scan, daemon=True, name="man-scanner-delayed").start()

        # ========== 感官系统（视觉）：后台独立线程，有注意力时才启动 ==========
        try:
            from bin.ai_lib import senses as _senses
            if _senses.load_config().get("attentions"):
                _senses.start()
                log_info("感官线程已启动（视觉）", request_id)
        except Exception as e:
            log_warning(f"感官线程启动失败: {str(e)}", request_id)''')

# ── 关闭：在 _disable_logging 之前收尾 ──
patch(
    "core/shutdown.py",
    '''    _disable_logging(ctx)''',
    '''    # 感官线程先收尾（之后日志会被关闭，且要落盘 vision.md）
    try:
        from bin.ai_lib import senses as _senses
        _senses.stop()
    except Exception:
        pass

    _disable_logging(ctx)''')
