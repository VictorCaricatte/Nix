import sys
import os
import stat
import posixpath
import re
import time
import tempfile
import threading

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QFrame, QLabel, QLineEdit, QPushButton, QTreeWidget, 
    QTreeWidgetItem, QPlainTextEdit, QProgressBar, 
    QFileDialog, QInputDialog, QMessageBox, QMenu, QColorDialog,
    QComboBox, QDockWidget, QTabWidget, QTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent, QMimeData, QUrl
from PyQt6.QtGui import QColor, QAction, QDrag, QPixmap, QIcon

from config import ConfigManager
from ssh import SSHManager
import backend
from i18n import t
from widgets import ExplorerTree
from dialogs import RemoteEditorDialog, ImageViewerDialog, TextViewerDialog, ScreensManagerDialog, EnvManagerDialog

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class Interface(QMainWindow):
    sig_log = pyqtSignal(str)
    sig_monitor = pyqtSignal(float, float, list)
    sig_os_info = pyqtSignal(str)
    sig_explorer = pyqtSignal()
    sig_screens = pyqtSignal(object, str)
    sig_viewer = pyqtSignal(str, str, str)
    sig_image_viewer = pyqtSignal(str, bytes)
    sig_msg = pyqtSignal(str, str, str)
    sig_conn_state = pyqtSignal(bool, str) 
    sig_env_list = pyqtSignal(object, str, list)
    sig_ask_sudo = pyqtSignal(list, object)
    sig_transfer_progress = pyqtSignal(int, int, str)
    sig_screen_status = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nix")
        self.resize(1300, 850)
        
        self.config_mgr = ConfigManager()
        self.ssh_mgr = SSHManager()
        
        self.remote_path = "/home"
        self.ctrl_a_pressed = False
        
        self.command_history = []
        self.history_index = -1
        self.active_transfers = {}

        self.sig_log.connect(self.log_local_slot)
        self.sig_monitor.connect(self.update_monitor_ui_slot)
        self.sig_os_info.connect(self.update_os_info_slot)
        self.sig_explorer.connect(self.update_explorer_slot)
        self.sig_screens.connect(self.update_screens_ui_slot)
        self.sig_viewer.connect(self.open_file_viewer_slot)
        self.sig_image_viewer.connect(self.open_image_viewer_slot)
        self.sig_msg.connect(self.show_msg_slot)
        self.sig_conn_state.connect(self.update_conn_btn_slot)
        self.sig_env_list.connect(self.update_env_list_slot)
        self.sig_ask_sudo.connect(self.ask_sudo_slot)
        self.sig_transfer_progress.connect(self.update_progress_slot)
        self.sig_screen_status.connect(self.set_screen_status)

        self.create_core_widgets()
        self.apply_layout()
        self.update_ui_texts()

    def update_ui_texts(self):
        lang = self.config_mgr.language
        self.cb_sessions.setItemText(0, t("new_session", lang))
        self.entry_host.setPlaceholderText(t("host_placeholder", lang))
        self.entry_pass.setPlaceholderText(t("pass_placeholder", lang))
        self.entry_key.setPlaceholderText(t("key_placeholder", lang))
        
        if not self.ssh_mgr.is_connected:
            self.btn_conn.setText(t("connect", lang))
        else:
            self.btn_conn.setText(t("disconnect", lang))
            
        self.btn_screens.setText(f"📺 {t('screens', lang)}")
        self.btn_env.setText(f"≡ {t('env_list', lang)}")
        self.btn_term_style.setText(f"🖥️ {t('term_style', lang)}")
        self.btn_mode.setText(f"🌓 {t('mode', lang)}")
        self.btn_color.setText(f"🎨 {t('theme', lang)}")
        self.btn_bg_color.setText(f"🖌️ {t('bg_color', lang)}")
        self.btn_term_color.setText(f"💻 {t('term_color', lang)}")
        self.btn_layout.setText(f"🔲 {t('layout', lang)}")
        self.btn_lang.setText(f"🌐 {t('language', lang)}")

        self.lbl_term.setText(f"💻 {t('terminal', lang)}")
        if "IN SCREEN" not in self.lbl_screen_status.text() and "TELA" not in self.lbl_screen_status.text():
            self.lbl_screen_status.setText(f"🟢 {t('state_main', lang)}")
            
        self.btn_clear.setText(t("clear_local", lang))
        self.btn_force.setText(t("force_main", lang))
        
        self.lbl_proc.setText(f"☷ {t('processes', lang)}")
        self.sys_tabs.setTabText(0, t("sys_mon", lang))
        self.sys_tabs.setTabText(1, t("os_info", lang))
        
        self.proc_tree.setHeaderLabels([t("process", lang), t("cpu", lang), t("mem", lang)])
        
        # Add a 5th column for Progress in the Explorer
        self.explorer.setHeaderLabels([t("name", lang), t("size", lang), t("type", lang), t("permissions", lang), "Progress"])
        self.explorer.setColumnWidth(4, 120)
        
        self.filter_input.setPlaceholderText(t("filter_placeholder", lang))
        
        self.btn_up_dir.setText(t("up_dir", lang))
        self.btn_up_file.setText(t("up_file", lang))
        self.btn_down.setText(t("down_file", lang))

    def get_stylesheet(self):
        thm = self.config_mgr.theme
        mode = thm.get("mode", "dark")
        accent = thm.get("accent", "#d95c50")
        term_color = thm.get("terminal_color", "#ffb86c")
        base_bg = thm.get("bg_color", "#151015")
        term_style = thm.get("term_style", "standard")
        
        if mode == "dark":
            bg = base_bg
            card = "#1e161e" 
            fg = "#f8f8f2"
            term_bg = "#0d0a0d"
            border = "transparent"
            input_border = "#444"
            btn_disabled_bg = "#444444"
            btn_disabled_fg = "#888888"
            hover_fg = bg
        else:
            bg = "#f0f2f6"
            card = "#ffffff"
            fg = "#000000"
            term_bg = "#ffffff"
            border = "#cccccc"
            input_border = "#cccccc"
            btn_disabled_bg = "#cccccc"
            btn_disabled_fg = "#888888"
            hover_fg = "#ffffff"
            
        if term_style == "matrix":
            term_font = "'Courier New', monospace"
            term_weight = "bold"
            term_bg_eff = "#000000"
        elif term_style == "retro":
            term_font = "'VT100', 'Courier', monospace"
            term_weight = "bold"
            term_bg_eff = "#1a1a1a"
        else: 
            term_font = "'Consolas', 'Courier New', monospace"
            term_weight = "normal"
            term_bg_eff = term_bg
            
        return f"""
        QMainWindow, QDialog {{ background-color: {bg}; color: {fg}; }}
        QWidget {{ font-family: 'Segoe UI', sans-serif; color: {fg}; }}
        QDockWidget {{ color: {accent}; font-weight: bold; titlebar-close-icon: url(''); titlebar-normal-icon: url(''); }}
        QDockWidget::title {{ background: {card}; padding: 6px; border-bottom: 2px solid {accent}; }}
        QFrame#Card {{ background-color: {card}; border-radius: 12px; border: 1px solid {border}; }}
        QLineEdit {{ background-color: {bg}; color: {fg}; border: 1px solid {input_border}; border-radius: 6px; padding: 6px; }}
        QLineEdit:focus {{ border: 1px solid {accent}; }}
        QPushButton {{ background-color: {accent}; color: white; border-radius: 6px; padding: 6px 14px; font-weight: bold; border: none; }}
        QPushButton:hover {{ background-color: {fg}; color: {hover_fg}; }}
        QPushButton:disabled {{ background-color: {btn_disabled_bg}; color: {btn_disabled_fg}; }}
        QTreeWidget, QListWidget {{ background-color: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 8px; outline: none; padding: 5px; }}
        QTreeWidget::item, QListWidget::item {{ padding: 4px; border-radius: 4px; }}
        QTreeWidget::item:selected, QListWidget::item:selected {{ background-color: {accent}; color: white; }}
        QHeaderView::section {{ background-color: {card}; color: {accent}; padding: 6px; font-weight: bold; border: none; border-bottom: 2px solid {accent}; }}
        QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; background: {card}; }}
        QTabBar::tab {{ background: {bg}; color: {fg}; padding: 8px 16px; border: 1px solid {border}; border-bottom-color: {border}; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
        QTabBar::tab:selected {{ background: {card}; color: {accent}; font-weight: bold; border-bottom-color: {card}; }}
        QTextEdit, QPlainTextEdit {{ background-color: {term_bg_eff}; color: {term_color}; border: 1px solid {border}; border-radius: 8px; padding: 10px; font-family: {term_font}; font-size: 13px; font-weight: {term_weight}; }}
        QScrollBar:vertical {{ background-color: {bg}; width: 12px; margin: 0px; border-radius: 6px; }}
        QScrollBar::handle:vertical {{ background-color: {accent}; min-height: 20px; border-radius: 6px; margin: 2px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QLabel#Title {{ font-size: 15px; font-weight: bold; color: {accent}; }}
        QLabel#MonitorValue {{ font-size: 26px; font-weight: bold; color: {fg}; }}
        QProgressBar {{ background-color: {bg}; border-radius: 4px; text-align: center; color: transparent; border: 1px solid {border}; }}
        QProgressBar::chunk {{ background-color: {accent}; border-radius: 4px; }}
        QMenu {{ background-color: {card}; border-radius: 6px; padding: 5px; border: 1px solid {border}; }}
        QMenu::item {{ padding: 6px 20px; border-radius: 4px; color: {fg}; }}
        QMenu::item:selected {{ background-color: {accent}; color: white; }}
        """

    def apply_theme(self):
        self.setStyleSheet(self.get_stylesheet())

    def toggle_theme_mode(self):
        current_mode = self.config_mgr.theme.get("mode", "dark")
        self.config_mgr.theme["mode"] = "light" if current_mode == "dark" else "dark"
        self.config_mgr.save_config()
        self.apply_theme()

    def cycle_term_style(self):
        styles = ["standard", "retro", "matrix"]
        current = self.config_mgr.theme.get("term_style", "standard")
        idx = styles.index(current) if current in styles else 0
        next_style = styles[(idx + 1) % len(styles)]
        self.config_mgr.theme["term_style"] = next_style
        self.config_mgr.save_config()
        self.apply_theme()
        lang = self.config_mgr.language
        style_name = t(f"style_{next_style}", lang)
        self.sig_log.emit(f"[{t('term_style', lang)}: {style_name}]")

    def toggle_language(self):
        current_lang = self.config_mgr.language
        self.config_mgr.language = "pt" if current_lang == "en" else "en"
        self.config_mgr.save_config()
        self.update_ui_texts()

    def change_theme_color(self):
        current_color = self.config_mgr.theme.get("accent", "#d95c50")
        color = QColorDialog.getColor(initial=QColor(current_color), parent=self)
        if color.isValid():
            self.config_mgr.theme['accent'] = color.name()
            self.config_mgr.save_config()
            self.apply_theme()

    def change_bg_color(self):
        current_color = self.config_mgr.theme.get("bg_color", "#151015")
        color = QColorDialog.getColor(initial=QColor(current_color), parent=self)
        if color.isValid():
            self.config_mgr.theme['bg_color'] = color.name()
            self.config_mgr.save_config()
            self.apply_theme()

    def change_terminal_color(self):
        current_color = self.config_mgr.theme.get("terminal_color", "#ffb86c")
        color = QColorDialog.getColor(initial=QColor(current_color), parent=self)
        if color.isValid():
            self.config_mgr.theme['terminal_color'] = color.name()
            self.config_mgr.save_config()
            self.apply_theme()

    def toggle_layout(self):
        current_layout = self.config_mgr.theme.get("layout", "classic")
        self.config_mgr.theme["layout"] = "dock" if current_layout == "classic" else "classic"
        self.config_mgr.save_config()
        self.apply_layout()

    def create_card(self):
        frame = QFrame()
        frame.setObjectName("Card")
        return frame

    def apply_layout(self):
        self.conn_inner.setParent(None)
        self.exp_inner.setParent(None)
        self.term_inner.setParent(None)
        self.sys_inner.setParent(None)

        central = self.centralWidget()
        if central:
            central.setParent(None)
            central.deleteLater()
        
        for dock in self.findChildren(QDockWidget):
            self.removeDockWidget(dock)
            dock.setWidget(None)
            dock.deleteLater()

        layout_type = self.config_mgr.theme.get("layout", "classic")
        lang = self.config_mgr.language

        def update_style(widget, name):
            widget.setObjectName(name)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

        if layout_type == "dock":
            update_style(self.conn_inner, "")
            update_style(self.exp_inner, "")
            update_style(self.term_inner, "")
            update_style(self.sys_inner, "")

            self.conn_dock = QDockWidget(t("conn_config", lang), self)
            self.conn_dock.setAllowedAreas(Qt.DockWidgetArea.TopDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea)
            self.conn_dock.setWidget(self.conn_inner)
            self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.conn_dock)

            self.exp_dock = QDockWidget(t("file_exp", lang), self)
            self.exp_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
            self.exp_dock.setWidget(self.exp_inner)
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.exp_dock)

            self.sys_dock = QDockWidget(t("sys_mon", lang), self)
            self.sys_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
            self.sys_dock.setWidget(self.sys_inner)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.sys_dock)

            self.setCentralWidget(self.term_inner)
        else:
            update_style(self.conn_inner, "Card")
            update_style(self.exp_inner, "Card")
            update_style(self.term_inner, "Card")
            update_style(self.sys_inner, "Card")

            main_widget = QWidget()
            main_layout = QVBoxLayout(main_widget)
            main_layout.setContentsMargins(10, 10, 10, 10)
            main_layout.setSpacing(10)
            
            main_layout.addWidget(self.conn_inner)
            
            h_splitter = QSplitter(Qt.Orientation.Horizontal)
            v_splitter = QSplitter(Qt.Orientation.Vertical)
            
            v_splitter.addWidget(self.exp_inner)
            v_splitter.addWidget(self.term_inner)
            v_splitter.setSizes([400, 300])
            
            h_splitter.addWidget(v_splitter)
            h_splitter.addWidget(self.sys_inner)
            h_splitter.setSizes([800, 300])
            
            main_layout.addWidget(h_splitter, 1)
            
            self.setCentralWidget(main_widget)
            
        self.apply_theme()

    def create_core_widgets(self):
        self.conn_inner = self.create_card()
        conn_layout = QHBoxLayout(self.conn_inner)
        conn_layout.setContentsMargins(10, 5, 5, 5)

        self.logo_label = QLabel()
        pixmap = QPixmap(resource_path("Nix.jpg")) 
        if not pixmap.isNull():
            self.logo_label.setPixmap(pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        
        self.cb_sessions = QComboBox()
        self.cb_sessions.addItem("New Session")
        self.cb_sessions.addItems(self.config_mgr.sessions.keys())
        self.cb_sessions.currentIndexChanged.connect(self.load_session)
        
        self.entry_host = QLineEdit()
        self.entry_pass = QLineEdit()
        self.entry_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.entry_key = QLineEdit()
        
        btn_browse_key = QPushButton("📂")
        btn_browse_key.setFixedWidth(40)
        btn_browse_key.clicked.connect(self.browse_ssh_key)
        
        btn_save = QPushButton("💾")
        btn_save.setFixedWidth(40)
        btn_save.clicked.connect(self.save_current_session)
        
        self.btn_conn = QPushButton()
        self.btn_conn.clicked.connect(self.handle_connection)
        
        self.btn_screens = QPushButton()
        self.btn_screens.clicked.connect(self.show_screens_manager)
        
        self.btn_env = QPushButton()
        self.btn_env.clicked.connect(self.show_env_list)

        self.btn_term_style = QPushButton()
        self.btn_term_style.clicked.connect(self.cycle_term_style)

        self.btn_mode = QPushButton()
        self.btn_mode.clicked.connect(self.toggle_theme_mode)

        self.btn_color = QPushButton()
        self.btn_color.clicked.connect(self.change_theme_color)
        
        self.btn_bg_color = QPushButton()
        self.btn_bg_color.clicked.connect(self.change_bg_color)
        
        self.btn_term_color = QPushButton()
        self.btn_term_color.clicked.connect(self.change_terminal_color)

        self.btn_layout = QPushButton()
        self.btn_layout.clicked.connect(self.toggle_layout)

        self.btn_lang = QPushButton()
        self.btn_lang.clicked.connect(self.toggle_language)

        conn_layout.addWidget(self.logo_label)
        conn_layout.addWidget(self.cb_sessions)
        conn_layout.addWidget(self.entry_host)
        conn_layout.addWidget(self.entry_pass)
        conn_layout.addWidget(self.entry_key)
        conn_layout.addWidget(btn_browse_key)
        conn_layout.addWidget(btn_save)
        conn_layout.addWidget(self.btn_conn)
        conn_layout.addWidget(self.btn_screens)
        conn_layout.addWidget(self.btn_env)
        conn_layout.addWidget(self.btn_term_style)
        conn_layout.addWidget(self.btn_mode)
        conn_layout.addWidget(self.btn_color)
        conn_layout.addWidget(self.btn_bg_color)
        conn_layout.addWidget(self.btn_term_color)
        conn_layout.addWidget(self.btn_layout)
        conn_layout.addWidget(self.btn_lang)

        self.exp_inner = self.create_card()
        exp_layout = QVBoxLayout(self.exp_inner)
        exp_layout.setContentsMargins(5, 5, 5, 5)
        
        path_layout = QHBoxLayout()
        btn_home = QPushButton("🏠")
        btn_home.setFixedWidth(40)
        btn_home.clicked.connect(self.go_home)
        
        btn_back = QPushButton("←")
        btn_back.setFixedWidth(40)
        btn_back.clicked.connect(self.navigate_back)
        
        self.current_path = QLabel("/home")
        
        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(40)
        btn_refresh.clicked.connect(lambda: self.sig_explorer.emit())
        
        self.btn_up_dir = QPushButton()
        self.btn_up_dir.clicked.connect(self.upload_dir_dialog)
        
        self.btn_up_file = QPushButton()
        self.btn_up_file.clicked.connect(self.upload_file_dialog)
        
        self.btn_down = QPushButton()
        self.btn_down.clicked.connect(self.download_file_dialog)

        path_layout.addWidget(btn_home)
        path_layout.addWidget(btn_back)
        path_layout.addWidget(self.current_path, 1)
        path_layout.addWidget(btn_refresh)
        path_layout.addWidget(self.btn_up_dir)
        path_layout.addWidget(self.btn_up_file)
        path_layout.addWidget(self.btn_down)
        exp_layout.addLayout(path_layout)
        
        self.filter_input = QLineEdit()
        self.filter_input.textChanged.connect(self.filter_explorer)
        exp_layout.addWidget(self.filter_input)

        self.explorer = ExplorerTree()
        # Enable multiple selection via Ctrl or Shift
        self.explorer.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.explorer.setColumnWidth(0, 250)
        self.explorer.itemDoubleClicked.connect(self.on_item_double_click)
        self.explorer.files_dropped.connect(self.on_drop_files)
        self.explorer.file_dragged_out.connect(self.handle_drag_out)
        
        self.explorer.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.explorer.customContextMenuRequested.connect(self.show_context_menu)

        exp_layout.addWidget(self.explorer)

        self.term_inner = self.create_card()
        term_layout = QVBoxLayout(self.term_inner)
        term_layout.setContentsMargins(5, 5, 5, 5)
        
        top_term_layout = QHBoxLayout()
        self.lbl_term = QLabel()
        self.lbl_term.setObjectName("Title")
        self.lbl_screen_status = QLabel()
        self.lbl_screen_status.setObjectName("Status")
        self.lbl_screen_status.setStyleSheet("color: #28a745;")
        
        top_term_layout.addWidget(self.lbl_term)
        top_term_layout.addStretch()
        top_term_layout.addWidget(self.lbl_screen_status)
        term_layout.addLayout(top_term_layout)
        
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        term_layout.addWidget(self.output)

        input_layout = QHBoxLayout()
        lbl_prompt = QLabel("❯")
        lbl_prompt.setStyleSheet("color: #89DDFF; font-weight: bold; font-size: 16px;")
        
        self.cmd_input = QLineEdit()
        self.cmd_input.returnPressed.connect(self.send_command)
        self.cmd_input.installEventFilter(self)
        
        self.btn_clear = QPushButton()
        self.btn_clear.clicked.connect(self.clear_terminal)
        
        self.btn_force = QPushButton()
        self.btn_force.clicked.connect(lambda: self.set_screen_status(False))

        input_layout.addWidget(lbl_prompt)
        input_layout.addWidget(self.cmd_input, 1)
        input_layout.addWidget(self.btn_clear)
        input_layout.addWidget(self.btn_force)
        term_layout.addLayout(input_layout)

        self.sys_inner = self.create_card()
        sys_layout = QVBoxLayout(self.sys_inner)
        sys_layout.setContentsMargins(5, 5, 5, 5)
        
        self.sys_tabs = QTabWidget()
        
        self.tab_monitor = QWidget()
        tab_monitor_layout = QVBoxLayout(self.tab_monitor)
        
        stats_layout = QHBoxLayout()
        
        cpu_layout = QVBoxLayout()
        lbl_cpu_t = QLabel("CPU")
        lbl_cpu_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cpu_label = QLabel("0%")
        self.cpu_label.setObjectName("MonitorValue")
        self.cpu_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cpu_bar = QProgressBar()
        self.cpu_bar.setRange(0, 100)
        self.cpu_bar.setFixedHeight(8)
        self.cpu_bar.setTextVisible(False)
        cpu_layout.addWidget(lbl_cpu_t)
        cpu_layout.addWidget(self.cpu_label)
        cpu_layout.addWidget(self.cpu_bar)
        
        ram_layout = QVBoxLayout()
        lbl_ram_t = QLabel("RAM")
        lbl_ram_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ram_label = QLabel("0%")
        self.ram_label.setObjectName("MonitorValue")
        self.ram_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ram_bar = QProgressBar()
        self.ram_bar.setRange(0, 100)
        self.ram_bar.setFixedHeight(8)
        self.ram_bar.setTextVisible(False)
        ram_layout.addWidget(lbl_ram_t)
        ram_layout.addWidget(self.ram_label)
        ram_layout.addWidget(self.ram_bar)
        
        stats_layout.addLayout(cpu_layout)
        stats_layout.addLayout(ram_layout)
        tab_monitor_layout.addLayout(stats_layout)
        
        self.lbl_proc = QLabel()
        self.lbl_proc.setObjectName("Title")
        tab_monitor_layout.addWidget(self.lbl_proc)
        
        self.proc_tree = QTreeWidget()
        self.proc_tree.setColumnWidth(0, 120)
        tab_monitor_layout.addWidget(self.proc_tree)

        self.tab_os = QWidget()
        tab_os_layout = QVBoxLayout(self.tab_os)
        self.os_info_text = QTextEdit()
        self.os_info_text.setReadOnly(True)
        self.os_info_text.setStyleSheet("font-family: 'Consolas', monospace; font-size: 14px;")
        tab_os_layout.addWidget(self.os_info_text)

        self.sys_tabs.addTab(self.tab_monitor, "Monitor")
        self.sys_tabs.addTab(self.tab_os, "OS Info")
        
        sys_layout.addWidget(self.sys_tabs)

    def browse_ssh_key(self):
        lang = self.config_mgr.language
        file, _ = QFileDialog.getOpenFileName(self, t("sel_key", lang), os.path.expanduser("~/.ssh"))
        if file:
            self.entry_key.setText(file)

    def load_session(self):
        session_name = self.cb_sessions.currentText()
        if session_name in self.config_mgr.sessions:
            data = self.config_mgr.sessions[session_name]
            self.entry_host.setText(data.get("host", ""))
            self.entry_key.setText(data.get("key", ""))
        else:
            self.entry_host.clear()
            self.entry_key.clear()
            self.entry_pass.clear()

    def eventFilter(self, obj, event):
        if obj == self.cmd_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Tab:
                self.autocomplete_terminal()
                return True
            elif event.key() == Qt.Key.Key_Up:
                self.command_history_up()
                return True
            elif event.key() == Qt.Key.Key_Down:
                self.command_history_down()
                return True
            
            if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                if event.key() == Qt.Key.Key_A:
                    self.ctrl_a_pressed = True
                    return super().eventFilter(obj, event)
                elif event.key() == Qt.Key.Key_C:
                    if self.ssh_mgr.shell: self.ssh_mgr.shell.send('\x03')
                    return True
            
            if self.ctrl_a_pressed and event.key() == Qt.Key.Key_D:
                if self.ssh_mgr.shell: self.ssh_mgr.shell.send('\x01d')
                self.ctrl_a_pressed = False
                self.set_screen_status(False)
                self.sig_log.emit("[Nix: Detach]")
                return True
                
            if event.key() not in [Qt.Key.Key_Control, Qt.Key.Key_A]:
                self.ctrl_a_pressed = False

        return super().eventFilter(obj, event)

    def filter_explorer(self, text):
        search_term = text.lower()
        for i in range(self.explorer.topLevelItemCount()):
            item = self.explorer.topLevelItem(i)
            item_name = item.text(0).lower()
            if search_term in item_name:
                item.setHidden(False)
            else:
                item.setHidden(True)

    def show_context_menu(self, pos):
        if not self.ssh_mgr.is_connected: return
        item = self.explorer.itemAt(pos)
        if not item: return
        
        lang = self.config_mgr.language
        menu = QMenu(self)
        
        action_open = QAction(t("open", lang), self)
        action_open.triggered.connect(lambda: self.on_item_double_click(item, 0))
        
        action_edit = QAction(t("edit_nano", lang), self)
        action_edit.triggered.connect(lambda: self.ctx_edit(item))
        
        action_copy = QAction(t("copy_path", lang), self)
        action_copy.triggered.connect(lambda: self.ctx_copy_path(item))
        
        action_rename = QAction(t("rename", lang), self)
        action_rename.triggered.connect(lambda: self.ctx_rename(item))
        
        action_move = QAction(t("move", lang), self)
        action_move.triggered.connect(lambda: self.ctx_move(item))
        
        action_delete = QAction(t("delete", lang), self)
        action_delete.triggered.connect(lambda: self.ctx_delete(item))
        
        menu.addSeparator()
        
        filename, new_path, item_type = self.get_item_info(item)
        
        if item_type == "Directory" or item_type == t("directory", lang):
            action_compress = QAction(t("compress", lang), self)
            action_compress.triggered.connect(lambda: self.ctx_compress(new_path, filename))
            menu.addAction(action_compress)
            
        elif filename.endswith(('.tar.gz', '.tgz', '.zip', '.tar')):
            action_extract = QAction(t("extract", lang), self)
            action_extract.triggered.connect(lambda: self.ctx_extract(new_path, filename))
            menu.addAction(action_extract)

        menu.addSeparator()
        action_props = QAction(t("properties", lang), self)
        action_props.triggered.connect(lambda: self.ctx_properties(item))
        
        menu.addAction(action_open)
        menu.addAction(action_edit)
        menu.addSeparator()
        menu.addAction(action_copy)
        menu.addAction(action_rename)
        menu.addAction(action_move)
        menu.addSeparator()
        menu.addAction(action_delete)
        menu.addAction(action_props)
        
        menu.exec(self.explorer.viewport().mapToGlobal(pos))

    def get_item_info(self, item):
        filename = item.text(0).split(" ", 1)[-1].strip()
        item_type = item.text(2)
        new_path = f"/{filename}" if self.remote_path == "/" else posixpath.join(self.remote_path, filename)
        return filename, new_path, item_type

    def ctx_edit(self, item):
        filename, new_path, item_type = self.get_item_info(item)
        if item_type == "File" or item_type == t("file", self.config_mgr.language):
            self.process_comand_nano(f"nano {new_path}")

    def ctx_copy_path(self, _):
        items = self.explorer.selectedItems()
        if not items: return
        paths = [self.get_item_info(item)[1] for item in items]
        QApplication.clipboard().setText("\n".join(paths))
        self.sig_log.emit(t("path_copied", self.config_mgr.language) + f": {len(paths)} items")

    def ctx_rename(self, item):
        lang = self.config_mgr.language
        filename, new_path, item_type = self.get_item_info(item)
        new_name, ok = QInputDialog.getText(self, t("rename", lang), f"{t('new_name', lang)} {filename}:", text=filename)
        if ok and new_name and new_name != filename:
            target_path = posixpath.join(self.remote_path, new_name)
            try:
                with self.ssh_mgr.lock: self.ssh_mgr.sftp.rename(new_path, target_path)
                self.sig_explorer.emit()
            except Exception as e:
                self.sig_msg.emit("error", t("error", lang), str(e))

    def ctx_move(self, _):
        items = self.explorer.selectedItems()
        if not items: return
        lang = self.config_mgr.language
        target_dir, ok = QInputDialog.getText(self, t("move", lang), f"{t('move_to', lang)}", text=self.remote_path)
        if ok and target_dir and target_dir != self.remote_path:
            for item in items:
                filename, new_path, item_type = self.get_item_info(item)
                target_path = posixpath.join(target_dir, filename)
                try:
                    with self.ssh_mgr.lock: self.ssh_mgr.sftp.rename(new_path, target_path)
                except Exception as e:
                    self.sig_msg.emit("error", t("error", lang), str(e))
            self.sig_explorer.emit()

    def ctx_delete(self, _):
        lang = self.config_mgr.language
        items = self.explorer.selectedItems()
        if not items: return
        
        if len(items) == 1:
            filename = self.get_item_info(items[0])[0]
            msg = f"{t('confirm_del', lang)} '{filename}'?\n{t('cannot_undo', lang)}"
        else:
            msg = f"{t('confirm_del', lang)} {len(items)} items?\n{t('cannot_undo', lang)}"
            
        reply = QMessageBox.question(self, t("delete", lang), msg)
        if reply == QMessageBox.StandardButton.Yes:
            for item in items:
                filename, new_path, item_type = self.get_item_info(item)
                try:
                    if item_type == "Directory" or item_type == t("directory", lang):
                        safe_path = new_path.replace('"', '\\"')
                        self.ssh_mgr.execute(f'rm -rf "{safe_path}"')
                    else:
                        with self.ssh_mgr.lock: self.ssh_mgr.sftp.remove(new_path)
                except Exception as e:
                    self.sig_msg.emit("error", t("error", lang), f"{filename}: {str(e)}")
            QTimer.singleShot(500, lambda: self.sig_explorer.emit())

    def ctx_properties(self, item):
        lang = self.config_mgr.language
        filename, new_path, item_type = self.get_item_info(item)
        try:
            with self.ssh_mgr.lock: st = self.ssh_mgr.sftp.stat(new_path)
            size = self.format_file_size(st.st_size)
            perms = stat.filemode(st.st_mode)
            mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))
            info = f"{t('name', lang)}: {filename}\nPath: {new_path}\n{t('type', lang)}: {item_type}\n{t('size', lang)}: {size}\n{t('permissions', lang)}: {perms}\nModified: {mtime}"
            self.sig_msg.emit("info", t("properties", lang), info)
        except Exception as e:
             self.sig_msg.emit("error", t("error", lang), str(e))

    def ctx_compress(self, path, filename):
        safe_name = filename.replace('"', '\\"')
        parent = posixpath.dirname(path)
        cmd = f'cd "{parent}" && tar -czf "{safe_name}.tar.gz" "{safe_name}"'
        self.ssh_mgr.execute(cmd)
        QTimer.singleShot(1500, lambda: self.sig_explorer.emit())

    def ctx_extract(self, path, filename):
        safe_path = path.replace('"', '\\"')
        parent = posixpath.dirname(path)
        if filename.endswith('.zip'):
            cmd = f'cd "{parent}" && unzip -o "{safe_path}"'
        else:
            cmd = f'cd "{parent}" && tar -xzf "{safe_path}"'
        self.ssh_mgr.execute(cmd)
        QTimer.singleShot(1500, lambda: self.sig_explorer.emit())

    def handle_drag_out(self, item):
        items = self.explorer.selectedItems()
        urls = []
        temp_dir = tempfile.gettempdir()
        lang = self.config_mgr.language
        
        try:
            for i in items:
                filename, remote_path, item_type = self.get_item_info(i)
                if item_type != "File" and item_type != t("file", lang): 
                    continue
                
                with self.ssh_mgr.lock:
                    size = self.ssh_mgr.sftp.stat(remote_path).st_size
                    if size > 50 * 1024 * 1024:
                        self.sig_msg.emit("warn", t("warning", lang), f"{filename}: {t('file_large', lang)}")
                        continue
                    local_path = os.path.join(temp_dir, filename)
                    self.ssh_mgr.sftp.get(remote_path, local_path)
                    urls.append(QUrl.fromLocalFile(local_path))
                    
            if urls:
                drag = QDrag(self.explorer)
                mime_data = QMimeData()
                mime_data.setUrls(urls)
                drag.setMimeData(mime_data)
                drag.exec(Qt.DropAction.CopyAction)
        except Exception:
            pass

    def log_local_slot(self, message):
        self.output.appendPlainText(f"\n> {message}")

    def show_msg_slot(self, msg_type, title, text):
        if msg_type == "error": QMessageBox.critical(self, title, text)
        elif msg_type == "warn": QMessageBox.warning(self, title, text)
        else: QMessageBox.information(self, title, text)
        
    def update_conn_btn_slot(self, enabled, text):
        self.btn_conn.setEnabled(enabled)
        self.btn_conn.setText(text)

    def update_env_list_slot(self, win, content, envs):
        if hasattr(win, 'update_ui'):
            win.update_ui(content, envs)

    def ask_sudo_slot(self, result_list, event):
        text, ok = QInputDialog.getText(self, "Sudo", t("enter_sudo", self.config_mgr.language), QLineEdit.EchoMode.Password)
        if ok: result_list[0] = text
        event.set()

    def set_screen_status(self, in_screen, name=""):
        lang = self.config_mgr.language
        if in_screen:
            self.lbl_screen_status.setText(f"🟠 {t('state_screen', lang)} ({name})")
            self.lbl_screen_status.setStyleSheet("color: #ff8c00;")
        else:
            self.lbl_screen_status.setText(f"🟢 {t('state_main', lang)}")
            self.lbl_screen_status.setStyleSheet("color: #28a745;")

    def clear_terminal(self):
        self.output.clear()

    def autocomplete_terminal(self):
        if not self.ssh_mgr.is_connected or not self.ssh_mgr.sftp: return
        cmd = self.cmd_input.text()
        if not cmd: return
        
        parts = cmd.split()
        last_word = parts[-1]
        try:
            if '/' in last_word:
                dir_path = posixpath.dirname(last_word)
                prefix = posixpath.basename(last_word)
                if not dir_path: dir_path = "/"
                elif not dir_path.startswith('/'): dir_path = posixpath.join(self.remote_path, dir_path)
                with self.ssh_mgr.lock: items = self.ssh_mgr.sftp.listdir(dir_path)
            else:
                prefix = last_word
                with self.ssh_mgr.lock: items = self.ssh_mgr.sftp.listdir(self.remote_path)
            
            matches = [m for m in items if m.startswith(prefix)]
            if not matches: return
                
            common = os.path.commonprefix(matches)
            if common and common != prefix:
                new_word = common
                if '/' in last_word: 
                    new_word = posixpath.join(posixpath.dirname(last_word), new_word)
                new_cmd = cmd[:len(cmd)-len(last_word)] + new_word
                self.cmd_input.setText(new_cmd)
            elif len(matches) > 1:
                self.sig_log.emit(f"Options: {'   '.join(matches)}")
        except: pass

    def command_history_up(self):
        if self.command_history and self.history_index < len(self.command_history) - 1:
            self.history_index += 1
            self.cmd_input.setText(self.command_history[self.history_index])

    def command_history_down(self):
        if self.command_history:
            if self.history_index > 0:
                self.history_index -= 1
                self.cmd_input.setText(self.command_history[self.history_index])
            elif self.history_index == 0:
                self.history_index = -1
                self.cmd_input.clear()

    def send_command(self):
        cmd = self.cmd_input.text().strip()
        if not cmd: return
        
        if cmd.startswith("screen -S") or cmd.startswith("screen -r"):
            parts = cmd.split()
            if len(parts) > 2: self.set_screen_status(True, parts[2])
            else: self.set_screen_status(True, "Active")
        elif cmd == "exit":
            self.set_screen_status(False)
        
        if cmd.startswith("nano ") or cmd.startswith("sudo nano "):
            self.process_comand_nano(cmd)
            self.cmd_input.clear()
            return
            
        if not self.command_history or self.command_history[0] != cmd:
            self.command_history.insert(0, cmd)
        self.history_index = -1
        
        if cmd == "clear": 
            self.clear_terminal()
            if self.ssh_mgr.shell: self.ssh_mgr.shell.send("clear\n")
            self.cmd_input.clear()
            return

        if cmd.startswith("cd "):
            target = cmd[3:].strip()
            try:
                if target == "..": self.navigate_back()
                elif target == "~" or target == "": self.go_home()
                else:
                    new_path = target if target.startswith("/") else posixpath.join(self.remote_path, target)
                    with self.ssh_mgr.lock:
                        self.ssh_mgr.sftp.chdir(new_path)
                        self.remote_path = self.ssh_mgr.sftp.getcwd() or new_path
                    self.sig_explorer.emit()
            except Exception: pass

        if self.ssh_mgr.shell:
            self.ssh_mgr.shell.send(cmd + "\n")
            
        self.cmd_input.clear()

    def process_comand_nano(self, cmd):
        use_sudo = "sudo" in cmd
        filename = cmd.split()[-1]
        if filename == "nano": return
        
        sudo_pwd = None
        if use_sudo:
            pwd, ok = QInputDialog.getText(self, "Sudo", t("enter_sudo", self.config_mgr.language), QLineEdit.EchoMode.Password)
            if not ok: return
            sudo_pwd = pwd

        editor = RemoteEditorDialog(self, filename, use_sudo, sudo_pwd)
        editor.exec()

    def go_home(self):
        self.remote_path = "/home" 
        if self.ssh_mgr.is_connected:
            try:
                stdin, stdout, stderr = self.ssh_mgr.execute("pwd")
                real_home = stdout.read().decode().strip()
                if real_home: self.remote_path = real_home
            except: pass
        self.sig_explorer.emit()

    def navigate_back(self):
        if not self.ssh_mgr.is_connected: return
        if self.remote_path != "/":
            parent = posixpath.dirname(self.remote_path)
            try:
                with self.ssh_mgr.lock: 
                    self.ssh_mgr.sftp.chdir(parent)
                    self.remote_path = parent if parent else "/"
                self.sig_explorer.emit()
            except Exception: pass

    def on_item_double_click(self, item, column):
        if not self.ssh_mgr.is_connected or not self.ssh_mgr.sftp: return
        try:
            item_text = item.text(0)
            filename = item_text.split(" ", 1)[1].strip() if len(item_text.split(" ", 1)) > 1 else item_text.strip()
            item_type = item.text(2)
            new_path = f"/{filename}" if self.remote_path == "/" else posixpath.join(self.remote_path, filename)
            
            if item_type == "Directory" or item_type == t("directory", self.config_mgr.language):
                with self.ssh_mgr.lock: 
                    self.ssh_mgr.sftp.chdir(new_path)
                    self.remote_path = new_path
                self.sig_explorer.emit()
            else:
                threading.Thread(target=self.preview_file, args=(new_path, filename), daemon=True).start()
        except: pass

    def preview_file(self, file_path, filename):
        try:
            with self.ssh_mgr.lock: 
                size = self.ssh_mgr.sftp.stat(file_path).st_size
            
            valid_image_exts = ('.png', '.jpg', '.jpeg', '.svg', '.gif', '.bmp')
            if filename.lower().endswith(valid_image_exts):
                if size > 15 * 1024 * 1024:
                    return
                with self.ssh_mgr.lock:
                    with self.ssh_mgr.sftp.open(file_path, 'r') as f: 
                        img_data = f.read()
                self.sig_image_viewer.emit(filename, img_data)
                return

            lazy_exts = ('.fasta', '.fna', '.vcf', '.tsv', '.csv', '.sam', '.fastq')
            is_lazy = filename.lower().endswith(lazy_exts) or size > 1024 * 1024
            
            read_size = 100 * 1024 if is_lazy else size 
            
            with self.ssh_mgr.lock:
                with self.ssh_mgr.sftp.open(file_path, 'r') as f: 
                    content = f.read(read_size).decode('utf-8', errors='replace')
            
            if is_lazy and size > read_size:
                content += f"\n\n--- [TRUNCATED] ---"

            self.sig_viewer.emit(file_path, filename, content)
            
        except Exception: 
            pass

    def open_image_viewer_slot(self, filename, img_data):
        viewer = ImageViewerDialog(self, filename, img_data)
        viewer.show()

    def open_file_viewer_slot(self, file_path, filename, content):
        viewer = TextViewerDialog(self, file_path, filename, content)
        viewer.show()

    def format_file_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0: return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    def get_file_icon(self, filename):
        ext = os.path.splitext(filename)[1].lower()
        icons = {
            '.py': '🐍', '.txt': '📝', '.csv': '📊', '.tsv': '📊', '.json': '📋', 
            '.sh': '🐚', '.fastq': '🧬', '.fasta': '🧬', '.fna': '🧬', '.gbk': '🧬', 
            '.gbff': '🧬', '.png': '🖼️', '.jpg': '🖼️', '.jpeg': '🖼️', '.svg': '🖼️',
            '.tar': '📦', '.gz': '📦', '.zip': '📦'
        }
        return icons.get(ext, '📄')

    def update_explorer_slot(self):
        if not self.ssh_mgr.is_connected or not self.ssh_mgr.sftp: return
        lang = self.config_mgr.language
        
        # Não atualiza se existirem transferências ativas para não excluir as barras de progresso visuais
        if self.active_transfers: 
            return 
            
        try:
            self.explorer.clear()
            self.current_path.setText(self.remote_path)
            with self.ssh_mgr.lock: files = self.ssh_mgr.sftp.listdir_attr(self.remote_path)
            files.sort(key=lambda x: (not stat.S_ISDIR(x.st_mode), x.filename.lower()))
            for f in files:
                icon = "📁" if stat.S_ISDIR(f.st_mode) else self.get_file_icon(f.filename)
                ftype = t("directory", lang) if stat.S_ISDIR(f.st_mode) else t("file", lang)
                size = "-" if stat.S_ISDIR(f.st_mode) else self.format_file_size(f.st_size)
                item = QTreeWidgetItem([f"{icon}  {f.filename}", size, ftype, stat.filemode(f.st_mode), ""])
                self.explorer.addTopLevelItem(item)
            
            if self.filter_input.text():
                self.filter_explorer(self.filter_input.text())
        except: pass

    def update_progress_slot(self, transferred, total, transfer_id):
        if transfer_id in self.active_transfers:
            pbar = self.active_transfers[transfer_id]
            try:
                if total > 0:
                    pbar.setValue(int((transferred / total) * 100))
            except RuntimeError:
                pass

    def sftp_progress_callback(self, transferred, total, transfer_id):
        self.sig_transfer_progress.emit(transferred, total, transfer_id)

    def create_upload_item(self, local_path):
        filename = os.path.basename(local_path)
        item = QTreeWidgetItem([f"⬆️  {filename}", "-", t("uploading", self.config_mgr.language), "-", ""])
        self.explorer.addTopLevelItem(item)
        pbar = QProgressBar()
        pbar.setFixedHeight(12)
        pbar.setRange(0, 100)
        self.explorer.setItemWidget(item, 4, pbar)
        transfer_id = f"up_{filename}"
        self.active_transfers[transfer_id] = pbar

    def upload_file_dialog(self):
        if not self.ssh_mgr.is_connected: return
        files, _ = QFileDialog.getOpenFileNames(self, t("select_files", self.config_mgr.language))
        if files: 
            for f in files: self.create_upload_item(f)
            threading.Thread(target=self.upload_files_thread, args=(files,), daemon=True).start()

    def upload_dir_dialog(self):
        if not self.ssh_mgr.is_connected: return
        dir_path = QFileDialog.getExistingDirectory(self, t("select_folder", self.config_mgr.language))
        if dir_path: 
            self.create_upload_item(dir_path)
            threading.Thread(target=self.upload_files_thread, args=([dir_path],), daemon=True).start()

    def on_drop_files(self, files):
        if not self.ssh_mgr.is_connected: return
        if files: 
            for f in files: self.create_upload_item(f)
            threading.Thread(target=self.upload_files_thread, args=(files,), daemon=True).start()

    def _get_sudo_pwd(self):
        pwd = [None]
        event = threading.Event()
        self.sig_ask_sudo.emit(pwd, event)
        event.wait()
        return pwd[0]

    def upload_files_thread(self, paths):
        sudo_pwd = [None]
        for local_path in paths:
            filename = os.path.basename(local_path)
            remote_file_path = posixpath.join(self.remote_path, filename)
            transfer_id = f"up_{filename}"
            try:
                cb = lambda t, tot: self.sftp_progress_callback(t, tot, transfer_id)
                backend.upload_recursive(self.ssh_mgr, local_path, remote_file_path, sudo_pwd, self._get_sudo_pwd, progress_cb=cb)
            except Exception:
                pass
            finally:
                if transfer_id in self.active_transfers:
                    del self.active_transfers[transfer_id]
        
        QTimer.singleShot(500, lambda: self.sig_explorer.emit())

    def download_file_dialog(self):
        items = self.explorer.selectedItems()
        if not items: return
        local_dir = QFileDialog.getExistingDirectory(self, t("destination", self.config_mgr.language))
        if not local_dir: return
        
        for item in items:
            filename, remote_path, item_type = self.get_item_info(item)
            is_dir = item_type == t("directory", self.config_mgr.language) or item_type == "Directory"
            
            pbar = QProgressBar()
            pbar.setFixedHeight(12)
            pbar.setRange(0, 100)
            self.explorer.setItemWidget(item, 4, pbar)
            transfer_id = f"dl_{filename}"
            self.active_transfers[transfer_id] = pbar
            
            threading.Thread(target=self.download_thread, args=(remote_path, os.path.join(local_dir, filename), is_dir, transfer_id), daemon=True).start()

    def download_thread(self, remote_path, local_path, is_dir, transfer_id):
        try:
            cb = lambda t, tot: self.sftp_progress_callback(t, tot, transfer_id)
            if is_dir:
                backend.download_directory_recursive(self.ssh_mgr, remote_path, local_path, progress_cb=cb)
            else:
                with self.ssh_mgr.lock: 
                    self.ssh_mgr.sftp.get(remote_path, local_path, callback=cb)
        except Exception:
            pass
        finally:
            if transfer_id in self.active_transfers:
                del self.active_transfers[transfer_id]
            QTimer.singleShot(500, lambda: self.sig_explorer.emit())

    def show_screens_manager(self):
        lang = self.config_mgr.language
        if not self.ssh_mgr.is_connected:
            QMessageBox.warning(self, t("warning", lang), t("connect_first", lang))
            return
        self.screens_win = ScreensManagerDialog(self)
        self.screens_win.show()

    def update_screens_ui_slot(self, win, output):
        if hasattr(win, 'update_ui'):
            win.update_ui(output)

    def show_env_list(self):
        lang = self.config_mgr.language
        if not self.ssh_mgr.is_connected:
            QMessageBox.warning(self, t("warning", lang), t("connect_first", lang))
            return
        self.env_win = EnvManagerDialog(self)
        self.env_win.show()

    def handle_connection(self):
        lang = self.config_mgr.language
        if not self.ssh_mgr.is_connected:
            self.update_conn_btn_slot(False, t("connecting", lang))
            threading.Thread(target=self.connect_ssh, daemon=True).start()
        else:
            self.ssh_mgr.disconnect()
            self.update_conn_btn_slot(True, t("connect", lang))

    def connect_ssh(self):
        lang = self.config_mgr.language
        try:
            host_str = self.entry_host.text()
            u, h = host_str.split("@") if "@" in host_str else ("user", host_str)
            key_path = self.entry_key.text()
            
            self.ssh_mgr.connect(h, u, password=self.entry_pass.text(), key_filename=key_path)
            
            try:
                stdin, stdout, stderr = self.ssh_mgr.execute("pwd")
                real_home = stdout.read().decode().strip()
                if real_home: self.remote_path = real_home
            except: pass
            
            threading.Thread(target=self.terminal_read_loop, daemon=True).start()
            threading.Thread(target=self.monitor_loop, daemon=True).start()
            threading.Thread(target=self.fetch_os_thread, daemon=True).start()
            
            self.sig_conn_state.emit(True, t("disconnect", lang))
            time.sleep(1) 
            self.sig_explorer.emit()
        except Exception:
            self.sig_conn_state.emit(True, t("connect", lang))

    def fetch_os_thread(self):
        info = backend.fetch_os_info(self.ssh_mgr)
        self.sig_os_info.emit(info)

    def update_os_info_slot(self, info_text):
        self.os_info_text.setPlainText(info_text)

    def terminal_read_loop(self):
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
        while self.ssh_mgr.is_connected:
            if self.ssh_mgr.shell and self.ssh_mgr.shell.recv_ready():
                try:
                    data = self.ssh_mgr.shell.recv(4096).decode('utf-8', errors='replace')
                    if data:
                        clean_data = ansi_escape.sub('', data)
                        clean_data_str = clean_data.replace('\r\n', '\n').replace('\r', '')
                        
                        if "[detached from" in clean_data_str.lower() or "[screen is terminating]" in clean_data_str.lower():
                            self.sig_screen_status.emit(False, "")
                            
                        self.sig_log.emit(clean_data_str.strip('\n'))
                except Exception:
                    break
            time.sleep(0.01)

    def monitor_loop(self):
        while self.ssh_mgr.is_connected:
            try:
                cmd = ("grep 'cpu ' /proc/stat; sleep 1; grep 'cpu ' /proc/stat; "
                       "echo '==MEM=='; cat /proc/meminfo; echo '==PROCS=='; "
                       "ps -eo pid,pcpu,pmem,comm --sort=-pcpu --no-headers | head -n 15")
                stdin, stdout, stderr = self.ssh_mgr.execute(cmd)
                output = stdout.read().decode().strip()
                
                cpu_usage, mem_usage, procs = backend.parse_monitor_output(output)
                self.sig_monitor.emit(cpu_usage, mem_usage, procs)
            except: time.sleep(4)

    def update_monitor_ui_slot(self, cpu, ram, procs):
        try:
            self.cpu_label.setText(f"{cpu:.1f}%")
            self.cpu_bar.setValue(int(cpu))
            
            self.ram_label.setText(f"{ram:.1f}%")
            self.ram_bar.setValue(int(ram))
            
            self.proc_tree.clear()
            for p in procs: 
                item = QTreeWidgetItem([p[3], f"{p[1]}%", f"{p[2]}%"])
                self.proc_tree.addTopLevelItem(item)
        except: pass

    def save_current_session(self):
        lang = self.config_mgr.language
        name, ok = QInputDialog.getText(self, t("save", lang), t("session_name", lang))
        if ok and name: 
            self.config_mgr.sessions[name] = {
                "host": self.entry_host.text(),
                "key": self.entry_key.text()
            }
            self.config_mgr.save_config()
            
            self.cb_sessions.clear()
            self.cb_sessions.addItem(t("new_session", lang))
            self.cb_sessions.addItems(self.config_mgr.sessions.keys())
            self.cb_sessions.setCurrentText(name)
