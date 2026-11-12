<img width="720" height="720" alt="Nix" src="https://github.com/user-attachments/assets/1db665b5-dd7e-4367-a1b6-e5bbd5a70bb8" />

# Nix

**A visual desktop client for managing Linux servers over SSH.**  
*Made with love, for everyone who prefers seeing things clearly. ♥*

[![Download](https://img.shields.io/badge/Download-Windows-bd93f9?style=for-the-badge&logo=windows)](https://github.com/VictorCaricatte/Nix/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![PyQt6](https://img.shields.io/badge/PyQt6-GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://pypi.org/project/PyQt6/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

---

## What is Nix?

Nix is a desktop application that brings a clean, visual interface to managing Linux servers over SSH. No more memorizing commands or staring at a blank terminal — Nix gives you a full graphical window into your server, from everyday file management to advanced DevOps workflows.

> **Nix doesn't try to replace the terminal. It makes server management more approachable — and a lot faster.**

---

## Download

Ready-to-use Windows builds are on the **[Releases page](https://github.com/VictorCaricatte/Nix/releases/latest)** — no Python installation required.

| File | What it is |
|------|------------|
| `Nix_Setup_<version>.exe` | **Installer** (recommended) — Start menu shortcut, uninstaller |
| `Nix_Portable_<version>.zip` | **Portable** — unzip anywhere and run `Nix.exe` |

Download, run, and connect.

---

## What's new in 1.3.0

- **The file explorer follows the terminal.** `cd` typed anywhere (terminal or command box, including `cd -` and `cd a && cd b`) moves the explorer too.
- **`sudo su - user` / `su` / `sudo -i` just work.** The sudo password is asked only once — the one you type in the terminal — and the explorer switches to the new user automatically, then back on `exit`.
- **GNU `screen` aware.** Attaching to a screen (even one created outside Nix) takes the explorer to the folder where that screen was left; detaching brings it back.
- **Password prompts are protected.** When the server asks for a password, the command box masks it and it is never saved to the command history.

---

## Features

### SSH Connection
- Connect using **password** or **SSH private key** (`user@host`, `user@host:2222` or `ssh -p 2222 user@host`)
- Save and reuse named sessions (passwords encrypted at rest)
- **Host key verification** — unknown servers must be confirmed, changed keys are refused
- Automatic **reconnect** banner when the connection drops, returning to the folder you were in
- Multi-tab support — manage several servers side by side
- X11 forwarding and compression flags (`-X -C`)
- System tray integration — stays running in the background when minimized

### Integrated Terminal
- **Real terminal emulator** (VT/xterm, 256 colors) — `vim`, `htop`, `nano`, `less` and mouse-aware apps work
- Visual styles: **Standard**, **Retro**, **Matrix**, **CRT** and **CRT Blue**
- Command history, bash-style **Tab completion**, and a snippet library for frequent commands
- Tracks the shell's current folder, user and `screen` session (bash, zsh and dash)
- State indicator — shows whether you're in the main shell or inside a `screen` session

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

Toggle with the **Layout** button in the toolbar.

---

## Customization

- Dark / Light mode
- Custom accent color, background color, and terminal text color
- Five terminal styles (Standard / Retro / Matrix / CRT / CRT Blue)
- Adjustable terminal font size (`A+` / `A-`, or `Ctrl` + mouse wheel)
- Full **English / Portuguese (BR) / Spanish** language support — switch in the UI at any time

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| F1 | Show all shortcuts |
| F5 | Refresh explorer |
| F6 | Rename selected file |
| F7 | New folder |
| F8 | Delete selected |
| F9 | Clear terminal |
| F10 | Connect / Disconnect |
| Ctrl+Shift+T / Ctrl+Shift+W | New tab / Close tab |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / Previous tab |
| Ctrl + / Ctrl - / Ctrl 0 | Zoom in / out / reset |
| Ctrl+Shift+M | Show / hide the System Monitor |
| Ctrl+Shift+C / Ctrl+Shift+V | Copy / Paste in the terminal |

---

## Getting Started

### Option 1 — Windows build (recommended)

1. Download `Nix_Setup_<version>.exe` from the **[Releases page](https://github.com/VictorCaricatte/Nix/releases/latest)**
2. Install and open Nix
3. Enter `user@host`, provide a password or SSH key, and click **Connect**

### Option 2 — Run from source (Windows, Linux, macOS)

```bash
git clone https://github.com/VictorCaricatte/Nix.git
cd Nix
pip install -r requirements.txt
cd Nix
python Nix.py
```

Optional extras: `pip install pandas openpyxl pygments` (XLSX table viewer and syntax highlighting).

### Building the Windows installer

Requires [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`).

```powershell
pip install -r requirements.txt pyinstaller
powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1 -Version 1.3.0
```

Output goes to `installer_output\` (installer `.exe` + portable `.zip`). If the script says dependencies are missing, pass the right interpreter with `-Python C:\path\to\python.exe`.

---

## Project Structure

```
.
├── Nix/
│   ├── Nix.py              # Entry point
│   ├── main_window.py      # Main window: toolbar, tabs, layout, theme, sessions
│   ├── tab_panel.py        # Per-session panel: terminal, explorer, system monitor
│   ├── terminal.py         # Real terminal emulator widget (pyte-based VT renderer)
│   ├── ssh.py              # SSH/SFTP connection manager (Paramiko)
│   ├── backend.py          # SSH command helpers, shell integration, monitor parsing
│   ├── devops_dialogs.py   # DevOps feature dialogs (tunnels, cron, logs, sync, …)
│   ├── dialogs.py          # General dialogs (file viewer, table viewer, editor, …)
│   ├── widgets.py          # Custom drag-and-drop file explorer widget
│   ├── config.py           # Configuration persistence (sessions, theme, snippets)
│   ├── i18n.py             # Translations (English / Portuguese / Spanish)
│   ├── frontend.py         # Re-export shim (backward compat)
│   ├── Nix.spec            # PyInstaller build recipe
│   └── version_info.txt    # Windows version metadata for Nix.exe
├── installer/
│   ├── build_installer.ps1 # One-command build: PyInstaller + Inno Setup + ZIP
│   └── Nix.iss             # Inno Setup script
└── requirements.txt
```

---

## Security Notes

- Saved session passwords are **encrypted** (Fernet, key kept in the OS keyring); nothing is stored unless you save a session
- **Host keys are verified** against `~/.ssh/known_hosts`; new servers must be approved and a changed key blocks the connection (possible MITM)
- The sudo password is kept **only in memory** for the current connection and cleared on disconnect
- Passwords typed at a server prompt are masked and **never written to the command history**
- Shell integration reports are signed with a random per-connection token, so text printed by remote programs cannot spoof them
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
