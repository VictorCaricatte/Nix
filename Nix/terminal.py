"""
terminal.py — Terminal real embutido (emulador VT baseado em pyte).

O widget PyteTerminal mantém uma matriz de tela via pyte (cores ANSI/256/24-bit,
cursor, limpeza de tela, regiões de scroll) e a renderiza com QPainter.
Isso permite que apps interativos (vim, htop, less, nano, tmux) funcionem
de verdade dentro do Nix, ao contrário do antigo QPlainTextEdit que
descartava todas as sequências de escape.

Camadas implementadas aqui:
  - Renderização da grade do pyte (com scrollback próprio via HistoryScreen)
  - Tradução de teclado Qt -> sequências VT (setas, F1-F12, Ctrl, Alt...)
  - Seleção com mouse + copiar/colar
  - Redimensionamento dinâmico do PTY conforme o tamanho do widget/fonte
"""

import socket
import threading

import pyte
from PyQt6.QtWidgets import QAbstractScrollArea, QMenu, QApplication, QFrame
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QFont, QFontMetricsF, QColor, QAction, QPalette,
    QRadialGradient, QBrush,
)

# Paleta ANSI para fundo ESCURO (tons Dracula, combinando com o accent #bd93f9)
_ANSI_DARK = {
    "black":   "#21222c", "red":     "#ff5555", "green":   "#50fa7b",
    "brown":   "#f1fa8c", "blue":    "#bd93f9", "magenta": "#ff79c6",
    "cyan":    "#8be9fd", "white":   "#f8f8f2",
    "brightblack":   "#6272a4", "brightred":     "#ff6e6e",
    "brightgreen":   "#69ff94", "brightbrown":   "#ffffa5",
    "brightblue":    "#d6acff", "brightmagenta": "#ff92df",
    "brightcyan":    "#a4ffff", "brightwhite":   "#ffffff",
}
# Paleta ANSI para fundo CLARO (tons One Light: cores escurecidas para
# permanecerem legíveis sobre #f5f5f5 — a paleta escura sumia no claro)
_ANSI_LIGHT = {
    "black":   "#383a42", "red":     "#e45649", "green":   "#50a14f",
    "brown":   "#c18401", "blue":    "#4078f2", "magenta": "#a626a4",
    "cyan":    "#0184bc", "white":   "#a0a1a7",
    "brightblack":   "#696c77", "brightred":     "#ca1243",
    "brightgreen":   "#40803f", "brightbrown":   "#986801",
    "brightblue":    "#366ad0", "brightmagenta": "#8a2c87",
    "brightcyan":    "#016b93", "brightwhite":   "#383a42",
}
_BRIGHT_OF = {
    "black": "brightblack", "red": "brightred", "green": "brightgreen",
    "brown": "brightbrown", "blue": "brightblue", "magenta": "brightmagenta",
    "cyan": "brightcyan", "white": "brightwhite",
}
_SEL_BG_DARK  = QColor("#44475a")
_SEL_BG_LIGHT = QColor("#b3d4fc")

# Teclas especiais -> sequências VT (modo normal; suficiente para
# bash/vim/htop/nano com TERM=xterm-256color)
_KEY_SEQS = {
    Qt.Key.Key_Up:       "\x1b[A",  Qt.Key.Key_Down:     "\x1b[B",
    Qt.Key.Key_Right:    "\x1b[C",  Qt.Key.Key_Left:     "\x1b[D",
    Qt.Key.Key_Home:     "\x1b[H",  Qt.Key.Key_End:      "\x1b[F",
    Qt.Key.Key_PageUp:   "\x1b[5~", Qt.Key.Key_PageDown: "\x1b[6~",
    Qt.Key.Key_Insert:   "\x1b[2~", Qt.Key.Key_Delete:   "\x1b[3~",
    Qt.Key.Key_Backtab:  "\x1b[Z",
    Qt.Key.Key_F1:  "\x1bOP",   Qt.Key.Key_F2:  "\x1bOQ",
    Qt.Key.Key_F3:  "\x1bOR",   Qt.Key.Key_F4:  "\x1bOS",
    Qt.Key.Key_F5:  "\x1b[15~", Qt.Key.Key_F6:  "\x1b[17~",
    Qt.Key.Key_F7:  "\x1b[18~", Qt.Key.Key_F8:  "\x1b[19~",
    Qt.Key.Key_F9:  "\x1b[20~", Qt.Key.Key_F10: "\x1b[21~",
    Qt.Key.Key_F11: "\x1b[23~", Qt.Key.Key_F12: "\x1b[24~",
}

# Nome ("F5") -> sequência VT, para atalhos do app repassarem F-keys ao
# terminal quando ele está com foco (ex.: F10 sai do htop, não desconecta).
FKEY_SEQS = {
    "F1": "\x1bOP",   "F2": "\x1bOQ",   "F3": "\x1bOR",   "F4": "\x1bOS",
    "F5": "\x1b[15~", "F6": "\x1b[17~", "F7": "\x1b[18~", "F8": "\x1b[19~",
    "F9": "\x1b[20~", "F10": "\x1b[21~", "F11": "\x1b[23~", "F12": "\x1b[24~",
}


class PyteTerminal(QAbstractScrollArea):
    """Terminal VT real: recebe bytes do canal SSH via feed() e renderiza."""

    def __init__(self, parent_tab=None, history=3000):
        super().__init__()
        self.parent_tab = parent_tab
        self._history_size = history

        self._cols = 120
        self._rows = 30
        self.screen = pyte.HistoryScreen(self._cols, self._rows,
                                         history=history, ratio=0.35)
        self.stream = pyte.Stream(self.screen)

        self.auto_scroll = True
        self._sel_anchor = None      # (linha_absoluta, coluna)
        self._sel_end = None
        self._def_fg = QColor("#a6accd")
        self._def_bg = QColor("#0d0a0d")
        self._ansi = _ANSI_DARK
        self._sel_bg = _SEL_BG_DARK
        self._crt = False            # efeitos CRT (scanlines, glow, vinheta)
        self._font_cache = {}

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().valueChanged.connect(
            lambda _: self.viewport().update())

        # IME (acentos/dead-keys em pt-BR) precisa disso habilitado
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)

        self._font = QFont("Consolas", 13)
        self._recalc_metrics()

        # Repaint agrupado: muitas chegadas pequenas de dados viram 1 repaint
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(15)
        self._flush_timer.timeout.connect(self._refresh)

        # Segura o repaint enquanto o hook do shell imprime e apaga o próprio eco
        self._repaint_held = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._release_repaint_now)

        # Debounce do resize (evita spam de resize_pty ao arrastar a janela)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(180)
        self._resize_timer.timeout.connect(lambda: self._apply_resize(force=True))

    # ------------------------------------------------------------------ #
    #  Entrada de dados                                                   #
    # ------------------------------------------------------------------ #
    def feed(self, text):
        """Alimenta texto vindo do canal SSH (chamar na thread da GUI)."""
        if not text:
            return
        try:
            self.stream.feed(text)
        except Exception:
            pass  # sequência exótica não pode derrubar o terminal
        if not self._repaint_held and not self._flush_timer.isActive():
            self._flush_timer.start()

    def hold_repaint(self, max_ms):
        """Não redesenha por até max_ms (ver release_repaint)."""
        self._repaint_held = True
        self._hold_timer.start(max_ms)

    def release_repaint(self, delay_ms=60):
        """Libera o repaint após uma folga para o prompt novo chegar."""
        if self._repaint_held:
            self._hold_timer.start(delay_ms)

    def _release_repaint_now(self):
        self._repaint_held = False
        self._refresh()

    def cursor_line(self):
        """Texto da linha do cursor até ele; vazio se há algo à direita (o
        usuário moveu o cursor para o meio do que digitou)."""
        cur = self.screen.cursor
        row = self.screen.buffer[cur.y]
        if "".join(row[x].data for x in range(cur.x, self.screen.columns)).strip():
            return ""
        return "".join(row[x].data for x in range(min(cur.x, self.screen.columns)))

    def send_text(self, data, notify=True):
        """Envia texto ao shell. notify=False é para o que o próprio Nix digita:
        não conta como entrada do usuário."""
        self._send(data, notify)

    def append_local(self, message):
        """Mensagem local do Nix (ações da explorer, avisos) em cinza."""
        msg = str(message).replace("\r\n", "\n").replace("\n", "\r\n")
        self.feed(f"\r\n\x1b[38;5;245m> {msg}\x1b[0m\r\n")

    # Compatibilidade com a API antiga do QPlainTextEdit
    def appendPlainText(self, text):
        self.append_local(text.lstrip("\n"))

    def setMaximumBlockCount(self, _n):
        pass  # o limite agora é o history do pyte

    def clear(self):
        self.screen.reset()
        try:
            self.screen.history.top.clear()
            self.screen.history.bottom.clear()
        except Exception:
            pass
        self._sel_anchor = self._sel_end = None
        self._refresh()

    # ------------------------------------------------------------------ #
    #  Tema / fonte                                                       #
    # ------------------------------------------------------------------ #
    def set_terminal_style(self, family, size, bold, fg_hex, bg_hex,
                           light_palette=False, crt=False):
        self._font = QFont(family, max(6, int(size)))
        self._font.setBold(bool(bold))
        self._font_cache.clear()
        self._def_fg = QColor(fg_hex)
        self._def_bg = QColor(bg_hex)
        self._ansi = _ANSI_LIGHT if light_palette else _ANSI_DARK
        self._sel_bg = _SEL_BG_LIGHT if light_palette else _SEL_BG_DARK
        self._crt = bool(crt)
        pal = self.viewport().palette()
        pal.setColor(QPalette.ColorRole.Base, self._def_bg)
        self.viewport().setPalette(pal)
        self._recalc_metrics()
        self._apply_resize(force=True)
        self.viewport().update()

    def _recalc_metrics(self):
        fm = QFontMetricsF(self._font)
        self._cell_w = max(1.0, fm.horizontalAdvance("M"))
        self._cell_h = max(1.0, fm.lineSpacing())
        self._ascent = fm.ascent()

    def _font_for(self, bold, italics, underline):
        key = (bold, italics, underline)
        f = self._font_cache.get(key)
        if f is None:
            f = QFont(self._font)
            if bold:
                f.setBold(True)
            f.setItalic(italics)
            f.setUnderline(underline)
            self._font_cache[key] = f
        return f

    # ------------------------------------------------------------------ #
    #  Tamanho do PTY                                                     #
    # ------------------------------------------------------------------ #
    def term_size(self):
        return self._cols, self._rows

    def sync_pty_size(self):
        """Força o PTY remoto a assumir o tamanho atual do widget."""
        self._apply_resize(force=True)

    def _apply_resize(self, force=False):
        vp = self.viewport()
        cols = max(20, int(vp.width() // self._cell_w))
        rows = max(4, int(vp.height() // self._cell_h))
        if not force and cols == self._cols and rows == self._rows:
            return
        old_rows = self.screen.lines
        self._cols, self._rows = cols, rows
        try:
            if rows < old_rows:
                self._push_top_lines_to_history(old_rows - rows)
            self.screen.resize(rows, cols)
            if rows < old_rows:
                # pyte apaga as linhas do topo mas não move o cursor: acompanha
                self.screen.cursor.y = max(0, min(rows - 1,
                                                  self.screen.cursor.y - (old_rows - rows)))
        except Exception:
            pass
        tab = self.parent_tab
        if tab is not None and getattr(tab.ssh_mgr, "is_connected", False):
            threading.Thread(target=tab.ssh_mgr.resize_pty,
                             args=(cols, rows), daemon=True).start()
        self._refresh()

    def _push_top_lines_to_history(self, count):
        """Ao encolher a tela o pyte DESCARTA as linhas do topo; antes disso,
        move-as para o scrollback para o texto não sumir (e voltar a ser
        rolável) quando o painel é reduzido e depois ampliado."""
        hist = self.screen.history.top
        for y in range(min(count, self.screen.lines)):
            line = self.screen.buffer[y]
            if any(ch.data.strip() for ch in line.values()):
                hist.append(dict(line))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start()

    # ------------------------------------------------------------------ #
    #  Scrollback                                                         #
    # ------------------------------------------------------------------ #
    def _hist_len(self):
        try:
            return len(self.screen.history.top)
        except Exception:
            return 0

    def _total_lines(self):
        return self._hist_len() + self._rows

    def _refresh(self):
        sb = self.verticalScrollBar()
        visible = max(1, int(self.viewport().height() // self._cell_h))
        new_max = max(0, self._total_lines() - visible)
        at_bottom = sb.value() >= sb.maximum()
        sb.blockSignals(True)
        sb.setRange(0, new_max)
        sb.setPageStep(visible)
        if self.auto_scroll or at_bottom:
            sb.setValue(new_max)
        sb.blockSignals(False)
        self.viewport().update()

    def _line_chars(self, abs_row):
        """Retorna o dict de células (col -> Char) da linha absoluta."""
        hist = self._hist_len()
        if abs_row < hist:
            try:
                return self.screen.history.top[abs_row]
            except Exception:
                return {}
        row = abs_row - hist
        if 0 <= row < self._rows:
            return self.screen.buffer[row]
        return {}

    def _line_text(self, abs_row):
        chars = self._line_chars(abs_row)
        out = []
        for x in range(self._cols):
            ch = chars.get(x) if hasattr(chars, "get") else None
            out.append(ch.data if ch is not None and ch.data else " ")
        return "".join(out).rstrip()

    # ------------------------------------------------------------------ #
    #  Cores                                                              #
    # ------------------------------------------------------------------ #
    def _color(self, spec, is_fg, bold=False):
        if spec in ("default", None, ""):
            return self._def_fg if is_fg else self._def_bg
        if bold and is_fg and spec in _BRIGHT_OF:
            spec = _BRIGHT_OF[spec]
        named = self._ansi.get(spec)
        if named:
            return QColor(named)
        # pyte entrega 256-color/truecolor como hex sem '#'
        return QColor("#" + spec) if len(spec) in (6, 3) else self._def_fg

    # ------------------------------------------------------------------ #
    #  Renderização                                                       #
    # ------------------------------------------------------------------ #
    def paintEvent(self, _event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), self._def_bg)

        top = self.verticalScrollBar().value()
        visible = int(self.viewport().height() // self._cell_h) + 1
        hist = self._hist_len()
        sel = self._normalized_selection()
        cw, chh = self._cell_w, self._cell_h

        for vis_row in range(visible):
            abs_row = top + vis_row
            if abs_row >= self._total_lines():
                break
            chars = self._line_chars(abs_row)
            y = vis_row * chh

            # Agrupa células consecutivas com os mesmos atributos (runs)
            run_text = []
            run_start = 0
            run_attrs = None

            def flush(end_col):
                if run_attrs is None or not run_text:
                    return
                fg, bg, bold, ital, under = run_attrs
                x0 = run_start * cw
                w = (end_col - run_start) * cw
                if bg != self._def_bg:
                    painter.fillRect(QRectF(x0, y, w, chh), bg)
                painter.setFont(self._font_for(bold, ital, under))
                text = "".join(run_text)
                align = (Qt.AlignmentFlag.AlignLeft |
                         Qt.AlignmentFlag.AlignVCenter)
                if self._crt and text.strip():
                    # "Glow" de fósforo: o texto sangra levemente p/ os lados
                    glow = QColor(fg)
                    glow.setAlpha(80)
                    painter.setPen(glow)
                    painter.drawText(QRectF(x0 - 1, y, w + 2, chh), align, text)
                    painter.drawText(QRectF(x0 + 1, y, w + 2, chh), align, text)
                painter.setPen(fg)
                painter.drawText(QRectF(x0, y, w, chh), align, text)

            for x in range(self._cols):
                ch = chars.get(x) if hasattr(chars, "get") else None
                if ch is None:
                    data, fg_s, bg_s = " ", "default", "default"
                    bold = ital = under = rev = False
                else:
                    data = ch.data if ch.data else " "
                    fg_s, bg_s = ch.fg, ch.bg
                    bold, ital, under, rev = (ch.bold, ch.italics,
                                              ch.underscore, ch.reverse)
                fg = self._color(fg_s, True, bold)
                bg = self._color(bg_s, False)
                if rev:
                    fg, bg = bg, fg
                if sel and self._in_selection(sel, abs_row, x):
                    bg = self._sel_bg
                attrs = (fg.rgb(), bg.rgb(), bold, ital, under)
                if run_attrs is None:
                    run_attrs = (fg, bg, bold, ital, under)
                    run_key = attrs
                    run_start = x
                elif attrs != run_key:
                    flush(x)
                    run_text = []
                    run_attrs = (fg, bg, bold, ital, under)
                    run_key = attrs
                    run_start = x
                run_text.append(data)
            flush(self._cols)

            # Cursor (apenas na região "viva" da tela, não no scrollback)
            cur = self.screen.cursor
            if (not cur.hidden and abs_row == hist + cur.y
                    and self.parent_tab is not None
                    and getattr(self.parent_tab.ssh_mgr, "is_connected", False)):
                cx = cur.x * cw
                rect = QRectF(cx, y, cw, chh)
                if self.hasFocus():
                    painter.fillRect(rect, self._def_fg)
                    ch = chars.get(cur.x) if hasattr(chars, "get") else None
                    data = ch.data if ch is not None and ch.data else " "
                    painter.setFont(self._font_for(False, False, False))
                    painter.setPen(self._def_bg)
                    painter.drawText(rect,
                                     Qt.AlignmentFlag.AlignLeft |
                                     Qt.AlignmentFlag.AlignVCenter, data)
                else:
                    painter.setPen(self._def_fg)
                    painter.drawRect(rect.adjusted(0, 0, -1, -1))

        if self._crt:
            self._paint_crt_overlay(painter)

    def _paint_crt_overlay(self, painter):
        """Efeitos de monitor CRT (estilo cool-retro-term): scanlines
        horizontais + vinheta escurecendo os cantos."""
        vp = self.viewport().rect()
        # Scanlines: 1px escuro a cada 3px
        painter.setPen(QColor(0, 0, 0, 90))
        yy = 0
        while yy < vp.height():
            painter.drawLine(0, yy, vp.width(), yy)
            yy += 3
        # Vinheta radial (cantos mais escuros, como um tubo de imagem)
        grad = QRadialGradient(
            QPointF(vp.width() / 2, vp.height() / 2),
            max(vp.width(), vp.height()) * 0.75)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(0.75, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 130))
        painter.fillRect(vp, QBrush(grad))

    # ------------------------------------------------------------------ #
    #  Teclado                                                            #
    # ------------------------------------------------------------------ #
    def _send(self, data, notify=True):
        tab = self.parent_tab
        if not (tab and tab.ssh_mgr.is_connected and tab.ssh_mgr.shell):
            return
        shell = tab.ssh_mgr.shell
        if notify and isinstance(data, str):
            tab.note_terminal_input(data)
        # O canal tem timeout curto (para o read loop); envios grandes (paste)
        # podem ser parciais — reenviamos o restante em vez de perder dados.
        buf = data.encode("utf-8") if isinstance(data, str) else data
        try:
            for _ in range(50):
                if not buf:
                    break
                try:
                    sent = shell.send(buf)
                except socket.timeout:
                    continue
                if sent <= 0:
                    break
                buf = buf[sent:]
        except Exception:
            pass

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)

        # Atalhos do próprio widget
        if ctrl and shift and key == Qt.Key.Key_C:
            self.copy_selection(); return
        if ctrl and shift and key == Qt.Key.Key_V:
            self.paste_clipboard(); return
        if shift and key == Qt.Key.Key_PageUp:
            sb = self.verticalScrollBar()
            sb.setValue(sb.value() - max(1, self._rows - 1)); return
        if shift and key == Qt.Key.Key_PageDown:
            sb = self.verticalScrollBar()
            sb.setValue(sb.value() + max(1, self._rows - 1)); return

        # Ctrl+C: copia se houver seleção, senão manda SIGINT (como em
        # terminais modernos)
        if ctrl and key == Qt.Key.Key_C and self._has_selection():
            self.copy_selection(); return

        seq = None
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            seq = "\r"
        elif key == Qt.Key.Key_Backspace:
            seq = "\x7f"
        elif key == Qt.Key.Key_Tab:
            seq = "\t"
        elif key == Qt.Key.Key_Escape:
            seq = "\x1b"
        elif key in _KEY_SEQS:
            seq = _KEY_SEQS[key]
        elif ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            seq = chr(key - Qt.Key.Key_A.value + 1)
        elif ctrl and key == Qt.Key.Key_Space:
            seq = "\x00"
        elif event.text():
            seq = event.text()

        if seq is not None:
            if alt and len(seq) == 1 and seq not in ("\x1b",):
                seq = "\x1b" + seq
            self._send(seq)
            if self.auto_scroll:
                sb = self.verticalScrollBar()
                sb.setValue(sb.maximum())
            return
        super().keyPressEvent(event)

    def send_fkey(self, name):
        """Envia uma F-key por nome ("F10") ao shell remoto."""
        seq = FKEY_SEQS.get(name)
        if seq:
            self._send(seq)

    def inputMethodEvent(self, event):
        commit = event.commitString()
        if commit:
            self._send(commit)
        event.accept()

    # ------------------------------------------------------------------ #
    #  Mouse / seleção / mouse-reporting (xterm)                          #
    # ------------------------------------------------------------------ #
    def _pos_to_cell(self, pos):
        col = max(0, min(self._cols - 1, int(pos.x() // self._cell_w)))
        row = self.verticalScrollBar().value() + int(pos.y() // self._cell_h)
        row = max(0, min(self._total_lines() - 1, row))
        return row, col

    def _mouse_tracking(self):
        """True se o app remoto (htop, vim...) pediu eventos de mouse.
        O pyte guarda modos privados DECSET deslocados 5 bits."""
        m = self.screen.mode
        return any((mode << 5) in m for mode in (9, 1000, 1002, 1003))

    def _report_mouse(self, btn, pos, pressed, motion=False):
        """Envia o clique ao app remoto no protocolo xterm (SGR ou X10)."""
        col = max(0, min(self._cols - 1, int(pos.x() // self._cell_w)))
        abs_row = self.verticalScrollBar().value() + int(pos.y() // self._cell_h)
        srow = abs_row - self._hist_len()   # linha na tela "viva"
        if srow < 0 or srow >= self._rows:
            return  # clique no scrollback: não pertence ao app
        b = btn + (32 if motion else 0)
        if (1006 << 5) in self.screen.mode:      # SGR (moderno)
            self._send(f"\x1b[<{b};{col + 1};{srow + 1}{'M' if pressed else 'm'}")
        else:                                     # X10 (legado)
            if not pressed:
                b = 3
            self._send("\x1b[M" + chr(32 + b) + chr(33 + col) + chr(33 + srow))

    @staticmethod
    def _qt_btn(event):
        if event.button() == Qt.MouseButton.LeftButton:   return 0
        if event.button() == Qt.MouseButton.MiddleButton: return 1
        if event.button() == Qt.MouseButton.RightButton:  return 2
        return None

    def mousePressEvent(self, event):
        self.setFocus()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        # App remoto quer o mouse (e sem Shift, que força seleção local)
        if self._mouse_tracking() and not shift:
            btn = self._qt_btn(event)
            if btn is not None:
                self._report_mouse(btn, event.position(), pressed=True)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._sel_anchor = self._pos_to_cell(event.position())
            self._sel_end = None
            self.viewport().update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.paste_clipboard()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self._mouse_tracking() and not shift:
            btn = self._qt_btn(event)
            if btn is not None:
                self._report_mouse(btn, event.position(), pressed=False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self._mouse_tracking() and not shift:
            # Arrastar com botão pressionado (modos 1002/1003)
            m = self.screen.mode
            if ((1002 << 5) in m or (1003 << 5) in m) and event.buttons():
                if event.buttons() & Qt.MouseButton.LeftButton:     btn = 0
                elif event.buttons() & Qt.MouseButton.MiddleButton: btn = 1
                else:                                               btn = 2
                self._report_mouse(btn, event.position(), pressed=True, motion=True)
            event.accept()
            return
        if event.buttons() & Qt.MouseButton.LeftButton and self._sel_anchor:
            self._sel_end = self._pos_to_cell(event.position())
            self.viewport().update()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._mouse_tracking() and not (event.modifiers() &
                                           Qt.KeyboardModifier.ShiftModifier):
            btn = self._qt_btn(event)
            if btn is not None:
                self._report_mouse(btn, event.position(), pressed=True)
            event.accept()
            return
        # Duplo clique seleciona a "palavra" sob o cursor
        row, col = self._pos_to_cell(event.position())
        text = self._line_text(row)
        if col >= len(text):
            return
        seps = " \t|&;()<>'\""
        start = col
        while start > 0 and text[start - 1] not in seps:
            start -= 1
        end = col
        while end < len(text) and text[end] not in seps:
            end += 1
        self._sel_anchor = (row, start)
        self._sel_end = (row, end - 1)
        self.viewport().update()

    def _has_selection(self):
        return (self._sel_anchor is not None and self._sel_end is not None
                and self._sel_anchor != self._sel_end)

    def _normalized_selection(self):
        if not self._has_selection():
            return None
        a, b = self._sel_anchor, self._sel_end
        return (a, b) if a <= b else (b, a)

    @staticmethod
    def _in_selection(sel, row, col):
        (r0, c0), (r1, c1) = sel
        if row < r0 or row > r1:
            return False
        if r0 == r1:
            return c0 <= col <= c1
        if row == r0:
            return col >= c0
        if row == r1:
            return col <= c1
        return True

    def selected_text(self):
        sel = self._normalized_selection()
        if not sel:
            return ""
        (r0, c0), (r1, c1) = sel
        lines = []
        for row in range(r0, r1 + 1):
            text = self._line_text(row)
            if r0 == r1:
                lines.append(text[c0:c1 + 1])
            elif row == r0:
                lines.append(text[c0:])
            elif row == r1:
                lines.append(text[:c1 + 1])
            else:
                lines.append(text)
        return "\n".join(lines)

    def copy_selection(self):
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)

    def paste_clipboard(self):
        text = QApplication.clipboard().text()
        if text:
            self._send(text.replace("\r\n", "\r").replace("\n", "\r"))

    def select_all(self):
        self._sel_anchor = (0, 0)
        self._sel_end = (self._total_lines() - 1, self._cols - 1)
        self.viewport().update()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() // 120
        # Ctrl + roda = zoom da fonte (como em qualquer terminal moderno)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            ui = getattr(self.parent_tab, "parent_ui", None)
            if ui is not None and steps:
                (ui.increase_font if steps > 0 else ui.decrease_font)()
            event.accept()
            return
        # App em modo mouse (htop, less com mouse): roda vai para o app
        if self._mouse_tracking() and not (event.modifiers() &
                                           Qt.KeyboardModifier.ShiftModifier):
            btn = 64 if steps > 0 else 65   # wheel-up / wheel-down
            for _ in range(abs(steps)):
                self._report_mouse(btn, event.position(), pressed=True)
            event.accept()
            return
        sb = self.verticalScrollBar()
        sb.setValue(sb.value() - steps * 3)
        event.accept()

    def contextMenuEvent(self, event):
        # Clique direito pertence ao app remoto quando o mouse está ativo
        if self._mouse_tracking():
            event.ignore()
            return
        menu = QMenu(self)
        a_copy = QAction("Copy  (Ctrl+Shift+C)", self)
        a_copy.triggered.connect(self.copy_selection)
        a_copy.setEnabled(self._has_selection())
        a_paste = QAction("Paste  (Ctrl+Shift+V)", self)
        a_paste.triggered.connect(self.paste_clipboard)
        a_all = QAction("Select All", self)
        a_all.triggered.connect(self.select_all)
        a_clear = QAction("Clear", self)
        a_clear.triggered.connect(self.clear)
        for a in (a_copy, a_paste, a_all, a_clear):
            menu.addAction(a)
        menu.exec(event.globalPos())
