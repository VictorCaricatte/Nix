import threading
import socket
import os
import sys
import hashlib
import base64


def host_key_fingerprint(key):
    """Fingerprint SHA256 no formato do OpenSSH (ex.: 'SHA256:abc…')."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def parse_host_string(text, default_user="user", default_port=22):
    """Interpreta o campo de host e devolve (usuario, host, porta).

    Aceita: 'user@host', 'user@host:2222', 'host', '[::1]:2222' e também o
    formato de linha de comando do OpenSSH: 'ssh -p 10022 user@host',
    'user@host -p 10022', '-p10022 user@host'.
    """
    import re
    s = (text or "").strip()
    port = default_port
    m = re.search(r"(?:^|\s)-p\s*(\d+)(?=\s|$)", s)
    if m:
        port = int(m.group(1))
        s = (s[:m.start()] + " " + s[m.end():]).strip()
    tokens = [tk for tk in s.split() if tk != "ssh"]
    s = tokens[-1] if tokens else ""
    user = default_user
    if "@" in s:
        user, s = s.rsplit("@", 1)
    m6 = re.fullmatch(r"\[(.+)\](?::(\d+))?", s)
    if m6:
        s = m6.group(1)
        if m6.group(2):
            port = int(m6.group(2))
    elif s.count(":") == 1:
        h, p = s.split(":")
        if p.isdigit():
            s, port = h, int(p)
    if not (1 <= port <= 65535):
        raise ValueError(f"Porta inválida: {port}")
    return (user or default_user), s, port


class SudoPasswordRequired(Exception):
    """O sudo pede senha e nenhuma foi informada."""


class SudoAuthError(Exception):
    """O sudo recusou a senha (ou o usuário não tem essa permissão)."""


class _NixHostKeyPolicy:
    """Política de host key: rejeita chaves desconhecidas por padrão (evita MITM).

    Só confia numa chave nova se o callback do usuário (host_key_callback)
    aprovar explicitamente — e então grava em known_hosts para as próximas
    conexões. Se a chave de um host conhecido MUDAR, o paramiko levanta
    BadHostKeyException antes de chegar aqui (proteção contra MITM ativo)."""

    def __init__(self, manager):
        self.manager = manager

    def missing_host_key(self, client, hostname, key):
        import paramiko
        cb = self.manager._host_key_callback
        approved = False
        if cb is not None:
            try:
                approved = bool(cb(hostname, key.get_name(),
                                   host_key_fingerprint(key)))
            except Exception:
                approved = False
        if not approved:
            raise paramiko.SSHException(
                f"Host key desconhecida para '{hostname}' — conexão rejeitada.")
        # Aprovado: memoriza e persiste em ~/.ssh/known_hosts
        client.get_host_keys().add(hostname, key.get_name(), key)
        try:
            path = self.manager._known_hosts_path
            os.makedirs(os.path.dirname(path), exist_ok=True)
            client.save_host_keys(path)
        except Exception:
            pass


class SSHManager:
    def __init__(self):
        self.client = None
        self.sftp = None
        self.shell = None
        self.lock = threading.Lock()
        self.is_connected = False
        self.username = ""
        self.sftp_user = ""           # usuário com que o SFTP está aberto agora
        self._host_key_callback = None
        self._known_hosts_path = os.path.join(
            os.path.expanduser("~"), ".ssh", "known_hosts")

    def _x11_handler(self, channel, src_addr, src_port):
        def handle_connection():
            x11_sock = None
            connected = False
            
            try:
                if sys.platform != 'win32' and os.path.exists('/tmp/.X11-unix/X0'):
                    x11_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    x11_sock.connect('/tmp/.X11-unix/X0')
                    connected = True
            except Exception:
                pass
                
            if not connected:
                try:
                    x11_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    display = os.environ.get('DISPLAY', '127.0.0.1:0.0')
                    host = '127.0.0.1'
                    port = 6000
                    if ':' in display:
                        parts = display.split(':')
                        if parts[0] and parts[0] not in ('localhost', ''):
                            host = parts[0]
                        disp_num = parts[1].split('.')[0]
                        port = 6000 + int(disp_num)
                    
                    x11_sock.connect((host, port))
                    connected = True
                except Exception:
                    try:
                        x11_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        x11_sock.connect(('127.0.0.1', 6000))
                        connected = True
                    except Exception:
                        pass

            if not connected:
                channel.close()
                return

            def forward(src, dst):
                try:
                    while True:
                        data = src.recv(4096)
                        if not data:
                            break
                        dst.sendall(data)
                except Exception:
                    pass
                finally:
                    try:
                        src.close()
                    except:
                        pass
                    try:
                        dst.close()
                    except:
                        pass

            threading.Thread(target=forward, args=(channel, x11_sock), daemon=True).start()
            threading.Thread(target=forward, args=(x11_sock, channel), daemon=True).start()

        threading.Thread(target=handle_connection, daemon=True).start()

    def connect(self, host, username, password=None, key_filename=None,
                use_x11=False, host_key_callback=None, port=22):
        import paramiko
        self._host_key_callback = host_key_callback
        self.client = paramiko.SSHClient()
        # Verificação real de host key: carrega known_hosts do sistema/usuário
        # e rejeita chaves desconhecidas (a menos que o usuário aprove no
        # callback). Substitui o antigo AutoAddPolicy, que aceitava qualquer
        # chave silenciosamente — vulnerável a MITM.
        try:
            self.client.load_system_host_keys()
        except Exception:
            pass
        if os.path.exists(self._known_hosts_path):
            try:
                self.client.load_host_keys(self._known_hosts_path)
            except Exception:
                pass
        self.client.set_missing_host_key_policy(_NixHostKeyPolicy(self))

        connect_kwargs = {
            "hostname": host,
            "port": int(port),
            "username": username,
            "timeout": 10,
            "compress": use_x11,
            "allow_agent": True,
            "look_for_keys": True,
        }

        if password:
            connect_kwargs["password"] = password
        if key_filename and key_filename.strip():
            connect_kwargs["key_filename"] = key_filename

        self.client.connect(**connect_kwargs)

        self.client.get_transport().set_keepalive(30)

        self.shell = self.client.get_transport().open_session()
        if use_x11:
            try:
                self.shell.request_x11(handler=self._x11_handler)
            except Exception:
                pass
                
        # xterm-256color: habilita cores e apps de tela cheia (vim, htop...)
        # no terminal real do Nix; o tamanho é sincronizado depois via resize_pty.
        self.shell.get_pty(term='xterm-256color', width=120, height=30)
        self.shell.invoke_shell()
        
        self.sftp = self.client.open_sftp()
        self.username = username
        self.sftp_user = username
        self.is_connected = True

    def _sudo_needs_password(self, qu, sudo_password):
        """Confere o acesso ao sudo ANTES de abrir o SFTP. Assim uma senha
        errada vira um erro imediato, em vez de 15s de espera no handshake.
        Devolve True se a senha precisa ser enviada ao sudo."""
        _, stdout, _ = self.client.exec_command(f"sudo -n -u {qu} true", timeout=15)
        if stdout.channel.recv_exit_status() == 0:
            return False
        if not sudo_password:
            raise SudoPasswordRequired()
        stdin, stdout, _ = self.client.exec_command(
            f"sudo -S -p '' -u {qu} true", timeout=15)
        stdin.write(sudo_password + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        if stdout.channel.recv_exit_status() != 0:
            raise SudoAuthError()
        return True

    def switch_sftp_user(self, target_user, sudo_password):
        """Reabre o SFTP como target_user. Levanta SudoPasswordRequired se o
        sudo pede senha e nenhuma foi dada, e SudoAuthError se foi recusada."""
        import paramiko
        import shlex
        qu = shlex.quote(target_user)  # evita injeção via nome de usuário
        sftp_paths = ("/usr/lib/openssh/sftp-server /usr/libexec/openssh/sftp-server "
                      "/usr/lib/sftp-server /usr/libexec/sftp-server "
                      "/usr/lib/ssh/sftp-server /usr/lib64/ssh/sftp-server "
                      "/usr/libexec/ssh/sftp-server")
        find_cmd = f"for p in {sftp_paths}; do if [ -x $p ]; then exec $p; fi; done"

        send_password = False
        if self.username == "root":
            # root não precisa de senha, e servidor mínimo pode nem ter sudo
            cmd = f"su -s /bin/sh {qu} -c '{find_cmd}'"
        else:
            send_password = self._sudo_needs_password(qu, sudo_password)
            cmd = f"sudo -S -p '' -u {qu} sh -c '{find_cmd}'"

        channel = self.client.get_transport().open_session()
        channel.exec_command(cmd)

        if send_password:
            channel.sendall(sudo_password + "\n")

        # Sem timeout, se o sudo falhar o sftp-server nunca sobe e o handshake
        # do SFTPClient (que lê a versão do servidor) bloqueia a thread PARA
        # SEMPRE. O timeout transforma isso num erro tratável.
        channel.settimeout(15)
        try:
            new_sftp = paramiko.SFTPClient(channel)
            channel.settimeout(None)  # transferências grandes não podem ter timeout curto
            with self.lock:
                if self.sftp:
                    self.sftp.close()
                self.sftp = new_sftp
                self.sftp_user = target_user
            return True
        except Exception as e:
            try: channel.close()
            except Exception: pass
            raise Exception(f"Não foi possível iniciar SFTP como {target_user}. Verifique a senha ou permissões.")

    def reset_sftp_user(self):
        """Volta o SFTP ao usuário da conexão SSH."""
        new_sftp = self.client.open_sftp()
        with self.lock:
            if self.sftp:
                self.sftp.close()
            self.sftp = new_sftp
            self.sftp_user = self.username

    def resize_pty(self, cols, rows):
        """Redimensiona o PTY do shell (chamado quando o widget muda de tamanho)."""
        try:
            if self.shell and self.is_connected:
                self.shell.resize_pty(width=cols, height=rows)
        except Exception:
            pass

    def execute(self, command, timeout=None):
        """Executa um comando num canal novo.

        timeout: se definido (segundos), aplica-se às leituras do canal — um
        comando de fundo que trave (esperando input, por ex.) levanta
        socket.timeout em vez de prender a thread e vazar a sessão do servidor
        para sempre. NÃO use timeout curto em comandos legítimos longos
        (tar/apt/upload); deixe None nesses casos.
        """
        if self.client and self.is_connected:
            return self.client.exec_command(command, timeout=timeout)
        raise Exception("Not connected to SSH")

    def disconnect(self):
        self.is_connected = False
        self.sftp_user = ""
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
