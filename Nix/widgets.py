import sys
import os
from PyQt6.QtWidgets import QTreeWidget
from PyQt6.QtCore import pyqtSignal, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

class ExplorerTree(QTreeWidget):
    files_dropped = pyqtSignal(list)
    file_dragged_out = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
        else:
            super().dropEvent(event)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item:
            self.file_dragged_out.emit(item)
