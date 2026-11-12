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
    QCheckBox, QDialog, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QAction, QPixmap, QIcon, QKeySequence

from config import ConfigManager, encrypt_password, decrypt_password
from i18n import t
from dialogs import LocalFileExplorerDialog
from tab_panel import ConnectionTab

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

TERM_STYLES = ["standard", "retro", "matrix", "crt", "crt_blue"]


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
        self._fleet_widget = None

        self.create_core_widgets()
        self._setup_tray()
        self.apply_layout()
        self.add_tab()
        self.update_ui_texts()
        self._setup_shortcuts()
        QTimer.singleShot(0, self._add_fleet_tab)

    def _add_fleet_tab(self):
        from devops_dialogs import FleetDashboardWidget
        fleet = FleetDashboardWidget(self, self.config_mgr, self._on_fleet_connect)
        self._fleet_widget = fleet
        lang = self.config_mgr.language
        idx = self.tab_widget.insertTab(0, fleet, f" {t('fleet_tab', lang)}")
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
        from devops_dialogs import BatchExecDialog
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
            (t("term_style", lang), 'fa5s.terminal',  "SUB_STYLE"),
            (t("layout",     lang), 'fa5s.columns',   "SUB_LAYOUT"),
            (t("language",   lang), 'fa5s.language',  "SUB_LANG"),
        ]:
            if slot == "SUB_LANG":
                self._fill_choice_menu(
                    menu.addMenu(qta.icon(icon_name, color=ic), label),
                    [("en", "English"), ("pt", "Português"), ("es", "Español")],
                    lang, self.set_language)
                continue
            if slot == "SUB_STYLE":
                self._fill_choice_menu(
                    menu.addMenu(qta.icon(icon_name, color=ic), label),
                    [(n, t(f"style_{n}", lang)) for n in TERM_STYLES],
                    self.config_mgr.theme.get("term_style", "standard"),
                    self.set_term_style)
                continue
            if slot == "SUB_LAYOUT":
                self._fill_choice_menu(
                    menu.addMenu(qta.icon(icon_name, color=ic), label),
                    [("classic", t("layout_classic", lang)), ("dock", t("layout_dock", lang))],
                    self.config_mgr.theme.get("layout", "classic"),
                    self.set_layout)
                continue
            act = QAction(qta.icon(icon_name, color=ic), label, self)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.exec(self.btn_settings_menu.mapToGlobal(
            self.btn_settings_menu.rect().bottomLeft()))

    @staticmethod
    def _fill_choice_menu(submenu, choices, current, on_pick):
        """Preenche um submenu com opções exclusivas (a atual vem marcada), para
        escolher direto em vez de ciclar uma a uma."""
        for key, label in choices:
            act = QAction(label, submenu)
            act.setCheckable(True)
            act.setChecked(key == current)
            act.triggered.connect(lambda _=False, k=key: on_pick(k))
            submenu.addAction(act)

    def _open_fleet_tab(self):
        if self._fleet_widget is not None:
            idx = self.tab_widget.indexOf(self._fleet_widget)
            if idx >= 0:
                self.tab_widget.setCurrentIndex(idx)
                return
        self._add_fleet_tab()
        self.tab_widget.setCurrentIndex(self.tab_widget.indexOf(self._fleet_widget))

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
            (t("shortcuts",      lang),  'fa5s.keyboard',       self.show_shortcuts),
        ]:
            act = QAction(qta.icon(icon_name, color=ic), label, self)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.exec(self.btn_tools_menu.mapToGlobal(
            self.btn_tools_menu.rect().bottomLeft()))

    def _setup_shortcuts(self):
        from terminal import PyteTerminal

        def guarded(key, fn):
            # Se o terminal está com foco, a F-key pertence ao app remoto
            # (ex.: F10 = sair do htop) — repassa em vez de executar o atalho
            # do Nix (F10 desconectava o servidor no meio do htop!).
            def run():
                w = QApplication.focusWidget()
                while w is not None:
                    if isinstance(w, PyteTerminal):
                        w.send_fkey(key)
                        return
                    w = w.parentWidget()
                fn()
            return run

        for key, fn in [
            ("F5",  lambda: self.current_tab.sig_explorer.emit() if self.current_tab and self.current_tab.ssh_mgr.is_connected else None),
            ("F6",  lambda: self.current_tab.shortcut_rename()   if self.current_tab else None),
            ("F7",  lambda: self.current_tab.shortcut_mkdir()    if self.current_tab else None),
            ("F8",  lambda: self.current_tab.shortcut_delete()   if self.current_tab else None),
            ("F9",  lambda: self.current_tab.clear_terminal()    if self.current_tab else None),
            ("F10", self.handle_connection),
            ("F1",  self.show_shortcuts),
        ]:
            act = QAction(self); act.setShortcut(key); act.triggered.connect(guarded(key, fn)); self.addAction(act)

        # Atalhos do Nix que NÃO conflitam com o shell remoto (Ctrl+T/W/L etc.
        # pertencem ao bash/readline, por isso as abas usam Ctrl+Shift).
        def add(keys, fn):
            act = QAction(self)
            act.setShortcuts([QKeySequence(k) for k in keys])
            act.triggered.connect(fn)
            self.addAction(act)

        add(["Ctrl+Shift+T"], self.add_tab)
        add(["Ctrl+Shift+W"], lambda: self.close_tab(self.tab_widget.currentIndex()))
        add(["Ctrl+Tab", "Ctrl+PgDown"], lambda: self._cycle_tab(1))
        add(["Ctrl+Shift+Tab", "Ctrl+Shift+Backtab", "Ctrl+PgUp"], lambda: self._cycle_tab(-1))
        add(["Ctrl++", "Ctrl+="], self.increase_font)
        add(["Ctrl+-"], self.decrease_font)
        add(["Ctrl+0"], self.reset_font)
        add(["Ctrl+Shift+M"], lambda: self.current_tab.toggle_monitor_panel() if self.current_tab else None)

    def _cycle_tab(self, step):
        n = self.tab_widget.count()
        if n > 1:
            self.tab_widget.setCurrentIndex((self.tab_widget.currentIndex() + step) % n)

    def show_shortcuts(self):
        lang = self.config_mgr.language
        rows = [
            ("Ctrl+Shift+T", "sc_new_tab"), ("Ctrl+Shift+W", "sc_close_tab"),
            ("Ctrl+Tab / Ctrl+PgDn", "sc_next_tab"), ("Ctrl+Shift+Tab / Ctrl+PgUp", "sc_prev_tab"),
            ("Ctrl + / Ctrl =", "sc_zoom_in"), ("Ctrl -", "sc_zoom_out"), ("Ctrl 0", "sc_zoom_reset"), ("Ctrl+Shift+M", "sc_monitor"),
            ("Ctrl + wheel", "sc_wheel"), ("Tab", "sc_tab"),
            ("F1", "sc_help"), ("F5", "sc_refresh"), ("F6", "sc_rename"), ("F7", "sc_mkdir"),
            ("F8", "sc_delete"), ("F9", "sc_clear"), ("F10", "sc_connect"),
        ]
        html = "<table cellpadding='4'>" + "".join(
            f"<tr><td><b>{k}</b></td><td>{t(v, lang)}</td></tr>" for k, v in rows
        ) + "</table><p>" + t("shortcuts_hint", lang) + "</p>"
        dlg = QDialog(self)
        dlg.setWindowTitle(t("shortcuts", lang))
        lay = QVBoxLayout(dlg)
        lbl = QLabel(html)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(lbl)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        dlg.exec()

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
        if any_connected:
            lang = self.config_mgr.language
            box = QMessageBox(self)
            box.setWindowTitle("Nix")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(t("close_msg", lang))
            btn_quit = box.addButton(t("close_quit", lang), QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(t("transfer_cancel", lang), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is btn_quit:
                self._disconnect_all()
                event.accept()
                QApplication.quit()
            else:
                event.ignore()
        else:
            self._disconnect_all()
            event.accept()

    def _disconnect_all(self):
        for i in range(self.tab_widget.count()):
            w = self.tab_widget.widget(i)
            if isinstance(w, ConnectionTab) and w.ssh_mgr.is_connected:
                try:
                    w.ssh_mgr.disconnect()
                except Exception:
                    pass

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
        tab.sig_conn_state.connect(self._on_tab_state_changed)
        idx = self.tab_widget.addTab(tab, t("new_tab", lang))
        self._on_tab_state_changed(tab, "idle")
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

    _STATE_COLORS = {"connected": "#28a745", "connecting": "#ffb000",
                     "lost": "#dc3545", "idle": "#888888"}

    def _on_tab_state_changed(self, tab, state):
        """Bolinha colorida na aba: verde conectado, âmbar conectando, vermelho
        conexão perdida, cinza desconectado."""
        idx = self.tab_widget.indexOf(tab)
        if idx < 0:
            return
        color = self._STATE_COLORS.get(state, "#888888")
        self.tab_widget.tabBar().setTabIcon(idx, qta.icon('fa5s.circle', color=color))
        lang = self.config_mgr.language
        tips = {"connected": t("disconnect", lang), "connecting": t("connecting", lang),
                "lost": t("conn_lost_title", lang), "idle": t("connect", lang)}
        self.tab_widget.tabBar().setTabToolTip(idx, tips.get(state, ""))

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
        self.conn_inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        conn_outer = QVBoxLayout(self.conn_inner)
        conn_outer.setContentsMargins(0, 0, 0, 0)
        conn_outer.setSpacing(0)
        self.conn_row1 = QWidget()
        conn_layout = QHBoxLayout(self.conn_row1)
        conn_layout.setContentsMargins(10, 5, 5, 5)
        # 2ª linha (só no modo compacto): chave SSH e X11, atrás do botão "..."
        self.conn_row2 = QWidget()
        self._row2_layout = QHBoxLayout(self.conn_row2)
        self._row2_layout.setContentsMargins(10, 0, 10, 6)
        self.conn_row2.hide()
        conn_outer.addWidget(self.conn_row1)
        conn_outer.addWidget(self.conn_row2)
        self._conn_compact = False
        self._conn_scroll = None

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

        btn_browse_key = self.btn_browse_key = QPushButton()
        btn_browse_key.setIcon(qta.icon('fa5s.folder-open', color='white'))
        btn_browse_key.setFixedWidth(40); btn_browse_key.clicked.connect(self.browse_ssh_key)

        self.btn_more = QPushButton()
        self.btn_more.setIcon(qta.icon('fa5s.ellipsis-h', color='white'))
        self.btn_more.setFixedWidth(40)
        self.btn_more.setCheckable(True)
        self.btn_more.setVisible(False)
        self.btn_more.toggled.connect(self._toggle_conn_row2)

        self.btn_save = QPushButton()
        self.btn_save.setIcon(qta.icon('fa5s.save', color='white'))
        self.btn_save.setFixedWidth(40); self.btn_save.clicked.connect(self.save_current_session)

        self.btn_manage_sessions = QPushButton()
        self.btn_manage_sessions.setIcon(qta.icon('fa5s.list', color='white'))
        self.btn_manage_sessions.setFixedWidth(40)
        self.btn_manage_sessions.clicked.connect(self.manage_sessions)

        self.btn_edit_session = QPushButton()
        self.btn_edit_session.setIcon(qta.icon('fa5s.pencil-alt', color='white'))
        self.btn_edit_session.setFixedWidth(40)
        self.btn_edit_session.setVisible(False)
        self.btn_edit_session.clicked.connect(self.unlock_session_fields)

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

        for w in [self.logo_label, self.combo_sessions, self.btn_manage_sessions]:
            conn_layout.addWidget(w)
        conn_layout.addWidget(self.entry_host, 3)
        conn_layout.addWidget(self.entry_pass, 2)
        conn_layout.addWidget(self.btn_show_pass)
        conn_layout.addWidget(self.entry_key, 2)
        for w in [btn_browse_key, self.btn_save, self.btn_edit_session, self.btn_more, self.chk_x11, self.btn_conn,
                  self.btn_new_tab, self.btn_tools_menu, self.btn_settings_menu]:
            conn_layout.addWidget(w)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self.on_tab_changed)

    def _toggle_conn_row2(self, checked):
        self.conn_row2.setVisible(checked and self._conn_compact)
        self._update_conn_height()

    def _update_conn_height(self):
        h = 60 + (46 if self.conn_row2.isVisibleTo(self.conn_inner) else 0)
        self.conn_inner.setMaximumHeight(h)
        try:
            if self._conn_scroll is not None:
                self._conn_scroll.setFixedHeight(h + 16)
        except RuntimeError:
            self._conn_scroll = None

    def _set_conn_compact(self, compact):
        """Janela estreita: chave SSH e X11 saem da barra principal e ficam numa
        2ª linha, aberta pelo botão '...'. Evita rolar a barra na horizontal."""
        if compact == self._conn_compact:
            return
        self._conn_compact = compact
        l1 = self.conn_row1.layout()
        extras = (self.entry_key, self.btn_browse_key, self.chk_x11)
        for w in extras:
            l1.removeWidget(w)
            self._row2_layout.removeWidget(w)
        if compact:
            self._row2_layout.addWidget(self.entry_key, 1)
            self._row2_layout.addWidget(self.btn_browse_key)
            self._row2_layout.addWidget(self.chk_x11)
        else:
            i = l1.indexOf(self.btn_show_pass) + 1
            l1.insertWidget(i, self.entry_key, 2)
            l1.insertWidget(i + 1, self.btn_browse_key)
            l1.insertWidget(l1.indexOf(self.btn_edit_session) + 1, self.chk_x11)
        for w in extras:
            w.show()
        self.btn_more.setVisible(compact)
        self.conn_row2.setVisible(compact and self.btn_more.isChecked())
        self._update_conn_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "btn_more"):
            self._set_conn_compact(self.width() < 1150)

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
            # Folga p/ a barra horizontal (6px) + conteúdo de 60px: com 66 o
            # scrollbar aparecia/sumia em loop de layout (stack overflow).
            self._conn_scroll = cs
            cs.setFixedHeight(76)
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

        self._update_conn_height()
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
        base_bg    = thm.get("bg_color", "#151015")
        term_style = thm.get("term_style", "standard")

        _DEFAULT_DARK_TERM_COLOR = "#a6accd"

        if mode == "dark":
            bg = base_bg; card = "#1e161e"; fg = "#f8f8f2"; term_bg = "#0d0a0d"
            border = "transparent"; input_border = "#444"
            btn_dis_bg = "#444444"; btn_dis_fg = "#888888"
            hover_bg = fg; hover_fg = bg
            divider_color = "#555555"
            term_color = thm.get("terminal_color", _DEFAULT_DARK_TERM_COLOR)
        else:
            bg = "#f0f2f6"; card = "#ffffff"; fg = "#000000"; term_bg = "#f5f5f5"
            border = "#cccccc"; input_border = "#cccccc"
            btn_dis_bg = "#cccccc"; btn_dis_fg = "#888888"
            # Hover no claro: tom mais escuro do accent (o antigo preto/branco
            # deixava o botão ilegível ao passar o mouse)
            hover_bg = QColor(accent).darker(125).name(); hover_fg = "#ffffff"
            divider_color = "#d0d0d0"
            stored_tc = thm.get("terminal_color", _DEFAULT_DARK_TERM_COLOR)
            term_color = "#2d3436" if stored_tc == _DEFAULT_DARK_TERM_COLOR else stored_tc

        # Estilos retro têm fundo escuro próprio: o texto usa a cor de fósforo
        # do estilo (senão, no modo claro, ficava cinza-escuro sobre preto).
        if term_style == "matrix":
            term_font = "'Courier New', monospace"; term_weight = "bold"; term_bg_eff = "#000000"; term_color = "#00ff41"
        elif term_style == "retro":
            term_font = "'VT100', 'Courier', monospace"; term_weight = "bold"; term_bg_eff = "#1a1a1a"; term_color = "#ffb000"
        elif term_style == "crt":
            term_font = "'Courier New', monospace"; term_weight = "bold"; term_bg_eff = "#0a1206"; term_color = "#33ff33"
        elif term_style == "crt_blue":
            term_font = "'Courier New', monospace"; term_weight = "bold"; term_bg_eff = "#050d18"; term_color = "#4dc4ff"
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
        QPushButton:hover {{ background-color: {hover_bg}; color: {hover_fg}; }}
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
        QSplitter::handle {{ background-color: {input_border}; margin: 0px; padding: 0px; }}
        QSplitter::handle:hover, QSplitter::handle:pressed {{ background-color: {accent}; }}
        QSplitter::handle:horizontal {{ width: 3px; }}
        QSplitter::handle:vertical {{ height: 3px; }}
        QMainWindow::separator {{ background-color: {input_border}; width: 3px; height: 3px; }}
        QMainWindow::separator:hover {{ background-color: {accent}; }}
        QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; background: {card}; }}
        QTabBar::tab {{ background: {bg}; color: {fg}; padding: 8px 16px; border: 1px solid {border}; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
        QTabBar::tab:selected {{ background: {card}; color: {accent}; font-weight: bold; }}
        QTextEdit, QPlainTextEdit {{ background-color: {term_bg_eff}; color: {term_color}; border: 1px solid {border}; border-radius: 8px; padding: 10px; font-family: {term_font}; font-size: {fs}px; font-weight: {term_weight}; }}
        QTextEdit#OSInfoText {{ background-color: {term_bg_eff}; color: {term_color}; border: 1px solid {border}; border-radius: 8px; padding: 10px; font-family: 'Consolas', monospace; font-size: 12px; font-weight: normal; }}
        QScrollBar:vertical {{ background-color: {bg}; width: 12px; border-radius: 6px; }}
        QScrollBar::handle:vertical {{ background-color: {accent}; min-height: 20px; border-radius: 6px; margin: 2px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar:horizontal {{ background-color: {bg}; height: 6px; border-radius: 3px; }}
        QScrollBar::handle:horizontal {{ background-color: {accent}; min-width: 20px; border-radius: 3px; margin: 1px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        QScrollArea#ConnScrollArea {{ background-color: transparent; border: none; }}
        QScrollArea#TransferScroll {{ background-color: {card}; border: none; }}
        QScrollArea#TransferScroll > QWidget {{ background-color: {card}; }}
        QWidget#TransferList {{ background-color: {card}; }}
        QLabel#Title {{ font-size: 15px; font-weight: bold; color: {accent}; }}
        QLabel#PromptLabel {{ color: {accent}; font-weight: bold; font-size: 16px; }}
        QLabel#MonitorValue {{ font-size: 26px; font-weight: bold; color: {fg}; }}
        QFrame#MonitorDivider {{ background-color: {divider_color}; max-height: 1px; border: none; }}
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
        for i in range(self.tab_widget.count()):
            w = self.tab_widget.widget(i)
            if isinstance(w, ConnectionTab):
                w.refresh_transfer_panel_theme()
                w.refresh_terminal_theme()

    def toggle_theme_mode(self):
        self.config_mgr.theme["mode"] = "light" if self.config_mgr.theme.get("mode") == "dark" else "dark"
        self.config_mgr.save_config(); self.apply_theme()

    def cycle_term_style(self):
        styles = TERM_STYLES
        cur    = self.config_mgr.theme.get("term_style", "standard")
        nxt    = styles[(styles.index(cur) + 1) % len(styles)] if cur in styles else "standard"
        self.set_term_style(nxt)

    def set_term_style(self, nxt):
        if nxt not in TERM_STYLES:
            return
        self.config_mgr.theme["term_style"] = nxt
        self.config_mgr.save_config(); self.apply_theme()
        lang = self.config_mgr.language
        if self.current_tab: self.current_tab.sig_log.emit(f"[{t('term_style', lang)}: {t(f'style_{nxt}', lang)}]")

    def set_language(self, code):
        if code in ("en", "pt", "es") and code != self.config_mgr.language:
            self.config_mgr.language = code
            self.config_mgr.save_config(); self.update_ui_texts()

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

    def reset_font(self):
        self.terminal_font_size = 13
        self.config_mgr.theme["font_size"] = 13
        self.config_mgr.save_config(); self.apply_theme()

    def decrease_font(self):
        if self.terminal_font_size > 6:
            self.terminal_font_size -= 1
            self.config_mgr.theme["font_size"] = self.terminal_font_size
            self.config_mgr.save_config(); self.apply_theme()

    def toggle_layout(self):
        cur = self.config_mgr.theme.get("layout", "classic")
        self.set_layout("dock" if cur == "classic" else "classic")

    def set_layout(self, layout):
        if layout not in ("classic", "dock") or layout == self.config_mgr.theme.get("layout"):
            return
        self.config_mgr.theme["layout"] = layout
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
        self.btn_edit_session.setToolTip(t("session_edit", lang))
        self.btn_manage_sessions.setToolTip(t("sessions_manage", lang))
        self.btn_conn.setToolTip(t("tip_connect", lang))
        self.btn_new_tab.setToolTip(t("add_tab", lang) + "  (Ctrl+Shift+T)")
        self.btn_more.setToolTip(t("more_options", lang))
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

    def _set_session_locked(self, locked):
        """Sessão salva carregada fica só-leitura: evita editar sem querer os
        dados guardados e ficar com o combo apontando p/ um nome que já não
        corresponde ao que está nos campos."""
        for w in (self.entry_host, self.entry_key):
            w.setReadOnly(locked)
        # A senha só fica bloqueada se foi SALVA na sessão; se não foi, o
        # usuário precisa poder digitá-la para entrar.
        saved = self.config_mgr.sessions.get(self.combo_sessions.currentText(), {})
        self.entry_pass.setReadOnly(locked and bool(saved.get("password")))
        self.btn_edit_session.setVisible(locked)
        lang = self.config_mgr.language
        self.combo_sessions.setToolTip(
            t("session_locked", lang) if locked else t("tip_sessions_ctx", lang))

    def unlock_session_fields(self):
        lang = self.config_mgr.language
        if QMessageBox.question(
                self, t("confirm", lang), t("session_edit_confirm", lang),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self._set_session_locked(False)

    def load_session(self, name):
        if name in self.config_mgr.sessions:
            data = self.config_mgr.sessions[name]
            self.entry_host.setText(data.get("host", ""))
            self.entry_key.setText(data.get("key", ""))
            pwd = decrypt_password(data.get("password", ""))
            self.entry_pass.setText(pwd)
            self._set_session_locked(True)
        else:
            self._set_session_locked(False)

    def save_current_session(self):
        lang = self.config_mgr.language
        current = self.combo_sessions.currentText()
        default_name = current if current in self.config_mgr.sessions else ""
        name, ok = QInputDialog.getText(self, t("save", lang), t("session_name", lang),
                                        text=default_name)
        name = name.strip() if name else name
        if ok and name:
            if name in self.config_mgr.sessions and QMessageBox.question(
                    self, t("save", lang), t("session_overwrite", lang).format(name=name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
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
            self.combo_sessions.blockSignals(True)
            self.combo_sessions.setCurrentText(name)
            self.combo_sessions.blockSignals(False)
            self._set_session_locked(True)

    def show_session_menu(self, pos):
        lang    = self.config_mgr.language
        current = self.combo_sessions.currentText()
        if current == t("saved_sessions", lang) or current not in self.config_mgr.sessions: return
        menu = QMenu(self)
        act_ren = QAction(qta.icon('fa5s.pen', color=self._menu_icon_color()),
                          f"{t('session_rename', lang)}: {current}", self)
        act_ren.triggered.connect(lambda: self.rename_session(current))
        act  = QAction(qta.icon('fa5s.trash', color='#dc3545'), f"{t('delete', lang)}: {current}", self)
        act.triggered.connect(lambda: self.delete_session(current))
        menu.addAction(act_ren)
        menu.addAction(act)
        menu.exec(self.combo_sessions.mapToGlobal(pos))

    def delete_session(self, name, confirm=True):
        if name not in self.config_mgr.sessions:
            return False
        lang = self.config_mgr.language
        if confirm and QMessageBox.question(
                self, t("delete", lang), t("session_delete_confirm", lang).format(name=name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return False
        del self.config_mgr.sessions[name]; self.config_mgr.save_config()
        idx = self.combo_sessions.findText(name)
        if idx >= 0: self.combo_sessions.removeItem(idx)
        self._set_session_locked(False)
        return True

    def rename_session(self, old):
        """Renomeia uma sessão salva mantendo host/chave/senha e a ordem."""
        sessions = self.config_mgr.sessions
        if old not in sessions:
            return None
        lang = self.config_mgr.language
        new, ok = QInputDialog.getText(self, t("session_rename", lang),
                                       t("session_name", lang), text=old)
        new = new.strip() if new else ""
        if not ok or not new or new == old:
            return None
        if new in sessions:
            QMessageBox.warning(self, t("session_rename", lang),
                                t("session_exists", lang).format(name=new))
            return None
        self.config_mgr.sessions = {(new if k == old else k): v for k, v in sessions.items()}
        self.config_mgr.save_config()
        idx = self.combo_sessions.findText(old)
        if idx >= 0:
            self.combo_sessions.setItemText(idx, new)   # dispara load_session(new): ok
        return new

    def manage_sessions(self):
        """Lista todas as sessões salvas com botões Renomear / Excluir."""
        from PyQt6.QtWidgets import QListWidget
        lang = self.config_mgr.language
        dlg = QDialog(self)
        dlg.setWindowTitle(t("sessions_manage", lang))
        dlg.resize(420, 340)
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        lay.addWidget(lst)

        def refill(select=None):
            lst.clear()
            for name, data in self.config_mgr.sessions.items():
                lst.addItem(f"{name}   \u2014   {data.get('host', '')}")
                lst.item(lst.count() - 1).setData(Qt.ItemDataRole.UserRole, name)
            for i in range(lst.count()):
                if lst.item(i).data(Qt.ItemDataRole.UserRole) == select:
                    lst.setCurrentRow(i)

        def current_name():
            it = lst.currentItem()
            return it.data(Qt.ItemDataRole.UserRole) if it else None

        def do_rename():
            old = current_name()
            if old:
                new = self.rename_session(old)
                if new:
                    refill(new)

        def do_delete():
            name = current_name()
            if name and self.delete_session(name):
                refill()

        row = QHBoxLayout()
        b_ren = QPushButton(t("session_rename", lang))
        b_ren.setIcon(qta.icon('fa5s.pen', color='white'))
        b_ren.clicked.connect(do_rename)
        b_del = QPushButton(t("delete", lang))
        b_del.setIcon(qta.icon('fa5s.trash', color='white'))
        b_del.clicked.connect(do_delete)
        row.addWidget(b_ren)
        row.addWidget(b_del)
        row.addStretch()
        lay.addLayout(row)
        lst.itemDoubleClicked.connect(lambda _i: do_rename())
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        refill(self.combo_sessions.currentText())
        dlg.exec()

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
