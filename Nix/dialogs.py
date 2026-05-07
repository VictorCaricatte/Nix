from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QPlainTextEdit, QMessageBox, QPushButton, QLabel, 
    QHBoxLayout, QFrame, QTextEdit, QListWidget, QFileDialog, QLineEdit,
    QTableView, QHeaderView, QApplication, QWidget, QTreeWidget, QTreeWidgetItem
)
from PyQt6.QtCore import Qt, QTimer, QSortFilterProxyModel, QRect, QRegularExpression
from PyQt6.QtGui import (
    QPixmap, QStandardItemModel, QStandardItem, QColor, QFont, 
    QPainter, QTextFormat, QSyntaxHighlighter, QTextCharFormat
)
import posixpath
import threading
import os
import csv
import io

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_for_filename, guess_lexer
    from pygments.formatters import HtmlFormatter
    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

from i18n import t, TRANSLATIONS

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.codeEditor = editor

    def sizeHint(self):
        return self.codeEditor.line_number_area_width()

    def paintEvent(self, event):
        self.codeEditor.line_number_area_paint_event(event)

class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        self.update_line_number_area_width(0)
        self.highlight_current_line()

    def line_number_area_width(self):
        digits = 1
        max_num = max(1, self.blockCount())
        while max_num >= 10:
            max_num //= 10
            digits += 1
        space = 15 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def highlight_current_line(self):
        extra_selections = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor("#2a2a35") 
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#1e1e2e")) 

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#6c7086")) 
                painter.drawText(0, top, self.line_number_area.width() - 5, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, number)

            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

class SyntaxHighlighter(QSyntaxHighlighter):
    def __init__(self, document, is_dark_mode=True):
        super().__init__(document)
        self.highlighting_rules = []
        
        if is_dark_mode:
            c_keyword = QColor("#c678dd")  
            c_type = QColor("#e5c07b")     
            c_string = QColor("#98c379")   
            c_comment = QColor("#5c6370")  
            c_number = QColor("#d19a66")   
        else:
            c_keyword = QColor("#a626a4")
            c_type = QColor("#c18401")
            c_string = QColor("#50a14f")
            c_comment = QColor("#a0a1a7")
            c_number = QColor("#986801")

        keyword_format = QTextCharFormat()
        keyword_format.setForeground(c_keyword)
        keyword_format.setFontWeight(QFont.Weight.Bold)
        keywords = [
            "\\bdef\\b", "\\bclass\\b", "\\bimport\\b", "\\bfrom\\b", "\\bif\\b", "\\belse\\b", "\\belif\\b", 
            "\\bwhile\\b", "\\bfor\\b", "\\bin\\b", "\\breturn\\b", "\\bbreak\\b", "\\bcontinue\\b", "\\bpass\\b",
            "\\bvar\\b", "\\blet\\b", "\\bconst\\b", "\\bfunction\\b", "\\btrue\\b", "\\bfalse\\b", "\\bNone\\b",
            "\\becho\\b", "\\bfi\\b", "\\bdone\\b", "\\bcase\\b", "\\besac\\b", "\\bexport\\b"
        ]
        for word in keywords:
            self.highlighting_rules.append((QRegularExpression(word), keyword_format))

        type_format = QTextCharFormat()
        type_format.setForeground(c_type)
        types = ["\\bint\\b", "\\bstr\\b", "\\bfloat\\b", "\\bbool\\b", "\\blist\\b", "\\bdict\\b", "\\bprint\\b", "\\bconsole\\b"]
        for word in types:
            self.highlighting_rules.append((QRegularExpression(word), type_format))

        number_format = QTextCharFormat()
        number_format.setForeground(c_number)
        self.highlighting_rules.append((QRegularExpression("\\b[0-9]+\\b"), number_format))

        self.string_format = QTextCharFormat()
        self.string_format.setForeground(c_string)
        self.highlighting_rules.append((QRegularExpression("\".*?\""), self.string_format))
        self.highlighting_rules.append((QRegularExpression("'.*?'"), self.string_format))

        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(c_comment)
        self.highlighting_rules.append((QRegularExpression("#[^\n]*"), self.comment_format))
        self.highlighting_rules.append((QRegularExpression("//[^\n]*"), self.comment_format))

    def highlightBlock(self, text):
        for pattern, format in self.highlighting_rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format)

class RemoteEditorDialog(QDialog):
    def __init__(self, parent, filename, use_sudo, sudo_pwd):
        super().__init__(parent)
        self.parent_ui = parent
        self.filename = filename
        self.use_sudo = use_sudo
        self.sudo_pwd = sudo_pwd
        self.lang = parent.config_mgr.language
        
        if self.filename.startswith('/'):
            self.full_path = self.filename
        else:
            self.full_path = posixpath.join(self.parent_ui.remote_path, self.filename)
        
        self.setWindowTitle(f"Nano: {filename}")
        self.resize(1000, 700)
        self.layout = QVBoxLayout(self)
        
        self.txt_editor = CodeEditor()
        self.layout.addWidget(self.txt_editor)
        
        is_dark = parent.config_mgr.theme.get("mode", "dark") == "dark"
        self.highlighter = SyntaxHighlighter(self.txt_editor.document(), is_dark_mode=is_dark)
        
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
                safe_path = self.full_path.replace('"', '\\"')
                stdin, stdout, stderr = self.parent_ui.ssh_mgr.execute(f'sudo -S cat "{safe_path}"')
                stdout.channel.settimeout(5.0)
                stderr.channel.settimeout(5.0)
                
                stdin.write(self.sudo_pwd + "\n")
                stdin.flush()
                stdin.close()
                
                err_out = ""
                try:
                    err_out = stderr.read().decode('utf-8', errors='replace').lower()
                except Exception:
                    pass
                    
                content_bytes = b""
                try:
                    content_bytes = stdout.read()
                except Exception:
                    pass
                    
                content = content_bytes.decode('utf-8', errors='replace')
                
                if "incorrect password" in err_out or "senha incorreta" in err_out or "try again" in err_out or "tente novamente" in err_out:
                    QMessageBox.critical(self, t("error", self.lang), f"{t('incorrect_pass', self.lang)}\n{err_out.strip()}")
                    QTimer.singleShot(0, self.reject)
                    return
            else:
                sftp = self.parent_ui.ssh_mgr.client.open_sftp()
                with sftp.open(self.full_path, 'r') as f:
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
                
                safe_path = self.full_path.replace('"', '\\"')
                cmd = f'sudo -S mv {tmp_file} "{safe_path}" && sudo -S chown root:root "{safe_path}"'
                
                stdin, stdout, stderr = self.parent_ui.ssh_mgr.execute(cmd)
                stdin.write(self.sudo_pwd + "\n")
                stdin.flush()
                stdin.close()
                
                err_output = stderr.read().decode('utf-8', errors='replace').lower()
                if "incorrect password" in err_output or "senha incorreta" in err_output or "try again" in err_output or "tente novamente" in err_output:
                    QMessageBox.critical(self, t("error", self.lang), t("incorrect_pass", self.lang))
                    return
                QMessageBox.information(self, t("success", self.lang), t("saved_root", self.lang))
            else:
                sftp = self.parent_ui.ssh_mgr.client.open_sftp()
                with sftp.open(self.full_path, 'w') as f:
                    f.write(new_content)
                sftp.close()
                QMessageBox.information(self, t("success", self.lang), t("saved", self.lang))
        except Exception as e:
            QMessageBox.critical(self, t("error", self.lang), str(e))

class TableViewerDialog(QDialog):
    def __init__(self, parent, file_path, filename, content):
        super().__init__(parent)
        self.parent_ui = parent
        self.file_path = file_path
        self.filename = filename
        self.lang = parent.config_mgr.language
        self.delimiter = ','
        
        self.setWindowTitle(f"{t('table_viewer', self.lang)}: {filename}")
        self.resize(1000, 700)
        
        layout = QVBoxLayout(self)
        
        top_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(t("search_table", self.lang))
        self.search_input.textChanged.connect(self.filter_table)
        top_layout.addWidget(self.search_input)
        
        btn_copy = QPushButton(t("copy_table", self.lang) if "copy_table" in TRANSLATIONS[self.lang] else "Copy")
        btn_copy.clicked.connect(self.copy_selection)
        top_layout.addWidget(btn_copy)
        
        btn_save = QPushButton(t("save_table", self.lang) if "save_table" in TRANSLATIONS[self.lang] else "Save")
        btn_save.clicked.connect(self.save_table)
        top_layout.addWidget(btn_save)
        
        layout.addLayout(top_layout)
        
        self.table_view = QTableView()
        self.model = QStandardItemModel()
        self.proxy_model = QSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy_model.setFilterKeyColumn(-1)
        
        self.table_view.setModel(self.proxy_model)
        self.table_view.setSortingEnabled(True)
        layout.addWidget(self.table_view)
        
        self.populate_data(filename, content)
        
    def populate_data(self, filename, content):
        if filename.lower().endswith('.xlsx'):
            if not HAS_PANDAS:
                QMessageBox.warning(self, "Aviso", "As bibliotecas 'pandas' e 'openpyxl' são necessárias para editar .xlsx. O ambiente local não as possui.")
                return
            try:
                df = pd.read_excel(io.BytesIO(content))
                self.model.setHorizontalHeaderLabels([str(c) for c in df.columns])
                for _, row in df.iterrows():
                    row_items = [QStandardItem(str(cell)) for cell in row]
                    self.model.appendRow(row_items)
            except Exception as e:
                QMessageBox.critical(self, t("error", self.lang), f"Erro Pandas: {str(e)}")
        else:
            self.delimiter = '\t' if filename.lower().endswith('.tsv') else ','
            if filename.lower().endswith('.csv') and ';' in content[:1024] and ',' not in content[:1024]:
                self.delimiter = ';'
                
            f = io.StringIO(content)
            reader = csv.reader(f, delimiter=self.delimiter)
            
            try:
                headers = next(reader)
                self.model.setHorizontalHeaderLabels(headers)
                
                for row_data in reader:
                    row_items = [QStandardItem(str(cell)) for cell in row_data]
                    self.model.appendRow(row_items)
            except StopIteration:
                pass
            except Exception:
                pass
        
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            self.copy_selection()
        else:
            super().keyPressEvent(event)

    def copy_selection(self):
        selection = self.table_view.selectionModel().selectedIndexes()
        if not selection: return
        
        rows = sorted(list(set(index.row() for index in selection)))
        columns = sorted(list(set(index.column() for index in selection)))
        
        clipboard_string = ""
        for row in rows:
            row_data = []
            for col in columns:
                idx = self.proxy_model.index(row, col)
                if idx in selection:
                    row_data.append(str(idx.data()))
                else:
                    row_data.append("")
            clipboard_string += "\t".join(row_data) + "\n"
            
        QApplication.clipboard().setText(clipboard_string)

    def filter_table(self, text):
        self.proxy_model.setFilterFixedString(text)

    def save_table(self):
        mode = 'w'
        try:
            if self.filename.lower().endswith('.xlsx'):
                if not HAS_PANDAS: return
                data = []
                for row in range(self.model.rowCount()):
                    row_data = []
                    for col in range(self.model.columnCount()):
                        item = self.model.item(row, col)
                        row_data.append(item.text() if item else "")
                df = pd.DataFrame(data, columns=[self.model.horizontalHeaderItem(i).text() for i in range(self.model.columnCount())])
                output = io.BytesIO()
                df.to_excel(output, index=False)
                new_content = output.getvalue()
                mode = 'wb'
            else:
                output = io.StringIO()
                writer = csv.writer(output, delimiter=self.delimiter)
                headers = [self.model.horizontalHeaderItem(i).text() for i in range(self.model.columnCount())]
                writer.writerow(headers)
                for row in range(self.model.rowCount()):
                    row_data = []
                    for col in range(self.model.columnCount()):
                        item = self.model.item(row, col)
                        row_data.append(item.text() if item else "")
                    writer.writerow(row_data)
                new_content = output.getvalue()

            try:
                with self.parent_ui.ssh_mgr.lock:
                    with self.parent_ui.ssh_mgr.sftp.open(self.file_path, mode) as f:
                        f.write(new_content)
                QMessageBox.information(self, t("success", self.lang), t("saved", self.lang))
            except Exception as e:
                if "Permission" in str(e) or "denied" in str(e).lower():
                    tmp_file = "$HOME/.nebula_table_tmp"
                    with self.parent_ui.ssh_mgr.client.open_sftp() as sftp:
                        with sftp.open(tmp_file, mode) as f:
                            f.write(new_content)
                            
                    safe_path = self.file_path.replace('"', '\\"')
                    pwd = self.parent_ui.sudo_cache[0]
                    if not pwd:
                        pwd = self.parent_ui._get_sudo_pwd()
                        self.parent_ui.sudo_cache[0] = pwd
                        
                    if not pwd:
                        raise Exception("SUDO cancelado")
                        
                    cmd = f'sudo -S mv {tmp_file} "{safe_path}" && sudo -S chown root:root "{safe_path}"'
                    stdin, stdout, stderr = self.parent_ui.ssh_mgr.execute(cmd)
                    stdin.write(pwd + "\n")
                    stdin.flush()
                    err_out = stderr.read().decode().lower()
                    if "incorrect password" in err_out or "senha incorreta" in err_out:
                        self.parent_ui.sudo_cache[0] = None
                        raise Exception("Senha do sudo incorreta.")
                    QMessageBox.information(self, t("success", self.lang), t("saved_root", self.lang))
                else:
                    raise e
        except Exception as e:
            QMessageBox.critical(self, t("error", self.lang), f"Erro ao salvar: {str(e)}")

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
            threading.Thread(target=self.parent_ui.download_thread, args=(self.file_path, os.path.join(dest, self.filename), False, "dl_full"), daemon=True).start()

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
        self.parent_ui.cmd_input.setText(f"screen -x {name}")
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

        custom_layout = QHBoxLayout()
        self.custom_conda_path = QLineEdit()
        self.custom_conda_path.setPlaceholderText(t("custom_conda_path", self.lang))
        self.custom_conda_path.setText(self.parent_ui.config_mgr.conda_path)
        btn_custom = QPushButton(t("fetch_manual", self.lang))
        btn_custom.clicked.connect(self.fetch_envs_custom)
        custom_layout.addWidget(self.custom_conda_path)
        custom_layout.addWidget(btn_custom)
        layout.addLayout(custom_layout)
        
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

    def fetch_envs_custom(self):
        path = self.custom_conda_path.text().strip()
        if path:
            self.parent_ui.config_mgr.conda_path = path
            self.parent_ui.config_mgr.save_config()
            self.fetch_envs(custom_path=path)

    def fetch_envs(self, custom_path=None):
        def fetch():
            cmd = (
                "source ~/.bashrc 2>/dev/null; "
                "source ~/.bash_profile 2>/dev/null; "
                "conda info --envs 2>/dev/null || "
                "$HOME/miniconda3/bin/conda info --envs 2>/dev/null || "
                "$HOME/anaconda3/bin/conda info --envs 2>/dev/null || "
                "/opt/conda/bin/conda info --envs 2>/dev/null"
            )
            if custom_path:
                if custom_path.endswith('bin/conda') or custom_path.endswith('/conda'):
                    cmd += f" || {custom_path} info --envs 2>/dev/null"
                else:
                    cmd += f" || {posixpath.join(custom_path, 'bin/conda')} info --envs 2>/dev/null"
                    
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


class AdvancedSearchDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_ui = parent
        self.lang = parent.config_mgr.language
        
        title = "Advanced Search / Busca Avançada"
        if self.lang == "pt": title = "Busca Avançada no Servidor"
        elif self.lang == "es": title = "Búsqueda Avanzada en Servidor"
        
        self.setWindowTitle(title)
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        form_layout = QHBoxLayout()
        self.input_dir = QLineEdit(self.parent_ui.remote_path)
        self.input_dir.setPlaceholderText("Diretório Inicial (ex: /home)")
        
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("Nome do arquivo (ex: *.py)")
        
        self.input_content = QLineEdit()
        self.input_content.setPlaceholderText("Conteúdo dentro do arquivo (Grep)")
        
        btn_search = QPushButton("Buscar" if self.lang != "en" else "Search")
        btn_search.setStyleSheet("background-color: #007bff; color: white;")
        btn_search.clicked.connect(self.run_search)
        
        form_layout.addWidget(QLabel("Dir:"))
        form_layout.addWidget(self.input_dir)
        form_layout.addWidget(QLabel("Nome:" if self.lang != "en" else "Name:"))
        form_layout.addWidget(self.input_name)
        form_layout.addWidget(QLabel("Conteúdo:" if self.lang != "en" else "Content:"))
        form_layout.addWidget(self.input_content)
        form_layout.addWidget(btn_search)
        
        layout.addLayout(form_layout)
        
        self.results_tree = QTreeWidget()
        self.results_tree.setHeaderLabels(["Caminho do Arquivo", "Detalhes"])
        self.results_tree.setColumnWidth(0, 400)
        self.results_tree.itemDoubleClicked.connect(self.on_item_double_click)
        layout.addWidget(self.results_tree)
        
    def run_search(self):
        self.results_tree.clear()
        search_dir = self.input_dir.text().strip() or "."
        name = self.input_name.text().strip()
        content = self.input_content.text().strip()
        
        if not name and not content:
            QMessageBox.warning(self, "Aviso", "Preencha o campo de Nome ou Conteúdo para buscar.")
            return
            
        cmd = ""
        if content:
            # Usa o grep recursivo para achar o texto dentro de arquivos
            safe_content = content.replace("'", "'\\''")
            cmd = f"grep -rn '{safe_content}' \"{search_dir}\" | head -n 100"
        else:
            # Usa o find para achar o arquivo pelo nome
            safe_name = name.replace("'", "'\\''")
            cmd = f"find \"{search_dir}\" -type f -iname '*{safe_name}*' | head -n 100"
            
        self.results_tree.addTopLevelItem(QTreeWidgetItem(["Buscando...", cmd]))
        threading.Thread(target=self._do_search, args=(cmd,), daemon=True).start()
        
    def _do_search(self, cmd):
        try:
            stdin, stdout, stderr = self.parent_ui.ssh_mgr.execute(cmd)
            out = stdout.read().decode('utf-8', errors='replace').strip()
            err = stderr.read().decode('utf-8', errors='replace').strip()
            QTimer.singleShot(0, lambda: self._update_results(out, err))
        except Exception as e:
            QTimer.singleShot(0, lambda: self._update_results("", str(e)))
            
    def _update_results(self, out, err):
        self.results_tree.clear()
        if err and not out:
            self.results_tree.addTopLevelItem(QTreeWidgetItem(["Erro na busca", err]))
            return
            
        lines = out.splitlines()
        if not lines:
            self.results_tree.addTopLevelItem(QTreeWidgetItem(["Nenhum resultado encontrado.", ""]))
            return
            
        for line in lines:
            parts = line.split(":", 1)
            # Se usou Grep, ele retorna /caminho/do/arquivo:Linha_do_codigo
            if len(parts) == 2 and self.input_content.text().strip():
                item = QTreeWidgetItem([parts[0], parts[1].strip()])
            else:
                item = QTreeWidgetItem([line, "Encontrado (Find)"])
            self.results_tree.addTopLevelItem(item)
            
    def on_item_double_click(self, item, col):
        path = item.text(0)
        if not path or path.startswith("Buscando") or path.startswith("Erro") or path.startswith("Nenhum"):
            return
            
        # Ao clicar duas vezes no resultado, o File Explorer principal viaja para a pasta do arquivo
        target_dir = posixpath.dirname(path)
        if target_dir:
            self.parent_ui.remote_path = target_dir
            self.parent_ui.cmd_input.setText(f"cd \"{target_dir}\"")
            self.parent_ui.send_command()
        
        self.close()
