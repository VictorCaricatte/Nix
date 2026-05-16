
<img width="720" height="720" alt="Nix" src="https://github.com/user-attachments/assets/1db665b5-dd7e-4367-a1b6-e5bbd5a70bb8" />

# Nix

**A visual desktop client for managing Linux servers over SSH.**  
*Made with love, for everyone who prefers seeing things clearly. ♥*

[![Download](https://img.shields.io/badge/Download-Nix.exe-bd93f9?style=for-the-badge&logo=windows)](https://github.com/VictorCaricatte/Nix/releases/download/Nix/Nix.exe)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![PyQt6](https://img.shields.io/badge/PyQt6-GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://pypi.org/project/PyQt6/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

---

## What is Nix?

Nix is a desktop application that brings a clean, visual interface to managing Linux servers over SSH. No more memorizing commands or staring at a blank terminal — Nix gives you a full graphical window into your server, from everyday file management to advanced DevOps workflows.

> **Nix doesn't try to replace the terminal. It makes server management more approachable — and a lot faster.**

---

## Download

A ready-to-use Windows executable is available — no Python installation required.

**[→ Download Nix.exe](https://github.com/VictorCaricatte/Nix/releases/download/v1.2.1/Nix_Windows.exe)**

Just download, run, and connect.

---

## Features

### SSH Connection
- Connect using **password** or **SSH private key**
- Save and reuse named sessions (passwords encrypted at rest)
- Multi-tab support — manage several servers side by side
- X11 forwarding and compression flags (`-X -C`)
- System tray integration — stays running in the background when minimized

### Integrated Terminal
- Full interactive shell embedded in the UI
- Three visual styles: **Standard**, **Retro**, **Matrix**
- Command history, tab-autocomplete, and a snippet library for frequent commands
- State indicator — shows whether you're in the main shell or inside a `screen` / `tmux` session

### File Explorer
- Tree-view browser with icons by file type
- Upload files and folders by **drag-and-drop** from your local machine
- Download files directly to your desktop
- File operations: rename, move, delete, compress (`.tar.gz`), extract, copy path
- Inline editing via **Nano** with a shortcut bar (`^O Save`, `^X Exit`, …)
- Quick filter bar and **deep search** across the remote file system
- Favorites — bookmark frequently accessed directories
- **Git integration** — files colored by status (staged / modified / untracked), branch shown in the path bar, git commands in the right-click menu

### System Monitor (right panel)
- Live **CPU**, **RAM**, and **network** usage with progress bars
- Running process list — right-click to send SIGTERM or SIGKILL (requires sudo)
- Active user sessions — right-click to disconnect (uses sudo)
- OS Info tab — `neofetch` / `fastfetch` or a built-in system info fallback
- Snippet library tab — save and run one-click commands
- **Services tab** — quick systemd service count + launch the full Service Manager
- **Ports tab** — live table of listening ports (`ss -tulpn`) + launch the full Port Monitor

### Table Viewer
- Opens CSV, TSV, and XLSX files directly from the remote file system
- Loads data in a background thread — **no UI freeze**, even on 50 000-row files
- Filter / search across all columns in real time
- Copy selection to clipboard (Ctrl+C), save filtered results locally

### Screen Manager
- List, attach, detach, and kill GNU `screen` sessions
- Create new named sessions with one click

### Conda Environment Manager
- List all Conda environments on the server
- Activate or deactivate without typing a command

---

## DevOps Tools

All DevOps features are accessible from the **DevOps button** (⚙) in the file explorer toolbar. Each opens a dedicated dialog.

| Tool | Description |
|------|-------------|
| **SSH Tunnel Manager** | Create Local, Remote, and Dynamic (SOCKS5) port-forwarding tunnels with live status |
| **Cron Editor** | Visual `crontab` editor with preset schedules (hourly, daily, every N minutes, …) |
| **Log Viewer** | Live `tail -f` with regex filter and color-coding by severity (ERROR / WARN / INFO) |
| **File Sync** | Side-by-side local ↔ remote comparison — push or pull individual files |
| **SSH Key Manager** | Generate Ed25519/RSA keys, push public keys to the server, edit `authorized_keys` |
| **Service Manager** | Full systemd panel — start, stop, restart, enable, disable services with live output |
| **Port Monitor** | Visual table of `ss -tulpn` with auto-refresh and protocol filter |

### Fleet Dashboard
The **Fleet** tab shows all saved sessions at a glance — online/offline status (TCP probe on port 22) and a quick-connect button for each server.

### Batch / Cluster Execution
The **Batch Exec** toolbar button lets you run a single command on multiple connected sessions simultaneously, with per-server output in a results panel.

---

## Layout Options

| Layout | Description |
|--------|-------------|
| **Classic** | Connection bar on top, vertical split of Explorer + Terminal on the left, System Monitor on the right |
| **Dock** | Explorer, Terminal, and System Monitor as three horizontal columns (wider screens) |

Toggle with the **Layout** button or `Ctrl` shortcut.

---

## Customization

- Dark / Light mode
- Custom accent color, background color, and terminal text color
- Three terminal font styles (Standard / Retro / Matrix)
- Adjustable terminal font size (`A+` / `A-`)
- Full **English / Portuguese (BR) / Spanish** language support — switch in the UI at any time

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| F5 | Refresh explorer |
| F6 | Rename selected file |
| F7 | New folder |
| F8 | Delete selected |
| F9 | Clear terminal |
| F10 | Connect / Disconnect |
| Ctrl+C | Copy selection (table viewer) |

---

## Getting Started

### Option 1 — Executable (recommended)

1. Download **[Nix.exe](https://github.com/VictorCaricatte/Nix/releases/download/v1.2.1/Nix_Windows.exe)**
2. Run it — no installation needed
3. Enter `user@host`, provide a password or SSH key, and click **Connect**

### Option 2 — Run from Source

**Requirements:**
```
Python 3.10+
pip install PyQt6 paramiko qtawesome pandas openpyxl
```

**Run:**
```bash
git clone https://github.com/VictorCaricatte/Nix.git
cd Nix/Nix
python Nix.py
```

---

## Project Structure

```
Nix/
├── Nix.py              # Entry point
├── frontend.py         # Re-export shim (backward compat)
├── main_window.py      # Main window: toolbar, tabs, layout, theme, sessions
├── tab_panel.py        # Per-session panel: terminal, explorer, system monitor
├── devops_dialogs.py   # DevOps feature dialogs (tunnels, cron, logs, sync, …)
├── dialogs.py          # General dialogs (file viewer, table viewer, editor, …)
├── backend.py          # SSH command helpers, system monitor parsing
├── ssh.py              # SSH/SFTP connection manager (Paramiko)
├── widgets.py          # Custom drag-and-drop file explorer widget
├── config.py           # Configuration persistence (sessions, theme, snippets)
├── i18n.py             # Translations (English / Portuguese / Spanish)
└── Nix.jpg             # App icon
```

---

## Security Notes

- Passwords are **never stored in plain text** unless you explicitly save a session; saved passwords are encrypted
- Nix uses Paramiko's `AutoAddPolicy` for host key handling — exercise caution with unknown hosts
- `sudo` operations prompt for the sudo password once per session and cache it only in memory
- SSH key generation uses Paramiko (Ed25519 or RSA 4096); private keys are written only to your local `~/.ssh/`

---

## Contributing

Nix started as a personal tool to help friends. If you have ideas, bug reports, or improvements, open an issue or a pull request — contributions are welcome!

---

## License

This project is licensed under the [MIT License](LICENSE).

---

<div align="center">
Made with ♥ by <a href="https://github.com/VictorCaricatte">VictorCaricatte</a> and <a href="https://github.com/angelobc-blip">Angelo Barbanti</a>
</div>
