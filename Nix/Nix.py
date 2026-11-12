import sys
import os
from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QFont
from PyQt6.QtCore import Qt

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def _make_splash(app):
    logo_path = resource_path("Nix.jpg")
    pix = QPixmap(logo_path) if os.path.exists(logo_path) else QPixmap()
    if pix.isNull() or max(pix.width(), pix.height()) < 100:
        pix = QPixmap(320, 180)
        pix.fill(QColor("#151015"))
        painter = QPainter(pix)
        painter.setPen(QColor("#bd93f9"))
        f = QFont("Segoe UI", 36, QFont.Weight.Bold)
        painter.setFont(f)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "Nix")
        painter.end()
    else:
        pix = pix.scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    splash = QSplashScreen(pix)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    return splash

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(resource_path("Nix.jpg")))

    splash = _make_splash(app)
    splash.show()
    app.processEvents()

    from frontend import Interface
    window = Interface()
    splash.finish(window)

    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        if available.width() < 1300 or available.height() < 850:
            window.showMaximized()
        else:
            window.show()
    else:
        window.show()
    # Ctrl+C no console que iniciou o Nix não deve fechar o app (nem gerar
    # traceback): só fecha pelo diálogo de saída da janela.
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    sys.exit(app.exec())
