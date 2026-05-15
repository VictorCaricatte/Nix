import json
import os
import base64
import hashlib
import uuid

try:
    from cryptography.fernet import Fernet, InvalidToken
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

def _machine_key() -> bytes:
    """Deriva uma chave Fernet determinística a partir do MAC address da máquina."""
    try:
        seed = str(uuid.getnode()).encode() + b"nix_ssh_v1"
        return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    except Exception:
        return base64.urlsafe_b64encode(b"nix_fallback_key_padded_xxxxx!!")

def encrypt_password(plain: str) -> str:
    """Retorna a senha cifrada com prefixo 'ENC:'. Retorna o texto original se crypto não disponível."""
    if not _CRYPTO_OK or not plain:
        return plain
    try:
        return "ENC:" + Fernet(_machine_key()).encrypt(plain.encode()).decode()
    except Exception:
        return plain

def decrypt_password(value: str) -> str:
    """Decifra senha com prefixo 'ENC:'. Retorna o valor original caso não consiga."""
    if not _CRYPTO_OK or not value or not value.startswith("ENC:"):
        return value
    try:
        return Fernet(_machine_key()).decrypt(value[4:].encode()).decode()
    except (InvalidToken, Exception):
        return ""

class ConfigManager:
    def __init__(self, config_file="config.json"):
        self.config_file = config_file
        self.sessions = {}
        self.snippets = {"System Info": "uname -a", "Disk Usage": "df -h"}
        self.language = "en"
        self.conda_path = ""
        self.command_history = []
        self.favorites = {}

        self.theme = {
            "mode": "dark",
            "accent": "#bd93f9",
            "terminal_color": "#a6accd",
            "bg_color": "#151015",
            "layout": "classic",
            "term_style": "standard"
        }
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                    self.sessions = data.get("sessions", {})
                    self.snippets = data.get("snippets", self.snippets)
                    self.theme = data.get("theme", self.theme)
                    self.language = data.get("language", "en")
                    self.conda_path = data.get("conda_path", "")
                    self.command_history = data.get("command_history", [])
                    self.favorites = data.get("favorites", {})

                    if "layout" not in self.theme:
                        self.theme["layout"] = "classic"
                    if "bg_color" not in self.theme:
                        self.theme["bg_color"] = "#151015"
                    if "term_style" not in self.theme:
                        self.theme["term_style"] = "standard"
            except Exception:
                pass

    def save_config(self):
        try:
            with open(self.config_file, "w") as f:
                json.dump({
                    "sessions": self.sessions,
                    "snippets": self.snippets,
                    "theme": self.theme,
                    "language": self.language,
                    "conda_path": self.conda_path,
                    "command_history": self.command_history[:500],
                    "favorites": self.favorites
                }, f, indent=4)
        except Exception:
            pass
