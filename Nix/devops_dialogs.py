import os, re, stat, socket, threading, time, posixpath, io

import paramiko
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView, QMessageBox,
    QPlainTextEdit, QFrame, QCheckBox, QApplication, QFileDialog,
    QProgressBar, QListWidget, QAbstractItemView, QWidget, QScrollArea,
    QSizePolicy, QInputDialog, QSplitter, QTabWidget, QTreeWidget,
    QTreeWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QBrush

import qtawesome as qta
from i18n import t

class TunnelManagerDialog(QDialog):
    """Visual SSH tunnel manager — local, remote and dynamic (SOCKS5)."""

    def __init__(self, parent, ssh_mgr, lang="en"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self._tunnels = {}

        self.setWindowTitle("SSH Tunnel Manager")
        self.resize(800, 480)
        layout = QVBoxLayout(self)

        form = QHBoxLayout()
        self.combo_type = QComboBox()
        self.combo_type.addItems(["Local", "Remote", "Dynamic (SOCKS5)"])
        self.combo_type.setFixedWidth(160)
        self.combo_type.currentIndexChanged.connect(self._on_type_change)

        self.inp_local_port  = QLineEdit(); self.inp_local_port.setPlaceholderText("Local port"); self.inp_local_port.setFixedWidth(100)
        self.inp_remote_host = QLineEdit(); self.inp_remote_host.setPlaceholderText("Remote host (e.g. 127.0.0.1)"); self.inp_remote_host.setFixedWidth(160)
        self.inp_remote_port = QLineEdit(); self.inp_remote_port.setPlaceholderText("Remote port"); self.inp_remote_port.setFixedWidth(100)

        btn_add = QPushButton("Add Tunnel")
        btn_add.setIcon(qta.icon('fa5s.plus', color='white'))
        btn_add.clicked.connect(self._add_tunnel)

        for w in [QLabel("Type:"), self.combo_type, QLabel("Local port:"), self.inp_local_port,
                  QLabel("→ Remote:"), self.inp_remote_host, QLabel(":"), self.inp_remote_port, btn_add]:
            form.addWidget(w)
        layout.addLayout(form)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["Type", "Local Port", "Remote Host", "Remote Port", "Status"])
        self.tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.tbl)

        btns = QHBoxLayout()
        btn_stop = QPushButton("Stop Selected")
        btn_stop.setIcon(qta.icon('fa5s.stop', color='#dc3545'))
        btn_stop.clicked.connect(self._stop_selected)
        btn_remove = QPushButton("Remove")
        btn_remove.setIcon(qta.icon('fa5s.trash', color='#dc3545'))
        btn_remove.clicked.connect(self._remove_selected)
        btn_close = QPushButton("Close"); btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_stop); btns.addWidget(btn_remove); btns.addStretch(); btns.addWidget(btn_close)
        layout.addLayout(btns)

    def _on_type_change(self, idx):
        is_socks = (idx == 2)
        self.inp_remote_host.setEnabled(not is_socks)
        self.inp_remote_port.setEnabled(not is_socks)

    def _add_tunnel(self):
        try:
            local_port = int(self.inp_local_port.text())
        except ValueError:
            QMessageBox.warning(self, "Error", "Enter a valid local port."); return

        ttype        = self.combo_type.currentText()
        remote_host  = self.inp_remote_host.text().strip() or "127.0.0.1"
        try:
            remote_port = int(self.inp_remote_port.text()) if self.inp_remote_port.text().strip() else local_port
        except ValueError:
            remote_port = local_port

        tid = f"{ttype}:{local_port}"
        if tid in self._tunnels:
            QMessageBox.warning(self, "Error", f"Tunnel on port {local_port} already exists."); return

        stop_ev = threading.Event()
        target_fn = {
            "Local":            self._run_local_tunnel,
            "Remote":           self._run_remote_tunnel,
            "Dynamic (SOCKS5)": self._run_socks5_tunnel,
        }[ttype]
        args = (local_port, remote_host, remote_port, stop_ev) if ttype != "Dynamic (SOCKS5)" else (local_port, stop_ev)
        t_thread = threading.Thread(target=target_fn, args=args, daemon=True)

        row = self.tbl.rowCount()
        self.tbl.insertRow(row)
        for col, val in enumerate([ttype, str(local_port),
                                    remote_host if ttype != "Dynamic (SOCKS5)" else "-",
                                    str(remote_port) if ttype != "Dynamic (SOCKS5)" else "-"]):
            self.tbl.setItem(row, col, QTableWidgetItem(val))
        status_item = QTableWidgetItem("Starting…")
        self.tbl.setItem(row, 4, status_item)

        self._tunnels[tid] = {'stop': stop_ev, 'thread': t_thread, 'status_item': status_item}
        t_thread.start()
        QTimer.singleShot(600, lambda: self._check_status(tid))

    def _check_status(self, tid):
        info = self._tunnels.get(tid)
        if not info: return
        if info['thread'].is_alive():
            info['status_item'].setText("● Active")
            info['status_item'].setForeground(QBrush(QColor("#28a745")))
        else:
            info['status_item'].setText("● Failed")
            info['status_item'].setForeground(QBrush(QColor("#dc3545")))

    def _run_local_tunnel(self, local_port, remote_host, remote_port, stop_ev):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(('127.0.0.1', local_port)); srv.listen(5); srv.settimeout(1.0)
            while not stop_ev.is_set():
                try:
                    client_sock, _ = srv.accept()
                except socket.timeout:
                    continue
                try:
                    ch = self.ssh_mgr.client.get_transport().open_channel(
                        'direct-tcpip', (remote_host, remote_port), ('127.0.0.1', local_port))
                    threading.Thread(target=self._fwd2, args=(client_sock, ch), daemon=True).start()
                except Exception:
                    client_sock.close()
            srv.close()
        except Exception:
            pass

    def _run_remote_tunnel(self, local_port, remote_host, remote_port, stop_ev):
        try:
            transport = self.ssh_mgr.client.get_transport()
            transport.request_port_forward('', remote_port)
            while not stop_ev.is_set():
                ch = transport.accept(1.0)
                if ch is None: continue
                try:
                    local_sock = socket.create_connection((remote_host, local_port))
                    threading.Thread(target=self._fwd2, args=(ch, local_sock), daemon=True).start()
                except Exception:
                    ch.close()
            transport.cancel_port_forward('', remote_port)
        except Exception:
            pass

    def _run_socks5_tunnel(self, local_port, stop_ev):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(('127.0.0.1', local_port)); srv.listen(5); srv.settimeout(1.0)
            while not stop_ev.is_set():
                try:
                    client_sock, _ = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._handle_socks5, args=(client_sock,), daemon=True).start()
            srv.close()
        except Exception:
            pass

    def _handle_socks5(self, sock):
        try:
            hdr = sock.recv(2)
            if len(hdr) < 2: return
            sock.recv(hdr[1]); sock.send(b'\x05\x00')
            req = sock.recv(4)
            if len(req) < 4 or req[1] != 1: return
            atype = req[3]
            if atype == 1:
                addr = socket.inet_ntoa(sock.recv(4))
            elif atype == 3:
                addr = sock.recv(sock.recv(1)[0]).decode()
            else:
                return
            port = int.from_bytes(sock.recv(2), 'big')
            try:
                ch = self.ssh_mgr.client.get_transport().open_channel(
                    'direct-tcpip', (addr, port), ('127.0.0.1', 0))
                sock.send(b'\x05\x00\x00\x01' + b'\x00' * 4 + b'\x00\x00')
                threading.Thread(target=self._fwd2, args=(sock, ch), daemon=True).start()
            except Exception:
                sock.send(b'\x05\x05\x00\x01' + b'\x00' * 4 + b'\x00\x00')
                sock.close()
        except Exception:
            try: sock.close()
            except: pass

    def _fwd2(self, a, b):
        def _one(src, dst):
            try:
                while True:
                    d = src.recv(4096)
                    if not d: break
                    dst.sendall(d)
            except Exception:
                pass
            finally:
                for x in (src, dst):
                    try: x.close()
                    except: pass
        threading.Thread(target=_one, args=(a, b), daemon=True).start()
        _one(b, a)

    def _stop_selected(self):
        row = self.tbl.currentRow()
        if row < 0: return
        tid = f"{self.tbl.item(row, 0).text()}:{self.tbl.item(row, 1).text()}"
        info = self._tunnels.get(tid)
        if info:
            info['stop'].set()
            info['status_item'].setText("⏹ Stopped")

    def _remove_selected(self):
        row = self.tbl.currentRow()
        if row < 0: return
        tid = f"{self.tbl.item(row, 0).text()}:{self.tbl.item(row, 1).text()}"
        info = self._tunnels.pop(tid, None)
        if info: info['stop'].set()
        self.tbl.removeRow(row)

    def closeEvent(self, event):
        for info in self._tunnels.values():
            info['stop'].set()
        super().closeEvent(event)

class CronEditorDialog(QDialog):
    def __init__(self, parent, ssh_mgr, lang="en"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self.setWindowTitle("Cron Job Editor")
        self.resize(960, 560)
        layout = QVBoxLayout(self)

        tb = QHBoxLayout()
        for lbl, icon, fn in [
            ("Refresh", 'fa5s.sync',        self._load),
            ("Add Job", 'fa5s.plus',        self._add_row),
            ("Delete",  'fa5s.trash',       self._delete_selected),
            ("Save",    'fa5s.save',        self._save),
        ]:
            btn = QPushButton(lbl); btn.setIcon(qta.icon(icon, color='white')); btn.clicked.connect(fn); tb.addWidget(btn)
        tb.addStretch()
        layout.addLayout(tb)

        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(["Minute", "Hour", "Day", "Month", "Weekday", "Command"])
        self.tbl.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for i in range(5): self.tbl.setColumnWidth(i, 80)
        layout.addWidget(self.tbl)

        hl = QHBoxLayout()
        hl.addWidget(QLabel("Preset:"))
        self.combo_preset = QComboBox()
        self.combo_preset.addItems([
            "— select —", "@reboot", "@hourly", "@daily", "@weekly", "@monthly",
            "Every 5 min", "Every 15 min", "Every hour", "Daily at 02:00", "Daily at midnight",
        ])
        self.combo_preset.currentTextChanged.connect(self._apply_preset)
        hl.addWidget(self.combo_preset); hl.addStretch()
        layout.addLayout(hl)

        self._load()

    _PRESETS = {
        "@reboot":          ("@reboot", "", "", "", ""),
        "@hourly":          ("0", "*", "*", "*", "*"),
        "@daily":           ("0", "0", "*", "*", "*"),
        "@weekly":          ("0", "0", "*", "*", "0"),
        "@monthly":         ("0", "0", "1", "*", "*"),
        "Every 5 min":      ("*/5", "*", "*", "*", "*"),
        "Every 15 min":     ("*/15", "*", "*", "*", "*"),
        "Every hour":       ("0", "*", "*", "*", "*"),
        "Daily at 02:00":   ("0", "2", "*", "*", "*"),
        "Daily at midnight":("0", "0", "*", "*", "*"),
    }

    def _load(self):
        try:
            _, out, _ = self.ssh_mgr.execute("crontab -l 2>/dev/null")
            lines = out.read().decode().splitlines()
            self.tbl.setRowCount(0)
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'): continue
                if line.startswith('@'):
                    parts = line.split(None, 1)
                    self._add_row(parts[0], "", "", "", "", parts[1] if len(parts) > 1 else "")
                else:
                    parts = line.split(None, 5)
                    if len(parts) >= 6:
                        self._add_row(*parts[:5], parts[5])
                    elif len(parts) == 5:
                        self._add_row(*parts[:5], "")
        except Exception:
            pass

    def _add_row(self, m="*", h="*", dom="*", month="*", dow="*", cmd=""):
        r = self.tbl.rowCount(); self.tbl.insertRow(r)
        for col, val in enumerate([m, h, dom, month, dow, cmd]):
            self.tbl.setItem(r, col, QTableWidgetItem(str(val)))

    def _delete_selected(self):
        for r in sorted({i.row() for i in self.tbl.selectedItems()}, reverse=True):
            self.tbl.removeRow(r)

    def _apply_preset(self, text):
        row = self.tbl.currentRow()
        if row < 0 or text not in self._PRESETS: return
        for col, val in enumerate(self._PRESETS[text]):
            if val:
                self.tbl.setItem(row, col, QTableWidgetItem(val))

    def _save(self):
        lines = []
        for r in range(self.tbl.rowCount()):
            cols = [(self.tbl.item(r, c) or QTableWidgetItem("")).text().strip() for c in range(6)]
            m, h, dom, month, dow, cmd = cols
            if not cmd: continue
            lines.append(f"{m} {cmd}" if m.startswith('@') else f"{m} {h} {dom} {month} {dow} {cmd}")
        try:
            stdin, _, err = self.ssh_mgr.execute("crontab -")
            stdin.write(("\n".join(lines) + "\n").encode())
            stdin.channel.shutdown_write()
            error = err.read().decode().strip()
            if error:
                QMessageBox.warning(self, "Warning", error)
            else:
                QMessageBox.information(self, "Success", "Crontab saved.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

class ServiceManagerDialog(QDialog):
    _sig_rows = pyqtSignal(list)

    def __init__(self, parent, ssh_mgr, lang="en"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self._all_rows = []
        self.setWindowTitle("Service Manager (systemd)")
        self.resize(960, 600)
        layout = QVBoxLayout(self)

        tb = QHBoxLayout()
        self.filter_inp = QLineEdit(); self.filter_inp.setPlaceholderText("Filter services…"); self.filter_inp.textChanged.connect(self._filter)
        tb.addWidget(self.filter_inp, 1)
        for lbl, icon, act in [
            ("Start",   'fa5s.play',         "start"),
            ("Stop",    'fa5s.stop',         "stop"),
            ("Restart", 'fa5s.redo',         "restart"),
            ("Enable",  'fa5s.toggle-on',    "enable"),
            ("Disable", 'fa5s.toggle-off',   "disable"),
            ("Refresh", 'fa5s.sync',         None),
        ]:
            btn = QPushButton(lbl); btn.setIcon(qta.icon(icon, color='white'))
            if act:
                btn.clicked.connect(lambda _, a=act: self._action(a))
            else:
                btn.clicked.connect(self._load)
            tb.addWidget(btn)
        layout.addLayout(tb)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["Service", "Sub-state", "Active", "Enabled"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 4): self.tbl.setColumnWidth(i, 100)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.tbl)

        self.log_area = QPlainTextEdit(); self.log_area.setReadOnly(True); self.log_area.setMaximumHeight(110)
        layout.addWidget(QLabel("Command output:")); layout.addWidget(self.log_area)

        self._sig_rows.connect(self._populate)
        self._load()

    def _load(self):
        self.log_area.appendPlainText("Loading services…")
        threading.Thread(target=self._load_thread, daemon=True).start()

    def _load_thread(self):
        try:
            _, o1, _ = self.ssh_mgr.execute(
                "systemctl list-units --type=service --all --no-pager --no-legend 2>/dev/null | head -250")
            _, o2, _ = self.ssh_mgr.execute(
                "systemctl list-unit-files --type=service --no-pager --no-legend 2>/dev/null | head -250")
            enabled_map = {}
            for ln in o2.read().decode().splitlines():
                parts = ln.split()
                if len(parts) >= 2: enabled_map[parts[0]] = parts[1]
            rows = []
            for ln in o1.read().decode().splitlines():
                parts = ln.split(None, 4)
                if len(parts) >= 3:
                    name   = parts[0]
                    active = parts[2] if len(parts) > 2 else ""
                    sub    = parts[3] if len(parts) > 3 else ""
                    en     = enabled_map.get(name, "?")
                    rows.append((name, sub, active, en))
            self._all_rows = rows
            self._sig_rows.emit(rows)
        except Exception as e:
            QTimer.singleShot(0, lambda: self.log_area.appendPlainText(f"Error: {e}"))

    def _populate(self, rows):
        text = self.filter_inp.text().lower()
        self.tbl.setRowCount(0)
        for name, sub, active, en in rows:
            if text and text not in name.lower(): continue
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            self.tbl.setItem(r, 0, QTableWidgetItem(name))
            si = QTableWidgetItem(sub)
            if sub == "running":  si.setForeground(QBrush(QColor("#28a745")))
            elif sub == "failed": si.setForeground(QBrush(QColor("#dc3545")))
            self.tbl.setItem(r, 1, si)
            self.tbl.setItem(r, 2, QTableWidgetItem(active))
            ei = QTableWidgetItem(en)
            if en == "enabled":   ei.setForeground(QBrush(QColor("#28a745")))
            elif en == "disabled":ei.setForeground(QBrush(QColor("#dc3545")))
            self.tbl.setItem(r, 3, ei)
        self.log_area.appendPlainText(f"Loaded {self.tbl.rowCount()} services.")

    def _filter(self, text):
        self._populate(self._all_rows)

    def _action(self, action):
        row = self.tbl.currentRow()
        if row < 0: QMessageBox.warning(self, "No selection", "Select a service."); return
        svc = self.tbl.item(row, 0).text()
        threading.Thread(target=self._run_action, args=(action, svc), daemon=True).start()

    def _run_action(self, action, svc):
        try:
            _, out, err = self.ssh_mgr.execute(f"sudo -n systemctl {action} {svc} 2>&1")
            output = (out.read() + err.read()).decode('utf-8', errors='replace').strip()
            QTimer.singleShot(0, lambda: self.log_area.appendPlainText(f"$ systemctl {action} {svc}\n{output}"))
            QTimer.singleShot(1500, self._load)
        except Exception as e:
            QTimer.singleShot(0, lambda: self.log_area.appendPlainText(f"Error: {e}"))

class LogViewerDialog(QDialog):
    _sig_line = pyqtSignal(str)

    def __init__(self, parent, ssh_mgr, lang="en", initial_path="/var/log/syslog"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self._stop_ev = threading.Event()
        self._channel = None
        self._filter_re = None

        self.setWindowTitle("Log Viewer — Live Tail")
        self.resize(1050, 680)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.path_inp = QLineEdit(initial_path); self.path_inp.setPlaceholderText("/var/log/syslog")
        self.btn_tail = QPushButton("▶ Start Tail"); self.btn_tail.setCheckable(True)
        self.btn_tail.clicked.connect(self._toggle_tail)
        self.chk_scroll = QCheckBox("Auto-scroll"); self.chk_scroll.setChecked(True)
        top.addWidget(QLabel("Log:")); top.addWidget(self.path_inp, 1)
        top.addWidget(self.btn_tail); top.addWidget(self.chk_scroll)
        layout.addLayout(top)

        flt = QHBoxLayout()
        self.filter_inp = QLineEdit(); self.filter_inp.setPlaceholderText("Regex filter (e.g. ERROR|WARN)…")
        self.filter_inp.textChanged.connect(self._update_filter)
        btn_clear = QPushButton("Clear log"); btn_clear.clicked.connect(self._clear_log)
        flt.addWidget(QLabel("Filter:")); flt.addWidget(self.filter_inp, 1); flt.addWidget(btn_clear)
        layout.addLayout(flt)

        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumBlockCount(5000)
        self.log_area.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_area)

        self.lbl_status = QLabel("Ready — enter a log path and click Start Tail.")
        layout.addWidget(self.lbl_status)

        self._sig_line.connect(self._append_line)

    def _clear_log(self):
        self.log_area.clear()

    def _update_filter(self, text):
        self._filter_re = re.compile(text, re.IGNORECASE) if text.strip() else None

    def _toggle_tail(self, checked):
        if checked:
            self._stop_ev.clear()
            self.btn_tail.setText("⏹ Stop Tail")
            self.lbl_status.setText(f"Tailing: {self.path_inp.text()}")
            threading.Thread(target=self._tail_thread, daemon=True).start()
        else:
            self._stop_ev.set()
            if self._channel:
                try: self._channel.close()
                except: pass
            self.btn_tail.setText("▶ Start Tail")
            self.lbl_status.setText("Stopped.")

    def _tail_thread(self):
        path = self.path_inp.text().strip()
        try:
            transport = self.ssh_mgr.client.get_transport()
            self._channel = transport.open_session()
            self._channel.exec_command(f"tail -n 200 -f -- {path}")
            buf = ""
            while not self._stop_ev.is_set():
                if self._channel.recv_ready():
                    buf += self._channel.recv(4096).decode('utf-8', errors='replace')
                    lines = buf.split('\n')
                    buf = lines[-1]
                    for ln in lines[:-1]:
                        if ln: self._sig_line.emit(ln)
                elif self._channel.exit_status_ready():
                    break
                else:
                    time.sleep(0.05)
        except Exception as e:
            self._sig_line.emit(f"[Error: {e}]")
        finally:
            QTimer.singleShot(0, lambda: self.btn_tail.setChecked(False))
            QTimer.singleShot(0, lambda: self._toggle_tail(False))

    def _append_line(self, line):
        if self._filter_re and not self._filter_re.search(line):
            return
        upper = line.upper()
        fmt = QTextCharFormat()
        if any(w in upper for w in ("ERROR", "CRIT", "FATAL", "FAIL")):
            fmt.setForeground(QColor("#ff5555"))
        elif any(w in upper for w in ("WARN",)):
            fmt.setForeground(QColor("#ffb86c"))
        elif any(w in upper for w in ("INFO", "NOTICE", "SUCCESS")):
            fmt.setForeground(QColor("#50fa7b"))
        else:
            fmt.setForeground(QColor("#f8f8f2"))
        cur = self.log_area.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(line + "\n", fmt)
        if self.chk_scroll.isChecked():
            self.log_area.verticalScrollBar().setValue(
                self.log_area.verticalScrollBar().maximum())

    def closeEvent(self, event):
        self._stop_ev.set()
        if self._channel:
            try: self._channel.close()
            except: pass
        super().closeEvent(event)

class BatchExecDialog(QDialog):
    _sig_result = pyqtSignal(str)

    def __init__(self, parent, connected_tabs):
        """connected_tabs: list of (title: str, tab: ConnectionTab)"""
        super().__init__(parent)
        self.connected_tabs = connected_tabs
        self.setWindowTitle("Batch / Cluster Execution")
        self.resize(720, 540)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Select servers (hold Ctrl for multi-select):"))
        self.srv_list = QListWidget()
        self.srv_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for title, _ in connected_tabs:
            item = self.srv_list.addItem(title)
        for i in range(self.srv_list.count()):
            self.srv_list.item(i).setSelected(True)
        self.srv_list.setMaximumHeight(140)
        layout.addWidget(self.srv_list)

        layout.addWidget(QLabel("Command to execute on all selected sessions:"))
        self.cmd_inp = QLineEdit(); self.cmd_inp.setPlaceholderText("e.g. sudo apt update && sudo apt upgrade -y")
        layout.addWidget(self.cmd_inp)

        self.chk_confirm = QCheckBox("Confirm before executing"); self.chk_confirm.setChecked(True)
        layout.addWidget(self.chk_confirm)

        btns = QHBoxLayout()
        btn_run = QPushButton("▶ Execute on Selected")
        btn_run.setIcon(qta.icon('fa5s.play', color='white'))
        btn_run.clicked.connect(self._execute)
        btn_close = QPushButton("Close"); btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_run); btns.addStretch(); btns.addWidget(btn_close)
        layout.addLayout(btns)

        layout.addWidget(QLabel("Results:"))
        self.results_area = QPlainTextEdit(); self.results_area.setReadOnly(True)
        layout.addWidget(self.results_area)

        self._sig_result.connect(self.results_area.appendPlainText)

    def _execute(self):
        cmd = self.cmd_inp.text().strip()
        if not cmd: QMessageBox.warning(self, "No command", "Enter a command."); return
        selected_titles = [self.srv_list.item(i).text() for i in range(self.srv_list.count())
                           if self.srv_list.item(i).isSelected()]
        if not selected_titles: QMessageBox.warning(self, "No selection", "Select at least one server."); return
        if self.chk_confirm.isChecked():
            if QMessageBox.question(self, "Confirm",
                f"Execute on {len(selected_titles)} server(s):\n{', '.join(selected_titles)}\n\nCommand:\n{cmd}"
            ) != QMessageBox.StandardButton.Yes: return
        self.results_area.clear()
        for title, tab in self.connected_tabs:
            if title in selected_titles:
                threading.Thread(target=self._run_on, args=(title, tab, cmd), daemon=True).start()

    def _run_on(self, title, tab, cmd):
        try:
            _, out, err = tab.ssh_mgr.execute(cmd)
            stdout = out.read().decode('utf-8', errors='replace').strip()
            stderr = err.read().decode('utf-8', errors='replace').strip()
            result = f"{'='*40}\n[{title}]\n"
            if stdout: result += stdout + "\n"
            if stderr: result += f"[stderr] {stderr}\n"
        except Exception as e:
            result = f"{'='*40}\n[{title}]\nError: {e}\n"
        self._sig_result.emit(result)

class SSHKeyManagerDialog(QDialog):
    def __init__(self, parent, ssh_mgr, lang="en"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self.setWindowTitle("SSH Key Manager")
        self.resize(820, 560)
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        local_tab = QWidget(); ll = QVBoxLayout(local_tab)
        ll.addWidget(QLabel("Local SSH key pairs in ~/.ssh/:"))
        self.local_list = QListWidget()
        ll.addWidget(self.local_list)

        gen = QHBoxLayout()
        self.combo_ktype = QComboBox(); self.combo_ktype.addItems(["Ed25519 (recommended)", "RSA 4096"])
        self.inp_kname = QLineEdit("id_ed25519_nix"); self.inp_kname.setPlaceholderText("Key filename")
        btn_gen = QPushButton("Generate"); btn_gen.setIcon(qta.icon('fa5s.key', color='white')); btn_gen.clicked.connect(self._generate_key)
        gen.addWidget(QLabel("Type:")); gen.addWidget(self.combo_ktype); gen.addWidget(QLabel("Name:")); gen.addWidget(self.inp_kname, 1); gen.addWidget(btn_gen)
        ll.addLayout(gen)

        btn_push = QPushButton("Push selected public key to server  (ssh-copy-id equivalent)")
        btn_push.setIcon(qta.icon('fa5s.upload', color='white')); btn_push.clicked.connect(self._push_key)
        ll.addWidget(btn_push)
        tabs.addTab(local_tab, "Local Keys")

        remote_tab = QWidget(); rl = QVBoxLayout(remote_tab)
        rl.addWidget(QLabel("Remote ~/.ssh/authorized_keys:"))
        self.auth_keys_edit = QPlainTextEdit()
        rl.addWidget(self.auth_keys_edit)
        rb = QHBoxLayout()
        for lbl, fn in [("Load from server", self._load_ak), ("Save to server", self._save_ak)]:
            btn = QPushButton(lbl); btn.clicked.connect(fn); rb.addWidget(btn)
        rl.addLayout(rb)
        tabs.addTab(remote_tab, "authorized_keys")

        layout.addWidget(tabs)
        self.lbl_status = QLabel(""); layout.addWidget(self.lbl_status)
        self._refresh_local()

    def _refresh_local(self):
        self.local_list.clear()
        ssh_dir = os.path.expanduser("~/.ssh")
        if os.path.isdir(ssh_dir):
            for f in sorted(os.listdir(ssh_dir)):
                if not f.endswith('.pub') and os.path.exists(os.path.join(ssh_dir, f + ".pub")):
                    self.local_list.addItem(f)

    def _generate_key(self):
        name = self.inp_kname.text().strip() or "id_nix"
        ssh_dir = os.path.expanduser("~/.ssh"); os.makedirs(ssh_dir, exist_ok=True)
        path = os.path.join(ssh_dir, name)
        if os.path.exists(path):
            if QMessageBox.question(self, "Overwrite?", f"'{name}' exists. Overwrite?") != QMessageBox.StandardButton.Yes: return
        try:
            key = paramiko.Ed25519Key.generate() if "Ed25519" in self.combo_ktype.currentText() else paramiko.RSAKey.generate(4096)
            key.write_private_key_file(path)
            with open(path + ".pub", 'w') as f:
                f.write(f"{key.get_name()} {key.get_base64()} nix-generated\n")
            self.lbl_status.setText(f"Key generated: {path}")
            self._refresh_local()
            QMessageBox.information(self, "Success", f"Generated:\n{path}\n{path}.pub")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _push_key(self):
        item = self.local_list.currentItem()
        if not item: QMessageBox.warning(self, "No selection", "Select a key."); return
        pub_path = os.path.expanduser(f"~/.ssh/{item.text()}.pub")
        try:
            with open(pub_path) as f: pub_key = f.read().strip()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Cannot read public key:\n{e}"); return
        try:
            safe_key = pub_key.replace("'", "'\\''")
            cmd = (f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                   f"echo '{safe_key}' >> ~/.ssh/authorized_keys && "
                   f"sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys && "
                   f"chmod 600 ~/.ssh/authorized_keys")
            _, _, err = self.ssh_mgr.execute(cmd)
            if err_txt := err.read().decode().strip():
                QMessageBox.warning(self, "Warning", err_txt)
            else:
                self.lbl_status.setText(f"Key '{item.text()}' pushed.")
                QMessageBox.information(self, "Success", "Public key pushed successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _load_ak(self):
        try:
            _, out, _ = self.ssh_mgr.execute("cat ~/.ssh/authorized_keys 2>/dev/null || echo ''")
            self.auth_keys_edit.setPlainText(out.read().decode('utf-8', errors='replace'))
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _save_ak(self):
        content = self.auth_keys_edit.toPlainText()
        try:
            stdin, _, _ = self.ssh_mgr.execute("cat > ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys")
            stdin.write(content.encode()); stdin.channel.shutdown_write()
            QMessageBox.information(self, "Success", "authorized_keys saved.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

class FileSyncDialog(QDialog):
    _sig_diff = pyqtSignal(list)
    _sig_status = pyqtSignal(str)

    def __init__(self, parent, ssh_mgr, remote_path, lang="en"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self._remote_base = remote_path
        self._diffs = []
        self.setWindowTitle("File Sync  Local ↔ Remote")
        self.resize(1020, 650)
        layout = QVBoxLayout(self)

        paths = QHBoxLayout()
        self.inp_local  = QLineEdit(); self.inp_local.setPlaceholderText("Local folder…")
        btn_browse = QPushButton("Browse"); btn_browse.clicked.connect(self._browse)
        self.inp_remote = QLineEdit(remote_path); self.inp_remote.setPlaceholderText("Remote folder…")
        btn_compare = QPushButton("Compare"); btn_compare.setIcon(qta.icon('fa5s.exchange-alt', color='white')); btn_compare.clicked.connect(self._compare)
        paths.addWidget(QLabel("Local:")); paths.addWidget(self.inp_local, 1); paths.addWidget(btn_browse)
        paths.addWidget(QLabel("Remote:")); paths.addWidget(self.inp_remote, 1); paths.addWidget(btn_compare)
        layout.addLayout(paths)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["File", "Status", "Local Size", "Remote Size"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.tbl)

        sb = QHBoxLayout()
        btn_sel_all = QPushButton("Select All"); btn_sel_all.clicked.connect(self.tbl.selectAll)
        btn_push = QPushButton("Push Selected →"); btn_push.setIcon(qta.icon('fa5s.arrow-right', color='white')); btn_push.clicked.connect(self._push)
        btn_pull = QPushButton("← Pull Selected"); btn_pull.setIcon(qta.icon('fa5s.arrow-left',  color='white')); btn_pull.clicked.connect(self._pull)
        for w in [btn_sel_all, btn_push, btn_pull]: sb.addWidget(w)
        sb.addStretch()
        layout.addLayout(sb)

        self.lbl_status = QLabel("Select folders and click Compare."); layout.addWidget(self.lbl_status)
        self._sig_diff.connect(self._populate_diff)
        self._sig_status.connect(self.lbl_status.setText)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select local folder")
        if d: self.inp_local.setText(d)

    def _compare(self):
        local = self.inp_local.text().strip(); remote = self.inp_remote.text().strip()
        if not local or not os.path.isdir(local):
            QMessageBox.warning(self, "Error", "Select a valid local folder."); return
        self.lbl_status.setText("Comparing…")
        threading.Thread(target=self._compare_thread, args=(local, remote), daemon=True).start()

    def _compare_thread(self, local_dir, remote_dir):
        local_files = {}
        for root, _, files in os.walk(local_dir):
            for fname in files:
                rel = os.path.relpath(os.path.join(root, fname), local_dir).replace('\\', '/')
                local_files[rel] = os.path.getsize(os.path.join(root, fname))

        remote_files = {}
        def _ls(rpath, prefix=""):
            try:
                with self.ssh_mgr.lock:
                    items = self.ssh_mgr.sftp.listdir_attr(rpath)
                for item in items:
                    rel = f"{prefix}/{item.filename}" if prefix else item.filename
                    if stat.S_ISDIR(item.st_mode):
                        _ls(posixpath.join(rpath, item.filename), rel)
                    else:
                        remote_files[rel] = item.st_size or 0
            except Exception:
                pass
        _ls(remote_dir)

        diffs = []
        for f in sorted(set(local_files) | set(remote_files)):
            ls = local_files.get(f); rs = remote_files.get(f)
            if ls is None:   status = "Remote only"
            elif rs is None: status = "Local only"
            elif ls != rs:   status = "Different"
            else:            status = "In sync"
            diffs.append((f, status, ls, rs))
        self._diffs = diffs
        self._sig_diff.emit(diffs)

    def _populate_diff(self, diffs):
        _colors = {"Remote only": "#2196F3", "Local only": "#FF9800",
                   "Different": "#f1fa8c", "In sync": "#50fa7b"}
        self.tbl.setRowCount(0)
        for fname, status, ls, rs in diffs:
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            self.tbl.setItem(r, 0, QTableWidgetItem(fname))
            si = QTableWidgetItem(status); si.setForeground(QBrush(QColor(_colors.get(status, "#f8f8f2"))))
            self.tbl.setItem(r, 1, si)
            self.tbl.setItem(r, 2, QTableWidgetItem(str(ls) if ls is not None else "—"))
            self.tbl.setItem(r, 3, QTableWidgetItem(str(rs) if rs is not None else "—"))
        self._sig_status.emit(f"Comparison done: {len(diffs)} files.")

    def _push(self):
        local = self.inp_local.text().strip(); remote = self.inp_remote.text().strip()
        files = [self.tbl.item(r, 0).text() for r in {i.row() for i in self.tbl.selectedItems()}
                 if self.tbl.item(r, 1).text() != "Remote only"]
        if not files: QMessageBox.warning(self, "No selection", "Select files to push."); return
        threading.Thread(target=self._push_thread, args=(local, remote, files), daemon=True).start()

    def _push_thread(self, local_dir, remote_dir, files):
        for i, fname in enumerate(files, 1):
            try:
                lp = os.path.join(local_dir, fname.replace('/', os.sep))
                rp = posixpath.join(remote_dir, fname)
                rparent = posixpath.dirname(rp)
                try:
                    with self.ssh_mgr.lock: self.ssh_mgr.sftp.mkdir(rparent)
                except Exception: pass
                with self.ssh_mgr.lock: self.ssh_mgr.sftp.put(lp, rp)
            except Exception as e:
                self._sig_status.emit(f"Error: {e}")
            self._sig_status.emit(f"Uploading {i}/{len(files)}: {fname}")
        self._sig_status.emit("Push complete.")

    def _pull(self):
        local = self.inp_local.text().strip(); remote = self.inp_remote.text().strip()
        files = [self.tbl.item(r, 0).text() for r in {i.row() for i in self.tbl.selectedItems()}]
        if not files: QMessageBox.warning(self, "No selection", "Select files to pull."); return
        threading.Thread(target=self._pull_thread, args=(local, remote, files), daemon=True).start()

    def _pull_thread(self, local_dir, remote_dir, files):
        for i, fname in enumerate(files, 1):
            try:
                rp = posixpath.join(remote_dir, fname)
                lp = os.path.join(local_dir, fname.replace('/', os.sep))
                os.makedirs(os.path.dirname(lp), exist_ok=True)
                with self.ssh_mgr.lock: self.ssh_mgr.sftp.get(rp, lp)
            except Exception as e:
                self._sig_status.emit(f"Error: {e}")
            self._sig_status.emit(f"Downloading {i}/{len(files)}: {fname}")
        self._sig_status.emit("Pull complete.")

class PortMonitorDialog(QDialog):
    _sig_data = pyqtSignal(list)

    def __init__(self, parent, ssh_mgr, lang="en"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self._all_rows = []
        self._timer = QTimer(); self._timer.timeout.connect(self._refresh)
        self.setWindowTitle("Port & Connection Monitor")
        self.resize(1020, 580)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.filter_inp = QLineEdit(); self.filter_inp.setPlaceholderText("Filter (port, address, process)…"); self.filter_inp.textChanged.connect(self._apply_filter)
        self.combo_proto = QComboBox(); self.combo_proto.addItems(["All", "TCP", "UDP"]); self.combo_proto.currentTextChanged.connect(self._apply_filter)
        btn_ref = QPushButton("Refresh"); btn_ref.setIcon(qta.icon('fa5s.sync', color='white')); btn_ref.clicked.connect(self._refresh)
        self.btn_auto = QPushButton("Auto-refresh OFF"); self.btn_auto.setCheckable(True); self.btn_auto.clicked.connect(self._toggle_auto)
        top.addWidget(self.filter_inp, 1); top.addWidget(self.combo_proto)
        top.addWidget(btn_ref); top.addWidget(self.btn_auto)
        layout.addLayout(top)

        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(["Proto", "Local Address", "Port", "State", "PID", "Process"])
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.tbl)

        self.lbl_status = QLabel("Click Refresh."); layout.addWidget(self.lbl_status)
        self._sig_data.connect(self._populate)
        self._refresh()

    def _refresh(self):
        self.lbl_status.setText("Loading…")
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            _, out, _ = self.ssh_mgr.execute("ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null")
            rows = self._parse(out.read().decode())
            self._sig_data.emit(rows)
        except Exception as e:
            QTimer.singleShot(0, lambda: self.lbl_status.setText(f"Error: {e}"))

    def _parse(self, text):
        rows = []
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 5: continue
            proto = parts[0]
            local = parts[4] if len(parts) > 4 else ""
            state = parts[1] if len(parts) > 1 else ""
            addr, port = (local.rsplit(':', 1) if ':' in local else (local, ""))
            pid = process = ""
            for p in parts:
                if 'pid=' in p: pid = p.split('pid=')[1].split(',')[0].rstrip(')')
                if '"' in p: process = p.split('"')[1] if p.count('"') >= 2 else p.split('"')[1]
            rows.append((proto, addr, port, state, pid, process))
        return rows

    def _populate(self, rows):
        self._all_rows = rows
        self._apply_filter()

    def _apply_filter(self):
        text = self.filter_inp.text().lower()
        proto_f = self.combo_proto.currentText().lower()
        self.tbl.setRowCount(0)
        for proto, addr, port, state, pid, process in self._all_rows:
            if proto_f != "all" and proto_f not in proto.lower(): continue
            if text and text not in f"{addr} {port} {process} {pid}".lower(): continue
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            items = [proto, addr, port, state, pid, process]
            for col, val in enumerate(items):
                item = QTableWidgetItem(val)
                if col == 3 and "listen" in state.lower():
                    item.setForeground(QBrush(QColor("#50fa7b")))
                self.tbl.setItem(r, col, item)
        self.lbl_status.setText(f"{self.tbl.rowCount()} entries")

    def _toggle_auto(self, checked):
        if checked: self._timer.start(5000); self.btn_auto.setText("Auto-refresh ON")
        else:       self._timer.stop();      self.btn_auto.setText("Auto-refresh OFF")

    def closeEvent(self, event):
        self._timer.stop(); super().closeEvent(event)

class FleetDashboardWidget(QWidget):
    """Shows status of all saved servers with quick-connect."""
    _sig_status = pyqtSignal(str, str)

    def __init__(self, parent, config_mgr, connect_callback):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.connect_callback = connect_callback
        self._cards = {}

        layout = QVBoxLayout(self)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Fleet Dashboard — Saved Servers"))
        hdr.addStretch()
        btn_ref = QPushButton("Refresh All"); btn_ref.setIcon(qta.icon('fa5s.sync', color='white'))
        btn_ref.clicked.connect(self._check_all)
        hdr.addWidget(btn_ref)
        layout.addLayout(hdr)

        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.addStretch()
        scroll.setWidget(self.cards_container)
        layout.addWidget(scroll)

        self._sig_status.connect(self._update_status)
        self._build_cards()
        self._check_all()

    def _build_cards(self):
        for info in list(self._cards.values()):
            info['widget'].setParent(None)
        self._cards.clear()
        for name, data in self.config_mgr.sessions.items():
            card = QFrame(); card.setObjectName("Card"); card.setFixedHeight(72)
            cl = QHBoxLayout(card)
            status_lbl = QLabel("● Checking…"); status_lbl.setFixedWidth(170)
            info_lbl = QLabel(f"<b>{name}</b><br><small>{data.get('host','')}</small>")
            info_lbl.setTextFormat(Qt.TextFormat.RichText)
            btn_conn = QPushButton("Connect"); btn_conn.setFixedWidth(80)
            btn_conn.clicked.connect(lambda _, n=name: self.connect_callback(n))
            cl.addWidget(status_lbl); cl.addWidget(info_lbl, 1); cl.addWidget(btn_conn)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
            self._cards[name] = {'widget': card, 'status_lbl': status_lbl}

    def refresh_sessions(self):
        self._build_cards(); self._check_all()

    def _check_all(self):
        for name, data in self.config_mgr.sessions.items():
            if name in self._cards:
                self._cards[name]['status_lbl'].setText("● Checking…")
                threading.Thread(target=self._probe, args=(name, data.get('host', '')), daemon=True).start()

    def _probe(self, name, host_str):
        try:
            h = host_str.split('@')[-1].split(':')[0]
            port = int(host_str.split(':')[1]) if ':' in host_str.split('@')[-1] else 22
            sock = socket.socket(); sock.settimeout(3)
            ok = sock.connect_ex((h, port)) == 0; sock.close()
            self._sig_status.emit(name, "● Online" if ok else "● Offline")
        except Exception:
            self._sig_status.emit(name, "● Unreachable")

    def _update_status(self, name, status):
        card = self._cards.get(name)
        if not card: return
        card['status_lbl'].setText(status)
        if "Online" in status:
            color = "#28a745"
        elif "Checking" in status:
            color = "#888888"
        else:
            color = "#dc3545"
        card['status_lbl'].setStyleSheet(f"color: {color};")


class PackageManagerDialog(QDialog):
    _sig_rows = pyqtSignal(list)
    _sig_status = pyqtSignal(str)

    def __init__(self, parent, ssh_mgr, lang="en"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self._all_rows = []
        self._pkg_mgr = None
        self.setWindowTitle("Package Manager")
        self.resize(1000, 640)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.filter_inp = QLineEdit()
        self.filter_inp.setPlaceholderText("Search packages…")
        self.filter_inp.textChanged.connect(self._filter)
        self.combo_view = QComboBox()
        self.combo_view.addItems(["Installed", "Upgradable"])
        self.combo_view.currentTextChanged.connect(self._load)
        btn_ref = QPushButton("Refresh")
        btn_ref.setIcon(qta.icon('fa5s.sync', color='white'))
        btn_ref.clicked.connect(self._load)
        top.addWidget(self.filter_inp, 1)
        top.addWidget(self.combo_view)
        top.addWidget(btn_ref)
        layout.addLayout(top)

        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(["Package", "Version", "Description"])
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl.setColumnWidth(0, 220); self.tbl.setColumnWidth(1, 120)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.tbl)

        btns = QHBoxLayout()
        self.inp_pkg = QLineEdit()
        self.inp_pkg.setPlaceholderText("Package name to install…")
        btn_install = QPushButton("Install")
        btn_install.setIcon(qta.icon('fa5s.download', color='white'))
        btn_install.clicked.connect(self._install)
        btn_remove = QPushButton("Remove")
        btn_remove.setIcon(qta.icon('fa5s.trash', color='#dc3545'))
        btn_remove.clicked.connect(self._remove)
        btn_upgrade = QPushButton("Upgrade All")
        btn_upgrade.setIcon(qta.icon('fa5s.arrow-up', color='#28a745'))
        btn_upgrade.clicked.connect(self._upgrade_all)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        for w in [self.inp_pkg, btn_install, btn_remove, btn_upgrade]:
            btns.addWidget(w)
        btns.addStretch()
        btns.addWidget(btn_close)
        layout.addLayout(btns)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        layout.addWidget(self.log)

        self._sig_rows.connect(self._populate)
        self._sig_status.connect(self.log.appendPlainText)
        self._detect_and_load()

    def _detect_and_load(self):
        threading.Thread(target=self._detect_thread, daemon=True).start()

    def _detect_thread(self):
        for mgr, check in [("apt", "which apt"), ("dnf", "which dnf"),
                            ("yum", "which yum"), ("pacman", "which pacman"),
                            ("zypper", "which zypper")]:
            try:
                _, o, _ = self.ssh_mgr.execute(check + " 2>/dev/null")
                if o.read().decode().strip():
                    self._pkg_mgr = mgr
                    break
            except Exception:
                pass
        if self._pkg_mgr:
            self._sig_status.emit(f"Detected package manager: {self._pkg_mgr}")
            self._load_thread()
        else:
            self._sig_status.emit("No supported package manager detected.")

    def _load(self):
        if not self._pkg_mgr:
            self._detect_and_load(); return
        threading.Thread(target=self._load_thread, daemon=True).start()

    def _load_thread(self):
        try:
            mode = self.combo_view.currentText()
            if self._pkg_mgr == "apt":
                if mode == "Upgradable":
                    cmd = "apt list --upgradable 2>/dev/null | tail -n +2"
                else:
                    cmd = "dpkg-query -W -f='${Package}\t${Version}\t${binary:Summary}\n' 2>/dev/null"
            elif self._pkg_mgr in ("dnf", "yum"):
                if mode == "Upgradable":
                    cmd = f"{self._pkg_mgr} check-update 2>/dev/null | grep -v '^$' | tail -n +3"
                else:
                    cmd = f"rpm -qa --queryformat '%{{NAME}}\t%{{VERSION}}-%{{RELEASE}}\t%{{SUMMARY}}\n' 2>/dev/null"
            elif self._pkg_mgr == "pacman":
                if mode == "Upgradable":
                    cmd = "checkupdates 2>/dev/null || pacman -Qu 2>/dev/null"
                else:
                    cmd = "pacman -Q 2>/dev/null"
            elif self._pkg_mgr == "zypper":
                cmd = "zypper packages --installed-only 2>/dev/null | grep '^i' | awk '{print $3\"\\t\"$5\"\\t\"}'"
            else:
                return
            _, out, _ = self.ssh_mgr.execute(cmd)
            rows = []
            for line in out.read().decode().splitlines():
                parts = line.strip().split('\t')
                if len(parts) >= 2:
                    rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
                elif len(parts) == 1 and parts[0]:
                    p = parts[0].split()
                    rows.append((p[0], p[1] if len(p) > 1 else "", ""))
            self._all_rows = rows
            self._sig_rows.emit(rows)
            self._sig_status.emit(f"Loaded {len(rows)} packages.")
        except Exception as e:
            self._sig_status.emit(f"Error: {e}")

    def _populate(self, rows):
        text = self.filter_inp.text().lower()
        self.tbl.setRowCount(0)
        for pkg, ver, desc in rows:
            if text and text not in (pkg + desc).lower(): continue
            r = self.tbl.rowCount(); self.tbl.insertRow(r)
            self.tbl.setItem(r, 0, QTableWidgetItem(pkg))
            self.tbl.setItem(r, 1, QTableWidgetItem(ver))
            self.tbl.setItem(r, 2, QTableWidgetItem(desc))

    def _filter(self, text):
        self._populate(self._all_rows)

    def _run_pkg_cmd(self, cmd):
        self._sig_status.emit(f"$ {cmd}")
        threading.Thread(target=self._exec_pkg, args=(cmd,), daemon=True).start()

    def _exec_pkg(self, cmd):
        try:
            _, out, err = self.ssh_mgr.execute(cmd)
            combined = (out.read() + err.read()).decode('utf-8', errors='replace').strip()
            self._sig_status.emit(combined[-2000:] if len(combined) > 2000 else combined)
            QTimer.singleShot(1500, self._load)
        except Exception as e:
            self._sig_status.emit(f"Error: {e}")

    def _install(self):
        pkg = self.inp_pkg.text().strip()
        if not pkg: QMessageBox.warning(self, "No package", "Enter a package name."); return
        install_cmds = {"apt": f"sudo -n apt install -y {pkg}",
                        "dnf": f"sudo -n dnf install -y {pkg}",
                        "yum": f"sudo -n yum install -y {pkg}",
                        "pacman": f"sudo -n pacman -S --noconfirm {pkg}",
                        "zypper": f"sudo -n zypper install -y {pkg}"}
        self._run_pkg_cmd(install_cmds.get(self._pkg_mgr, ""))

    def _remove(self):
        row = self.tbl.currentRow()
        if row < 0: QMessageBox.warning(self, "No selection", "Select a package."); return
        pkg = self.tbl.item(row, 0).text()
        if QMessageBox.question(self, "Confirm", f"Remove '{pkg}'?") != QMessageBox.StandardButton.Yes: return
        remove_cmds = {"apt": f"sudo -n apt remove -y {pkg}",
                       "dnf": f"sudo -n dnf remove -y {pkg}",
                       "yum": f"sudo -n yum remove -y {pkg}",
                       "pacman": f"sudo -n pacman -R --noconfirm {pkg}",
                       "zypper": f"sudo -n zypper remove -y {pkg}"}
        self._run_pkg_cmd(remove_cmds.get(self._pkg_mgr, ""))

    def _upgrade_all(self):
        if QMessageBox.question(self, "Confirm", "Upgrade all packages?") != QMessageBox.StandardButton.Yes: return
        upgrade_cmds = {"apt": "sudo -n apt upgrade -y",
                        "dnf": "sudo -n dnf upgrade -y",
                        "yum": "sudo -n yum update -y",
                        "pacman": "sudo -n pacman -Syu --noconfirm",
                        "zypper": "sudo -n zypper update -y"}
        self._run_pkg_cmd(upgrade_cmds.get(self._pkg_mgr, ""))


class UserManagerDialog(QDialog):
    _sig_users = pyqtSignal(list)
    _sig_groups = pyqtSignal(list)
    _sig_log = pyqtSignal(str)

    def __init__(self, parent, ssh_mgr, lang="en", has_sudo=False):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self._has_sudo = has_sudo
        self.setWindowTitle("User & Group Manager")
        self.resize(960, 600)
        layout = QVBoxLayout(self)

        if not has_sudo:
            warn = QLabel("Read-only: add/delete operations require sudo or root access.")
            warn.setStyleSheet("color: #ffb86c; font-style: italic; padding: 4px;")
            layout.addWidget(warn)

        tabs = QTabWidget()

        users_w = QWidget(); ul = QVBoxLayout(users_w)
        ub = QHBoxLayout()
        self.btn_add_u = QPushButton("Add User");        self.btn_add_u.setIcon(qta.icon('fa5s.user-plus',  color='white'));   self.btn_add_u.clicked.connect(self._add_user)
        self.btn_del_u = QPushButton("Delete User");     self.btn_del_u.setIcon(qta.icon('fa5s.user-minus', color='#dc3545')); self.btn_del_u.clicked.connect(self._delete_user)
        self.btn_chpwd = QPushButton("Change Password"); self.btn_chpwd.setIcon(qta.icon('fa5s.key',        color='white'));   self.btn_chpwd.clicked.connect(self._change_password)
        btn_ref_u      = QPushButton("Refresh");         btn_ref_u.setIcon(qta.icon('fa5s.sync',            color='white'));   btn_ref_u.clicked.connect(self._load_users)
        self.btn_add_u.setEnabled(has_sudo); self.btn_del_u.setEnabled(has_sudo)
        for b in [self.btn_add_u, self.btn_del_u, self.btn_chpwd, btn_ref_u]: ub.addWidget(b)
        ub.addStretch(); ul.addLayout(ub)
        self.tbl_users = QTableWidget(0, 5)
        self.tbl_users.setHorizontalHeaderLabels(["Username", "UID", "GID", "Home", "Shell"])
        self.tbl_users.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_users.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_users.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ul.addWidget(self.tbl_users)
        tabs.addTab(users_w, "Users")

        groups_w = QWidget(); gl = QVBoxLayout(groups_w)
        gb = QHBoxLayout()
        self.btn_add_g = QPushButton("Add Group");         self.btn_add_g.setIcon(qta.icon('fa5s.users',    color='white'));   self.btn_add_g.clicked.connect(self._add_group)
        self.btn_del_g = QPushButton("Delete Group");      self.btn_del_g.setIcon(qta.icon('fa5s.trash',    color='#dc3545')); self.btn_del_g.clicked.connect(self._delete_group)
        btn_add_to_g   = QPushButton("Add User to Group"); btn_add_to_g.setIcon(qta.icon('fa5s.user-tag',  color='white'));   btn_add_to_g.clicked.connect(self._add_to_group)
        btn_ref_g      = QPushButton("Refresh");           btn_ref_g.setIcon(qta.icon('fa5s.sync',          color='white'));   btn_ref_g.clicked.connect(self._load_groups)
        self.btn_add_g.setEnabled(has_sudo); self.btn_del_g.setEnabled(has_sudo)
        for b in [self.btn_add_g, self.btn_del_g, btn_add_to_g, btn_ref_g]: gb.addWidget(b)
        gb.addStretch(); gl.addLayout(gb)
        self.tbl_groups = QTableWidget(0, 3)
        self.tbl_groups.setHorizontalHeaderLabels(["Group", "GID", "Members"])
        self.tbl_groups.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl_groups.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_groups.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        gl.addWidget(self.tbl_groups)
        tabs.addTab(groups_w, "Groups")

        layout.addWidget(tabs)
        self.log_area = QPlainTextEdit(); self.log_area.setReadOnly(True); self.log_area.setMaximumHeight(90)
        layout.addWidget(QLabel("Output:")); layout.addWidget(self.log_area)

        self._sig_users.connect(self._populate_users)
        self._sig_groups.connect(self._populate_groups)
        self._sig_log.connect(self.log_area.appendPlainText)
        self._load_users(); self._load_groups()

    def _load_users(self):
        threading.Thread(target=self._fetch_users, daemon=True).start()

    def _fetch_users(self):
        try:
            _, out, _ = self.ssh_mgr.execute("getent passwd 2>/dev/null || cat /etc/passwd 2>/dev/null")
            raw = out.read().decode('utf-8', errors='replace')
            rows = []
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith('#'): continue
                p = line.split(':')
                user  = p[0] if len(p) > 0 else ""
                uid   = p[2] if len(p) > 2 else ""
                gid   = p[3] if len(p) > 3 else ""
                home  = p[5] if len(p) > 5 else ""
                shell = p[6] if len(p) > 6 else ""
                if user:
                    rows.append((user, uid, gid, home, shell))
            self._sig_users.emit(rows)
            self._sig_log.emit(f"Loaded {len(rows)} users.")
        except Exception as e:
            self._sig_log.emit(f"Error loading users: {e}")

    def _populate_users(self, rows):
        self.tbl_users.setRowCount(0)
        for user, uid, gid, home, shell in rows:
            r = self.tbl_users.rowCount(); self.tbl_users.insertRow(r)
            is_system = False
            try: is_system = int(uid) < 1000
            except (ValueError, TypeError): pass
            for col, val in enumerate([user, uid, gid, home, shell]):
                item = QTableWidgetItem(str(val))
                if col == 0 and is_system:
                    item.setForeground(QBrush(QColor("#888")))
                self.tbl_users.setItem(r, col, item)

    def _load_groups(self):
        threading.Thread(target=self._fetch_groups, daemon=True).start()

    def _fetch_groups(self):
        try:
            _, out, _ = self.ssh_mgr.execute("getent group 2>/dev/null || cat /etc/group 2>/dev/null")
            raw = out.read().decode('utf-8', errors='replace')
            rows = []
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith('#'): continue
                p = line.split(':')
                grp     = p[0] if len(p) > 0 else ""
                gid     = p[2] if len(p) > 2 else ""
                members = p[3] if len(p) > 3 else ""
                if grp:
                    rows.append((grp, gid, members))
            self._sig_groups.emit(rows)
            self._sig_log.emit(f"Loaded {len(rows)} groups.")
        except Exception as e:
            self._sig_log.emit(f"Error loading groups: {e}")

    def _populate_groups(self, rows):
        self.tbl_groups.setRowCount(0)
        for grp, gid, members in rows:
            r = self.tbl_groups.rowCount(); self.tbl_groups.insertRow(r)
            for col, val in enumerate([grp, gid, members]):
                self.tbl_groups.setItem(r, col, QTableWidgetItem(val))

    def _run_cmd(self, cmd):
        self._sig_log.emit(f"$ {cmd}")
        threading.Thread(target=self._exec_cmd, args=(cmd,), daemon=True).start()

    def _exec_cmd(self, cmd):
        try:
            _, out, err = self.ssh_mgr.execute(cmd)
            combined = (out.read() + err.read()).decode('utf-8', errors='replace').strip()
            if combined: self._sig_log.emit(combined)
            QTimer.singleShot(800, self._load_users)
            QTimer.singleShot(800, self._load_groups)
        except Exception as e:
            self._sig_log.emit(f"Error: {e}")

    def _add_user(self):
        name, ok = QInputDialog.getText(self, "Add User", "Username:")
        if not ok or not name.strip(): return
        self._run_cmd(f"sudo -n useradd -m {name.strip()} 2>&1")

    def _delete_user(self):
        row = self.tbl_users.currentRow()
        if row < 0: return
        user = self.tbl_users.item(row, 0).text()
        if QMessageBox.question(self, "Confirm", f"Delete user '{user}'?") != QMessageBox.StandardButton.Yes: return
        self._run_cmd(f"sudo -n userdel -r {user} 2>&1")

    def _change_password(self):
        row = self.tbl_users.currentRow()
        if row < 0: return
        user = self.tbl_users.item(row, 0).text()
        pwd, ok = QInputDialog.getText(self, "Change Password", f"New password for {user}:", QLineEdit.EchoMode.Password)
        if not ok or not pwd: return
        self._run_cmd(f"echo '{user}:{pwd}' | sudo -n chpasswd 2>&1")

    def _add_group(self):
        name, ok = QInputDialog.getText(self, "Add Group", "Group name:")
        if not ok or not name.strip(): return
        self._run_cmd(f"sudo -n groupadd {name.strip()} 2>&1")

    def _delete_group(self):
        row = self.tbl_groups.currentRow()
        if row < 0: return
        grp = self.tbl_groups.item(row, 0).text()
        if QMessageBox.question(self, "Confirm", f"Delete group '{grp}'?") != QMessageBox.StandardButton.Yes: return
        self._run_cmd(f"sudo -n groupdel {grp} 2>&1")

    def _add_to_group(self):
        row_g = self.tbl_groups.currentRow()
        row_u = self.tbl_users.currentRow()
        if row_g < 0 or row_u < 0:
            QMessageBox.information(self, "Select both", "Select a user in Users tab and a group in Groups tab."); return
        grp  = self.tbl_groups.item(row_g, 0).text()
        user = self.tbl_users.item(row_u, 0).text()
        self._run_cmd(f"sudo -n usermod -aG {grp} {user} 2>&1")


class FirewallDialog(QDialog):
    _sig_rules = pyqtSignal(str)
    _sig_status = pyqtSignal(str)

    def __init__(self, parent, ssh_mgr, lang="en"):
        super().__init__(parent)
        self.ssh_mgr = ssh_mgr
        self.lang = lang
        self._fw_type = None
        self.setWindowTitle("Firewall Manager")
        self.resize(960, 620)
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.lbl_fw = QLabel("Detecting firewall…")
        self.lbl_fw.setStyleSheet("font-weight:bold;")
        btn_ref = QPushButton("Refresh Rules"); btn_ref.setIcon(qta.icon('fa5s.sync', color='white')); btn_ref.clicked.connect(self._load_rules)
        self.btn_enable = QPushButton("Enable Firewall"); self.btn_enable.setIcon(qta.icon('fa5s.shield-alt', color='#28a745')); self.btn_enable.clicked.connect(self._enable_fw)
        self.btn_disable = QPushButton("Disable Firewall"); self.btn_disable.setIcon(qta.icon('fa5s.shield-alt', color='#dc3545')); self.btn_disable.clicked.connect(self._disable_fw)
        top.addWidget(self.lbl_fw, 1); top.addWidget(btn_ref); top.addWidget(self.btn_enable); top.addWidget(self.btn_disable)
        layout.addLayout(top)

        pwd_row = QHBoxLayout()
        lbl_pwd = QLabel("Sudo password:")
        self.inp_sudo_pwd = QLineEdit()
        self.inp_sudo_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.inp_sudo_pwd.setPlaceholderText("Required for firewall commands")
        self.inp_sudo_pwd.setMaximumWidth(220)
        pwd_row.addWidget(lbl_pwd)
        pwd_row.addWidget(self.inp_sudo_pwd)
        pwd_row.addStretch()
        layout.addLayout(pwd_row)

        self.rules_area = QPlainTextEdit(); self.rules_area.setReadOnly(True)
        self.rules_area.setFont(QFont("Consolas", 10))
        layout.addWidget(self.rules_area)

        action_row = QHBoxLayout()
        self.inp_port = QLineEdit(); self.inp_port.setPlaceholderText("Port (e.g. 8080)"); self.inp_port.setFixedWidth(130)
        self.combo_proto = QComboBox(); self.combo_proto.addItems(["tcp", "udp", "any"]); self.combo_proto.setFixedWidth(70)
        btn_allow = QPushButton("Allow Port"); btn_allow.setIcon(qta.icon('fa5s.check', color='#28a745')); btn_allow.clicked.connect(self._allow_port)
        btn_deny  = QPushButton("Deny Port");  btn_deny.setIcon(qta.icon('fa5s.ban',   color='#dc3545'));  btn_deny.clicked.connect(self._deny_port)
        self.inp_ip = QLineEdit(); self.inp_ip.setPlaceholderText("IP to block (e.g. 1.2.3.4)"); self.inp_ip.setFixedWidth(180)
        btn_block_ip = QPushButton("Block IP"); btn_block_ip.setIcon(qta.icon('fa5s.ban', color='#dc3545')); btn_block_ip.clicked.connect(self._block_ip)
        for w in [QLabel("Port:"), self.inp_port, self.combo_proto, btn_allow, btn_deny,
                  QLabel("  IP:"), self.inp_ip, btn_block_ip]:
            action_row.addWidget(w)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.log_area = QPlainTextEdit(); self.log_area.setReadOnly(True); self.log_area.setMaximumHeight(80)
        layout.addWidget(self.log_area)

        self._sig_rules.connect(self.rules_area.setPlainText)
        self._sig_status.connect(self.log_area.appendPlainText)
        self._detect_and_load()

    def _detect_and_load(self):
        sudo_pwd = self.inp_sudo_pwd.text()
        threading.Thread(target=self._detect_thread, args=(sudo_pwd,), daemon=True).start()

    def _detect_thread(self, sudo_pwd=""):
        try:
            _, o, _ = self.ssh_mgr.execute("which ufw 2>/dev/null")
            if o.read().decode().strip():
                self._fw_type = "ufw"
            else:
                _, o2, _ = self.ssh_mgr.execute("which iptables 2>/dev/null")
                if o2.read().decode().strip():
                    self._fw_type = "iptables"
                else:
                    _, o3, _ = self.ssh_mgr.execute("which firewall-cmd 2>/dev/null")
                    if o3.read().decode().strip():
                        self._fw_type = "firewalld"
            QTimer.singleShot(0, lambda: self.lbl_fw.setText(f"Firewall: {self._fw_type or 'Not detected'}"))
            if self._fw_type:
                self._load_rules_thread(sudo_pwd)
        except Exception as e:
            self._sig_status.emit(f"Error: {e}")

    def _load_rules(self):
        if not self._fw_type: self._detect_and_load(); return
        sudo_pwd = self.inp_sudo_pwd.text()
        threading.Thread(target=self._load_rules_thread, args=(sudo_pwd,), daemon=True).start()

    def _load_rules_thread(self, sudo_pwd=""):
        try:
            if self._fw_type == "ufw":
                cmd = "sudo -S -p '' ufw status verbose 2>&1"
            elif self._fw_type == "iptables":
                cmd = "sudo -S -p '' iptables -L -n -v --line-numbers 2>&1"
            else:
                cmd = "sudo -S -p '' firewall-cmd --list-all 2>&1"
            stdin, out, _ = self.ssh_mgr.execute(cmd)
            if sudo_pwd:
                stdin.write(sudo_pwd + "\n"); stdin.flush()
            text = out.read().decode('utf-8', errors='replace')
            self._sig_rules.emit(text)
        except Exception as e:
            self._sig_status.emit(f"Error: {e}")

    def _run_fw(self, cmd):
        cmd_s = cmd.replace("sudo -n", "sudo -S -p ''")
        self._sig_status.emit(f"$ {cmd}")
        sudo_pwd = self.inp_sudo_pwd.text()
        def _exec():
            try:
                stdin, out, err = self.ssh_mgr.execute(cmd_s)
                if sudo_pwd:
                    stdin.write(sudo_pwd + "\n"); stdin.flush()
                combined = (out.read() + err.read()).decode('utf-8', errors='replace').strip()
                if combined: self._sig_status.emit(combined)
                QTimer.singleShot(800, self._load_rules)
            except Exception as e:
                self._sig_status.emit(f"Error: {e}")
        threading.Thread(target=_exec, daemon=True).start()

    def _allow_port(self):
        port = self.inp_port.text().strip()
        if not port: return
        proto = self.combo_proto.currentText()
        if self._fw_type == "ufw":
            rule = f"{port}/{proto}" if proto != "any" else port
            self._run_fw(f"sudo -n ufw allow {rule}")
        elif self._fw_type == "iptables":
            p = f"-p {proto}" if proto != "any" else ""
            self._run_fw(f"sudo -n iptables -I INPUT {p} --dport {port} -j ACCEPT")
        else:
            self._run_fw(f"sudo -n firewall-cmd --permanent --add-port={port}/{proto if proto != 'any' else 'tcp'} && sudo -n firewall-cmd --reload")

    def _deny_port(self):
        port = self.inp_port.text().strip()
        if not port: return
        proto = self.combo_proto.currentText()
        if self._fw_type == "ufw":
            rule = f"{port}/{proto}" if proto != "any" else port
            self._run_fw(f"sudo -n ufw deny {rule}")
        elif self._fw_type == "iptables":
            p = f"-p {proto}" if proto != "any" else ""
            self._run_fw(f"sudo -n iptables -I INPUT {p} --dport {port} -j DROP")
        else:
            self._run_fw(f"sudo -n firewall-cmd --permanent --remove-port={port}/{proto if proto != 'any' else 'tcp'} && sudo -n firewall-cmd --reload")

    def _block_ip(self):
        ip = self.inp_ip.text().strip()
        if not ip: return
        if self._fw_type == "ufw":
            self._run_fw(f"sudo -n ufw deny from {ip}")
        elif self._fw_type == "iptables":
            self._run_fw(f"sudo -n iptables -I INPUT -s {ip} -j DROP")
        else:
            self._run_fw(f"sudo -n firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address={ip} drop' && sudo -n firewall-cmd --reload")

    def _enable_fw(self):
        if self._fw_type == "ufw":
            self._run_fw("sudo -n ufw --force enable")
        elif self._fw_type == "iptables":
            self._run_fw("sudo -n systemctl start iptables 2>/dev/null || sudo -n service iptables start 2>/dev/null")
        else:
            self._run_fw("sudo -n systemctl start firewalld && sudo -n systemctl enable firewalld")

    def _disable_fw(self):
        if QMessageBox.question(self, "Confirm", "Disable firewall?") != QMessageBox.StandardButton.Yes: return
        if self._fw_type == "ufw":
            self._run_fw("sudo -n ufw disable")
        elif self._fw_type == "iptables":
            self._run_fw("sudo -n systemctl stop iptables 2>/dev/null || sudo -n service iptables stop 2>/dev/null")
        else:
            self._run_fw("sudo -n systemctl stop firewalld")

