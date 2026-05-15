import paramiko
import threading
import socket
import os
import sys

class SSHManager:
    def __init__(self):
        self.client = None
        self.sftp = None
        self.shell = None
        self.lock = threading.Lock()
        self.is_connected = False
        self.sftp_user_stack = []

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

    def connect(self, host, username, password=None, key_filename=None, use_x11=False):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": host,
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
                
        self.shell.get_pty(term='vt100', width=120, height=40)
        self.shell.invoke_shell()
        
        self.sftp = self.client.open_sftp()
        self.sftp_user_stack = []
        self.is_connected = True

    def switch_sftp_user(self, target_user, sudo_password):
        check_cmd = f"sudo -n -u {target_user} true" if target_user != "root" else "sudo -n true"
        _, stdout, _ = self.client.exec_command(check_cmd)
        exit_status = stdout.channel.recv_exit_status()
        
        channel = self.client.get_transport().open_session()
        sftp_paths = "/usr/lib/openssh/sftp-server /usr/libexec/openssh/sftp-server /usr/lib/sftp-server /usr/libexec/sftp-server"
        find_cmd = f"for p in {sftp_paths}; do if [ -x $p ]; then exec $p; fi; done"
        
        cmd = f"sudo -S -p '' -u {target_user} sh -c '{find_cmd}'" if target_user != "root" else f"sudo -S -p '' sh -c '{find_cmd}'"
        channel.exec_command(cmd)
        
        if exit_status != 0 and sudo_password:
            channel.sendall(sudo_password + "\n")
        
        try:
            new_sftp = paramiko.SFTPClient(channel)
            with self.lock:
                if self.sftp:
                    self.sftp.close()
                self.sftp = new_sftp
                self.sftp_user_stack.append(target_user)
            return True
        except Exception as e:
            channel.close()
            raise Exception(f"Não foi possível iniciar SFTP como {target_user}. Verifique a senha ou permissões.")
            
    def pop_sftp_user(self, sudo_password):
        prev = None
        with self.lock:
            if self.sftp_user_stack:
                self.sftp_user_stack.pop()
                if self.sftp:
                    self.sftp.close()
                if self.sftp_user_stack:
                    prev = self.sftp_user_stack[-1]
                    self.sftp_user_stack.pop() 
                else:
                    self.sftp = self.client.open_sftp()
                    
        if prev:
            self.switch_sftp_user(prev, sudo_password)

    def execute(self, command):
        if self.client and self.is_connected:
            return self.client.exec_command(command)
        raise Exception("Not connected to SSH")

    def disconnect(self):
        self.is_connected = False
        self.sftp_user_stack.clear()
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
