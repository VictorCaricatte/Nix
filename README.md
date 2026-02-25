<div align="center">

![Nix](Nix.jpg)

# Nix 🐧

**A user-friendly graphical interface for Ubuntu Linux servers over SSH.**
*Made with love, for friends who prefer seeing things visually. ♥*

[![Download](https://img.shields.io/badge/Download-Nix.exe-bd93f9?style=for-the-badge&logo=windows)](https://github.com/VictorCaricatte/Nix/releases/download/Nix/Nix.exe)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![PyQt6](https://img.shields.io/badge/PyQt6-GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://pypi.org/project/PyQt6/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

</div>

---

## What is Nix?

Nix is a desktop application that brings a clean, visual interface to managing Ubuntu Linux servers over SSH. No more memorizing commands or staring at a blank terminal — Nix gives you a friendly window into your server, whether you're a beginner helping a friend out or just someone who appreciates having a proper GUI for the things you do most.

> **Nix is not trying to replace the terminal. It's here to make server management more approachable.**

---

##  Download

A ready-to-use Windows executable is available — no installation or Python required.

**[→ Download Nix.exe](https://github.com/VictorCaricatte/Nix/releases/download/Nix/Nix.exe)**

Just download, run, and connect to your server.

---

##  Features

###  SSH Connection
- Connect to any Ubuntu/Linux server using **password** or **SSH private key**
- Save and reuse sessions by name so you don't have to type credentials every time
- Supports both `user@host` format connections

###  Integrated Terminal
- Full interactive terminal embedded in the UI (VT100 emulation)
- Multiple terminal styles: **Standard**, **Retro**, and **Matrix**
- Keyboard shortcuts and a command snippet library for common operations
- State indicator showing whether you're in the main shell or inside a `screen` session

###  File Explorer
- Browse the remote server's file system with a tree view
- Upload files and folders by **drag-and-drop** from your desktop
- Download files directly to your machine
- File operations: rename, move, delete, compress (`.tar.gz`), extract, copy path
- Inline file editing via **Nano**, with a helper bar showing common shortcuts (`^O Save`, `^X Exit`, etc.)
- Filter files in the current directory with a search bar

###  System Monitor
- Real-time **CPU** and **RAM** usage with live progress bars
- Running process list with PID, CPU%, memory, and name
- OS Info panel — uses `neofetch` or `fastfetch` if available, falls back to a built-in system info script

###  Screen Manager
- List, attach, detach, and kill GNU `screen` sessions directly from the UI
- Create new named `screen` sessions with one click

###  Conda Environment Manager
- List all Conda environments on the server
- Activate or deactivate environments without typing a single command

###  Customization
- Dark/Light mode toggle
- Customizable accent color and background color
- Multiple layout options
- Full **English / Portuguese (BR)** language support, switchable in the UI

---

##  Getting Started

### Option 1 — Use the Executable (Recommended)

1. Download **[Nix.exe](https://github.com/VictorCaricatte/Nix/releases/download/Nix/Nix.exe)**
2. Run it — no installation needed
3. Enter your server's address in `user@host` format, provide your password or SSH key path, and click **Connect**

### Option 2 — Run from Source

**Requirements:**
- Python 3.10+
- `pip install PyQt6 paramiko`

**Run:**
```bash
git clone https://github.com/VictorCaricatte/Nix.git
cd Nix/Nix
python Nix.py
```

---

##  Project Structure

```
Nix/
├── Nix.py          # Entry point — launches the application
├── frontend.py     # Main UI window and layout
├── backend.py      # SSH command logic, file transfers, system monitoring
├── ssh.py          # SSH/SFTP connection manager (Paramiko)
├── dialogs.py      # All dialog windows (file viewer, properties, etc.)
├── widgets.py      # Custom drag-and-drop file explorer widget
├── config.py       # Configuration persistence (sessions, theme, snippets)
├── i18n.py         # Translations (English / Portuguese)
└── Nix.jpg         # App icon
```

---

##  Security Notes

- Passwords and SSH keys are **never stored in plain text** unless you explicitly save a session
- Nix uses `paramiko`'s `AutoAddPolicy` for host key handling — be mindful when connecting to unknown hosts
- `sudo` operations (like uploading to protected directories) prompt for the sudo password each session and do not persist it

---

##  Contributing

Nix was built as a personal tool to help friends. If you have ideas, improvements, or bug fixes, feel free to open an issue or a pull request — contributions are welcome!

---

##  License

This project is licensed under the [MIT License](LICENSE).

---

<div align="center">
Made with ♥ by <a href="https://github.com/VictorCaricatte">VictorCaricatte</a>
</div>
