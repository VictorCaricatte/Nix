
"""
tab_panel.py — InteractiveTerminal + ConnectionTab (one SSH session panel).
"""

import os
import stat
import posixpath
import re
import secrets
import socket
import time
import tempfile
import threading
import codecs

import qtawesome as qta
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QFrame, QLabel, QLineEdit, QPushButton, QTreeWidget,
    QTreeWidgetItem, QPlainTextEdit, QProgressBar, QSizePolicy,
    QFileDialog, QInputDialog, QMessageBox, QMenu, QTabWidget,
    QTextEdit, QListWidget, QListWidgetItem, QScrollArea, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QEvent, QMimeData, QUrl
from PyQt6.QtGui import QAction, QDrag, QBrush, QColor

from config import ConfigManager
from ssh import SSHManager, SudoAuthError, SudoPasswordRequired, parse_host_string
import backend
from i18n import t
from widgets import ExplorerTree, gui_single_shot
from terminal import PyteTerminal
from dialogs import (
    RemoteEditorDialog, ImageViewerDialog, TextViewerDialog,
    ScreensManagerDialog, EnvManagerDialog, TableViewerDialog,
    AdvancedSearchDialog, LocalFileExplorerDialog,
)

class InteractiveTerminal(QPlainTextEdit):
    """LEGADO: substituído pelo PyteTerminal (terminal.py). Mantido apenas
    para compatibilidade de import (frontend.py)."""
    def __init__(self, parent_tab=None):
        super().__init__()
        self.parent_tab = parent_tab

    def keyPressEvent(self, event):
        if self.parent_tab and self.parent_tab.ssh_mgr.is_connected and self.parent_tab.ssh_mgr.shell:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.parent_tab.ssh_mgr.shell.send('\r')
            elif event.key() == Qt.Key.Key_Backspace:
                self.parent_tab.ssh_mgr.shell.send('\x08')
            elif event.key() == Qt.Key.Key_Tab:
                self.parent_tab.ssh_mgr.shell.send('\t')
            elif event.text():
                self.parent_tab.ssh_mgr.shell.send(event.text())
            return
        super().keyPressEvent(event)

class ConnectionTab(QWidget):
    sig_log            = pyqtSignal(str)
    sig_term_data      = pyqtSignal(str)   # bytes crus (decodificados) do shell -> terminal
    sig_pty_sync       = pyqtSignal()      # pós-conexão: sincroniza tamanho do PTY
    sig_monitor        = pyqtSignal(float, float, list, dict, list, dict)
    sig_os_info        = pyqtSignal(str)
    sig_explorer       = pyqtSignal()
    sig_screens        = pyqtSignal(object, str)
    sig_viewer         = pyqtSignal(str, str, str)
    sig_image_viewer   = pyqtSignal(str, bytes)
    sig_table_viewer   = pyqtSignal(str, str, object)
    sig_msg            = pyqtSignal(str, str, str)
    sig_env_list       = pyqtSignal(object, str, list)
    # Container de resultado tipado como `object` (não `list`): em conexão
    # cross-thread o PyQt COPIA argumentos tipados `list`, então o valor
    # preenchido no slot não voltava para a thread que espera (senha/decisão
    # eram perdidas). Com `object` a mesma referência é preservada.
    sig_ask_sudo       = pyqtSignal(object, object)
    sig_ask_hostkey    = pyqtSignal(str, str, str, object, object)
    # object (não int): int do PyQt é 32 bits e estourava em arquivos > 2 GB
    sig_transfer_progress = pyqtSignal(object, object, str)
    sig_transfer_done  = pyqtSignal(str)
    sig_transfer_failed = pyqtSignal(str, str)   # (tid, "failed" | "cancelled")
    sig_conn_lost      = pyqtSignal(str)         # "lost" | "closed"
    sig_conn_state     = pyqtSignal(object, str)  # (tab, connected|connecting|lost|idle)
    sig_latency        = pyqtSignal(int)
    sig_lost_banner    = pyqtSignal(str)
    sig_completion     = pyqtSignal(object)
    sig_screen_status  = pyqtSignal(bool, str)
    sig_shell_report   = pyqtSignal(object)      # (token, usuário, host, sty, pasta)

    sig_conn_update   = pyqtSignal(object, bool, str)
    sig_tab_title      = pyqtSignal(object, str)
    sig_notify         = pyqtSignal(str, str)
    sig_svc_rows       = pyqtSignal(list)
    sig_port_rows      = pyqtSignal(list)

    def __init__(self, config_mgr, parent_interface=None):
        super().__init__()
        self.config_mgr   = config_mgr
        self.parent_ui    = parent_interface
        self.ssh_mgr      = SSHManager()

        self.remote_path    = "/home"
        self.ctrl_a_pressed = False
        self.sudo_cache     = [None]
        self.command_history = list(config_mgr.command_history)
        self.history_index  = -1
        self.active_transfers = {}
        self._last_progress_time = {}
        self.auto_scroll    = True
        self.last_rx = self.last_tx = self.last_time = 0
        self._conn_host = ""
        self._conn_user = ""
        self._has_sudo  = False
        self._svc_all_rows  = []
        self._port_all_rows = []
        self._port_auto_timer = QTimer()
        self._port_auto_timer.timeout.connect(self._refresh_ports_tab)
        self._last_conn = None        # (host_str, senha, chave, x11) p/ reconectar
        self._resume_path = None      # diretório a restaurar após reconectar
        self._home_path = ""
        self._monitor_visible = True  # lido pela thread do monitor
        self._monitor_flag_timer = QTimer(self)
        self._monitor_flag_timer.timeout.connect(self._update_monitor_flag)
        self._monitor_flag_timer.start(1000)
        # Terminal sem atividade por um instante: hora de olhar a linha do cursor
        self._quiet_timer = QTimer(self)
        self._quiet_timer.setSingleShot(True)
        self._quiet_timer.setInterval(300)
        self._quiet_timer.timeout.connect(self._on_terminal_quiet)
        self._sync_lock = threading.Lock()
        self._sync_busy = False
        self._sudo_typed = []         # senha sendo digitada num prompt do sudo
        self._remote_uname = ""
        self._login_shell = ""
        self._reset_shell_tracking()

        self.sig_shell_report.connect(self._on_shell_report)
        self.sig_log.connect(self.log_local_slot)
        self.sig_term_data.connect(self._feed_terminal_slot)
        self.sig_pty_sync.connect(self._sync_pty_slot)
        self.sig_monitor.connect(self.update_monitor_ui_slot)
        self.sig_os_info.connect(self.update_os_info_slot)
        self.sig_explorer.connect(self.update_explorer_slot)
        self.sig_screens.connect(self.update_screens_ui_slot)
        self.sig_viewer.connect(self.open_file_viewer_slot)
        self.sig_image_viewer.connect(self.open_image_viewer_slot)
        self.sig_table_viewer.connect(self.open_table_viewer_slot)
        self.sig_msg.connect(self.show_msg_slot)
        self.sig_env_list.connect(self.update_env_list_slot)
        self.sig_ask_sudo.connect(self.ask_sudo_slot)
        self.sig_ask_hostkey.connect(self.ask_hostkey_slot)
        self.sig_transfer_progress.connect(self.update_progress_slot)
        self.sig_transfer_done.connect(self._remove_transfer_row)
        self.sig_transfer_failed.connect(self._mark_transfer_failed)
        self.sig_conn_lost.connect(self._on_connection_lost)
        self.sig_latency.connect(self._update_latency)
        self.sig_lost_banner.connect(self._show_lost_banner)
        self.sig_completion.connect(self._apply_completion)
        self.sig_screen_status.connect(self.set_screen_status)
        self.sig_svc_rows.connect(self._populate_services_tab)
        self.sig_port_rows.connect(self._populate_ports_tab)

        self._current_panel_layout = "classic"
        self._git_branch = ""
        self._build_panels()

    @staticmethod
    def _shrinkable_row(row_layout):
        """Põe uma barra de botões numa área com rolagem horizontal: sem isso a
        soma dos botões impõe largura mínima e o splitter não deixa o painel
        encolher em telas pequenas (agora o excedente rola em vez de travar)."""
        inner = QWidget()
        inner.setLayout(row_layout)
        row_layout.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(inner)
        area.setFixedHeight(inner.sizeHint().height() + 10)
        area.setStyleSheet("QScrollArea { background: transparent; } "
                           "QScrollArea > QWidget > QWidget { background: transparent; }")
        return area

    def _build_panels(self):
        lang = self.config_mgr.language
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        # Aviso de conexão perdida (escondido até acontecer)
        self.lost_banner = QFrame()
        self.lost_banner.setObjectName("LostBanner")
        self.lost_banner.setStyleSheet(
            "QFrame#LostBanner { background-color: #b3261e; border-radius: 8px; }"
            "QFrame#LostBanner QLabel { color: white; font-weight: bold; background: transparent; }"
            "QFrame#LostBanner QPushButton { background-color: white; color: #b3261e; }"
            "QFrame#LostBanner QPushButton:hover { background-color: #ffd9d6; color: #b3261e; }")
        lb = QHBoxLayout(self.lost_banner)
        lb.setContentsMargins(12, 6, 8, 6)
        self.lbl_lost = QLabel()
        self.btn_reconnect = QPushButton()
        self.btn_reconnect.setIcon(qta.icon('fa5s.sync-alt', color='#b3261e'))
        self.btn_reconnect.clicked.connect(self.reconnect)
        btn_lost_close = QPushButton()
        btn_lost_close.setIcon(qta.icon('fa5s.times', color='#b3261e'))
        btn_lost_close.setFixedWidth(34)
        btn_lost_close.clicked.connect(self.lost_banner.hide)
        lb.addWidget(self.lbl_lost, 1)
        lb.addWidget(self.btn_reconnect)
        lb.addWidget(btn_lost_close)
        self.lost_banner.hide()
        root_layout.addWidget(self.lost_banner)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.setChildrenCollapsible(False)
        h_splitter.setHandleWidth(4)
        self._h_splitter = h_splitter

        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setOpaqueResize(True)
        v_splitter.setChildrenCollapsible(False)
        v_splitter.setHandleWidth(4)
        self._v_splitter = v_splitter

        self.exp_inner = QFrame()
        self.exp_inner.setObjectName("Card")
        self.exp_inner.setMinimumHeight(150)
        self.exp_inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        exp_layout = QVBoxLayout(self.exp_inner)
        exp_layout.setContentsMargins(5, 5, 5, 5)

        path_layout = QHBoxLayout()
        self.btn_home_exp = QPushButton()
        self.btn_home_exp.setIcon(qta.icon('fa5s.home', color='white'))
        self.btn_home_exp.setFixedWidth(40)
        self.btn_home_exp.clicked.connect(self.go_home)

        self.btn_back_exp = QPushButton()
        self.btn_back_exp.setIcon(qta.icon('fa5s.arrow-left', color='white'))
        self.btn_back_exp.setFixedWidth(40)
        self.btn_back_exp.clicked.connect(self.navigate_back)

        self.current_path = QLabel("/home")

        self.btn_copy_path = QPushButton()
        self.btn_copy_path.setIcon(qta.icon('fa5s.copy', color='white'))
        self.btn_copy_path.setFixedWidth(40)
        self.btn_copy_path.clicked.connect(self.copy_current_path)

        self.btn_favorites = QPushButton()
        self.btn_favorites.setIcon(qta.icon('fa5s.star', color='white'))
        self.btn_favorites.setFixedWidth(40)
        self.btn_favorites.clicked.connect(self.show_favorites_menu)

        self.btn_refresh_exp = QPushButton()
        self.btn_refresh_exp.setIcon(qta.icon('fa5s.sync-alt', color='white'))
        self.btn_refresh_exp.setFixedWidth(40)
        self.btn_refresh_exp.clicked.connect(lambda: self.sig_explorer.emit())

        self.btn_up_dir = QPushButton()
        self.btn_up_dir.setIcon(qta.icon('fa5s.folder-plus', color='white'))
        self.btn_up_dir.clicked.connect(self.upload_dir_dialog)

        self.btn_up_file = QPushButton()
        self.btn_up_file.setIcon(qta.icon('fa5s.file-upload', color='white'))
        self.btn_up_file.clicked.connect(self.upload_file_dialog)

        self.btn_down = QPushButton()
        self.btn_down.setIcon(qta.icon('fa5s.file-download', color='white'))
        self.btn_down.clicked.connect(self.download_file_dialog)

        self.btn_devops = QPushButton()
        self.btn_devops.setIcon(qta.icon('fa5s.tools', color='white'))
        self.btn_devops.setFixedWidth(40)
        self.btn_devops.setToolTip(t("tip_devops", lang))
        self.btn_devops.clicked.connect(self._show_devops_menu)

        for w in [self.btn_home_exp, self.btn_back_exp, self.current_path,
                  self.btn_copy_path, self.btn_favorites, self.btn_refresh_exp,
                  self.btn_up_dir, self.btn_up_file, self.btn_down, self.btn_devops]:
            if isinstance(w, QLabel):
                path_layout.addWidget(w, 1)
            else:
                path_layout.addWidget(w)
        exp_layout.addWidget(self._shrinkable_row(path_layout))

        search_layout = QHBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.textChanged.connect(self.filter_explorer)
        self.btn_search = QPushButton()
        self.btn_search.setIcon(qta.icon('fa5s.search', color='white'))
        self.btn_search.clicked.connect(self.show_search_dialog)
        search_layout.addWidget(self.filter_input)
        search_layout.addWidget(self.btn_search)
        exp_layout.addLayout(search_layout)

        self.explorer = ExplorerTree()
        self.explorer.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.explorer.setColumnWidth(0, 250)
        self.explorer.itemDoubleClicked.connect(self.on_item_double_click)
        self.explorer.files_dropped.connect(self.on_drop_files)
        self.explorer.file_dragged_out.connect(self.handle_drag_out)
        self.explorer.remote_move.connect(self._handle_remote_move)
        self.explorer.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.explorer.customContextMenuRequested.connect(self.show_context_menu)
        exp_layout.addWidget(self.explorer)

        self.transfer_panel = QFrame()
        self.transfer_panel.setObjectName("Card")
        self.transfer_panel.setMaximumHeight(160)
        self.transfer_panel.setVisible(False)
        tp_layout = QVBoxLayout(self.transfer_panel)
        tp_layout.setContentsMargins(6, 4, 6, 4)
        tp_layout.setSpacing(3)

        tp_header = QHBoxLayout()
        self.lbl_transfers = QLabel(" Transfers (0)")
        self.lbl_transfers.setObjectName("Title")
        self.btn_clear_done = QPushButton()
        self.btn_clear_done.setIcon(qta.icon('fa5s.times', color='white'))
        self.btn_clear_done.setFixedSize(22, 22)
        self.btn_clear_done.setToolTip("Fechar painel de transferências")
        self.btn_clear_done.clicked.connect(self._force_hide_transfer_panel)
        tp_header.addWidget(self.lbl_transfers)
        tp_header.addStretch()
        tp_header.addWidget(self.btn_clear_done)
        tp_layout.addLayout(tp_header)

        self._tp_scroll = QScrollArea()
        self._tp_scroll.setObjectName("TransferScroll")
        self._tp_scroll.setWidgetResizable(True)
        self._tp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tp_list_widget = QWidget()
        self._tp_list_widget.setObjectName("TransferList")
        self._tp_list_layout = QVBoxLayout(self._tp_list_widget)
        self._tp_list_layout.setSpacing(2)
        self._tp_list_layout.setContentsMargins(0, 0, 0, 0)
        self._tp_list_layout.addStretch()
        self._tp_scroll.setWidget(self._tp_list_widget)
        tp_layout.addWidget(self._tp_scroll)
        exp_layout.addWidget(self.transfer_panel)

        self.term_inner = QFrame()
        self.term_inner.setObjectName("Card")
        self.term_inner.setMinimumHeight(150)
        self.term_inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        term_layout = QVBoxLayout(self.term_inner)
        term_layout.setContentsMargins(5, 5, 5, 5)

        top_term = QHBoxLayout()
        self.lbl_term = QLabel()
        self.lbl_term.setObjectName("Title")

        self.btn_font_down = QPushButton()
        self.btn_font_down.setIcon(qta.icon('fa5s.search-minus', color='white'))
        self.btn_font_down.clicked.connect(lambda: self.parent_ui.decrease_font() if self.parent_ui else None)

        self.btn_font_up = QPushButton()
        self.btn_font_up.setIcon(qta.icon('fa5s.search-plus', color='white'))
        self.btn_font_up.clicked.connect(lambda: self.parent_ui.increase_font() if self.parent_ui else None)

        self.btn_auto_scroll = QPushButton()
        self.btn_auto_scroll.setIcon(qta.icon('fa5s.arrow-down', color='white'))
        self.btn_auto_scroll.setCheckable(True)
        self.btn_auto_scroll.setChecked(True)
        self.btn_auto_scroll.toggled.connect(self.toggle_auto_scroll)

        self.lbl_screen_status = QLabel()
        self.lbl_screen_status.setObjectName("Status")
        self.lbl_screen_status.setStyleSheet("color: #28a745;")
        self.lbl_screen_status.setMaximumWidth(260)

        for w in [self.lbl_term, self.btn_font_down, self.btn_font_up, self.btn_auto_scroll]:
            top_term.addWidget(w)
        top_term.addStretch()
        self.btn_toggle_monitor = QPushButton()
        self.btn_toggle_monitor.setIcon(qta.icon('fa5s.chart-line', color='white'))
        self.btn_toggle_monitor.setCheckable(True)
        self.btn_toggle_monitor.setToolTip("Monitor  (Ctrl+Shift+M)")
        self.btn_toggle_monitor.toggled.connect(self.set_monitor_hidden)
        top_term.addWidget(self.btn_toggle_monitor)
        self.lbl_latency = QLabel()
        self.lbl_latency.setVisible(False)
        top_term.addWidget(self.lbl_latency)
        top_term.addWidget(self.lbl_screen_status)
        term_layout.addWidget(self._shrinkable_row(top_term))

        self.output = PyteTerminal(self)
        term_layout.addWidget(self.output)
        self.refresh_terminal_theme()

        input_layout = QHBoxLayout()
        lbl_prompt = QLabel("❯")
        lbl_prompt.setObjectName("PromptLabel")
        self.cmd_input = QLineEdit()
        self.cmd_input.returnPressed.connect(self.send_command)
        self.cmd_input.installEventFilter(self)

        self.btn_clear = QPushButton()
        self.btn_clear.setIcon(qta.icon('fa5s.eraser', color='white'))
        self.btn_clear.clicked.connect(self.clear_terminal)

        self.btn_force = QPushButton()
        self.btn_force.setIcon(qta.icon('fa5s.unlink', color='white'))
        self.btn_force.clicked.connect(lambda: self.set_screen_status(False))

        input_layout.addWidget(lbl_prompt)
        input_layout.addWidget(self.cmd_input, 1)
        input_layout.addWidget(self.btn_clear)
        input_layout.addWidget(self.btn_force)
        term_layout.addLayout(input_layout)

        self.sys_inner = QFrame()
        self.sys_inner.setObjectName("Card")
        self.sys_inner.setMinimumWidth(220)
        sys_layout = QVBoxLayout(self.sys_inner)
        sys_layout.setContentsMargins(5, 5, 5, 5)

        self.sys_tabs = QTabWidget()

        self.tab_monitor = QWidget()
        tab_mon_layout = QVBoxLayout(self.tab_monitor)
        stats_layout = QHBoxLayout()

        cpu_box = QVBoxLayout()
        self.lbl_cpu_t = QLabel(t("cpu", lang))
        self.lbl_cpu_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cpu_label = QLabel("0%")
        self.cpu_label.setObjectName("MonitorValue")
        self.cpu_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cpu_bar = QProgressBar()
        self.cpu_bar.setRange(0, 100); self.cpu_bar.setFixedHeight(8); self.cpu_bar.setTextVisible(False)
        for w in [self.lbl_cpu_t, self.cpu_label, self.cpu_bar]: cpu_box.addWidget(w)

        ram_box = QVBoxLayout()
        self.lbl_ram_t = QLabel(t("ram", lang))
        self.lbl_ram_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ram_label = QLabel("0%")
        self.ram_label.setObjectName("MonitorValue")
        self.ram_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ram_bar = QProgressBar()
        self.ram_bar.setRange(0, 100); self.ram_bar.setFixedHeight(8); self.ram_bar.setTextVisible(False)
        self.lbl_mem_details = QLabel(f"{t('mem_free', lang)}: - / {t('mem_total', lang)}: -")
        self.lbl_mem_details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_mem_details.setStyleSheet("font-size: 11px; color: #888;")
        for w in [self.lbl_ram_t, self.ram_label, self.ram_bar, self.lbl_mem_details]: ram_box.addWidget(w)

        net_box = QVBoxLayout()
        self.lbl_net_t = QLabel(t("network_active", lang))
        self.lbl_net_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_net_rx = QLabel("▼ 0 B/s")
        self.lbl_net_tx = QLabel("▲ 0 B/s")
        for lbl in [self.lbl_net_rx, self.lbl_net_tx]:
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_net_rx.setStyleSheet("color: #28a745; font-weight: bold; font-size: 14px;")
        self.lbl_net_tx.setStyleSheet("color: #17a2b8; font-weight: bold; font-size: 14px;")
        for w in [self.lbl_net_t, self.lbl_net_rx, self.lbl_net_tx]: net_box.addWidget(w)

        stats_layout.addLayout(cpu_box)
        stats_layout.addLayout(ram_box)
        stats_layout.addLayout(net_box)
        tab_mon_layout.addLayout(stats_layout)

        div = QFrame(); div.setFrameShape(QFrame.Shape.HLine); div.setObjectName("MonitorDivider")
        tab_mon_layout.addWidget(div)

        bottom_mon = QHBoxLayout()
        users_vbox = QVBoxLayout()
        self.lbl_users = QLabel(t("active_users", lang)); self.lbl_users.setObjectName("Title")
        self.users_list = QListWidget(); self.users_list.setMinimumWidth(180)
        self.users_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.users_list.customContextMenuRequested.connect(self.show_users_context_menu)
        users_vbox.addWidget(self.lbl_users); users_vbox.addWidget(self.users_list)

        procs_vbox = QVBoxLayout()
        self.lbl_procs = QLabel(t("processes", lang)); self.lbl_procs.setObjectName("Title")
        self.proc_tree = QTreeWidget()
        self.proc_tree.setHeaderLabels([t("pid", lang), t("user", lang), "CPU%", "Mem%", t("process", lang)])
        for col, w in enumerate([50, 70, 50, 50]): self.proc_tree.setColumnWidth(col, w)
        self.proc_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.proc_tree.customContextMenuRequested.connect(self.show_proc_context_menu)
        procs_vbox.addWidget(self.lbl_procs); procs_vbox.addWidget(self.proc_tree)

        bottom_mon.addLayout(users_vbox); bottom_mon.addLayout(procs_vbox)
        tab_mon_layout.addLayout(bottom_mon)

        self.tab_os = QWidget()
        tab_os_layout = QVBoxLayout(self.tab_os)
        self.os_info_text = QTextEdit()
        self.os_info_text.setObjectName("OSInfoText")
        self.os_info_text.setReadOnly(True)
        tab_os_layout.addWidget(self.os_info_text)

        self.tab_snippets = QWidget()
        tab_snip_layout = QVBoxLayout(self.tab_snippets)
        self.list_snippets = QListWidget()
        self.list_snippets.itemDoubleClicked.connect(self.run_selected_snippet)
        tab_snip_layout.addWidget(self.list_snippets)

        snip_ctrl = QHBoxLayout()
        self.input_snip_name = QLineEdit(); self.input_snip_name.setPlaceholderText(t("snippet_name", lang))
        self.input_snip_cmd  = QLineEdit(); self.input_snip_cmd.setPlaceholderText(t("snippet_cmd", lang))
        snip_ctrl.addWidget(self.input_snip_name); snip_ctrl.addWidget(self.input_snip_cmd)

        snip_btns = QHBoxLayout()
        self.btn_add_snip = QPushButton(t("add_snippet", lang))
        self.btn_add_snip.setStyleSheet("background-color:#28a745;color:white;")
        self.btn_add_snip.clicked.connect(self.add_snippet)
        self.btn_del_snip = QPushButton(t("del_snippet", lang))
        self.btn_del_snip.setStyleSheet("background-color:#dc3545;color:white;")
        self.btn_del_snip.clicked.connect(self.del_snippet)
        self.btn_run_snip = QPushButton(t("run_snippet", lang))
        self.btn_run_snip.clicked.connect(self.run_selected_snippet)
        for b in [self.btn_add_snip, self.btn_del_snip, self.btn_run_snip]: snip_btns.addWidget(b)

        tab_snip_layout.addLayout(snip_ctrl)
        tab_snip_layout.addLayout(snip_btns)
        self.load_snippets_ui()

        self.tab_services = QWidget()
        ts_layout = QVBoxLayout(self.tab_services)
        ts_layout.setContentsMargins(4, 4, 4, 4); ts_layout.setSpacing(4)
        ts_hdr = QHBoxLayout()
        self.inp_svc_filter = QLineEdit()
        self.inp_svc_filter.setPlaceholderText("Filter services…")
        self.inp_svc_filter.textChanged.connect(self._filter_services_tab)
        self.btn_load_svcs = QPushButton()
        self.btn_load_svcs.setIcon(qta.icon('fa5s.sync', color='white'))
        self.btn_load_svcs.setFixedSize(28, 28)
        self.btn_load_svcs.setToolTip(t("refresh_services", lang))
        self.btn_load_svcs.clicked.connect(self._load_quick_services)
        self.lbl_svc_summary = QLabel("—")
        self.lbl_svc_summary.setStyleSheet("font-size: 11px; color: #888;")
        ts_hdr.addWidget(self.inp_svc_filter, 1)
        ts_hdr.addWidget(self.btn_load_svcs)
        ts_hdr.addWidget(self.lbl_svc_summary)
        ts_layout.addLayout(ts_hdr)
        self.tbl_services = QTableWidget(0, 4)
        self.tbl_services.setHorizontalHeaderLabels(["Service", "Sub-state", "Active", "Enabled"])
        self.tbl_services.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 4): self.tbl_services.setColumnWidth(i, 80)
        self.tbl_services.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_services.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_services.setAlternatingRowColors(True)
        self.tbl_services.verticalHeader().hide()
        self.tbl_services.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tbl_services.customContextMenuRequested.connect(self._show_service_ctx_menu)
        ts_layout.addWidget(self.tbl_services)
        self.btn_full_svc = QPushButton(t("open_service_mgr", lang))
        self.btn_full_svc.setIcon(qta.icon('fa5s.server', color='white'))
        self.btn_full_svc.clicked.connect(self._open_service_manager)
        ts_layout.addWidget(self.btn_full_svc)

        self.tab_ports = QWidget()
        tp_layout = QVBoxLayout(self.tab_ports)
        tp_layout.setContentsMargins(4, 4, 4, 4); tp_layout.setSpacing(4)
        tp_hdr = QHBoxLayout()
        self.inp_port_filter = QLineEdit()
        self.inp_port_filter.setPlaceholderText("Filter ports…")
        self.inp_port_filter.textChanged.connect(self._filter_ports_tab)
        self.combo_port_proto = QComboBox()
        self.combo_port_proto.addItems(["All", "TCP", "UDP"])
        self.combo_port_proto.setFixedWidth(70)
        self.combo_port_proto.currentTextChanged.connect(self._filter_ports_tab)
        self.btn_ref_ports = QPushButton()
        self.btn_ref_ports.setIcon(qta.icon('fa5s.sync', color='white'))
        self.btn_ref_ports.setFixedSize(28, 28)
        self.btn_ref_ports.setToolTip(t("refresh_ports", lang))
        self.btn_ref_ports.clicked.connect(self._refresh_ports_tab)
        self.btn_port_auto = QPushButton("Auto OFF")
        self.btn_port_auto.setCheckable(True)
        self.btn_port_auto.setFixedWidth(70)
        self.btn_port_auto.clicked.connect(self._toggle_port_auto_refresh)
        self.lbl_ports_summary = QLabel("—")
        self.lbl_ports_summary.setStyleSheet("font-size: 11px; color: #888;")
        tp_hdr.addWidget(self.inp_port_filter, 1)
        tp_hdr.addWidget(self.combo_port_proto)
        tp_hdr.addWidget(self.btn_ref_ports)
        tp_hdr.addWidget(self.btn_port_auto)
        tp_hdr.addWidget(self.lbl_ports_summary)
        tp_layout.addLayout(tp_hdr)
        self.tbl_ports = QTableWidget(0, 6)
        self.tbl_ports.setHorizontalHeaderLabels(["Proto", "Local Address", "Port", "State", "PID", "Process"])
        self.tbl_ports.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl_ports.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_ports.setAlternatingRowColors(True)
        self.tbl_ports.verticalHeader().hide()
        tp_layout.addWidget(self.tbl_ports)
        self.btn_full_ports = QPushButton(t("open_port_monitor", lang))
        self.btn_full_ports.setIcon(qta.icon('fa5s.project-diagram', color='white'))
        self.btn_full_ports.clicked.connect(self._open_port_monitor)
        tp_layout.addWidget(self.btn_full_ports)

        self.sys_tabs.addTab(self.tab_monitor,  t("sys_mon", lang))
        self.sys_tabs.addTab(self.tab_os,       t("os_info", lang))
        self.sys_tabs.addTab(self.tab_snippets, t("snippets_tab", lang))
        self.sys_tabs.addTab(self.tab_services, t("services_tab", lang))
        self.sys_tabs.addTab(self.tab_ports,    t("ports_tab", lang))
        self.sys_tabs.currentChanged.connect(self._on_sys_tab_changed)
        sys_layout.addWidget(self.sys_tabs)

        v_splitter.addWidget(self.exp_inner)
        v_splitter.addWidget(self.term_inner)
        h_splitter.addWidget(v_splitter)
        h_splitter.addWidget(self.sys_inner)
        root_layout.addWidget(h_splitter)
        QTimer.singleShot(50, self._set_initial_sizes)

    def _set_initial_sizes(self):
        self._set_sizes_for_layout(self._current_panel_layout)

    def _set_sizes_for_layout(self, layout_type):
        w = max(self._h_splitter.width(), 900)
        h = max(self._v_splitter.height(), 500)
        if layout_type == "dock":

            self._h_splitter.setSizes([int(w * 0.25), int(w * 0.50), int(w * 0.25)])
        else:
            self._h_splitter.setSizes([int(w * 0.73), int(w * 0.27)])
            self._v_splitter.setSizes([int(h * 0.57), int(h * 0.43)])

    def apply_panel_layout(self, layout_type):
        """Rearrange the three inner panels for classic or dock layout."""
        if self._current_panel_layout == layout_type:
            return

        if layout_type == "dock":

            self._h_splitter.insertWidget(0, self.exp_inner)
            self._h_splitter.insertWidget(1, self.term_inner)

            self._v_splitter.setParent(None)
        else:

            self._v_splitter.addWidget(self.exp_inner)
            self._v_splitter.addWidget(self.term_inner)
            self._h_splitter.insertWidget(0, self._v_splitter)
            self._v_splitter.show()

        self._current_panel_layout = layout_type
        QTimer.singleShot(50, lambda: self._set_sizes_for_layout(layout_type))

    def _show_devops_menu(self):
        if not self.ssh_mgr.is_connected:
            self.sig_msg.emit("warn", "DevOps", "Connect to a server first.")
            return
        from devops_dialogs import (
            TunnelManagerDialog, CronEditorDialog, ServiceManagerDialog,
            LogViewerDialog, SSHKeyManagerDialog, FileSyncDialog, PortMonitorDialog,
            PackageManagerDialog, UserManagerDialog, FirewallDialog,
        )
        lang = self.config_mgr.language
        ic = self._menu_icon_color()
        menu = QMenu(self)
        for label, icon_name, slot in [
            ("SSH Tunnels",    'fa5s.network-wired',   lambda: TunnelManagerDialog(self, self.ssh_mgr, lang).exec()),
            ("Cron Editor",    'fa5s.clock',            lambda: CronEditorDialog(self, self.ssh_mgr, lang).exec()),
            ("Log Viewer",     'fa5s.file-alt',         lambda: LogViewerDialog(self, self.ssh_mgr, lang).exec()),
            ("File Sync",      'fa5s.exchange-alt',     lambda: FileSyncDialog(self, self.ssh_mgr, self.remote_path, lang).exec()),
            ("SSH Key Mgr",    'fa5s.key',              lambda: SSHKeyManagerDialog(self, self.ssh_mgr, lang).exec()),
            ("Service Mgr",    'fa5s.server',           self._open_service_manager),
            ("Containers",     'fa5b.docker',           self._open_container_manager),
            ("Port Monitor",   'fa5s.project-diagram',  self._open_port_monitor),
            ("Package Mgr",    'fa5s.box-open',         lambda: PackageManagerDialog(self, self.ssh_mgr, lang).exec()),
            ("Users & Groups", 'fa5s.users-cog',        lambda: UserManagerDialog(self, self.ssh_mgr, lang, self._has_sudo or self._conn_user == "root").exec()),
            ("Firewall",       'fa5s.shield-alt',        lambda: FirewallDialog(self, self.ssh_mgr, lang).exec()),
        ]:
            act = QAction(qta.icon(icon_name, color=ic), label, self)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.exec(self.btn_devops.mapToGlobal(self.btn_devops.rect().bottomLeft()))

    def _menu_icon_color(self):
        if self.config_mgr.theme.get('mode', 'dark') == 'dark':
            return 'white'
        return self.config_mgr.theme.get('accent', '#bd93f9')

    def _open_service_manager(self):
        if not self.ssh_mgr.is_connected:
            self.sig_msg.emit("warn", "DevOps", "Not connected."); return
        from devops_dialogs import ServiceManagerDialog
        ServiceManagerDialog(self, self.ssh_mgr, self.config_mgr.language).exec()

    def _open_port_monitor(self):
        if not self.ssh_mgr.is_connected:
            self.sig_msg.emit("warn", "DevOps", "Not connected."); return
        from devops_dialogs import PortMonitorDialog
        PortMonitorDialog(self, self.ssh_mgr, self.config_mgr.language).exec()

    def _open_container_manager(self):
        if not self.ssh_mgr.is_connected:
            self.sig_msg.emit("warn", "DevOps", "Not connected."); return
        from devops_dialogs import ContainerManagerDialog
        ContainerManagerDialog(self, self.ssh_mgr, self.config_mgr.language).exec()

    def _on_sys_tab_changed(self, idx):
        self._update_monitor_flag()
        if idx == 3:
            self._load_quick_services()
        elif idx == 4:
            self._refresh_ports_tab()

    def _load_quick_services(self):
        if not self.ssh_mgr.is_connected: return
        self.lbl_svc_summary.setText("Loading…")
        threading.Thread(target=self._fetch_quick_services, daemon=True).start()

    def _fetch_quick_services(self):
        try:
            _, o1, _ = self.ssh_mgr.execute(
                "systemctl list-units --type=service --all --no-pager --no-legend 2>/dev/null | head -300", timeout=15)
            _, o2, _ = self.ssh_mgr.execute(
                "systemctl list-unit-files --type=service --no-pager --no-legend 2>/dev/null | head -300", timeout=15)
            enabled_map = {}
            for ln in o2.read().decode().splitlines():
                parts = ln.split()
                if len(parts) >= 2:
                    enabled_map[parts[0]] = parts[1]
            rows = []
            for ln in o1.read().decode().splitlines():
                parts = ln.split(None, 4)
                if len(parts) >= 3:
                    name   = parts[0]
                    active = parts[2] if len(parts) > 2 else ""
                    sub    = parts[3] if len(parts) > 3 else ""
                    en     = enabled_map.get(name, "?")
                    rows.append((name, sub, active, en))
            self._svc_all_rows = rows
            self.sig_svc_rows.emit(rows)
        except Exception as e:
            self.sig_msg.emit("error", "Services", str(e))

    def _populate_services_tab(self, rows):
        self._svc_all_rows = rows
        self._filter_services_tab()
        running = sum(1 for _, sub, _, _ in rows if sub == "running")
        self.lbl_svc_summary.setText(f"{running} running / {len(rows)} total")

    def _filter_services_tab(self):
        text = self.inp_svc_filter.text().lower()
        self.tbl_services.setRowCount(0)
        for name, sub, active, en in self._svc_all_rows:
            if text and text not in name.lower(): continue
            r = self.tbl_services.rowCount()
            self.tbl_services.insertRow(r)
            self.tbl_services.setItem(r, 0, QTableWidgetItem(name))
            si = QTableWidgetItem(sub)
            if sub == "running":   si.setForeground(QBrush(QColor("#28a745")))
            elif sub == "failed":  si.setForeground(QBrush(QColor("#dc3545")))
            self.tbl_services.setItem(r, 1, si)
            self.tbl_services.setItem(r, 2, QTableWidgetItem(active))
            ei = QTableWidgetItem(en)
            if en == "enabled":    ei.setForeground(QBrush(QColor("#28a745")))
            elif en == "disabled": ei.setForeground(QBrush(QColor("#dc3545")))
            self.tbl_services.setItem(r, 3, ei)

    def _show_service_ctx_menu(self, pos):
        row = self.tbl_services.currentRow()
        if row < 0: return
        svc = self.tbl_services.item(row, 0)
        if not svc: return
        svc_name = svc.text()
        ic = self._menu_icon_color()
        menu = QMenu(self)
        for label, icon_name, action in [
            ("Start",   'fa5s.play',       "start"),
            ("Stop",    'fa5s.stop',       "stop"),
            ("Restart", 'fa5s.redo',       "restart"),
            ("Enable",  'fa5s.toggle-on',  "enable"),
            ("Disable", 'fa5s.toggle-off', "disable"),
        ]:
            act = QAction(qta.icon(icon_name, color=ic), label, self)
            act.triggered.connect(lambda _, a=action, s=svc_name: self._service_tab_action(a, s))
            menu.addAction(act)
        menu.exec(self.tbl_services.viewport().mapToGlobal(pos))

    def _service_tab_action(self, action, svc):
        if not self.ssh_mgr.is_connected: return
        def run():
            try:
                _, out, err = self.ssh_mgr.execute(
                    f"sudo -n systemctl {backend.sh_quote(action)} {backend.sh_quote(svc)} 2>&1")
                result = (out.read() + err.read()).decode('utf-8', errors='replace').strip()
                msg = f"systemctl {action} {svc}" + (f"\n{result}" if result else " — OK")
                self.sig_log.emit(f"[DevOps] {msg}")
                self._fetch_quick_services()
            except Exception as e:
                self.sig_msg.emit("error", "Service", str(e))
        threading.Thread(target=run, daemon=True).start()

    def _refresh_ports_tab(self):
        if not self.ssh_mgr.is_connected: return
        self.lbl_ports_summary.setText("Loading…")
        threading.Thread(target=self._fetch_ports_quick, daemon=True).start()

    def _fetch_ports_quick(self):
        try:
            _, out, _ = self.ssh_mgr.execute("ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null", timeout=15)
            rows = []
            for line in out.read().decode().splitlines()[1:]:
                parts = line.split()
                if len(parts) < 5: continue
                proto = parts[0]
                local = parts[4] if len(parts) > 4 else ""
                state = parts[1] if len(parts) > 1 else ""
                addr, port = (local.rsplit(':', 1) if ':' in local else (local, ""))
                pid = process = ""
                for p in parts:
                    if 'pid=' in p: pid = p.split('pid=')[1].split(',')[0].rstrip(')')
                    if '"' in p: process = p.split('"')[1] if p.count('"') >= 2 else p.lstrip('"')
                rows.append((proto, addr, port, state, pid, process))
            self._port_all_rows = rows
            self.sig_port_rows.emit(rows)
        except Exception as e:
            self.sig_msg.emit("error", "Ports", str(e))

    def _populate_ports_tab(self, rows):
        self._port_all_rows = rows
        self._filter_ports_tab()

    def _filter_ports_tab(self):
        text = self.inp_port_filter.text().lower()
        proto_f = self.combo_port_proto.currentText().lower()
        self.tbl_ports.setRowCount(0)
        for proto, addr, port, state, pid, process in self._port_all_rows:
            if proto_f != "all" and proto_f not in proto.lower(): continue
            if text and text not in f"{addr} {port} {process} {pid}".lower(): continue
            r = self.tbl_ports.rowCount()
            self.tbl_ports.insertRow(r)
            for col, val in enumerate([proto, addr, port, state, pid, process]):
                item = QTableWidgetItem(val)
                if col == 3 and "listen" in state.lower():
                    item.setForeground(QBrush(QColor("#50fa7b")))
                self.tbl_ports.setItem(r, col, item)
        self.lbl_ports_summary.setText(f"{self.tbl_ports.rowCount()} entries")

    def _toggle_port_auto_refresh(self, checked):
        if checked:
            self._port_auto_timer.start(5000)
            self.btn_port_auto.setText("Auto ON")
        else:
            self._port_auto_timer.stop()
            self.btn_port_auto.setText("Auto OFF")

    def _fetch_git_status(self):
        """Runs async; emits via QTimer to update explorer colors and branch label."""
        try:
            rp = backend.sh_quote(self.remote_path)
            cmd = (f'git -C {rp} rev-parse --abbrev-ref HEAD 2>/dev/null'
                   f' && echo "==STATUS=="'
                   f' && git -C {rp} status --porcelain 2>/dev/null')
            _, out, _ = self.ssh_mgr.execute(cmd, timeout=15)
            text = out.read().decode()
            if "==STATUS==" not in text:
                return
            head, tail = text.split("==STATUS==", 1)
            branch = head.strip()
            git_map = {}
            for line in tail.strip().splitlines():
                if len(line) >= 3:
                    xy   = line[:2]
                    name = line[3:].strip().split(" -> ")[-1]
                    git_map[name.split("/")[0]] = xy
            gui_single_shot(0, lambda b=branch, m=git_map: self._apply_git_colors(b, m))
        except Exception:
            pass

    def _apply_git_colors(self, branch, git_map):
        if branch and branch != "HEAD":
            self._git_branch = branch
            cur = self.current_path.text().split("  ⎇")[0]
            self.current_path.setText(f"{cur}  ⎇ {branch}")
        else:
            self._git_branch = ""
        for i in range(self.explorer.topLevelItemCount()):
            item = self.explorer.topLevelItem(i)
            xy = git_map.get(item.text(0), "")
            if not xy:
                continue
            if "?" in xy:
                item.setForeground(0, QColor("#ff5555"))
            elif xy[0] in ('M', 'A', 'R', 'C') and xy[0] != ' ':
                item.setForeground(0, QColor("#50fa7b"))
            elif len(xy) > 1 and xy[1] == 'M':
                item.setForeground(0, QColor("#ffb86c"))
            elif 'D' in xy:
                item.setForeground(0, QColor("#ff5555"))

    def _run_git_cmd(self, cmd):
        if not self.ssh_mgr.is_connected: return
        self.cmd_input.setText(f'cd {backend.sh_quote(self.remote_path)} && {cmd}')
        self.send_command()

    def update_texts(self, lang):
        self.filter_input.setPlaceholderText(t("filter_placeholder", lang))
        self.btn_home_exp.setToolTip(t("tip_home", lang))
        self.btn_back_exp.setToolTip(t("tip_back", lang))
        self.btn_copy_path.setToolTip(t("tip_copy_path", lang))
        self.btn_favorites.setToolTip(t("tip_favorites", lang))
        self.btn_refresh_exp.setToolTip(t("tip_refresh", lang))
        self.btn_up_dir.setToolTip(t("tip_up_dir", lang));  self.btn_up_dir.setText(t("up_dir", lang))
        self.btn_up_file.setToolTip(t("tip_up_file", lang)); self.btn_up_file.setText(t("up_file", lang))
        self.btn_down.setToolTip(t("tip_download", lang));   self.btn_down.setText(t("down_file", lang))
        self.btn_search.setToolTip(t("tip_deep_search", lang))
        self.lbl_term.setText(f" {t('terminal', lang)}")
        self.btn_font_up.setToolTip(t("tip_font_up", lang) + "  (Ctrl +)")
        self.btn_font_down.setToolTip(t("tip_font_down", lang) + "  (Ctrl -)")
        self.btn_auto_scroll.setToolTip(t("tip_scroll_on", lang) if self.auto_scroll else t("tip_scroll_off", lang))
        self.btn_clear.setText(t("clear_local", lang));  self.btn_clear.setToolTip(t("tip_clear", lang))
        self.btn_force.setText(t("force_main", lang));   self.btn_force.setToolTip(t("tip_force_main", lang))
        if "IN SCREEN" not in self.lbl_screen_status.text() and "TELA" not in self.lbl_screen_status.text() and "PANTALLA" not in self.lbl_screen_status.text():
            self.lbl_screen_status.setText(f" {t('state_main', lang)}")
        self.lbl_cpu_t.setText(t("cpu", lang))
        self.lbl_ram_t.setText(t("ram", lang))
        self.lbl_net_t.setText(t("network_active", lang))
        self.lbl_users.setText(t("active_users", lang))
        self.lbl_procs.setText(t("processes", lang))
        self.proc_tree.setHeaderLabels([t("pid", lang), t("user", lang), "CPU%", "Mem%", t("process", lang)])
        self.input_snip_name.setPlaceholderText(t("snippet_name", lang))
        self.input_snip_cmd.setPlaceholderText(t("snippet_cmd", lang))
        self.btn_add_snip.setText(t("add_snippet", lang))
        self.btn_del_snip.setText(t("del_snippet", lang))
        self.btn_run_snip.setText(t("run_snippet", lang))
        self.explorer.setHeaderLabels([t("name", lang), t("size", lang), t("type", lang), t("permissions", lang), "Progress"])
        self.explorer.setColumnWidth(4, 120)
        for i in range(self.explorer.topLevelItemCount()):   # re-traduz a coluna "Tipo"
            it = self.explorer.topLevelItem(i)
            it.setText(2, t("directory" if self._item_kind(it) == "Directory" else "file", lang))
        self.sys_tabs.setTabText(0, t("sys_mon", lang))
        self.sys_tabs.setTabText(1, t("os_info", lang))
        if self.sys_tabs.count() > 2:
            self.sys_tabs.setTabText(2, t("snippets_tab", lang))
        if self.sys_tabs.count() > 3:
            self.sys_tabs.setTabText(3, t("services_tab", lang))
        if self.sys_tabs.count() > 4:
            self.sys_tabs.setTabText(4, t("ports_tab", lang))
        self.btn_devops.setToolTip(t("tip_devops", lang))
        mem_det_text = self.lbl_mem_details.text()
        if ": -" in mem_det_text:
            self.lbl_mem_details.setText(f"{t('mem_free', lang)}: - / {t('mem_total', lang)}: -")

    def handle_connection(self, host_str, password, key, x11):
        lang = self.config_mgr.language
        if not self.ssh_mgr.is_connected:
            self._last_conn = (host_str, password, key, x11)
            self.lost_banner.hide()
            self.sig_conn_update.emit(self, False, t("connecting", lang))
            self.sig_conn_state.emit(self, "connecting")
            threading.Thread(target=self.connect_ssh,
                             args=(host_str, password, key, x11), daemon=True).start()
        else:
            self._resume_path = None
            self.ssh_mgr.disconnect()
            self._reset_after_disconnect()
            self.sig_conn_update.emit(self, True, t("connect", lang))
            self.sig_tab_title.emit(self, t("new_tab", lang))
            self.sig_conn_state.emit(self, "idle")

    def _reset_after_disconnect(self):
        self._conn_host = ""
        self._conn_user = ""
        self._has_sudo  = False
        # Não deixa a senha do sudo na memória após desconectar — senão ela
        # persistiria e seria reusada ao conectar em OUTRO servidor.
        self.sudo_cache[0] = None
        self._sudo_typed.clear()
        self._quiet_timer.stop()
        self._reset_shell_tracking()
        self.cmd_input.setEchoMode(QLineEdit.EchoMode.Normal)
        self.lbl_latency.setVisible(False)

    # ------------------------------------------------------------------ #
    #  Conexão perdida / reconexão                                        #
    # ------------------------------------------------------------------ #
    def _on_connection_lost(self, reason):
        """Slot (thread da GUI): a conexão caiu sem o usuário pedir. Avisa no
        banner, no terminal, na aba e na bandeja do sistema."""
        if not self.ssh_mgr.is_connected:
            return  # já tratado (várias threads podem detectar a mesma queda)
        lang = self.config_mgr.language
        host = f"{self._conn_user}@{self._conn_host}"
        self._resume_path = self.remote_path
        try:
            self.ssh_mgr.disconnect()
        except Exception:
            pass
        self._reset_after_disconnect()
        self._conn_host = host.split("@", 1)[1]
        self._conn_user = host.split("@", 1)[0]
        key = "conn_closed_msg" if reason == "closed" else "conn_lost_msg"
        msg = t(key, lang).format(host=host)
        self._show_lost_banner(msg)
        self.sig_log.emit(f"[{msg}]")
        self.sig_conn_update.emit(self, True, t("connect", lang))
        self.sig_tab_title.emit(self, f"⚠ {host}")
        self.sig_conn_state.emit(self, "lost")
        self.sig_notify.emit(t("conn_lost_title", lang), msg)

    def _show_lost_banner(self, msg):
        lang = self.config_mgr.language
        self.lbl_lost.setText(f"⚠  {msg}")
        self.btn_reconnect.setText(t("reconnect", lang))
        self.btn_reconnect.setEnabled(self._last_conn is not None)
        self.lost_banner.show()

    def reconnect(self):
        """Reconecta com os mesmos dados da última conexão e volta ao diretório."""
        if self._last_conn is None or self.ssh_mgr.is_connected:
            return
        lang = self.config_mgr.language
        self.btn_reconnect.setEnabled(False)
        self.btn_reconnect.setText(t("reconnecting", lang))
        self.handle_connection(*self._last_conn)

    def _update_latency(self, ms):
        if not self.ssh_mgr.is_connected:
            return
        color = "#28a745" if ms < 100 else ("#ffb000" if ms < 300 else "#dc3545")
        self.lbl_latency.setText(f"● {ms} ms")
        self.lbl_latency.setStyleSheet(f"color: {color};")
        self.lbl_latency.setToolTip(t("latency", self.config_mgr.language))
        self.lbl_latency.setVisible(True)

    def _watchdog_loop(self, transport):
        """Detecta quedas silenciosas (cabo/Wi-Fi) e mede a latência: a cada 5s
        abre um canal mínimo. 2 falhas seguidas (ou transporte morto) = perdida."""
        mgr = self.ssh_mgr
        fails = 0

        def same_conn():
            return (mgr.is_connected and mgr.client is not None
                    and mgr.client.get_transport() is transport)

        while same_conn():
            ok = measured = False
            t0 = time.time()
            try:
                if transport.is_active():
                    _, stdout, _ = mgr.execute("true", timeout=6)
                    stdout.read()
                    ok = measured = True
            except (OSError, EOFError):
                ok = False      # timeout / socket caído: sem resposta do servidor
            except Exception:
                # O servidor RESPONDEU mas recusou o canal (ex.: MaxSessions
                # cheio): a conexão está viva, só não dá para medir a latência.
                ok = True
            if ok:
                fails = 0
                if measured:
                    self.sig_latency.emit(int((time.time() - t0) * 1000))
            else:
                fails += 1
            if not transport.is_active() or fails >= 2:
                if same_conn():
                    self.sig_conn_lost.emit("lost")
                return
            for _ in range(10):
                if not same_conn():
                    return
                time.sleep(0.5)

    def toggle_monitor_panel(self):
        self.btn_toggle_monitor.setChecked(not self.btn_toggle_monitor.isChecked())

    def set_monitor_hidden(self, hidden):
        """Minimiza (oculta) o painel lateral de monitoramento; o espaço vai
        para o terminal/explorador e o monitor para de consultar o servidor."""
        self.sys_inner.setVisible(not hidden)
        self._update_monitor_flag()

    def _update_monitor_flag(self):
        """Roda na thread da GUI: o monitor só consulta o servidor quando o
        painel de monitoramento está realmente visível."""
        try:
            win = self.window()
            self._monitor_visible = bool(
                self.isVisible() and not win.isMinimized()
                and self.sys_inner.isVisibleTo(self)
                and self.sys_tabs.currentIndex() == 0)
        except RuntimeError:
            self._monitor_visible = False

    def _confirm_host_key(self, hostname, keytype, fingerprint):
        """Callback chamado pela thread de conexão: pergunta na GUI e bloqueia
        até o usuário responder (mesmo padrão do pedido de senha sudo)."""
        import threading as _th
        result = [False]
        event = _th.Event()
        self.sig_ask_hostkey.emit(hostname, keytype, fingerprint, result, event)
        event.wait()
        return result[0]

    def connect_ssh(self, host_str, password, key, x11):
        lang = self.config_mgr.language
        # Toda nova conexão começa sem senha de sudo em cache — cobre o caso de
        # a conexão anterior ter caído (sem passar pelo disconnect) e o usuário
        # reconectar a OUTRO servidor.
        self.sudo_cache[0] = None
        try:
            u, h, port = parse_host_string(host_str)
            self.ssh_mgr.connect(h, u, password=password, key_filename=key,
                                 use_x11=x11, port=port,
                                 host_key_callback=self._confirm_host_key)
            self._reset_shell_tracking()
            self._remote_uname = self._login_shell = ""
            try:
                _, stdout, _ = self.ssh_mgr.execute('pwd; uname -n; printf "%s\\n" "$SHELL"')
                lines = stdout.read().decode().splitlines()
                if lines and lines[0].strip(): self.remote_path = lines[0].strip()
                if len(lines) >= 3:
                    self._remote_uname = lines[1].strip()
                    self._login_shell = posixpath.basename(lines[2].strip())
            except Exception:
                pass
            self._conn_host = h
            self._conn_user = u
            self._home_path = self.remote_path
            resume, self._resume_path = self._resume_path, None
            # Instala o hook no shell de login assim que o prompt aparecer
            self._hook_armed = True
            self._hook_initial = self._login_shell in ("bash", "zsh")
            threading.Thread(target=self.terminal_read_loop, daemon=True).start()
            threading.Thread(target=self._watchdog_loop,
                             args=(self.ssh_mgr.client.get_transport(),), daemon=True).start()
            self.sig_pty_sync.emit()  # ajusta o PTY ao tamanho real do widget
            threading.Thread(target=self.monitor_loop, daemon=True).start()
            threading.Thread(target=self.fetch_os_thread, daemon=True).start()
            threading.Thread(target=self._check_sudo_access, daemon=True).start()
            self.sig_conn_update.emit(self, True, t("disconnect", lang))
            self.sig_tab_title.emit(self, f"{u}@{h}")
            self.sig_conn_state.emit(self, "connected")
            if resume:
                self.sig_notify.emit("Nix", t("conn_restored", lang))
            else:
                self.sig_notify.emit("Nix", f"Connected: {u}@{h}")
            time.sleep(1)
            if resume:
                # Volta ao diretório em que o usuário estava antes da queda
                try:
                    with self.ssh_mgr.lock:
                        self.ssh_mgr.sftp.chdir(resume)
                        self.remote_path = self.ssh_mgr.sftp.getcwd() or resume
                    self.ssh_mgr.shell.send("cd " + backend.sh_quote(resume) + " && clear\r")
                except Exception:
                    pass
            self.sig_explorer.emit()
        except Exception as e:
            import paramiko
            self.sig_conn_update.emit(self, True, t("connect", lang))
            if self._resume_path is not None:
                # Falhou uma RECONEXÃO: mantém o aviso vermelho com o botão
                self.sig_conn_state.emit(self, "lost")
                self.sig_lost_banner.emit(t("conn_lost_msg", lang).format(
                    host=f"{self._conn_user}@{self._conn_host}"))
            else:
                self.sig_conn_state.emit(self, "idle")
            if isinstance(e, paramiko.BadHostKeyException):
                # Host conhecido, mas a chave MUDOU: possível MITM ativo.
                self.sig_msg.emit(
                    "error", t("hostkey_title", lang),
                    f"{t('hostkey_changed', lang)}\n\nHost: {getattr(e, 'hostname', '')}")
            else:
                error_msg = str(e) if str(e) else "Could not connect. Check credentials and network."
                if isinstance(e, OSError) or "Unable to connect" in error_msg:
                    error_msg += "\n\n" + t("port_hint", lang)
                self.sig_msg.emit("error", t("error", lang), f"Connection Error:\n{error_msg}")

    def _check_sudo_access(self):
        """Verifica se o usuário conectado é root ou pertence ao grupo sudo/wheel."""
        try:
            if self._conn_user == "root":
                self._has_sudo = True
                return
            _, stdout, _ = self.ssh_mgr.execute("groups 2>/dev/null", timeout=10)
            groups = stdout.read().decode().strip().split()
            self._has_sudo = any(g in groups for g in ("sudo", "wheel", "root", "admin", "sudoers"))
        except Exception:
            self._has_sudo = False

    def terminal_read_loop(self):
        """Lê o shell e alimenta o terminal real (pyte) com os bytes crus.

        Usa recv com timeout (sem busy-poll) e um decoder UTF-8 incremental
        para não quebrar caracteres multi-byte divididos entre chunks.
        """
        ansi = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        shell = self.ssh_mgr.shell
        try:
            shell.settimeout(0.1)
        except Exception:
            pass
        reason = "lost"
        pending = ""
        while self.ssh_mgr.is_connected and self.ssh_mgr.shell is shell:
            try:
                data = shell.recv(8192)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                reason = "closed"
                break  # EOF: canal fechado pelo servidor
            text = decoder.decode(data)
            if not text:
                continue
            text, reports, pending = backend.extract_shell_reports(pending + text)
            if text:
                clean = ansi.sub('', text).lower()
                if "[detached from" in clean or "[screen is terminating]" in clean:
                    self.sig_screen_status.emit(False, "")
                self.sig_term_data.emit(text)
            for report in reports:
                self.sig_shell_report.emit(report)
        # Saiu do loop sem o usuário ter pedido para desconectar (is_connected
        # continua True e o shell é o MESMO desta thread): a sessão caiu.
        if self.ssh_mgr.is_connected and self.ssh_mgr.shell is shell:
            self.sig_conn_lost.emit(reason)

    def _feed_terminal_slot(self, text):
        self.output.feed(text)
        self._quiet_timer.start()

    # ------------------------------------------------------------------ #
    #  Integração com o shell (pasta, usuário e screen atuais)             #
    # ------------------------------------------------------------------ #
    def _reset_shell_tracking(self):
        self._hook_token = secrets.token_hex(8)
        self._hook_armed = False      # falta confirmar que este shell tem o hook
        self._hook_initial = False    # shell de login: aceita prompt sem $/#
        self._hook_active = False     # já chegou relatório nesta conexão
        self._hook_fails = 0          # envios seguidos sem resposta
        self._shell_user = ""
        self._shell_cwd = ""
        self._shell_sty = ""
        self._sync_dirty = False
        self._sftp_refused_user = ""  # não insistir a cada prompt
        self._denied_path = ""

    def note_terminal_input(self, data):
        """Chamado para tudo que o usuário envia ao shell (terminal ou caixa de
        comando). Um Enter pode ter trocado de shell (su, screen -r, bash...)."""
        self._quiet_timer.start()
        if backend.is_sudo_prompt(self.output.cursor_line()):
            self._capture_sudo_password(data)
        else:
            self._sudo_typed.clear()
        if "\r" in data:
            self._hook_armed = True
            self._hook_initial = False

    def _capture_sudo_password(self, data):
        """Guarda a senha digitada no prompt do sudo: o SFTP precisa dela para
        acompanhar a troca de usuário, sem pedir a mesma senha outra vez."""
        for ch in data:
            if ch in "\r\n":
                self.sudo_cache[0] = "".join(self._sudo_typed)
                self._sudo_typed.clear()
            elif ch in "\x7f\x08":
                del self._sudo_typed[-1:]
            elif ch in "\x03\x15":
                self._sudo_typed.clear()
            elif ch >= " ":
                self._sudo_typed.append(ch)

    def _on_terminal_quiet(self):
        """Terminal parado: mascara a caixa de comando em prompt de senha e, se
        o Enter não resultou em relatório, instala o hook no shell atual."""
        if not self.ssh_mgr.is_connected:
            return
        line = self.output.cursor_line()
        masked = backend.is_password_prompt(line)
        self.cmd_input.setEchoMode(QLineEdit.EchoMode.Password if masked
                                   else QLineEdit.EchoMode.Normal)
        if not self._hook_armed or self._hook_fails >= 3:
            return
        if backend.is_shell_prompt(line) or (
                self._hook_initial and backend.is_initial_prompt(line)):
            self._inject_shell_hook(len(line))

    def _inject_shell_hook(self, prompt_len):
        cols = max(1, self.output.term_size()[0])
        length = len(backend.build_shell_hook(self._hook_token, 1))
        rows = (prompt_len + length) // cols + 1
        self._hook_armed = False
        self._hook_fails += 1
        self.output.hold_repaint(1000)
        self.output.send_text(backend.build_shell_hook(self._hook_token, rows) + "\r",
                              notify=False)

    def _on_shell_report(self, report):
        token, user, host, sty, path = report
        if token != self._hook_token:
            return  # forjado por algum texto exibido no terminal
        self._hook_armed = False
        self._hook_fails = 0
        self.output.release_repaint()
        if self._remote_uname and host != self._remote_uname:
            return  # ssh aninhado ou contêiner: não é o disco que o Explorer vê
        self._hook_active = True
        if sty != self._shell_sty:
            self._shell_sty = sty
            self.set_screen_status(bool(sty), sty.split(".", 1)[-1])
        self._shell_user, self._shell_cwd = user, path
        with self._sync_lock:
            self._sync_dirty = True
            if self._sync_busy:
                return
            self._sync_busy = True
        threading.Thread(target=self._shell_sync_worker, daemon=True).start()

    def _shell_sync_worker(self):
        """Leva o Explorer ao estado informado pelo shell. Fica fora da GUI
        porque o SFTP e o sudo podem demorar."""
        while True:
            with self._sync_lock:
                if not self._sync_dirty or not self.ssh_mgr.is_connected:
                    self._sync_busy = False
                    return
                self._sync_dirty = False
            user, path = self._shell_user, self._shell_cwd
            try:
                user_changed = self._follow_shell_user(user)
                if self._follow_shell_path(path, user_changed) or user_changed:
                    self.sig_explorer.emit()
            except Exception as e:
                self.sig_log.emit(f"[Nix] Explorer: {e}")

    def _follow_shell_user(self, user):
        mgr = self.ssh_mgr
        if user == mgr.sftp_user:
            # Saiu do usuário recusado: uma nova tentativa pode dar certo
            self._sftp_refused_user = ""
            return False
        if not user or self.active_transfers:
            return False
        if user == mgr.username:
            mgr.reset_sftp_user()
            self._sftp_refused_user = ""
            self.sig_log.emit(f"[Nix] SFTP reverted to: {user}")
            return True
        if user == self._sftp_refused_user:
            return False
        try:
            try:
                mgr.switch_sftp_user(user, self.sudo_cache[0])
            except SudoPasswordRequired:
                password = self._get_sudo_pwd() if self._has_sudo else None
                if not password:
                    raise
                self.sudo_cache[0] = password
                mgr.switch_sftp_user(user, password)
        except SudoPasswordRequired:
            self._sftp_refused_user = user
            self.sig_log.emit(f"[Nix] Explorer stays as {mgr.sftp_user}: "
                              f"sudo access is needed to open it as {user}")
            return False
        except SudoAuthError:
            self.sudo_cache[0] = None
            self._sftp_refused_user = user
            self.sig_log.emit(f"[Nix] Explorer stays as {mgr.sftp_user}: "
                              f"sudo refused access as {user}")
            return False
        except Exception as e:
            self._sftp_refused_user = user
            self.sig_log.emit(f"[Nix] Failed to elevate SFTP: {e}")
            return False
        self._sftp_refused_user = ""
        self.sig_log.emit(f"[Nix] SFTP elevated as: {user}")
        return True

    def _follow_shell_path(self, path, force):
        if not path or (path == self.remote_path and not force):
            return False
        try:
            with self.ssh_mgr.lock:
                self.ssh_mgr.sftp.chdir(path)
                # Mantém o $PWD lógico (getcwd resolveria symlinks e o caminho
                # deixaria de bater com o próximo relatório)
                self.remote_path = path
            self._denied_path = ""
            return True
        except Exception:
            if path != self._denied_path:
                self._denied_path = path
                self.sig_log.emit(f"[Nix] Explorer ({self.ssh_mgr.sftp_user}) "
                                  f"cannot open: {path}")
            return False

    def _sync_pty_slot(self):
        self.output.sync_pty_size()

    def refresh_terminal_theme(self):
        """Aplica tema/fonte do Nix (Standard/Retro/Matrix) ao terminal real.

        Retro e Matrix têm identidade própria (fundo escuro + fosforescente),
        então usam cores fixas legíveis mesmo no modo claro. O Standard segue
        o modo claro/escuro e a cor customizada ("Term Color").
        """
        thm = self.config_mgr.theme
        mode = thm.get("mode", "dark")
        style = thm.get("term_style", "standard")
        default_dark_fg = "#a6accd"
        custom_fg = thm.get("terminal_color", default_dark_fg)
        light_palette = False
        crt = False
        if style == "matrix":
            family, bold, bg = "Courier New", True, "#000000"
            fg = "#00ff41"          # verde Matrix clássico
        elif style == "retro":
            family, bold, bg = "Courier", True, "#1a1a1a"
            fg = "#ffb000"          # âmbar CRT
        elif style == "crt":
            # cool-retro-term: fósforo verde sobre tubo, com scanlines,
            # glow e vinheta (efeitos desenhados pelo próprio widget)
            family, bold, bg = "Courier New", True, "#0a1206"
            fg = "#33ff33"
            crt = True
        elif style == "crt_blue":
            # variação de fósforo azul (estilo monitores IBM antigos)
            family, bold, bg = "Courier New", True, "#050d18"
            fg = "#4dc4ff"
            crt = True
        else:
            family, bold = "Consolas", False
            if mode == "dark":
                bg, fg = "#0d0a0d", custom_fg
            else:
                bg = "#f5f5f5"
                fg = "#2d3436" if custom_fg == default_dark_fg else custom_fg
                light_palette = True   # paleta ANSI escurecida p/ fundo claro
        size = (self.parent_ui.terminal_font_size if self.parent_ui
                else thm.get("font_size", 13))
        self.output.set_terminal_style(family, size, bold, fg, bg,
                                       light_palette=light_palette, crt=crt)

    def monitor_loop(self):
        self.last_rx = self.last_tx = 0
        self.last_time = time.time()
        conn = self.ssh_mgr.client   # identifica ESTA conexão (evita loops duplicados após reconectar)
        alive = lambda: self.ssh_mgr.is_connected and self.ssh_mgr.client is conn
        fail_backoff = 0  # backoff exponencial em falhas consecutivas
        while alive():
            # Painel escondido (outra aba, janela minimizada/na bandeja ou outra
            # sub-aba): não gasta rede/CPU do servidor. Ao voltar, zera a base
            # do cálculo de tráfego para não mostrar uma média enorme.
            if not self._monitor_visible:
                self.last_rx = self.last_tx = 0
                time.sleep(0.5)
                continue
            try:
                cmd = ("grep 'cpu ' /proc/stat; sleep 1; grep 'cpu ' /proc/stat; "
                       "echo '==MEM=='; cat /proc/meminfo; echo '==NET=='; cat /proc/net/dev; "
                       "echo '==USERS=='; who; echo '==PROCS=='; "
                       "ps -eo pid,user,pcpu,pmem,comm --sort=-pcpu --no-headers | head -n 15")
                # timeout generoso: o comando leva ~1-2s (tem sleep 1); 20s só
                # dispara se o canal travar de vez, evitando vazar sessão.
                _, stdout, _ = self.ssh_mgr.execute(cmd, timeout=20)
                parsed = backend.parse_monitor_output(stdout.read().decode().strip())
                if len(parsed) == 3:
                    self.sig_monitor.emit(parsed[0], parsed[1], parsed[2], {}, [], {})
                else:
                    self.sig_monitor.emit(*parsed)
                fail_backoff = 0  # sucesso: volta ao ritmo normal
                # Ritmo dividido em fatias de 0.5s para reagir rápido ao
                # disconnect sem manter o loop ocioso preso num sleep longo.
                for _ in range(4):
                    if not alive():
                        return
                    time.sleep(0.5)
            except Exception:
                # Servidor sob carga / conexão instável: recua até 30s para não
                # martelar o servidor com reconexões e novos canais.
                fail_backoff = min(fail_backoff + 1, 6)
                for _ in range(2 ** fail_backoff):
                    if not alive():
                        return
                    time.sleep(0.5)

    def fetch_os_thread(self):
        info = backend.fetch_os_info(self.ssh_mgr)
        self.sig_os_info.emit(info)

    def log_local_slot(self, message):
        self.output.append_local(message)

    def show_msg_slot(self, msg_type, title, text):
        if msg_type == "error":    QMessageBox.critical(self, title, text)
        elif msg_type == "warn":   QMessageBox.warning(self, title, text)
        else:                      QMessageBox.information(self, title, text)

    def update_env_list_slot(self, win, content, envs):
        if hasattr(win, 'update_ui'): win.update_ui(content, envs)

    def ask_sudo_slot(self, result_list, event):
        text, ok = QInputDialog.getText(self, "Sudo", t("enter_sudo", self.config_mgr.language),
                                        QLineEdit.EchoMode.Password)
        if ok: result_list[0] = text
        event.set()

    def ask_hostkey_slot(self, hostname, keytype, fingerprint, result_list, event):
        """Pergunta ao usuário se confia numa host key desconhecida (1ª conexão)."""
        lang = self.config_mgr.language
        reply = QMessageBox.question(
            self, t("hostkey_title", lang),
            f"{t('hostkey_unknown', lang)}\n\n"
            f"Host: {hostname}\n{keytype} {fingerprint}\n\n"
            f"{t('hostkey_confirm', lang)}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        result_list[0] = (reply == QMessageBox.StandardButton.Yes)
        event.set()

    def set_screen_status(self, in_screen, name=""):
        lang = self.config_mgr.language
        if in_screen:
            self.lbl_screen_status.setText(f"● {t('state_screen', lang)} ({name})")
            self.lbl_screen_status.setStyleSheet("color: #ff8c00;")
        else:
            self.lbl_screen_status.setText(f"● {t('state_main', lang)}")
            self.lbl_screen_status.setStyleSheet("color: #28a745;")

    def update_os_info_slot(self, info_text):
        self.os_info_text.setPlainText(info_text)

    def update_screens_ui_slot(self, win, output):
        if hasattr(win, 'update_ui'): win.update_ui(output)

    @staticmethod
    def _fmt_eta(seconds):
        seconds = int(max(0, seconds))
        h, rem = divmod(seconds, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    def update_progress_slot(self, transferred, total, transfer_id):
        entry = self.active_transfers.get(transfer_id)
        if not entry or entry.get('state') != 'running':
            return
        try:
            if total > 0:
                entry['pbar'].setValue(int((transferred / total) * 100))
            now = time.time()
            last_t, last_b = entry['sample']
            dt = now - last_t
            if dt >= 0.5:
                inst = max(0, transferred - last_b) / dt
                speed = inst if entry['speed'] == 0 else 0.6 * entry['speed'] + 0.4 * inst
                entry['speed'] = speed
                entry['sample'] = (now, transferred)
                text = f"{self.format_file_size(speed)}/s"
                if speed > 0 and total > transferred:
                    text += f" \u00b7 {self._fmt_eta((total - transferred) / speed)}"
                entry['info'].setText(text)
        except RuntimeError:
            pass

    def update_monitor_ui_slot(self, cpu, ram, procs, net_bytes, users, mem_info):
        try:
            self.cpu_label.setText(f"{cpu:.1f}%"); self.cpu_bar.setValue(int(cpu))
            self.ram_label.setText(f"{ram:.1f}%"); self.ram_bar.setValue(int(ram))
            lang = self.config_mgr.language
            if mem_info:
                self.lbl_mem_details.setText(
                    f"{t('mem_free', lang)}: {mem_info.get('available', 0)} MB / "
                    f"{t('mem_total', lang)}: {mem_info.get('total', 0)} MB")
            now = time.time(); dt = now - self.last_time
            if dt > 0 and net_bytes:
                rx, tx = net_bytes.get('rx', 0), net_bytes.get('tx', 0)
                if self.last_rx > 0:
                    self.lbl_net_rx.setText(f"▼ {self.format_file_size((rx - self.last_rx) / dt)}/s")
                    self.lbl_net_tx.setText(f"▲ {self.format_file_size((tx - self.last_tx) / dt)}/s")
                self.last_rx, self.last_tx = rx, tx
            self.last_time = now
            self.proc_tree.clear()
            for p in procs:
                item = QTreeWidgetItem([p[0], p[1], f"{p[2]}%", f"{p[3]}%", p[4]] if len(p) >= 5
                                       else [p[0], "-", f"{p[1]}%", f"{p[2]}%", p[3]])
                self.proc_tree.addTopLevelItem(item)
            self.users_list.clear()
            for u in users:
                if isinstance(u, dict):
                    user = u.get("user", ""); tty = u.get("tty", ""); origin = u.get("origin", "")
                    label = f"{user} ({tty})" if tty else user
                    if origin:
                        label += f"  ⇐ {origin}"
                        tip = f"{user} @ {tty}\n{t('conn_from', lang)}: {origin}"
                    else:
                        tip = f"{user} @ {tty}\n{t('conn_local', lang)}"
                    item = QListWidgetItem(label)
                    item.setToolTip(tip)
                    item.setData(Qt.ItemDataRole.UserRole, u)
                    self.users_list.addItem(item)
                else:
                    self.users_list.addItem(u)
        except Exception:
            pass

    def clear_terminal(self):
        self.output.clear()

    def toggle_auto_scroll(self, checked):
        self.auto_scroll = checked
        self.output.auto_scroll = checked
        lang = self.config_mgr.language
        if checked:
            self.btn_auto_scroll.setToolTip(t("tip_scroll_on", lang))
            self.btn_auto_scroll.setIcon(qta.icon('fa5s.arrow-down', color='white'))
        else:
            self.btn_auto_scroll.setToolTip(t("tip_scroll_off", lang))
            self.btn_auto_scroll.setIcon(qta.icon('fa5s.lock', color='#ffb86c'))

    def eventFilter(self, obj, event):
        if obj == self.cmd_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Tab:
                self.autocomplete_terminal(); return True
            elif event.key() == Qt.Key.Key_Up:
                self.command_history_up(); return True
            elif event.key() == Qt.Key.Key_Down:
                self.command_history_down(); return True
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

    def autocomplete_terminal(self):
        """Tab estilo bash: completa a palavra sob o cursor (não só a última),
        funcionando com texto depois do cursor, aspas, espaços escapados, ~ e
        comandos (1ª palavra / depois de | ; && sudo). Usa o `compgen` do bash
        no servidor, então respeita o diretório atual e os comandos do PATH."""
        if not self.ssh_mgr.is_connected:
            return
        text = self.cmd_input.text()
        cursor = self.cmd_input.cursorPosition()
        ctx = backend.split_completion_context(text[:cursor])
        value = ctx["value"]
        cmd_mode = ctx["cmd_pos"] and "/" not in value and not value.startswith("~")
        if cmd_mode and not value:
            return   # completar TODOS os comandos do PATH não ajuda
        home = self._home_path or self.remote_path
        query = (home + value[1:]) if value.startswith("~") else value
        script = backend.build_completion_script(self.remote_path, query, cmd_mode)

        def work():
            matches = []
            try:
                _, stdout, _ = self.ssh_mgr.execute(script, timeout=8)
                out = stdout.read().decode("utf-8", errors="replace")
                matches = [ln for ln in out.split("\n") if ln]
            except Exception:
                pass
            self.sig_completion.emit({"text": text, "cursor": cursor, "ctx": ctx,
                                      "matches": matches, "home": home})
        threading.Thread(target=work, daemon=True).start()

    def _apply_completion(self, info):
        """Slot (GUI): insere o resultado do Tab se o usuário não mexeu na linha."""
        if (self.cmd_input.text() != info["text"]
                or self.cmd_input.cursorPosition() != info["cursor"]):
            return
        ctx, text, cursor = info["ctx"], info["text"], info["cursor"]
        insert, options = backend.compute_completion(ctx, info["matches"], info["home"])
        if insert is not None:
            self._replace_word(text, ctx["start"], cursor, insert)
        elif len(options) > 1:
            self._show_completion_menu(text, ctx, cursor, options)

    def _replace_word(self, text, start, cursor, insert):
        self.cmd_input.setText(text[:start] + insert + text[cursor:])
        self.cmd_input.setCursorPosition(start + len(insert))

    def _show_completion_menu(self, text, ctx, cursor, options):
        """Vários candidatos sem prefixo comum maior: lista clicável (setas + Enter)."""
        lang = self.config_mgr.language
        if len(options) > 40:
            self.sig_log.emit(t("tab_matches", lang).format(n=len(options)) + ":  "
                              + "   ".join(o.rstrip("/").split("/")[-1] + ("/" if o.endswith("/") else "")
                                           for o in options[:200]))
            return
        menu = QMenu(self.cmd_input)
        for opt in options:
            label = opt.rstrip("/").split("/")[-1] + ("/" if opt.endswith("/") else "")
            act = menu.addAction(label)
            act.triggered.connect(
                lambda _=False, o=opt: self._pick_completion(text, ctx, cursor, o))
        pos = self.cmd_input.mapToGlobal(self.cmd_input.rect().topLeft())
        pos.setY(pos.y() - menu.sizeHint().height())   # abre acima da linha de comando
        menu.exec(pos)
        self.cmd_input.setFocus()

    def _pick_completion(self, text, ctx, cursor, option):
        if self.cmd_input.text() != text:
            return
        insert, _ = backend.compute_completion(ctx, [option], self._home_path or self.remote_path)
        if insert is not None:
            self._replace_word(text, ctx["start"], cursor, insert)

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
        raw = self.cmd_input.text()
        # O shell está pedindo uma senha: a resposta vai sem alteração (senha
        # pode ter espaços) e nunca para o histórico, que é salvo em disco.
        if backend.is_password_prompt(self.output.cursor_line()):
            self.cmd_input.clear()
            self.output.send_text(raw + "\r")
            return

        cmd = raw.strip()
        if not cmd: return

        if cmd in ("exit", "logout") and not self._hook_active:
            self.set_screen_status(False)

        if cmd.startswith(("screen -S", "screen -r", "screen -x")):
            parts = cmd.split()
            self.set_screen_status(True, parts[2] if len(parts) > 2 else "Active")

        if cmd.startswith(("nano ", "sudo nano ")):
            self.process_comand_nano(cmd); self.cmd_input.clear(); return

        if not self.command_history or self.command_history[0] != cmd:
            self.command_history.insert(0, cmd)
            # Mantém o histórico limitado na memória: sem isso a lista cresce
            # sem parar e o Nix fica lento após muitos comandos.
            if len(self.command_history) > 500:
                del self.command_history[500:]
            self._save_command_history()
        self.history_index = -1

        if cmd == "clear":
            self.clear_terminal()
            self.output.send_text("clear\r")
            self.cmd_input.clear(); return

        # Com o hook ativo o próprio shell informa a pasta nova; este bloco só
        # atende shells sem suporte ao hook.
        if cmd.startswith("cd ") and not self._hook_active:
            target = cmd[3:].strip()
            try:
                if target == "..":    self.navigate_back(); self.cmd_input.clear(); return
                elif target in ("~", ""): self.go_home(); self.cmd_input.clear(); return
                else:
                    new_path = target if target.startswith("/") else posixpath.join(self.remote_path, target)
                    try:
                        with self.ssh_mgr.lock:
                            self.ssh_mgr.sftp.chdir(new_path)
                            self.remote_path = self.ssh_mgr.sftp.getcwd() or new_path
                        self.sig_explorer.emit()
                    except Exception as e:
                        self.sig_log.emit(f"cd error: {str(e)}")
            except Exception as e:
                self.sig_log.emit(f"cd error: {str(e)}")

        self.output.send_text(cmd + "\r")
        self.cmd_input.clear()

    def _save_command_history(self):
        self.config_mgr.command_history = self.command_history[:500]
        now = time.time()
        if now - getattr(self, '_last_history_save', 0) > 10:
            self.config_mgr.save_config()
            self._last_history_save = now

    def process_comand_nano(self, cmd):
        use_sudo = "sudo" in cmd
        filename = cmd.split()[-1]
        if filename == "nano": return
        sudo_pwd = None
        if use_sudo:
            if not self.sudo_cache[0]:
                lang = self.config_mgr.language
                pwd, ok = QInputDialog.getText(self, "Sudo", t("enter_sudo", lang), QLineEdit.EchoMode.Password)
                if not ok: return
                self.sudo_cache[0] = pwd
            sudo_pwd = self.sudo_cache[0]
        editor = RemoteEditorDialog(self, filename, use_sudo, sudo_pwd)
        editor.exec()

    def _get_sudo_pwd(self):
        pwd = [None]; event = threading.Event()
        self.sig_ask_sudo.emit(pwd, event); event.wait(); return pwd[0]

    def update_path_label(self):
        path = self.remote_path
        self.current_path.setText(("..." + path[-37:]) if len(path) > 40 else path)
        self.current_path.setToolTip(self.remote_path)
        self._update_fav_btn_icon()

    def copy_current_path(self):
        QApplication.clipboard().setText(self.remote_path)
        self.sig_log.emit(f"[{t('path_copied', self.config_mgr.language)}: {self.remote_path}]")

    def go_home(self):
        if self._hook_active and self.ssh_mgr.is_connected:
            # O shell sabe qual é a home do usuário atual (pode ser outro após
            # um su) e o relatório do próximo prompt atualiza o Explorer.
            self.output.send_text("cd\r")
            return
        self.remote_path = "/home"
        if self.ssh_mgr.is_connected:
            try:
                _, stdout, _ = self.ssh_mgr.execute("pwd")
                real = stdout.read().decode().strip()
                if real: self.remote_path = real
            except Exception:
                pass
            if self.ssh_mgr.shell: self.ssh_mgr.shell.send(f'cd {backend.sh_quote(self.remote_path)}\r')
        self.sig_explorer.emit()

    def navigate_back(self):
        if not self.ssh_mgr.is_connected: return
        if self.remote_path != "/":
            parent = posixpath.dirname(self.remote_path)
            try:
                with self.ssh_mgr.lock:
                    self.ssh_mgr.sftp.chdir(parent)
                    self.remote_path = parent or "/"
                if self.ssh_mgr.shell: self.ssh_mgr.shell.send(f'cd {backend.sh_quote(self.remote_path)}\r')
                self.sig_explorer.emit()
            except Exception as e:
                self.sig_msg.emit("error", t("error", self.config_mgr.language), str(e))

    def update_explorer_slot(self):
        if not self.ssh_mgr.is_connected or not self.ssh_mgr.sftp: return
        if self.active_transfers: return
        lang = self.config_mgr.language
        try:
            self.explorer.clear()
            self.update_path_label()
            with self.ssh_mgr.lock:
                files = self.ssh_mgr.sftp.listdir_attr(self.remote_path)
            files.sort(key=lambda x: (not stat.S_ISDIR(x.st_mode), x.filename.lower()))
            for f in files:
                try:
                    is_dir = stat.S_ISDIR(f.st_mode)
                    ftype  = t("directory", lang) if is_dir else t("file", lang)
                    size   = "-" if is_dir else self.format_file_size(f.st_size)
                    item   = QTreeWidgetItem([f.filename, size, ftype, stat.filemode(f.st_mode), ""])
                    item.setIcon(0, self.get_file_icon(f.filename, is_dir=is_dir))
                    # Tipo REAL guardado no item: não depende do texto traduzido
                    item.setData(0, Qt.ItemDataRole.UserRole, "Directory" if is_dir else "File")
                    self.explorer.addTopLevelItem(item)
                except Exception:
                    pass
            if self.filter_input.text(): self.filter_explorer(self.filter_input.text())
            threading.Thread(target=self._fetch_git_status, daemon=True).start()
            idx = self.sys_tabs.currentIndex()
            if idx in (3, 4):
                self._on_sys_tab_changed(idx)
        except Exception:
            pass

    def filter_explorer(self, text):
        s = text.lower()
        for i in range(self.explorer.topLevelItemCount()):
            item = self.explorer.topLevelItem(i)
            item.setHidden(s not in item.text(0).lower())

    def _item_kind(self, item):
        """"Directory" ou "File" — lido do dado guardado no item; só se faltar
        (item antigo) cai para o texto da coluna, em qualquer idioma."""
        kind = item.data(0, Qt.ItemDataRole.UserRole)
        if kind in ("Directory", "File"):
            return kind
        text = item.text(2)
        dir_names = {"Directory"} | {t("directory", l) for l in ("en", "pt", "es")}
        return "Directory" if text in dir_names else "File"

    def get_item_info(self, item):
        filename  = item.text(0).strip()
        item_type = self._item_kind(item)
        new_path  = f"/{filename}" if self.remote_path == "/" else posixpath.join(self.remote_path, filename)
        return filename, new_path, item_type

    def on_item_double_click(self, item, column):
        if not self.ssh_mgr.is_connected or not self.ssh_mgr.sftp: return
        try:
            filename, new_path, item_type = self.get_item_info(item)
            perms = item.text(3)
            if item_type in ("Directory", t("directory", self.config_mgr.language)):
                try:
                    with self.ssh_mgr.lock:
                        self.ssh_mgr.sftp.chdir(new_path)
                        self.remote_path = new_path
                    if self.ssh_mgr.shell: self.ssh_mgr.shell.send(f'cd {backend.sh_quote(self.remote_path)}\r')
                    self.sig_explorer.emit()
                except Exception as e:
                    self.sig_msg.emit("error", t("error", self.config_mgr.language), f"Access denied: {str(e)}")
            else:
                is_exec = 'x' in perms or filename.lower().endswith(('.sh', '.py', '.pl', '.js', '.appimage', '.run'))
                if is_exec:
                    reply = QMessageBox.question(self, t("execute_file", self.config_mgr.language),
                                                 f"{t('execute_prompt', self.config_mgr.language)} '{filename}'?\n\nYes: Run | No: View",
                                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
                    if reply == QMessageBox.StandardButton.Yes:
                        cmd = f"python3 \"{new_path}\"" if filename.endswith('.py') else \
                              f"bash \"{new_path}\""    if filename.endswith('.sh') else \
                              f"node \"{new_path}\""    if filename.endswith('.js') else f"\"{new_path}\""
                        self.cmd_input.setText(cmd); self.send_command(); return
                    elif reply == QMessageBox.StandardButton.Cancel:
                        return
                threading.Thread(target=self.preview_file, args=(new_path, filename), daemon=True).start()
        except Exception as e:
            self.sig_msg.emit("error", t("error", self.config_mgr.language), str(e))

    def preview_file(self, file_path, filename):
        try:
            with self.ssh_mgr.lock: size = self.ssh_mgr.sftp.stat(file_path).st_size
        except Exception:
            size = 0
        try:
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.svg', '.gif', '.bmp')):
                if size > 15 * 1024 * 1024: return
                with self.ssh_mgr.lock:
                    with self.ssh_mgr.sftp.open(file_path, 'r') as f: img_data = f.read()
                self.sig_image_viewer.emit(filename, img_data); return
            if filename.lower().endswith(('.csv', '.tsv', '.xlsx')):
                if size > 15 * 1024 * 1024:
                    self.sig_msg.emit("warn", t("warning", self.config_mgr.language), t("file_large", self.config_mgr.language)); return
                with self.ssh_mgr.lock:
                    with self.ssh_mgr.sftp.open(file_path, 'rb') as f: content = f.read()
                self.sig_table_viewer.emit(file_path, filename, content); return
            lazy = filename.lower().endswith(('.fasta', '.fna', '.vcf', '.sam', '.fastq', '.txt', '.py', '.json', '.sh')) or size > 1024 * 1024
            read_size = 100 * 1024 if lazy else max(size, 1)
            with self.ssh_mgr.lock:
                with self.ssh_mgr.sftp.open(file_path, 'r') as f: content = f.read(read_size).decode('utf-8', errors='replace')
            if lazy and size > read_size: content += "\n\n--- [TRUNCATED] ---"
            self.sig_viewer.emit(file_path, filename, content)
        except Exception as e:
            if "Permission" in str(e) or "denied" in str(e).lower():
                self.sig_msg.emit("warn", t("warning", self.config_mgr.language), f"Access denied: '{filename}'.")

    def open_image_viewer_slot(self, filename, img_data):
        ImageViewerDialog(self, filename, img_data).show()

    def open_table_viewer_slot(self, file_path, filename, content):
        TableViewerDialog(self, file_path, filename, content).show()

    def open_file_viewer_slot(self, file_path, filename, content):
        TextViewerDialog(self, file_path, filename, content).show()

    def format_file_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0: return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    _QTA_ICON_CACHE: dict = {}

    def get_file_icon(self, filename, is_dir=False):
        try:
            if is_dir:
                key = 'dir:#eccb5b'
                if key not in self._QTA_ICON_CACHE:
                    self._QTA_ICON_CACHE[key] = qta.icon('fa5s.folder', color='#eccb5b')
                return self._QTA_ICON_CACHE[key]
            ext = os.path.splitext(filename)[1].lower()
            icons = {
                '.py': ('fa5b.python', '#3776AB'), '.pyc': ('fa5b.python', '#3776AB'),
                '.js': ('fa5b.js', '#F7DF1E'), '.ts': ('fa5b.js-square', '#3178C6'),
                '.html': ('fa5b.html5', '#E34F26'), '.css': ('fa5b.css3-alt', '#1572B6'),
                '.c': ('fa5s.file-code', '#A8B9CC'), '.cpp': ('fa5s.file-code', '#00599C'),
                '.h': ('fa5s.file-code', '#A8B9CC'), '.hpp': ('fa5s.file-code', '#A8B9CC'),
                '.java': ('fa5b.java', '#007396'), '.go': ('fa5s.file-code', '#00ADD8'),
                '.rs': ('fa5s.cog', '#000000'), '.rb': ('fa5s.gem', '#CC342D'),
                '.php': ('fa5b.php', '#777BB4'), '.sh': ('fa5s.terminal', '#4EAA25'),
                '.bash': ('fa5s.terminal', '#4EAA25'), '.zsh': ('fa5s.terminal', '#4EAA25'),
                '.swift': ('fa5b.swift', '#F05138'), '.kt': ('fa5s.file-code', '#7F52FF'),
                '.r': ('fa5b.r-project', '#276DC3'), '.json': ('fa5s.file-code', '#CBCB41'),
                '.xml': ('fa5s.file-code', '#00608C'), '.yaml': ('fa5s.cogs', '#CB171E'),
                '.yml': ('fa5s.cogs', '#CB171E'), '.ini': ('fa5s.cogs', '#A9A9A9'),
                '.conf': ('fa5s.cogs', '#A9A9A9'), '.cfg': ('fa5s.cogs', '#A9A9A9'),
                '.toml': ('fa5s.cogs', '#A9A9A9'), '.env': ('fa5s.lock', '#A9A9A9'),
                '.csv': ('fa5s.file-csv', '#217346'), '.tsv': ('fa5s.file-csv', '#217346'),
                '.sql': ('fa5s.database', '#F29111'), '.db': ('fa5s.database', '#A9A9A9'),
                '.sqlite': ('fa5s.database', '#A9A9A9'), '.txt': ('fa5s.file-alt', '#A9A9A9'),
                '.md': ('fa5b.markdown', '#000000'), '.pdf': ('fa5s.file-pdf', '#F40F02'),
                '.doc': ('fa5s.file-word', '#2B579A'), '.docx': ('fa5s.file-word', '#2B579A'),
                '.xls': ('fa5s.file-excel', '#217346'), '.xlsx': ('fa5s.file-excel', '#217346'),
                '.ppt': ('fa5s.file-powerpoint', '#D24726'), '.pptx': ('fa5s.file-powerpoint', '#D24726'),
                '.png': ('fa5s.file-image', '#12B886'), '.jpg': ('fa5s.file-image', '#12B886'),
                '.jpeg': ('fa5s.file-image', '#12B886'), '.svg': ('fa5s.file-image', '#FFB13B'),
                '.gif': ('fa5s.file-image', '#12B886'), '.bmp': ('fa5s.file-image', '#12B886'),
                '.mp3': ('fa5s.file-audio', '#000000'), '.wav': ('fa5s.file-audio', '#000000'),
                '.mp4': ('fa5s.file-video', '#000000'), '.avi': ('fa5s.file-video', '#000000'),
                '.mkv': ('fa5s.file-video', '#000000'), '.mov': ('fa5s.file-video', '#000000'),
                '.tar': ('fa5s.file-archive', '#EFC050'), '.gz': ('fa5s.file-archive', '#EFC050'),
                '.tgz': ('fa5s.file-archive', '#EFC050'), '.zip': ('fa5s.file-archive', '#EFC050'),
                '.rar': ('fa5s.file-archive', '#EFC050'), '.7z': ('fa5s.file-archive', '#EFC050'),
                '.deb': ('fa5s.box', '#EFC050'), '.rpm': ('fa5s.box', '#EFC050'),
                '.fastq': ('fa5s.dna', '#20B2AA'), '.fasta': ('fa5s.dna', '#20B2AA'),
                '.bam': ('fa5s.dna', '#20B2AA'), '.sam': ('fa5s.dna', '#20B2AA'),
                '.vcf': ('fa5s.dna', '#20B2AA'), '.log': ('fa5s.clipboard-list', '#A9A9A9'),
                '.exe': ('fa5b.windows', '#000000'), '.iso': ('fa5s.compact-disc', '#000000'),
            }
            icon_name, color = icons.get(ext, ('fa5s.file', '#A9A9A9'))
            mode = self.config_mgr.theme.get("mode", "dark")
            effective_color = '#FFFFFF' if (mode == "dark" and color == '#000000') else color
            cache_key = f'{icon_name}:{effective_color}'
            if cache_key not in self._QTA_ICON_CACHE:
                self._QTA_ICON_CACHE[cache_key] = qta.icon(icon_name, color=effective_color)
            return self._QTA_ICON_CACHE[cache_key]
        except Exception:
            if 'default' not in self._QTA_ICON_CACHE:
                self._QTA_ICON_CACHE['default'] = qta.icon('fa5s.file', color='#A9A9A9')
            return self._QTA_ICON_CACHE['default']

    def show_context_menu(self, pos):
        if not self.ssh_mgr.is_connected: return
        lang = self.config_mgr.language
        ic = self._menu_icon_color()
        menu = QMenu(self)
        a_mkdir = QAction(qta.icon('fa5s.folder-plus', color=ic), t("new_folder", lang), self)
        a_mkdir.triggered.connect(self.shortcut_mkdir)
        a_newfile = QAction(qta.icon('fa5s.file-medical', color=ic), "New File", self)
        a_newfile.triggered.connect(self.shortcut_new_file)
        menu.addAction(a_mkdir); menu.addAction(a_newfile)
        item = self.explorer.itemAt(pos)
        if item:
            menu.addSeparator()
            filename, new_path, item_type = self.get_item_info(item)
            for icon, key, fn in [
                ('fa5s.folder-open', 'open',      lambda: self.on_item_double_click(item, 0)),
                ('fa5s.edit',        'edit_nano',  lambda: self.ctx_edit(item)),
                ('fa5s.copy',        'copy_path',  lambda: self.ctx_copy_path(item)),
                ('fa5s.i-cursor',    'rename',     lambda: self.ctx_rename(item)),
                ('fa5s.people-carry','move',       lambda: self.ctx_move(item)),
                ('fa5s.trash-alt',   'delete',     lambda: self.ctx_delete(item)),
            ]:
                a = QAction(qta.icon(icon, color=ic), t(key, lang), self)
                a.triggered.connect(fn)
                menu.addAction(a)
            menu.addSeparator()
            if item_type in ("Directory", t("directory", lang)):
                ac = QAction(qta.icon('fa5s.file-archive', color=ic), t("compress", lang), self)
                ac.triggered.connect(lambda: self.ctx_compress(new_path, filename))
                menu.addAction(ac)
            elif filename.endswith(('.tar.gz', '.tgz', '.zip', '.tar')):
                ae = QAction(qta.icon('fa5s.box-open', color=ic), t("extract", lang), self)
                ae.triggered.connect(lambda: self.ctx_extract(new_path, filename))
                menu.addAction(ae)
            menu.addSeparator()
            ap = QAction(qta.icon('fa5s.info-circle', color=ic), t("properties", lang), self)
            ap.triggered.connect(lambda: self.ctx_properties(item))
            menu.addAction(ap)

        if self._git_branch:
            menu.addSeparator()
            for git_label, git_cmd in [
                ("Git Status",    "git status"),
                ("Git Log (10)",  "git log --oneline -10"),
                ("Git Pull",      "git pull"),
                ("Git Diff",      "git diff"),
            ]:
                ag = QAction(qta.icon('fa5s.code-branch', color='#bd93f9'), git_label, self)
                ag.triggered.connect(lambda _, c=git_cmd: self._run_git_cmd(c))
                menu.addAction(ag)

        menu.exec(self.explorer.viewport().mapToGlobal(pos))

    def ctx_edit(self, item):
        filename, new_path, item_type = self.get_item_info(item)
        if item_type in ("File", t("file", self.config_mgr.language)):
            self.process_comand_nano(f"nano {new_path}")

    def ctx_copy_path(self, _):
        items = self.explorer.selectedItems()
        if not items: return
        paths = [self.get_item_info(i)[1] for i in items]
        QApplication.clipboard().setText("\n".join(paths))
        self.sig_log.emit(f"{t('path_copied', self.config_mgr.language)}: {len(paths)} items")

    def ctx_rename(self, item):
        lang = self.config_mgr.language
        filename, new_path, _ = self.get_item_info(item)
        new_name, ok = QInputDialog.getText(self, t("rename", lang), f"{t('new_name', lang)} {filename}:", text=filename)
        if ok and new_name and new_name != filename:
            try:
                with self.ssh_mgr.lock: self.ssh_mgr.sftp.rename(new_path, posixpath.join(self.remote_path, new_name))
                self.sig_explorer.emit()
            except Exception as e:
                self.sig_msg.emit("error", t("error", lang), str(e))

    def ctx_move(self, _):
        items = self.explorer.selectedItems()
        if not items: return
        lang = self.config_mgr.language
        target_dir, ok = QInputDialog.getText(self, t("move", lang), t("move_to", lang), text=self.remote_path)
        if ok and target_dir and target_dir != self.remote_path:
            for item in items:
                filename, new_path, _ = self.get_item_info(item)
                try:
                    with self.ssh_mgr.lock: self.ssh_mgr.sftp.rename(new_path, posixpath.join(target_dir, filename))
                except Exception as e:
                    self.sig_msg.emit("error", t("error", lang), str(e))
            self.sig_explorer.emit()

    def _run_fs_cmd_and_refresh(self, cmd):
        """Roda um comando de sistema de arquivos (rm/tar/unzip...) numa thread
        e SÓ atualiza o explorer quando ele realmente termina.

        Antes, o comando era disparado e esquecido ('fire-and-forget'): ao
        soltar a referência do canal o paramiko podia fechá-lo no meio de uma
        operação longa, truncando um tar/rm de pasta grande. Aqui lemos a saída
        até o EOF (o que espera o comando acabar e drena a janela do canal, sem
        deadlock) antes de sinalizar o refresh."""
        lang = self.config_mgr.language
        def run():
            try:
                _, out, err = self.ssh_mgr.execute(cmd)
                out.read()  # lê até EOF: espera o comando terminar de fato
                err_txt = err.read().decode('utf-8', errors='replace').strip()
                if err_txt:
                    self.sig_msg.emit("error", t("error", lang), err_txt)
            except Exception as e:
                self.sig_msg.emit("error", t("error", lang), str(e))
            self.sig_explorer.emit()
        threading.Thread(target=run, daemon=True).start()

    def ctx_delete(self, _):
        lang = self.config_mgr.language
        items = self.explorer.selectedItems()
        if not items: return
        msg = (f"{t('confirm_del', lang)} '{self.get_item_info(items[0])[0]}'?\n{t('cannot_undo', lang)}"
               if len(items) == 1 else f"{t('confirm_del', lang)} {len(items)} items?\n{t('cannot_undo', lang)}")
        if QMessageBox.question(self, t("delete", lang), msg) == QMessageBox.StandardButton.Yes:
            for item in items:
                filename, new_path, item_type = self.get_item_info(item)
                try:
                    if item_type in ("Directory", t("directory", lang)):
                        # dir: rm -rf pode demorar; roda em thread e espera terminar
                        self._run_fs_cmd_and_refresh(f'rm -rf {backend.sh_quote(new_path)}')
                    else:
                        with self.ssh_mgr.lock: self.ssh_mgr.sftp.remove(new_path)
                except Exception as e:
                    self.sig_msg.emit("error", t("error", lang), f"{filename}: {str(e)}")
            QTimer.singleShot(500, self.sig_explorer.emit)

    def ctx_properties(self, item):
        lang = self.config_mgr.language
        filename, new_path, item_type = self.get_item_info(item)
        try:
            with self.ssh_mgr.lock: st = self.ssh_mgr.sftp.stat(new_path)
            info = (f"{t('name', lang)}: {filename}\nPath: {new_path}\n"
                    f"{t('type', lang)}: {item_type}\n{t('size', lang)}: {self.format_file_size(st.st_size)}\n"
                    f"{t('permissions', lang)}: {stat.filemode(st.st_mode)}\n"
                    f"Modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}")
            self.sig_msg.emit("info", t("properties", lang), info)
        except Exception as e:
            self.sig_msg.emit("error", t("error", lang), str(e))

    def ctx_compress(self, path, filename):
        parent = posixpath.dirname(path)
        self._run_fs_cmd_and_refresh(
            f'cd {backend.sh_quote(parent)} && '
            f'tar -czf {backend.sh_quote(filename + ".tar.gz")} {backend.sh_quote(filename)}')

    def ctx_extract(self, path, filename):
        parent = posixpath.dirname(path)
        if filename.endswith('.zip'):
            cmd = f'cd {backend.sh_quote(parent)} && unzip -o {backend.sh_quote(path)}'
        else:
            cmd = f'cd {backend.sh_quote(parent)} && tar -xzf {backend.sh_quote(path)}'
        self._run_fs_cmd_and_refresh(cmd)

    def shortcut_rename(self):
        items = self.explorer.selectedItems()
        if items: self.ctx_rename(items[0])

    def shortcut_delete(self):
        if self.explorer.selectedItems(): self.ctx_delete(None)

    def shortcut_mkdir(self):
        if not self.ssh_mgr.is_connected: return
        lang = self.config_mgr.language
        new_dir, ok = QInputDialog.getText(self, t("new_folder", lang), t("folder_name", lang))
        if ok and new_dir:
            try:
                with self.ssh_mgr.lock: self.ssh_mgr.sftp.mkdir(posixpath.join(self.remote_path, new_dir))
                self.sig_explorer.emit()
            except Exception as e:
                self.sig_msg.emit("error", t("error", lang), str(e))

    def shortcut_new_file(self):
        if not self.ssh_mgr.is_connected: return
        lang = self.config_mgr.language
        new_file, ok = QInputDialog.getText(self, "New File", "File name (e.g. main.py):")
        if ok and new_file:
            try:
                with self.ssh_mgr.lock:
                    with self.ssh_mgr.sftp.open(posixpath.join(self.remote_path, new_file), 'w') as f: f.write("")
                self.sig_explorer.emit()
            except Exception as e:
                self.sig_msg.emit("error", t("error", lang), str(e))

    def show_proc_context_menu(self, pos):
        item = self.proc_tree.itemAt(pos)
        if not item:
            return
        if not self._has_sudo:
            QMessageBox.information(self, "Permissão negada",
                                    "Apenas usuários com sudo ou root podem encerrar processos.")
            return
        lang = self.config_mgr.language
        menu = QMenu(self)
        a1 = QAction(qta.icon('fa5s.hand-paper', color='orange'),
                     f"{t('kill_term', lang)} {item.text(0)} ({item.text(4)})", self)
        a1.triggered.connect(lambda: self.kill_process(item.text(0), force=False))
        a2 = QAction(qta.icon('fa5s.skull', color='red'),
                     f"{t('kill_force', lang)} {item.text(0)} ({item.text(4)})", self)
        a2.triggered.connect(lambda: self.kill_process(item.text(0), force=True))
        menu.addAction(a1); menu.addAction(a2)
        menu.exec(self.proc_tree.viewport().mapToGlobal(pos))

    def kill_process(self, pid, force=False):
        if not self.ssh_mgr.is_connected: return
        lang = self.config_mgr.language
        sig = '-9' if force else '-15'
        reply = QMessageBox.question(
            self, "Kill Process",
            f"Kill PID {pid}?\n\nYes = kill normal  |  No = kill via sudo",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        if reply == QMessageBox.StandardButton.No:
            if not self.sudo_cache[0]:
                pwd, ok = QInputDialog.getText(self, "Sudo", t("enter_sudo", lang), QLineEdit.EchoMode.Password)
                if not ok: return
                self.sudo_cache[0] = pwd
            threading.Thread(target=self._kill_process_sudo, args=(pid, sig), daemon=True).start()
        else:
            threading.Thread(target=self._kill_process_plain, args=(pid, sig), daemon=True).start()

    def _kill_process_plain(self, pid, sig):
        """Executa o kill sem sudo numa thread para não travar a GUI."""
        lang = self.config_mgr.language
        try:
            _, stdout, _ = self.ssh_mgr.execute(f"kill {backend.sh_quote(sig)} {backend.sh_quote(pid)}")
            stdout.channel.recv_exit_status()  # espera o comando terminar
            self.sig_log.emit(f"[kill {sig} {pid}]")
        except Exception as e:
            self.sig_log.emit(f"{t('error_kill', lang)} {e}")

    def _kill_process_sudo(self, pid, sig):
        """Executa o kill via sudo numa thread para não travar a GUI."""
        lang = self.config_mgr.language
        try:
            stdin, _, stderr = self.ssh_mgr.execute(
                f"sudo -S kill {backend.sh_quote(sig)} {backend.sh_quote(pid)}")
            stdin.write(self.sudo_cache[0] + "\n"); stdin.flush()
            err = stderr.read().decode().strip()
            if "incorrect" in err.lower() or "wrong password" in err.lower():
                self.sudo_cache[0] = None
                self.sig_msg.emit("warn", "Sudo", "Senha sudo incorreta.")
            else:
                self.sig_log.emit(f"[sudo kill {sig} {pid}]")
        except Exception as e:
            self.sig_log.emit(f"{t('error_kill', lang)} {e}")

    def show_users_context_menu(self, pos):
        item = self.users_list.itemAt(pos)
        if not item:
            return
        if not self._has_sudo:
            QMessageBox.information(self, "Permissão negada",
                                    "Apenas usuários com sudo ou root podem desconectar sessões.")
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            user_name = data.get("user", "")
            tty = data.get("tty", "")
        else:
            user_info = item.text()
            user_name = user_info.split()[0]
            tty = user_info.split("(")[1].split(")")[0] if "(" in user_info else ""
        lang = self.config_mgr.language
        menu = QMenu(self)
        a = QAction(qta.icon('fa5s.user-slash', color='red'), f"{t('disconnect', lang)} {user_name}", self)
        a.triggered.connect(lambda: self.kill_user_session(user_name, tty))
        menu.addAction(a)
        menu.exec(self.users_list.viewport().mapToGlobal(pos))

    def kill_user_session(self, user, tty):
        if not self.ssh_mgr.is_connected: return
        lang = self.config_mgr.language
        reply = QMessageBox.question(
            self, t("confirm", lang),
            f"Desconectar sessão de {user} via sudo?\n\nYes = executar com sudo  |  No = cancelar",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self.sudo_cache[0]:
            pwd, ok = QInputDialog.getText(self, "Sudo", t("enter_sudo", lang), QLineEdit.EchoMode.Password)
            if not ok: return
            self.sudo_cache[0] = pwd
        cmd = (f"sudo -S pkill -9 -t {backend.sh_quote(tty.replace('/dev/', ''))}"
               if tty else f"sudo -S pkill -9 -u {backend.sh_quote(user)}")
        threading.Thread(target=self._kill_user_session_worker, args=(cmd, user), daemon=True).start()

    def _kill_user_session_worker(self, cmd, user):
        """Desconecta a sessão via sudo numa thread para não travar a GUI."""
        try:
            stdin, _, stderr = self.ssh_mgr.execute(cmd)
            stdin.write(self.sudo_cache[0] + "\n"); stdin.flush()
            err = stderr.read().decode().strip()
            if "incorrect" in err.lower() or "wrong password" in err.lower():
                self.sudo_cache[0] = None
                self.sig_msg.emit("warn", "Sudo", "Senha sudo incorreta.")
            else:
                self.sig_log.emit(f"[{cmd}]")
        except Exception as e:
            self.sig_log.emit(f"[erro ao desconectar {user}: {e}]")

    def _add_transfer_row(self, tid, filename, direction, spec=None):
        lang = self.config_mgr.language
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(3, 2, 3, 2)
        rl.setSpacing(6)

        icon_lbl = QLabel()
        color = '#4CAF50' if direction == 'up' else '#2196F3'
        icon_name = 'fa5s.arrow-up' if direction == 'up' else 'fa5s.arrow-down'
        icon_lbl.setPixmap(qta.icon(icon_name, color=color).pixmap(14, 14))

        disp = (filename[:28] + "\u2026") if len(filename) > 28 else filename
        lbl = QLabel(disp)
        lbl.setToolTip(filename)

        info = QLabel("")
        info.setMinimumWidth(96)
        info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        pbar = QProgressBar()
        pbar.setFixedHeight(10)
        pbar.setRange(0, 100)
        pbar.setTextVisible(False)
        pbar.setFixedWidth(90)

        btn_retry = QPushButton()
        btn_retry.setIcon(qta.icon('fa5s.redo', color='white'))
        btn_retry.setFixedSize(22, 22)
        btn_retry.setToolTip(t("transfer_retry", lang))
        btn_retry.hide()
        btn_retry.clicked.connect(lambda _=False, i=tid: self._retry_transfer(i))

        btn_cancel = QPushButton()
        btn_cancel.setIcon(qta.icon('fa5s.times', color='white'))
        btn_cancel.setFixedSize(22, 22)
        btn_cancel.setToolTip(t("transfer_cancel", lang))
        btn_cancel.clicked.connect(lambda _=False, i=tid: self._cancel_transfer(i))

        rl.addWidget(icon_lbl)
        rl.addWidget(lbl, 1)
        rl.addWidget(info)
        rl.addWidget(pbar)
        rl.addWidget(btn_retry)
        rl.addWidget(btn_cancel)

        old = self.active_transfers.get(tid)
        if old:   # mesmo nome enviado de novo: troca a linha antiga
            self._remove_transfer_row(tid)

        count = self._tp_list_layout.count()
        self._tp_list_layout.insertWidget(count - 1, row)

        self.active_transfers[tid] = {
            'pbar': pbar, 'row': row, 'info': info, 'retry': btn_retry,
            'cancel_btn': btn_cancel, 'cancel': False, 'state': 'running',
            'spec': spec, 'sample': (time.time(), 0), 'speed': 0.0,
        }
        self.transfer_panel.setVisible(True)
        self._update_transfer_header()
        return pbar

    def _remove_transfer_row(self, tid):
        self._last_progress_time.pop(tid, None)
        entry = self.active_transfers.pop(tid, None)
        if entry:
            row = entry.get('row')
            if row:
                row.setParent(None)
                row.deleteLater()
        self._update_transfer_header()
        if not self.active_transfers:
            self.transfer_panel.setVisible(False)

    def _cancel_transfer(self, tid):
        """Botão X: cancela uma transferência em andamento; se já terminou
        (falhou/cancelada) apenas remove a linha."""
        entry = self.active_transfers.get(tid)
        if not entry:
            return
        if entry['state'] == 'running':
            entry['cancel'] = True          # a thread aborta no próximo callback
            entry['cancel_btn'].setEnabled(False)
        else:
            self._remove_transfer_row(tid)

    def _mark_transfer_failed(self, tid, state):
        entry = self.active_transfers.get(tid)
        if not entry:
            return
        lang = self.config_mgr.language
        entry['state'] = state
        entry['cancel_btn'].setEnabled(True)
        entry['cancel_btn'].setToolTip(t("transfer_clear", lang))
        entry['retry'].setVisible(entry.get('spec') is not None)
        if state == 'cancelled':
            entry['info'].setText(t("transfer_cancelled", lang))
            entry['info'].setStyleSheet("color: #ffb000;")
        else:
            entry['info'].setText(t("transfer_failed", lang))
            entry['info'].setStyleSheet("color: #dc3545;")
        self._update_transfer_header()

    def _retry_transfer(self, tid):
        entry = self.active_transfers.get(tid)
        if not entry or entry['state'] == 'running' or not entry.get('spec'):
            return
        if not self.ssh_mgr.is_connected:
            return
        spec = entry['spec']
        entry.update(state='running', cancel=False, sample=(time.time(), 0), speed=0.0)
        entry['pbar'].setValue(0)
        entry['info'].setText("")
        entry['info'].setStyleSheet("")
        entry['retry'].hide()
        entry['cancel_btn'].setToolTip(t("transfer_cancel", self.config_mgr.language))
        self._update_transfer_header()
        if spec['kind'] == 'up':
            threading.Thread(target=self._upload_worker,
                             args=([(spec['local'], spec['remote'], tid)],), daemon=True).start()
        else:
            threading.Thread(target=self._sequential_download_thread,
                             args=([(spec['remote'], spec['local'], spec['is_dir'], tid)],),
                             daemon=True).start()

    def _update_transfer_header(self):
        n = sum(1 for e in self.active_transfers.values() if e['state'] == 'running')
        self.lbl_transfers.setText(f" {t('transfers', self.config_mgr.language)} ({n})")

    def _force_hide_transfer_panel(self):
        for tid in [i for i, e in self.active_transfers.items() if e['state'] != 'running']:
            self._remove_transfer_row(tid)
        self.transfer_panel.setVisible(False)

    def refresh_transfer_panel_theme(self):
        mode = self.config_mgr.theme.get("mode", "dark")
        card = "#ffffff" if mode == "light" else "#1e161e"
        self._tp_scroll.setStyleSheet(f"background: {card}; border: none;")
        self._tp_scroll.viewport().setStyleSheet(f"background: {card};")

    def sftp_progress_callback(self, transferred, total, transfer_id):
        now = time.time()
        if now - self._last_progress_time.get(transfer_id, 0) >= 0.15 or transferred >= total:
            self._last_progress_time[transfer_id] = now
            self.sig_transfer_progress.emit(transferred, total, transfer_id)

    def _transfer_cb(self, tid, done, total):
        """Callback do SFTP (thread de transferência): aborta se o usuário
        cancelou; senão repassa o progresso (com throttle)."""
        entry = self.active_transfers.get(tid)
        if entry and entry.get('cancel'):
            raise backend.TransferCancelled()
        self.sftp_progress_callback(done, total, tid)

    def create_upload_item(self, local_path):
        filename = os.path.basename(local_path)
        transfer_id = f"up_{filename}"
        remote = posixpath.join(self.remote_path, filename)
        self._add_transfer_row(transfer_id, filename, "up",
                               spec={'kind': 'up', 'local': local_path, 'remote': remote})
        return transfer_id, remote

    def upload_file_dialog(self):
        if not self.ssh_mgr.is_connected: return
        files, _ = QFileDialog.getOpenFileNames(self, t("select_files", self.config_mgr.language))
        self._start_uploads(files)

    def upload_dir_dialog(self):
        if not self.ssh_mgr.is_connected: return
        d = QFileDialog.getExistingDirectory(self, t("select_folder", self.config_mgr.language))
        self._start_uploads([d] if d else [])

    def on_drop_files(self, files):
        if not self.ssh_mgr.is_connected: return
        self._start_uploads(files)

    def _start_uploads(self, paths):
        if not paths:
            return
        jobs = []
        for f in paths:
            tid, remote = self.create_upload_item(f)
            jobs.append((f, remote, tid))
        threading.Thread(target=self._upload_worker, args=(jobs,), daemon=True).start()

    def _upload_worker(self, jobs):
        sudo_pwd = [None]
        for local_path, remote_file, tid in jobs:
            filename = os.path.basename(local_path)
            state = "done"
            try:
                cb = lambda done, tot, tid=tid: self._transfer_cb(tid, done, tot)
                backend.upload_recursive(self.ssh_mgr, local_path, remote_file, sudo_pwd,
                                         self._get_sudo_pwd, progress_cb=cb)
                self.sig_notify.emit(t("upload_done", self.config_mgr.language), filename)
            except backend.TransferCancelled:
                state = "cancelled"
                if os.path.isfile(local_path):   # não deixa arquivo pela metade no servidor
                    try:
                        with self.ssh_mgr.lock:
                            self.ssh_mgr.sftp.remove(remote_file)
                    except Exception:
                        pass
            except Exception:
                state = "failed"
            if state == "done":
                self.sig_transfer_done.emit(tid)
            else:
                self.sig_transfer_failed.emit(tid, state)
        time.sleep(0.5)
        self.sig_explorer.emit()

    def download_file_dialog(self):
        items = self.explorer.selectedItems()
        if not items: return
        local_dir = QFileDialog.getExistingDirectory(self, t("destination", self.config_mgr.language))
        if not local_dir: return

        downloads = []
        for item in items:
            filename, remote_path, item_type = self.get_item_info(item)
            is_dir = item_type in (t("directory", self.config_mgr.language), "Directory")
            tid = f"dl_{filename}_{id(item)}"
            local_path = os.path.join(local_dir, filename)
            self._add_transfer_row(tid, filename, "down",
                                   spec={'kind': 'down', 'remote': remote_path,
                                         'local': local_path, 'is_dir': is_dir})
            downloads.append((remote_path, local_path, is_dir, tid))

        threading.Thread(target=self._sequential_download_thread, args=(downloads,), daemon=True).start()

    def _sequential_download_thread(self, downloads):
        for remote_path, local_path, is_dir, transfer_id in downloads:
            filename = os.path.basename(local_path)
            state = "done"
            try:
                if is_dir:
                    total_bytes = backend.count_remote_size(self.ssh_mgr, remote_path)
                    transferred_ref = [0]

                    def _cb(done, total, tid=transfer_id):
                        self._transfer_cb(tid, done, total)

                    if total_bytes > 0:
                        backend.download_directory_with_progress(
                            self.ssh_mgr, remote_path, local_path,
                            total_bytes, transferred_ref, progress_cb=_cb)
                    else:
                        backend.download_directory_recursive(self.ssh_mgr, remote_path, local_path)
                else:
                    def _cb_file(t2, tot, tid=transfer_id):
                        self._transfer_cb(tid, t2, tot)
                    with self.ssh_mgr.lock:
                        self.ssh_mgr.sftp.get(remote_path, local_path, callback=_cb_file)
                self.sig_notify.emit(t("download_done", self.config_mgr.language), filename)
            except backend.TransferCancelled:
                state = "cancelled"
                if not is_dir and os.path.isfile(local_path):   # descarta o parcial
                    try:
                        os.remove(local_path)
                    except OSError:
                        pass
            except Exception:
                state = "failed"
            if state == "done":
                self.sig_transfer_done.emit(transfer_id)
            else:
                self.sig_transfer_failed.emit(transfer_id, state)
        time.sleep(0.5)
        self.sig_explorer.emit()

    def handle_drag_out(self, item):
        items = self.explorer.selectedItems()
        urls  = []
        lang  = self.config_mgr.language
        try:
            for i in items:
                filename, remote_path, item_type = self.get_item_info(i)
                if item_type not in ("File", t("file", lang)): continue
                with self.ssh_mgr.lock:
                    size = self.ssh_mgr.sftp.stat(remote_path).st_size
                    if size > 50 * 1024 * 1024:
                        self.sig_msg.emit("warn", t("warning", lang), f"{filename}: {t('file_large', lang)}"); continue
                    local_path = os.path.join(tempfile.gettempdir(), filename)
                    self.ssh_mgr.sftp.get(remote_path, local_path)
                    urls.append(QUrl.fromLocalFile(local_path))
            if urls:
                drag = QDrag(self.explorer); mime = QMimeData(); mime.setUrls(urls)
                drag.setMimeData(mime); drag.exec(Qt.DropAction.CopyAction)
        except Exception:
            pass

    def _handle_remote_move(self, source_items, target_item):
        if not self.ssh_mgr.is_connected: return
        lang = self.config_mgr.language
        if self._item_kind(target_item) != "Directory": return
        target_name = target_item.text(0)
        target_path = posixpath.join(self.remote_path, target_name)
        sources = []
        for item in source_items:
            fname, fpath, _ = self.get_item_info(item)
            if fname != target_name:
                sources.append(fpath)
        if sources:
            threading.Thread(target=self._execute_remote_move, args=(sources, target_path), daemon=True).start()

    def _execute_remote_move(self, source_paths, target_dir):
        try:
            for src in source_paths:
                _, _, err = self.ssh_mgr.execute(
                    f'mv -f {backend.sh_quote(src)} {backend.sh_quote(target_dir + "/")}')
                err_txt = err.read().decode().strip()
                if err_txt:
                    gui_single_shot(0, lambda e=err_txt: self.sig_msg.emit("error", "Move", e))
            gui_single_shot(500, self.sig_explorer.emit)
        except Exception as e:
            gui_single_shot(0, lambda: self.sig_msg.emit("error", "Move", str(e)))

    def load_snippets_ui(self):
        self.list_snippets.clear()
        if not self.config_mgr.snippets:
            self.config_mgr.snippets = {
                "System Info": "uname -a", "Disk Usage": "df -h",
                "Update Ubuntu/Debian": "sudo apt update && sudo apt upgrade -y",
                "Clear RAM Cache": "sudo sync; echo 3 | sudo tee /proc/sys/vm/drop_caches",
                "List Docker Containers": "docker ps -a",
                "Network Open Ports": "netstat -tulpn | grep LISTEN"
            }
            self.config_mgr.save_config()
        for name, cmd in self.config_mgr.snippets.items():
            self.list_snippets.addItem(f"{name}  ➡️  {cmd}")

    def add_snippet(self):
        name = self.input_snip_name.text().strip(); cmd = self.input_snip_cmd.text().strip()
        if name and cmd:
            self.config_mgr.snippets[name] = cmd; self.config_mgr.save_config()
            self.input_snip_name.clear(); self.input_snip_cmd.clear(); self.load_snippets_ui()

    def del_snippet(self):
        sel = self.list_snippets.currentItem()
        if sel:
            name = sel.text().split("  ➡️  ")[0]
            if name in self.config_mgr.snippets:
                del self.config_mgr.snippets[name]; self.config_mgr.save_config(); self.load_snippets_ui()

    def _expand_snippet_vars(self, cmd):
        vars_needed = list(dict.fromkeys(re.findall(r'\{(\w+)\}', cmd)))
        if not vars_needed:
            return cmd
        for var in vars_needed:
            val, ok = QInputDialog.getText(self, "Snippet", f"Value for {{{var}}}:")
            if not ok:
                return None
            cmd = cmd.replace(f'{{{var}}}', val)
        return cmd

    def run_selected_snippet(self):
        sel = self.list_snippets.currentItem()
        if sel and self.ssh_mgr.is_connected and self.ssh_mgr.shell:
            cmd = sel.text().split("  ➡️  ")[1]
            cmd = self._expand_snippet_vars(cmd)
            if cmd is None:
                return
            self.output.send_text(cmd + "\r")
            self.sig_log.emit(f"[Snippet: {cmd}]")

    def show_screens_manager(self):
        lang = self.config_mgr.language
        if not self.ssh_mgr.is_connected:
            QMessageBox.warning(self, t("warning", lang), t("connect_first", lang)); return
        self.screens_win = ScreensManagerDialog(self); self.screens_win.show()

    def show_env_list(self):
        lang = self.config_mgr.language
        if not self.ssh_mgr.is_connected:
            QMessageBox.warning(self, t("warning", lang), t("connect_first", lang)); return
        self.env_win = EnvManagerDialog(self); self.env_win.show()

    def show_search_dialog(self):
        if not self.ssh_mgr.is_connected: return
        self.search_dialog = AdvancedSearchDialog(self); self.search_dialog.show()

    def show_favorites_menu(self):
        lang  = self.config_mgr.language
        favs  = self.config_mgr.favorites
        ic    = self._menu_icon_color()
        menu  = QMenu(self)
        is_fav = self.remote_path in favs.values()
        if is_fav:
            a = QAction(qta.icon('fa5s.star', color='gold'), t("remove_from_favorites", lang), self)
            a.triggered.connect(self._remove_current_favorite)
        else:
            a = QAction(qta.icon('fa5s.star', color=ic), t("add_to_favorites", lang), self)
            a.triggered.connect(self._add_current_favorite)
        menu.addAction(a)
        if favs:
            menu.addSeparator()
            for name, path in favs.items():
                fa = QAction(qta.icon('fa5s.folder', color='#eccb5b'), f"{name}  →  {path}", self)
                fa.triggered.connect(lambda checked, p=path: self._navigate_to_favorite(p))
                menu.addAction(fa)
            menu.addSeparator()
            ac = QAction(qta.icon('fa5s.trash', color='#dc3545'), "Clear all favorites", self)
            ac.triggered.connect(self._clear_all_favorites)
            menu.addAction(ac)
        else:
            menu.addSeparator()
            empty = QAction(t("no_favorites", lang), self); empty.setEnabled(False)
            menu.addAction(empty)
        menu.exec(self.btn_favorites.mapToGlobal(self.btn_favorites.rect().bottomLeft()))

    def _add_current_favorite(self):
        name = posixpath.basename(self.remote_path) or self.remote_path
        base_name = name; counter = 1
        while name in self.config_mgr.favorites:
            name = f"{base_name}_{counter}"; counter += 1
        self.config_mgr.favorites[name] = self.remote_path
        self.config_mgr.save_config()
        self.sig_log.emit(f"[{t('add_to_favorites', self.config_mgr.language)}: {self.remote_path}]")
        self._update_fav_btn_icon()

    def _remove_current_favorite(self):
        favs = self.config_mgr.favorites
        for k in [k for k, v in favs.items() if v == self.remote_path]: del favs[k]
        self.config_mgr.save_config(); self._update_fav_btn_icon()

    def _clear_all_favorites(self):
        self.config_mgr.favorites.clear(); self.config_mgr.save_config(); self._update_fav_btn_icon()

    def _navigate_to_favorite(self, path):
        if not self.ssh_mgr.is_connected: return
        try:
            with self.ssh_mgr.lock: self.ssh_mgr.sftp.chdir(path); self.remote_path = path
            self.sig_explorer.emit()
        except Exception as e:
            self.sig_msg.emit("error", t("error", self.config_mgr.language), str(e))

    def _update_fav_btn_icon(self):
        is_fav = self.remote_path in self.config_mgr.favorites.values()
        self.btn_favorites.setIcon(qta.icon('fa5s.star', color='gold' if is_fav else 'white'))
