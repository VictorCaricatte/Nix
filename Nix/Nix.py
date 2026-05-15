import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from frontend import Interface

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion") 
    app.setWindowIcon(QIcon(resource_path("Nix.jpg"))) 
    window = Interface()
    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        if available.width() < 1300 or available.height() < 850:
            window.showMaximized()
        else:
            window.show()
    else:
        window.show()
    sys.exit(app.exec())
