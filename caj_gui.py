# -*- coding: utf-8 -*-
"""
CAJ转PDF转换器.py —— 免费、纯本地、离线的图形界面（GUI，PySide6 版）

  * 界面基于 PySide6（Qt 官方 Python 绑定，LGPL 开源、免费）；
  * 转换核心 caj_converter.py 仅用 Python 标准库；
  * 支持 .caj / .kdh / .pdf，可添加文件或整个文件夹批量转换；
  * 转换在后台 QThread 中进行，界面不卡顿；带进度条、详细日志与拖拽；
  * 所有处理均在本机完成，不联网、不上传任何文件。

双击 start.bat 即可运行；也可执行：
    python caj_gui.py
"""

import os
import sys

from PySide6 import QtCore, QtGui, QtWidgets

# 从脚本所在目录导入同目录下的核心引擎
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import caj_converter as cc  # noqa: E402


# --------------------------------------------------------------------------- #
# 支持拖拽的文件列表
# --------------------------------------------------------------------------- #

class DropListWidget(QtWidgets.QListWidget):
    """接受从资源管理器拖入的文件/文件夹。"""

    filesDropped = QtCore.Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if event.mimeData().hasUrls():
            paths = [
                u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()
            ]
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


# --------------------------------------------------------------------------- #
# 后台转换任务
# --------------------------------------------------------------------------- #

class ConvertWorker(QtCore.QObject):
    progress = QtCore.Signal(int, int, object)
    finished = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, paths: list, out_dir) -> None:
        super().__init__()
        self._paths = paths
        self._out_dir = out_dir

    @QtCore.Slot()
    def run(self) -> None:
        try:
            results = cc.convert_many(self._paths, self._out_dir, self._on_progress)
            self.finished.emit(results)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    def _on_progress(self, i: int, total: int, result) -> None:
        self.progress.emit(i, total, result)


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #

class MainWindow(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CAJ 转 PDF 转换器（本地离线 · 免费）")
        self.resize(820, 660)
        self.setMinimumSize(720, 560)

        self.thread: QtCore.QThread | None = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QtWidgets.QLabel("CAJ / KDH 转 PDF 转换器")
        title.setStyleSheet("font-size:18px;font-weight:600;")
        root.addWidget(title)
        sub = QtWidgets.QLabel(
            "完全本地处理，文件不会上传；支持批量转换（CAJ 内嵌 PDF 提取、KDH 解密、xref 修复）"
        )
        sub.setStyleSheet("color:#666;")
        root.addWidget(sub)

        # ---- 输入区 ----
        in_box = QtWidgets.QGroupBox("待转换文件")
        in_lay = QtWidgets.QVBoxLayout(in_box)
        btn_row = QtWidgets.QHBoxLayout()
        b_add = QtWidgets.QPushButton("添加文件")
        b_add_dir = QtWidgets.QPushButton("添加文件夹")
        b_remove = QtWidgets.QPushButton("移除选中")
        b_clear = QtWidgets.QPushButton("清空列表")
        b_add.clicked.connect(self.add_files)
        b_add_dir.clicked.connect(self.add_folder)
        b_remove.clicked.connect(self.remove_selected)
        b_clear.clicked.connect(self.clear_list)
        for b in (b_add, b_add_dir, b_remove, b_clear):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        self.count_label = QtWidgets.QLabel("共 0 个文件")
        btn_row.addWidget(self.count_label)
        in_lay.addLayout(btn_row)

        self.list_widget = DropListWidget()
        self.list_widget.filesDropped.connect(self.add_paths)
        in_lay.addWidget(self.list_widget)
        hint = QtWidgets.QLabel("可将文件或文件夹直接拖入列表")
        hint.setStyleSheet("color:#888;")
        in_lay.addWidget(hint)
        root.addWidget(in_box, stretch=2)

        # ---- 输出区 ----
        out_box = QtWidgets.QGroupBox("输出设置")
        out_lay = QtWidgets.QGridLayout(out_box)
        self.same_dir_chk = QtWidgets.QCheckBox("输出到各源文件所在目录")
        self.same_dir_chk.setChecked(True)
        self.same_dir_chk.toggled.connect(self._toggle_out)
        out_lay.addWidget(self.same_dir_chk, 0, 0, 1, 3)
        out_lay.addWidget(QtWidgets.QLabel("统一输出目录："), 1, 0)
        self.out_edit = QtWidgets.QLineEdit()
        self.out_edit.setEnabled(False)
        self.browse_btn = QtWidgets.QPushButton("浏览…")
        self.browse_btn.setEnabled(False)
        self.browse_btn.clicked.connect(self.browse_out)
        out_lay.addWidget(self.out_edit, 1, 1)
        out_lay.addWidget(self.browse_btn, 1, 2)
        out_lay.setColumnStretch(1, 1)
        root.addWidget(out_box)

        # ---- 操作与进度 ----
        run_row = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("开始转换")
        self.start_btn.setMinimumHeight(34)
        self.start_btn.clicked.connect(self.start)
        self.open_btn = QtWidgets.QPushButton("打开输出文件夹")
        self.open_btn.clicked.connect(self.open_output)
        run_row.addWidget(self.start_btn)
        run_row.addWidget(self.open_btn)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setValue(0)
        run_row.addWidget(self.progress, 1)
        self.status_label = QtWidgets.QLabel("就绪")
        self.status_label.setMinimumWidth(70)
        self.status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        run_row.addWidget(self.status_label)
        root.addLayout(run_row)

        # ---- 日志 ----
        log_box = QtWidgets.QGroupBox("日志")
        log_lay = QtWidgets.QVBoxLayout(log_box)
        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QtGui.QFont("Consolas", 9))
        log_lay.addWidget(self.log_view)
        root.addWidget(log_box, stretch=1)

    # ------------------------------------------------------------------ #
    # 文件列表
    # ------------------------------------------------------------------ #
    def _existing(self) -> list:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    def add_paths(self, paths: list) -> None:
        existing = {os.path.abspath(p) for p in self._existing()}
        added = 0
        for p in paths:
            files = cc._collect_files([p]) if os.path.isdir(p) else [p]
            for fp in files:
                ap = os.path.abspath(fp)
                if ap not in existing:
                    existing.add(ap)
                    self.list_widget.addItem(fp)
                    added += 1
        self.count_label.setText(f"共 {self.list_widget.count()} 个文件")
        if added:
            self.log(f"已添加 {added} 个文件。")

    def add_files(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "选择 CAJ/KDH/PDF 文件", "",
            "CAJ 家族文件 (*.caj *.kdh *.pdf);;所有文件 (*.*)",
        )
        if paths:
            self.add_paths(paths)

    def add_folder(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择包含 CAJ/KDH/PDF 的文件夹")
        if d:
            self.add_paths([d])

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))
        self.count_label.setText(f"共 {self.list_widget.count()} 个文件")

    def clear_list(self) -> None:
        self.list_widget.clear()
        self.count_label.setText("共 0 个文件")

    # ------------------------------------------------------------------ #
    def _toggle_out(self) -> None:
        enabled = not self.same_dir_chk.isChecked()
        self.out_edit.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)

    def browse_out(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择统一输出目录")
        if d:
            self.out_edit.setText(d)

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self.thread is not None:
            return
        paths = self._existing()
        if not paths:
            QtWidgets.QMessageBox.warning(self, "提示", "请先添加要转换的文件。")
            return

        out_dir = None
        if not self.same_dir_chk.isChecked():
            out_dir = self.out_edit.text().strip()
            if not out_dir:
                QtWidgets.QMessageBox.warning(
                    self, "提示", "请选择统一输出目录，或勾选“输出到各源文件所在目录”。"
                )
                return
            os.makedirs(out_dir, exist_ok=True)

        self.start_btn.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setMaximum(len(paths))
        self.status_label.setText("转换中…")
        self.log("=" * 60)
        self.log(f"开始转换 {len(paths)} 个文件…")

        self.thread = QtCore.QThread(self)
        self.worker = ConvertWorker(paths, out_dir)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_cleanup)
        self.thread.start()

    @QtCore.Slot(int, int, object)
    def on_progress(self, i: int, total: int, result) -> None:
        self.progress.setValue(i)
        self.status_label.setText(f"{i}/{total}")
        if result.ok:
            self.log(f"[成功] {os.path.basename(result.source)} → {result.target}（{result.message}）")
        else:
            self.log(f"[失败] {os.path.basename(result.source)}：{result.message}")

    @QtCore.Slot(list)
    def on_finished(self, results: list) -> None:
        ok_n = sum(1 for r in results if r.ok)
        self.status_label.setText("完成")
        self.log(f"全部完成：成功 {ok_n} 个，失败 {len(results) - ok_n} 个。")
        QtWidgets.QMessageBox.information(
            self, "完成", f"转换结束。\n成功 {ok_n} 个，失败 {len(results) - ok_n} 个。"
        )

    @QtCore.Slot(str)
    def on_failed(self, msg: str) -> None:
        self.status_label.setText("出错")
        self.log(f"发生错误：{msg}")
        QtWidgets.QMessageBox.critical(self, "错误", msg)

    def _thread_cleanup(self) -> None:
        self.thread = None
        self.start_btn.setEnabled(True)

    # ------------------------------------------------------------------ #
    def open_output(self) -> None:
        if not self.same_dir_chk.isChecked() and self.out_edit.text().strip():
            target = self.out_edit.text().strip()
        else:
            existing = self._existing()
            target = os.path.dirname(existing[0]) if existing else os.path.expanduser("~")
        if os.path.isdir(target):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
        else:
            QtWidgets.QMessageBox.information(self, "提示", "输出目录尚不存在。")

    def log(self, text: str) -> None:
        self.log_view.append(text)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.thread is not None:
            self.thread.requestInterruption()
            self.thread.quit()
            self.thread.wait(2000)
        event.accept()


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
