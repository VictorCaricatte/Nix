"""
main_window.py — Interface (QMainWindow): toolbar, tabs, layout, theme, sessions.
"""

import sys
import os
import subprocess

import qtawesome as qta
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFrame, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QFileDialog, QInputDialog, QMessageBox, QMenu, QColorDialog,
    QDockWidget, QTabWidget, QComboBox, QScrollArea, QSystemTrayIcon,
    QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QAction, QPixmap, QIcon

from config import ConfigManager, encrypt_password, decrypt_password
from i18n import t
from dialogs import LocalFileExplorerDialog
from tab_panel import ConnectionTab
from devops_dialogs import FleetDashboardWidget, BatchExecDialog

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class Interface(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nix")
        self.resize(1300, 850)
        self.setMinimumSize(800, 560)
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks |
                            QMainWindow.DockOption.AllowNestedDocks |
                            QMainWindow.DockOption.AllowTabbedDocks)

        self.config_mgr = ConfigManager()
        self.terminal_font_size = self.config_mgr.theme.get("font_size", 13)
        self._local_explorer = None

        self.create_core_widgets()
        self._setup_tray()
        self.apply_layout()
        self._add_fleet_tab()
        self.add_tab()
        self.update_ui_texts()
        self._setup_shortcuts()

    def _add_fleet_tab(self):
        fleet = FleetDashboardWidget(self, self.config_mgr, self._on_fleet_connect)
        self._fleet_widget = fleet
        lang = self.config_mgr.language
        idx = self.tab_widget.addTab(fleet, f" {t('fleet_tab', lang)}")
        self.tab_widget.tabBar().setTabIcon(idx, qta.icon('fa5s.satellite-dish', color='white'))

    def _on_fleet_connect(self, session_name):
        """Called when user clicks Connect on a fleet card — loads session and switches to SSH tab."""
        if session_name in self.config_mgr.sessions:
            data = self.config_mgr.sessions[session_name]
            self.entry_host.setText(data.get("host", ""))
            self.entry_key.setText(data.get("key", ""))
            from config import decrypt_password
            self.entry_pass.setText(decrypt_password(data.get("password", "")))
        self.add_tab()
        self.handle_connection()

    def _open_batch_exec(self):
        connected = []
        for i in range(self.tab_widget.count()):
            w = self.tab_widget.widget(i)
            if isinstance(w, ConnectionTab) and w.ssh_mgr.is_connected:
                connected.append((self.tab_widget.tabText(i), w))
        if not connected:
            lang = self.config_mgr.language
            QMessageBox.information(self, t("batch_exec", lang),
                                    t("no_connected_sessions", lang))
            return
        BatchExecDialog(self, connected).exec()

    def _menu_icon_color(self):
        if self.config_mgr.theme.get('mode', 'dark') == 'dark':
            return 'white'
        return self.config_mgr.theme.get('accent', '#bd93f9')

    def _show_settings_menu(self):
        lang = self.config_mgr.language
        ic   = self._menu_icon_color()
        menu = QMenu(self)
        for label, icon_name, slot in [
            (t("mode",       lang), 'fa5s.adjust',    self.toggle_theme_mode),
            (t("theme",      lang), 'fa5s.palette',   self.change_theme_color),
            (t("bg_color",   lang), 'fa5s.fill-drip', self.change_bg_color),
            (t("term_color", lang), 'fa5s.font',      self.change_terminal_color),
            (t("term_style", lang), 'fa5s.terminal',  self.cycle_term_style),
            (t("layout",     lang), 'fa5s.columns',   self.toggle_layout),
            (t("language",   lang), 'fa5s.language',  self.toggle_language),
        ]:
            act = QAction(qta.icon(icon_name, color=ic), label, self)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.exec(self.btn_settings_menu.mapToGlobal(
            self.btn_settings_menu.rect().bottomLeft()))

    def _open_fleet_tab(self):
        for i in range(self.tab_widget.count()):
            if isinstance(self.tab_widget.widget(i), FleetDashboardWidget):
                self.tab_widget.setCurrentIndex(i)
                return
        self._add_fleet_tab()
        self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1)

    def _show_tools_menu(self):
        lang = self.config_mgr.language
        ic   = self._menu_icon_color()
        menu = QMenu(self)
        for label, icon_name, slot in [
            ("Fleet Dashboard",          'fa5s.satellite-dish', self._open_fleet_tab),
            (t("new_window",     lang),  'fa5s.plus-square',    self.open_new_window),
            (t("screens",        lang),  'fa5s.desktop',        lambda: self.current_tab.show_screens_manager() if self.current_tab else None),
            (t("env_list",       lang),  'fa5s.cubes',          lambda: self.current_tab.show_env_list() if self.current_tab else None),
            (t("local_explorer", lang),  'fa5s.laptop',         self.open_local_explorer),
            (t("batch_exec",     lang),  'fa5s.layer-group',    self._open_batch_exec),
        ]:
            act = QAction(qta.icon(icon_name, color=ic), label, self)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.exec(self.btn_tools_menu.mapToGlobal(
            self.btn_tools_menu.rect().bottomLeft()))

    def _setup_shortcuts(self):
        for key, fn in [
            ("F5",  lambda: self.current_tab.sig_explorer.emit() if self.current_tab and self.current_tab.ssh_mgr.is_connected else None),
            ("F6",  lambda: self.current_tab.shortcut_rename()   if self.current_tab else None),
            ("F7",  lambda: self.current_tab.shortcut_mkdir()    if self.current_tab else None),
            ("F8",  lambda: self.current_tab.shortcut_delete()   if self.current_tab else None),
            ("F9",  lambda: self.current_tab.clear_terminal()    if self.current_tab else None),
            ("F10", self.handle_connection),
        ]:
            act = QAction(self); act.setShortcut(key); act.triggered.connect(fn); self.addAction(act)

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None; return
        self.tray_icon = QSystemTrayIcon(self)
        icon_path = resource_path("Nix.jpg")
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            self.tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        tray_menu = QMenu()
        lang = self.config_mgr.language
        self._act_show_hide = QAction(t("tray_hide", lang), self)
        self._act_show_hide.triggered.connect(self._toggle_visibility)
        act_quit = QAction(t("tray_quit", lang), self)
        act_quit.triggered.connect(QApplication.quit)
        tray_menu.addAction(self._act_show_hide)
        tray_menu.addSeparator()
        tray_menu.addAction(act_quit)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(lambda reason:
            self._toggle_visibility() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray_icon.show()

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide()
            if self.tray_icon:
                lang = self.config_mgr.language
                self._act_show_hide.setText(t("tray_show", lang))
        else:
            self.show(); self.raise_(); self.activateWindow()
            if self.tray_icon:
                lang = self.config_mgr.language
                self._act_show_hide.setText(t("tray_hide", lang))

    def _show_tray_notification(self, title, message):
        if self.tray_icon and self.tray_icon.isSystemTrayAvailable():
            self.tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3000)

    def closeEvent(self, event):
        any_connected = any(
            isinstance(self.tab_widget.widget(i), ConnectionTab) and
            self.tab_widget.widget(i).ssh_mgr.is_connected
            for i in range(self.tab_widget.count())
        )
        if self.tray_icon and any_connected:
            event.ignore()
            self.hide()
            self._act_show_hide.setText(t("tray_show", self.config_mgr.language))
            self.tray_icon.showMessage("Nix", "Nix is minimized to tray.",
                                       QSystemTrayIcon.MessageIcon.Information, 2000)
        else:
            event.accept()

    @property
    def current_tab(self):
        w = self.tab_widget.currentWidget()
        return w if isinstance(w, ConnectionTab) else None

    def add_tab(self):
        lang = self.config_mgr.language
        tab  = ConnectionTab(self.config_mgr, parent_interface=self)
        tab.sig_conn_update.connect(self._on_tab_conn_update)
        tab.sig_tab_title.connect(self._on_tab_title_changed)
        tab.sig_notify.connect(self._show_tray_notification)
        idx = self.tab_widget.addTab(tab, t("new_tab", lang))
        self.tab_widget.setCurrentIndex(idx)
        tab.update_texts(lang)

        tab.apply_panel_layout(self.config_mgr.theme.get("layout", "classic"))
        self.apply_theme()

    def close_tab(self, idx):
        tab = self.tab_widget.widget(idx)
        if isinstance(tab, ConnectionTab) and tab.ssh_mgr.is_connected:
            lang = self.config_mgr.language
            if QMessageBox.question(self, t("confirm", lang), "Close this connected tab?") != QMessageBox.StandardButton.Yes:
                return
            tab.ssh_mgr.disconnect()
        self.tab_widget.removeTab(idx)
        if self.tab_widget.count() == 0:
            self.add_tab()

    def on_tab_changed(self, idx):
        tab = self.tab_widget.widget(idx)
        if not isinstance(tab, ConnectionTab): return
        lang = self.config_mgr.language
        if tab.ssh_mgr.is_connected:
            self.btn_conn.setEnabled(True)
            self.btn_conn.setText(t("disconnect", lang))
            self.setWindowTitle(f"Nix — {tab._conn_user}@{tab._conn_host}")
        else:
            self.btn_conn.setEnabled(True)
            self.btn_conn.setText(t("connect", lang))
            self.setWindowTitle("Nix")

    def _on_tab_conn_update(self, tab, enabled, text):
        if tab is self.current_tab:
            self.btn_conn.setEnabled(enabled)
            self.btn_conn.setText(text)

    def _on_tab_title_changed(self, tab, title):
        idx = self.tab_widget.indexOf(tab)
        if idx >= 0:
            self.tab_widget.setTabText(idx, title)
        if tab is self.current_tab:
            lang = self.config_mgr.language
            self.setWindowTitle(f"Nix — {title}" if title != t("new_tab", lang) else "Nix")

    def create_core_widgets(self):
        lang = self.config_mgr.language

        self.conn_inner = self.create_card()
        self.conn_inner.setMaximumHeight(60)
        self.conn_inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        conn_layout = QHBoxLayout(self.conn_inner)
        conn_layout.setContentsMargins(10, 5, 5, 5)

        self.logo_label = QLabel()
        pixmap = QPixmap(resource_path("Nix.jpg"))
        if not pixmap.isNull():
            self.logo_label.setPixmap(pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))

        self.combo_sessions = QComboBox()
        self.combo_sessions.addItem(t("saved_sessions", lang))
        self.combo_sessions.addItems(self.config_mgr.sessions.keys())
        self.combo_sessions.currentTextChanged.connect(self.load_session)
        self.combo_sessions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.combo_sessions.customContextMenuRequested.connect(self.show_session_menu)

        self.entry_host = QLineEdit()
        self.entry_pass = QLineEdit(); self.entry_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.entry_key  = QLineEdit()

        self.btn_show_pass = QPushButton()
        self.btn_show_pass.setIcon(qta.icon('fa5s.eye', color='white'))
        self.btn_show_pass.setFixedWidth(40); self.btn_show_pass.setCheckable(True)
        self.btn_show_pass.toggled.connect(lambda c: self.entry_pass.setEchoMode(
            QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password))

        btn_browse_key = QPushButton()
        btn_browse_key.setIcon(qta.icon('fa5s.folder-open', color='white'))
        btn_browse_key.setFixedWidth(40); btn_browse_key.clicked.connect(self.browse_ssh_key)

        self.btn_save = QPushButton()
        self.btn_save.setIcon(qta.icon('fa5s.save', color='white'))
        self.btn_save.setFixedWidth(40); self.btn_save.clicked.connect(self.save_current_session)

        self.btn_conn = QPushButton()
        self.btn_conn.setIcon(qta.icon('fa5s.plug', color='white'))
        self.btn_conn.clicked.connect(self.handle_connection)

        self.chk_x11 = QCheckBox()

        self.btn_new_win = QPushButton()
        self.btn_new_win.setIcon(qta.icon('fa5s.plus-square', color='white'))
        self.btn_new_win.clicked.connect(self.open_new_window)

        self.btn_new_tab = QPushButton()
        self.btn_new_tab.setIcon(qta.icon('fa5s.plus', color='white'))
        self.btn_new_tab.setFixedWidth(40); self.btn_new_tab.clicked.connect(self.add_tab)

        self.btn_screens = QPushButton()
        self.btn_screens.setIcon(qta.icon('fa5s.desktop', color='white'))
        self.btn_screens.clicked.connect(lambda: self.current_tab.show_screens_manager() if self.current_tab else None)

        self.btn_env = QPushButton()
        self.btn_env.setIcon(qta.icon('fa5s.cubes', color='white'))
        self.btn_env.clicked.connect(lambda: self.current_tab.show_env_list() if self.current_tab else None)

        self.btn_term_style = QPushButton()
        self.btn_term_style.setIcon(qta.icon('fa5s.terminal', color='white'))
        self.btn_term_style.clicked.connect(self.cycle_term_style)

        self.btn_mode = QPushButton()
        self.btn_mode.setIcon(qta.icon('fa5s.adjust', color='white'))
        self.btn_mode.clicked.connect(self.toggle_theme_mode)

        self.btn_color = QPushButton()
        self.btn_color.setIcon(qta.icon('fa5s.palette', color='white'))
        self.btn_color.clicked.connect(self.change_theme_color)

        self.btn_bg_color = QPushButton()
        self.btn_bg_color.setIcon(qta.icon('fa5s.fill-drip', color='white'))
        self.btn_bg_color.clicked.connect(self.change_bg_color)

        self.btn_term_color = QPushButton()
        self.btn_term_color.setIcon(qta.icon('fa5s.font', color='white'))
        self.btn_term_color.clicked.connect(self.change_terminal_color)

        self.btn_layout = QPushButton()
        self.btn_layout.setIcon(qta.icon('fa5s.columns', color='white'))
        self.btn_layout.clicked.connect(self.toggle_layout)

        self.btn_lang = QPushButton()
        self.btn_lang.setIcon(qta.icon('fa5s.language', color='white'))
        self.btn_lang.clicked.connect(self.toggle_language)

        self.btn_local_exp = QPushButton()
        self.btn_local_exp.setIcon(qta.icon('fa5s.laptop', color='white'))
        self.btn_local_exp.clicked.connect(self.open_local_explorer)

        self.btn_batch = QPushButton()
        self.btn_batch.setIcon(qta.icon('fa5s.layer-group', color='white'))
        self.btn_batch.setToolTip("Batch / Cluster Execution")
        self.btn_batch.clicked.connect(self._open_batch_exec)

        self.btn_tools_menu = QPushButton()
        self.btn_tools_menu.setIcon(qta.icon('fa5s.tools', color='white'))
        self.btn_tools_menu.setFixedWidth(40)
        self.btn_tools_menu.clicked.connect(self._show_tools_menu)

        self.btn_settings_menu = QPushButton()
        self.btn_settings_menu.setIcon(qta.icon('fa5s.cog', color='white'))
        self.btn_settings_menu.setFixedWidth(40)
        self.btn_settings_menu.clicked.connect(self._show_settings_menu)

        for w in [self.logo_label, self.combo_sessions]:
            conn_layout.addWidget(w)
        conn_layout.addWidget(self.entry_host, 3)
        conn_layout.addWidget(self.entry_pass, 2)
        conn_layout.addWidget(self.btn_show_pass)
        conn_layout.addWidget(self.entry_key, 2)
        for w in [btn_browse_key, self.btn_save, self.chk_x11, self.btn_conn,
                  self.btn_new_tab, self.btn_tools_menu, self.btn_settings_menu]:
            conn_layout.addWidget(w)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self.on_tab_changed)

    def create_card(self):
        f = QFrame(); f.setObjectName("Card"); return f

    def apply_layout(self):
        was_maximized  = self.isMaximized()
        saved_geometry = self.saveGeometry()

        parent = self.conn_inner.parent()
        if isinstance(parent, QScrollArea):
            parent.takeWidget()
        else:
            self.conn_inner.setParent(None)

        self.tab_widget.setParent(None)

        central = self.centralWidget()
        if central and central is not self.tab_widget and central is not self.conn_inner:
            central.setParent(None)
            central.deleteLater()

        for dock in self.findChildren(QDockWidget):
            self.removeDockWidget(dock)
            dock.deleteLater()

        layout_type = self.config_mgr.theme.get("layout", "classic")
        lang = self.config_mgr.language

        def make_conn_scroll():
            cs = QScrollArea()
            cs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            cs.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            cs.setWidgetResizable(True)
            cs.setFrameShape(QFrame.Shape.NoFrame)
            cs.setObjectName("ConnScrollArea")
            cs.setWidget(self.conn_inner)
            cs.setMaximumHeight(66)
            cs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            return cs

        if layout_type == "dock":
            self.conn_dock = QDockWidget(t("conn_config", lang), self)
            self.conn_dock.setAllowedAreas(
                Qt.DockWidgetArea.TopDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea)

            self.conn_dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetMovable |
                QDockWidget.DockWidgetFeature.DockWidgetFloatable)
            self.conn_dock.setWidget(make_conn_scroll())
            self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.conn_dock)
            self.setCentralWidget(self.tab_widget)
        else:
            for tab_i in range(self.tab_widget.count()):
                w = self.tab_widget.widget(tab_i)
                if isinstance(w, ConnectionTab):
                    w.exp_inner.setObjectName("Card")
                    w.term_inner.setObjectName("Card")
                    w.sys_inner.setObjectName("Card")

            main_widget = QWidget()
            ml = QVBoxLayout(main_widget)
            ml.setContentsMargins(10, 10, 10, 10)
            ml.setSpacing(10)
            ml.addWidget(make_conn_scroll())
            ml.addWidget(self.tab_widget, 1)
            self.setCentralWidget(main_widget)

        self.apply_theme()

        for i in range(self.tab_widget.count()):
            w = self.tab_widget.widget(i)
            if isinstance(w, ConnectionTab):
                w.apply_panel_layout(layout_type)

        if was_maximized:
            QTimer.singleShot(0, self.showMaximized)
        else:
            self.restoreGeometry(saved_geometry)

    def get_stylesheet(self):
        thm = self.config_mgr.theme
        mode       = thm.get("mode", "dark")
        accent     = thm.get("accent", "#bd93f9")
        term_color = thm.get("terminal_color", "#a6accd")
        base_bg    = thm.get("bg_color", "#151015")
        term_style = thm.get("term_style", "standard")

        if mode == "dark":
            bg = base_bg; card = "#1e161e"; fg = "#f8f8f2"; term_bg = "#0d0a0d"
            border = "transparent"; input_border = "#444"
            btn_dis_bg = "#444444"; btn_dis_fg = "#888888"; hover_fg = bg
        else:
            bg = "#f0f2f6"; card = "#ffffff"; fg = "#000000"; term_bg = "#ffffff"
            border = "#cccccc"; input_border = "#cccccc"
            btn_dis_bg = "#cccccc"; btn_dis_fg = "#888888"; hover_fg = "#ffffff"

        if term_style == "matrix":
            term_font = "'Courier New', monospace"; term_weight = "bold"; term_bg_eff = "#000000"
        elif term_style == "retro":
            term_font = "'VT100', 'Courier', monospace"; term_weight = "bold"; term_bg_eff = "#1a1a1a"
        else:
            term_font = "'Consolas', 'Courier New', monospace"; term_weight = "normal"; term_bg_eff = term_bg

        fs = self.terminal_font_size
        return f"""
        QMainWindow, QDialog {{ background-color: {bg}; color: {fg}; }}
        QWidget {{ font-family: 'Segoe UI', sans-serif; color: {fg}; }}
        QDockWidget {{ color: {accent}; font-weight: bold; }}
        QDockWidget::title {{ background: {card}; padding: 6px; border-bottom: 2px solid {accent}; }}
        QFrame#Card {{ background-color: {card}; border-radius: 12px; border: 1px solid {border}; }}
        QLineEdit, QComboBox {{ background-color: {bg}; color: {fg}; border: 1px solid {input_border}; border-radius: 6px; padding: 6px; }}
        QLineEdit:focus, QComboBox:focus {{ border: 1px solid {accent}; }}
        QComboBox::drop-down {{ border: none; }}
        QComboBox QAbstractItemView {{ background-color: {bg}; color: {fg}; selection-background-color: {accent}; selection-color: white; }}
        QPushButton {{ background-color: {accent}; color: white; border-radius: 6px; padding: 6px 14px; font-weight: bold; border: none; }}
        QPushButton:hover {{ background-color: {fg}; color: {hover_fg}; }}
        QPushButton:disabled {{ background-color: {btn_dis_bg}; color: {btn_dis_fg}; }}
        QTreeWidget, QListWidget, QTableView {{ background-color: {bg}; alternate-background-color: {card}; color: {fg}; border: 1px solid {border}; border-radius: 8px; outline: none; padding: 5px; }}
        QTreeWidget::item, QListWidget::item {{ padding: 4px; border-radius: 4px; }}
        QTreeWidget::item:selected, QListWidget::item:selected, QTableView::item:selected {{ background-color: {accent}; color: white; }}
        QTreeWidget::item:focus, QListWidget::item:focus, QTableView::item:focus {{ outline: none; border: none; }}
        QTreeView {{ background-color: {bg}; alternate-background-color: {card}; color: {fg}; border: 1px solid {border}; border-radius: 8px; outline: none; }}
        QTreeView::item {{ padding: 3px; border-radius: 4px; }}
        QTreeView::item:selected {{ background-color: {accent}; color: white; }}
        QTreeView::item:focus {{ outline: none; border: none; }}
        QHeaderView {{ background-color: {card}; border: none; }}
        QHeaderView::section {{ background-color: {card}; color: {fg}; padding: 6px; font-weight: bold; border: none; border-bottom: 1px solid {border}; }}
        QHeaderView::section:vertical {{ background-color: {card}; border: none; border-right: 1px solid {border}; padding: 4px; }}
        QTableView QHeaderView::section:vertical {{ background-color: {bg}; border: none; border-right: 1px solid {border}; color: {fg}; }}
        QSplitter::handle {{ background-color: {border}; }}
        QSplitter::handle:horizontal {{ width: 1px; }}
        QSplitter::handle:vertical {{ height: 1px; }}
        QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; background: {card}; }}
        QTabBar::tab {{ background: {bg}; color: {fg}; padding: 8px 16px; border: 1px solid {border}; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
        QTabBar::tab:selected {{ background: {card}; color: {accent}; font-weight: bold; }}
        QTextEdit, QPlainTextEdit {{ background-color: {term_bg_eff}; color: {term_color}; border: 1px solid {border}; border-radius: 8px; padding: 10px; font-family: {term_font}; font-size: {fs}px; font-weight: {term_weight}; }}
        QScrollBar:vertical {{ background-color: {bg}; width: 12px; border-radius: 6px; }}
        QScrollBar::handle:vertical {{ background-color: {accent}; min-height: 20px; border-radius: 6px; margin: 2px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar:horizontal {{ background-color: {bg}; height: 6px; border-radius: 3px; }}
        QScrollBar::handle:horizontal {{ background-color: {accent}; min-width: 20px; border-radius: 3px; margin: 1px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        QScrollArea#ConnScrollArea {{ background-color: transparent; border: none; }}
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
        if self._local_explorer:
            self._local_explorer.refresh_theme()

    def toggle_theme_mode(self):
        self.config_mgr.theme["mode"] = "light" if self.config_mgr.theme.get("mode") == "dark" else "dark"
        self.config_mgr.save_config(); self.apply_theme()

    def cycle_term_style(self):
        styles = ["standard", "retro", "matrix"]
        cur    = self.config_mgr.theme.get("term_style", "standard")
        nxt    = styles[(styles.index(cur) + 1) % 3] if cur in styles else "standard"
        self.config_mgr.theme["term_style"] = nxt
        self.config_mgr.save_config(); self.apply_theme()
        lang = self.config_mgr.language
        if self.current_tab: self.current_tab.sig_log.emit(f"[{t('term_style', lang)}: {t(f'style_{nxt}', lang)}]")

    def toggle_language(self):
        langs = ["en", "pt", "es"]
        cur   = self.config_mgr.language
        self.config_mgr.language = langs[(langs.index(cur) + 1) % 3] if cur in langs else "en"
        self.config_mgr.save_config(); self.update_ui_texts()

    def change_theme_color(self):
        c = QColorDialog.getColor(initial=QColor(self.config_mgr.theme.get("accent", "#bd93f9")), parent=self)
        if c.isValid(): self.config_mgr.theme['accent'] = c.name(); self.config_mgr.save_config(); self.apply_theme()

    def change_bg_color(self):
        c = QColorDialog.getColor(initial=QColor(self.config_mgr.theme.get("bg_color", "#151015")), parent=self)
        if c.isValid(): self.config_mgr.theme['bg_color'] = c.name(); self.config_mgr.save_config(); self.apply_theme()

    def change_terminal_color(self):
        c = QColorDialog.getColor(initial=QColor(self.config_mgr.theme.get("terminal_color", "#a6accd")), parent=self)
        if c.isValid(): self.config_mgr.theme['terminal_color'] = c.name(); self.config_mgr.save_config(); self.apply_theme()

    def increase_font(self):
        self.terminal_font_size += 1
        self.config_mgr.theme["font_size"] = self.terminal_font_size
        self.config_mgr.save_config(); self.apply_theme()

    def decrease_font(self):
        if self.terminal_font_size > 6:
            self.terminal_font_size -= 1
            self.config_mgr.theme["font_size"] = self.terminal_font_size
            self.config_mgr.save_config(); self.apply_theme()

    def toggle_layout(self):
        cur = self.config_mgr.theme.get("layout", "classic")
        self.config_mgr.theme["layout"] = "dock" if cur == "classic" else "classic"
        self.config_mgr.save_config(); self.apply_layout()

    def update_ui_texts(self):
        lang = self.config_mgr.language
        self.entry_host.setPlaceholderText(t("host_placeholder", lang))
        self.entry_pass.setPlaceholderText(t("pass_placeholder", lang))
        self.entry_key.setPlaceholderText(t("key_placeholder", lang))
        self.chk_x11.setText(t("x11_compress", lang))

        tab = self.current_tab
        if tab and tab.ssh_mgr.is_connected:
            self.btn_conn.setText(t("disconnect", lang))
        else:
            self.btn_conn.setText(t("connect", lang))

        self.combo_sessions.setToolTip(t("tip_sessions_ctx", lang))
        self.btn_show_pass.setToolTip(t("tip_show_pass", lang))
        self.btn_save.setToolTip(t("tip_save_session", lang))
        self.btn_conn.setToolTip(t("tip_connect", lang))
        self.btn_new_tab.setToolTip(t("add_tab", lang))
        self.btn_tools_menu.setToolTip("Tools")
        self.btn_settings_menu.setToolTip("Settings")
        self.combo_sessions.setItemText(0, t("saved_sessions", lang))

        for i in range(self.tab_widget.count()):
            w = self.tab_widget.widget(i)
            if isinstance(w, ConnectionTab):
                w.update_texts(lang)

        if self._local_explorer and self._local_explorer.isVisible():
            self._local_explorer.update_lang(lang)

        if self.tray_icon:
            self._act_show_hide.setText(t("tray_hide" if self.isVisible() else "tray_show", lang))

    def handle_connection(self):
        tab = self.current_tab
        if tab:
            tab.handle_connection(self.entry_host.text(), self.entry_pass.text(),
                                  self.entry_key.text(), self.chk_x11.isChecked())

    def load_session(self, name):
        if name in self.config_mgr.sessions:
            data = self.config_mgr.sessions[name]
            self.entry_host.setText(data.get("host", ""))
            self.entry_key.setText(data.get("key", ""))
            pwd = decrypt_password(data.get("password", ""))
            self.entry_pass.setText(pwd)

    def save_current_session(self):
        lang = self.config_mgr.language
        name, ok = QInputDialog.getText(self, t("save", lang), t("session_name", lang))
        if ok and name:
            save_pass = (
                self.entry_pass.text() and
                QMessageBox.question(
                    self, t("save", lang), "Salvar senha na sessão? (será criptografada)",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) == QMessageBox.StandardButton.Yes
            )
            self.config_mgr.sessions[name] = {
                "host": self.entry_host.text(),
                "key": self.entry_key.text(),
                "password": encrypt_password(self.entry_pass.text()) if save_pass else "",
            }
            self.config_mgr.save_config()
            if self.combo_sessions.findText(name) == -1:
                self.combo_sessions.addItem(name)

    def show_session_menu(self, pos):
        lang    = self.config_mgr.language
        current = self.combo_sessions.currentText()
        if current == t("saved_sessions", lang) or current not in self.config_mgr.sessions: return
        menu = QMenu(self)
        act  = QAction(qta.icon('fa5s.trash', color='#dc3545'), f"Delete: {current}", self)
        act.triggered.connect(lambda: self.delete_session(current))
        menu.addAction(act)
        menu.exec(self.combo_sessions.mapToGlobal(pos))

    def delete_session(self, name):
        if name in self.config_mgr.sessions:
            del self.config_mgr.sessions[name]; self.config_mgr.save_config()
            idx = self.combo_sessions.findText(name)
            if idx >= 0: self.combo_sessions.removeItem(idx)

    def browse_ssh_key(self):
        lang = self.config_mgr.language
        file, _ = QFileDialog.getOpenFileName(self, t("sel_key", lang), os.path.expanduser("~/.ssh"))
        if file: self.entry_key.setText(file)

    def open_new_window(self):
        if getattr(sys, 'frozen', False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable] + sys.argv)

    def on_drop_files(self, files):
        if self.current_tab:
            self.current_tab.on_drop_files(files)

    def open_local_explorer(self):
        lang = self.config_mgr.language
        if self._local_explorer is None or not self._local_explorer.isVisible():
            self._local_explorer = LocalFileExplorerDialog(self, lang)
        self._local_explorer.update_lang(lang)
        self._local_explorer.refresh_theme()
        self._local_explorer.show()
        self._local_explorer.raise_()
        self._local_explorer.activateWindow()
