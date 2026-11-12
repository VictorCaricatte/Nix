import json
import os
import base64
import hashlib
import uuid
import sys
import shutil

try:
    from cryptography.fernet import Fernet, InvalidToken
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

try:
    import keyring
    _KEYRING_OK = True
except Exception:
    _KEYRING_OK = False

_KEYRING_SERVICE = "Nix-SSH-Client"
_KEYRING_USER = "config-encryption-key"


def _machine_key() -> bytes:
    """Chave Fernet derivada do MAC (fraca — só ofuscação). Usada como
    FALLBACK quando o keyring do SO não está disponível, e para ler configs
    antigos criados antes do suporte a keyring."""
    try:
        seed = str(uuid.getnode()).encode() + b"nix_ssh_v1"
        return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    except Exception:
        return base64.urlsafe_b64encode(b"nix_fallback_key_padded_xxxxx!!")


def _keyring_key():
    """Chave Fernet aleatória guardada no cofre do SO (Windows Credential
    Manager / macOS Keychain / Secret Service). Muito mais forte que a chave
    derivada do MAC: não é recuperável só com acesso ao disco. Retorna None
    se o keyring não estiver disponível."""
    if not (_KEYRING_OK and _CRYPTO_OK):
        return None
    try:
        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        if not stored:
            stored = Fernet.generate_key().decode()
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, stored)
        return stored.encode()
    except Exception:
        return None


def _encrypt_keys():
    """Chave preferida para CIFRAR (keyring se houver, senão a do MAC)."""
    kk = _keyring_key()
    return kk if kk else _machine_key()


def _decrypt_keys():
    """Chaves para DECIFRAR, em ordem: keyring e depois MAC — assim configs
    antigos (cifrados com a chave do MAC) continuam legíveis após migrar."""
    keys = []
    kk = _keyring_key()
    if kk:
        keys.append(kk)
    keys.append(_machine_key())
    return keys


def encrypt_password(plain: str) -> str:
    """Retorna a senha cifrada com prefixo 'ENC:'. Retorna o texto original se crypto não disponível."""
    if not _CRYPTO_OK or not plain:
        return plain
    try:
        return "ENC:" + Fernet(_encrypt_keys()).encrypt(plain.encode()).decode()
    except Exception:
        return plain


def decrypt_password(value: str) -> str:
    """Decifra senha com prefixo 'ENC:'. Retorna o valor original caso não consiga."""
    if not _CRYPTO_OK or not value or not value.startswith("ENC:"):
        return value
    token = value[4:].encode()
    for key in _decrypt_keys():
        try:
            return Fernet(key).decrypt(token).decode()
        except Exception:
            continue
    return ""

def default_config_path():
    """Caminho do config do usuário: %APPDATA%/Nix/config.json (Windows) ou
    ~/.config/Nix/config.json. Fica FORA da pasta do programa, então o Nix pode
    ser instalado em qualquer lugar (inclusive Program Files) e atualizações /
    desinstalação não apagam as sessões salvas."""
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "Nix")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return "config.json"
    new_path = os.path.join(folder, "config.json")
    if not os.path.exists(new_path):
        # Migra o config antigo (ficava na pasta de trabalho / ao lado do .exe)
        legacy = [os.path.abspath("config.json")]
        if getattr(sys, "frozen", False):
            legacy.append(os.path.join(os.path.dirname(sys.executable), "config.json"))
        for old in legacy:
            if os.path.isfile(old):
                try:
                    shutil.copyfile(old, new_path)
                except OSError:
                    pass
                break
    return new_path


class ConfigManager:
    def __init__(self, config_file=None):
        self.config_file = config_file or default_config_path()
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
