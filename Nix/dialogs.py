from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPlainTextEdit, QMessageBox, QPushButton, QLabel, 
    QHBoxLayout, QFrame, QTextEdit, QListWidget, QFileDialog, QLineEdit
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
import posixpath
import threading
import os

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_for_filename, guess_lexer
    from pygments.formatters import HtmlFormatter
    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False

from i18n import t

class RemoteEditorDialog(QDialog):
    def __init__(self, parent, filename, use_sudo, sudo_pwd):
        super().__init__(parent)
        self.parent_ui = parent
        self.filename = filename
        self.use_sudo = use_sudo
        self.sudo_pwd = sudo_pwd
        self.lang = parent.config_mgr.language
        
        self.setWindowTitle(f"Nano: {filename}")
        self.resize(900, 600)
        self.layout = QVBoxLayout(self)
        
        self.txt_editor = QPlainTextEdit()
        self.layout.addWidget(self.txt_editor)
        
        nano_layout = QHBoxLayout()
        
        self.btn_save = QPushButton(t("nano_save", self.lang))
        self.btn_save.clicked.connect(self.save_file)
        
        self.btn_cut = QPushButton(t("nano_cut", self.lang))
        self.btn_cut.clicked.connect(self.txt_editor.cut)
        
        self.btn_paste = QPushButton(t("nano_paste", self.lang))
        self.btn_paste.clicked.connect(self.txt_editor.paste)

        self.btn_exit = QPushButton(t("nano_exit", self.lang))
        self.btn_exit.clicked.connect(self.close)
        
        nano_layout.addWidget(self.btn_save)
        nano_layout.addWidget(self.btn_cut)
        nano_layout.addWidget(self.btn_paste)
        nano_layout.addWidget(self.btn_exit)
        
        self.layout.addLayout(nano_layout)
        
        self.load_file()

    def load_file(self):
        content = ""
        try:
            if self.use_sudo:
                stdin, stdout, stderr = self.parent_ui.ssh_mgr.execute(f"sudo -S cat {self.filename}")
                stdin.write(self.sudo_pwd + "\n")
                stdin.flush()
                content = stdout.read().decode('utf-8', errors='replace')
                if "incorrect password" in stderr.read().decode('utf-8').lower():
                    QMessageBox.critical(self, t("error", self.lang), t("incorrect_pass", self.lang))
                    return
            else:
                sftp = self.parent_ui.ssh_mgr.client.open_sftp()
                with sftp.open(posixpath.join(self.parent_ui.remote_path, self.filename), 'r') as f:
                    content = f.read().decode('utf-8')
                sftp.close()
        except Exception:
            pass
        self.txt_editor.setPlainText(content)

    def save_file(self):
        new_content = self.txt_editor.toPlainText()
        try:
            if self.use_sudo:
                tmp_file = "$HOME/.nebula_nano_tmp.txt"
                sftp = self.parent_ui.ssh_mgr.client.open_sftp()
                with sftp.open(tmp_file, 'w') as f:
                    f.write(new_content)
                sftp.close()
                cmd = f"sudo -S mv {tmp_file} {self.filename} && sudo -S chown root:root {self.filename}"
                stdin, stdout, stderr = self.parent_ui.ssh_mgr.execute(cmd)
                stdin.write(self.sudo_pwd + "\n")
                stdin.flush()
                QMessageBox.information(self, t("success", self.lang), t("saved_root", self.lang))
            else:
                sftp = self.parent_ui.ssh_mgr.client.open_sftp()
                with sftp.open(posixpath.join(self.parent_ui.remote_path, self.filename), 'w') as f:
                    f.write(new_content)
                sftp.close()
                QMessageBox.information(self, t("success", self.lang), t("saved", self.lang))
        except Exception as e:
            QMessageBox.critical(self, t("error", self.lang), str(e))

class ImageViewerDialog(QDialog):
    def __init__(self, parent, filename, img_data):
        super().__init__(parent)
        self.lang = parent.config_mgr.language
        self.setWindowTitle(filename)
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        
        lbl_img = QLabel()
        lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        pixmap = QPixmap()
        pixmap.loadFromData(img_data)
        
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(750, 550, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl_img.setPixmap(scaled_pixmap)
        else:
            lbl_img.setText(t("cannot_load_img", self.lang))
            
        layout.addWidget(lbl_img)

class TextViewerDialog(QDialog):
    def __init__(self, parent, file_path, filename, content):
        super().__init__(parent)
        self.parent_ui = parent
        self.file_path = file_path
        self.filename = filename
        self.lang = parent.config_mgr.language
        
        self.setWindowTitle(filename)
        self.resize(950, 650)
        layout = QVBoxLayout(self)
        
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        
        if HAS_PYGMENTS:
            try:
                lexer = guess_lexer(content) if not filename else get_lexer_for_filename(filename, content)
                bg_color = "#11111b" if parent.config_mgr.theme.get("mode") == "dark" else "#ffffff"
                text_color = "#a6accd" if parent.config_mgr.theme.get("mode") == "dark" else "#000000"
                
                formatter = HtmlFormatter(style='monokai' if parent.config_mgr.theme.get("mode") == "dark" else 'default',
                                          full=True, noclasses=True, 
                                          cssstyles=f"background-color: {bg_color}; color: {text_color}; font-family: 'Consolas', monospace; font-size: 13px;")
                html_content = highlight(content, lexer, formatter)
                txt.setHtml(html_content)
            except Exception:
                txt.setPlainText(content)
        else:
            txt.setPlainText(content)
            
        layout.addWidget(txt)
        
        if "TRUNCATED" in content:
            btn_load_full = QPushButton(t("download_full", self.lang))
            btn_load_full.clicked.connect(self.download_full)
            layout.addWidget(btn_load_full)

    def download_full(self):
        dest = QFileDialog.getExistingDirectory(self, t("destination", self.lang))
        if dest:
            threading.Thread(target=self.parent_ui.download_thread, args=(self.file_path, os.path.join(dest, self.filename), False), daemon=True).start()

class ScreensManagerDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_ui = parent
        self.lang = parent.config_mgr.language
        self.setWindowTitle(t("screens", self.lang))
        self.resize(500, 450)
        
        main_layout = QVBoxLayout(self)
        
        create_layout = QHBoxLayout()
        self.input_new_screen = QLineEdit()
        self.input_new_screen.setPlaceholderText(t("screen_name_prompt", self.lang))
        btn_create = QPushButton(t("create_screen", self.lang))
        btn_create.setStyleSheet("background-color: #17a2b8; color: white;")
        btn_create.clicked.connect(self.create_screen)
        create_layout.addWidget(self.input_new_screen)
        create_layout.addWidget(btn_create)
        main_layout.addLayout(create_layout)

        top_layout = QHBoxLayout()
        btn_refresh = QPushButton(t("refresh_list", self.lang))
        btn_refresh.clicked.connect(self.refresh_screens)
        top_layout.addWidget(btn_refresh)
        top_layout.addStretch()
        main_layout.addLayout(top_layout)
        
        self.screens_container = QVBoxLayout()
        main_layout.addLayout(self.screens_container)
        main_layout.addStretch()
        
        self.refresh_screens()

    def create_screen(self):
        name = self.input_new_screen.text().strip()
        if name:
            self.parent_ui.ssh_mgr.execute(f"screen -dmS {name}")
            self.input_new_screen.clear()
            self.refresh_screens()

    def refresh_screens(self):
        for i in reversed(range(self.screens_container.count())): 
            self.screens_container.itemAt(i).widget().setParent(None)
            
        lbl = QLabel(t("fetching_screens", self.lang))
        self.screens_container.addWidget(lbl)
        
        def fetch():
            try:
                stdin, stdout, stderr = self.parent_ui.ssh_mgr.execute("screen -ls")
                out = stdout.read().decode()
                self.parent_ui.sig_screens.emit(self, out)
            except Exception:
                pass
        threading.Thread(target=fetch, daemon=True).start()

    def update_ui(self, output):
        for i in reversed(range(self.screens_container.count())): 
            self.screens_container.itemAt(i).widget().setParent(None)
            
        lines = output.splitlines()
        found = False
        for line in lines:
            line = line.strip()
            if line and "(" in line and line[0].isdigit():
                found = True
                full_name = line.split("(")[0].strip()
                
                frame = QFrame()
                frame.setObjectName("Card")
                layout = QHBoxLayout(frame)
                
                lbl = QLabel(f"📺 {full_name}")
                lbl.setStyleSheet("font-weight: bold;")
                layout.addWidget(lbl)
                
                btn_del = QPushButton(t("delete_btn", self.lang))
                btn_del.setStyleSheet("background-color: #dc3545;")
                btn_del.clicked.connect(lambda _, n=full_name: self.kill_screen(n))
                
                btn_out = QPushButton(t("detach_btn", self.lang))
                btn_out.setStyleSheet("background-color: #ffc107; color: black;")
                btn_out.clicked.connect(lambda _, n=full_name: self.detach_screen(n))
                
                btn_in = QPushButton(t("attach_btn", self.lang))
                btn_in.setStyleSheet("background-color: #28a745;")
                btn_in.clicked.connect(lambda _, n=full_name: self.attach_screen(n))
                
                layout.addWidget(btn_del)
                layout.addWidget(btn_out)
                layout.addWidget(btn_in)
                self.screens_container.addWidget(frame)
                
        if not found:
            self.screens_container.addWidget(QLabel(t("no_screens", self.lang)))

    def attach_screen(self, name):
        self.parent_ui.cmd_input.setText(f"screen -r {name}")
        self.parent_ui.send_command()
        self.close()

    def detach_screen(self, name):
        self.parent_ui.ssh_mgr.execute(f"screen -d {name}")
        self.parent_ui.sig_log.emit(f"Detached {name}")
        self.parent_ui.sig_screen_status.emit(False, "")
        self.refresh_screens()

    def kill_screen(self, name):
        reply = QMessageBox.question(self, t("confirm", self.lang), f"{t('kill_screen', self.lang)} {name}?")
        if reply == QMessageBox.StandardButton.Yes:
            self.parent_ui.ssh_mgr.execute(f"screen -S {name} -X quit")
            self.parent_ui.sig_screen_status.emit(False, "")
            self.refresh_screens()

class EnvManagerDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_ui = parent
        self.lang = parent.config_mgr.language
        self.setWindowTitle(t("env_list", self.lang))
        self.resize(700, 500)
        
        layout = QVBoxLayout(self)
        
        lbl = QLabel(t("fetching_envs", self.lang))
        layout.addWidget(lbl)
        
        self.env_list_widget = QListWidget()
        layout.addWidget(self.env_list_widget)
        
        btn_layout = QHBoxLayout()
        btn_activate = QPushButton(t("activate_sel", self.lang))
        btn_activate.setStyleSheet("background-color: #28a745; color: white;")
        
        btn_deactivate = QPushButton(t("deactivate_env", self.lang))
        btn_deactivate.setStyleSheet("background-color: #ffc107; color: black;")
        
        btn_layout.addWidget(btn_activate)
        btn_layout.addWidget(btn_deactivate)
        layout.addLayout(btn_layout)
        
        lbl_raw = QLabel(t("raw_output", self.lang))
        layout.addWidget(lbl_raw)
        
        self.env_raw_txt = QTextEdit()
        self.env_raw_txt.setReadOnly(True)
        self.env_raw_txt.setStyleSheet("background-color: #1e1e2e; color: #A6ACCD; font-family: 'Times New Roman'; font-size: 11pt;")
        self.env_raw_txt.setFixedHeight(150)
        layout.addWidget(self.env_raw_txt)

        btn_activate.clicked.connect(self.activate_env)
        self.env_list_widget.itemDoubleClicked.connect(self.activate_env)
        btn_deactivate.clicked.connect(self.deactivate_env)

        self.fetch_envs()

    def fetch_envs(self):
        def fetch():
            cmd = (
                "source ~/.bashrc 2>/dev/null; "
                "source ~/.bash_profile 2>/dev/null; "
                "conda info --envs 2>/dev/null || "
                "$HOME/miniconda3/bin/conda info --envs 2>/dev/null || "
                "$HOME/anaconda3/bin/conda info --envs 2>/dev/null || "
                "/opt/conda/bin/conda info --envs 2>/dev/null"
            )
            try:
                stdin, stdout, stderr = self.parent_ui.ssh_mgr.execute(cmd)
                stdout.channel.settimeout(10.0)
                out = stdout.read().decode('utf-8', errors='replace')
                
                envs = []
                for line in out.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "conda environments:" in line.lower(): 
                        continue
                    parts = line.split()
                    if parts:
                        env_name = parts[0]
                        if "/" in env_name or "\\" in env_name:
                            env_name = os.path.basename(env_name)
                        if env_name and env_name.lower() != "conda":
                            envs.append(env_name)
                
                envs = sorted(list(set(envs)))
                self.parent_ui.sig_env_list.emit(self, out, envs)
            except Exception as e:
                self.parent_ui.sig_env_list.emit(self, f"Error: {e}", [])

        threading.Thread(target=fetch, daemon=True).start()

    def update_ui(self, content, envs):
        self.env_raw_txt.setPlainText(content)
        self.env_list_widget.clear()
        if envs:
            for env in envs:
                self.env_list_widget.addItem(env)
        else:
            self.env_list_widget.addItem(t("no_envs", self.lang))

    def activate_env(self):
        selected = self.env_list_widget.currentItem()
        if selected:
            env_name = selected.text()
            if "⚠️" not in env_name and t("no_envs", self.lang) not in env_name: 
                self.parent_ui.cmd_input.setText(f"conda activate {env_name}")
                self.parent_ui.send_command()
                self.accept()

    def deactivate_env(self):
        self.parent_ui.cmd_input.setText("conda deactivate")
        self.parent_ui.send_command()
        self.accept()
