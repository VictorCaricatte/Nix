import sys
import os
from PyQt6.QtWidgets import QTreeWidget
from PyQt6.QtCore import pyqtSignal, QUrl, QObject, QTimer
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

class _GuiInvoker(QObject):
    """Vive na thread da GUI e executa callables enviados por QUALQUER thread.

    QTimer.singleShot chamado de uma thread Python comum não dispara (e imprime
    "QBasicTimer can only be used with threads started with QThread"); o sinal
    abaixo é enfileirado para a thread da GUI, onde o timer funciona.
    """
    _sig = pyqtSignal(int, object)

    def __init__(self):
        super().__init__()
        self._sig.connect(self._run)

    def _run(self, delay, fn):
        if delay > 0:
            QTimer.singleShot(delay, fn)
        else:
            fn()


_invoker = _GuiInvoker()


def gui_single_shot(ms, fn):
    """Mesma assinatura de QTimer.singleShot, mas seguro para chamar de threads."""
    _invoker._sig.emit(int(ms), fn)


class ExplorerTree(QTreeWidget):
    files_dropped = pyqtSignal(list)
    file_dragged_out = pyqtSignal(object)
    remote_move = pyqtSignal(list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self._dragging_items = []

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.source() is self or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.source() is self:
            target = self.itemAt(event.position().toPoint())
            if target:
                self.setCurrentItem(target)
                event.acceptProposedAction()
            else:
                event.ignore()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.source() is self:
            target = self.itemAt(event.position().toPoint())
            if target and self._dragging_items:
                self.remote_move.emit(self._dragging_items[:], target)
            event.accept()
            return
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
        else:
            super().dropEvent(event)

    def startDrag(self, supportedActions):
        self._dragging_items = self.selectedItems()[:]
        item = self.currentItem()
        if item:
            self.file_dragged_out.emit(item)
