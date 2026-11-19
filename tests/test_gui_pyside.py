# -*- coding: utf-8 -*-
"""
test_gui_pyside.py —— PySide6 图形界面冒烟 + 后台线程端到端测试

运行：
    python tests/test_gui_pyside.py
"""

import os
import sys
import tempfile

from PySide6 import QtCore, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import importlib.util  # noqa: E402

GUI_PATH = os.path.join(ROOT, "caj_gui.py")
spec = importlib.util.spec_from_file_location("gui_mod", GUI_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    # 屏蔽模态弹窗，避免无人交互时阻塞事件循环
    QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
    QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QtWidgets.QMessageBox.critical = staticmethod(lambda *a, **k: None)

    win = mod.MainWindow()
    win.show()
    app.processEvents()

    # 初始状态
    assert win.list_widget.count() == 0
    assert win.progress.value() == 0

    samples = os.path.join(HERE, "samples")
    caj = os.path.join(samples, "sample.caj")
    win.add_paths([caj])
    win.add_paths([samples])
    app.processEvents()
    n = win.list_widget.count()
    assert n >= 4, f"列表应包含样本文件，实际 {n}"
    print("列表文件数:", n, "|", win.count_label.text())

    # 去重
    before = win.list_widget.count()
    win.add_paths([caj])
    app.processEvents()
    assert win.list_widget.count() == before, "重复添加应被去重"

    # 输出模式切换
    win.same_dir_chk.setChecked(False)
    app.processEvents()
    assert win.out_edit.isEnabled(), "取消同目录后应可编辑统一目录"
    win.same_dir_chk.setChecked(True)
    app.processEvents()
    assert not win.out_edit.isEnabled()

    # 日志
    win.log("冒烟测试日志")
    assert "冒烟测试日志" in win.log_view.toPlainText()

    # ---- 后台线程端到端转换（统一输出到临时目录）----
    tmp_out = tempfile.mkdtemp(prefix="caj_gui_")
    win.same_dir_chk.setChecked(False)
    win.out_edit.setText(tmp_out)
    app.processEvents()

    loop = QtCore.QEventLoop()
    captured = {}

    def on_done(results):
        captured["results"] = results
        loop.quit()

    win.start()
    assert win.thread is not None and win.thread.isRunning()
    win.worker.finished.connect(on_done)
    loop.exec()

    # 等待线程收尾并清理
    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    q2 = QtCore.QEventLoop()
    timer.timeout.connect(q2.quit)
    timer.start(400)
    q2.exec()
    app.processEvents()

    results = captured["results"]
    ok = [r for r in results if r.ok]
    assert len(ok) >= 3, f"应至少成功3个，实际 {len(ok)}"
    for r in ok:
        assert os.path.isfile(r.target), f"输出不存在：{r.target}"
    assert win.thread is None, "线程应已清理"
    assert win.start_btn.isEnabled(), "完成后按钮应恢复可用"

    produced = sorted(os.listdir(tmp_out))
    print("临时输出目录产出:", produced)
    assert len(produced) == len(ok), "成功文件应全部落盘"

    win.close()
    print("\nPySide6 GUI 冒烟与后台线程端到端测试全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
