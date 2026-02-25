import paramiko
import threading

class SSHManager:
    def __init__(self):
        self.client = None
        self.sftp = None
        self.shell = None
        self.lock = threading.Lock()
        self.is_connected = False

    def connect(self, host, username, password=None, key_filename=None):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Conecta usando chave SSH se fornecida, senão usa senha
        if key_filename and key_filename.strip():
            self.client.connect(host, username=username, key_filename=key_filename, timeout=10)
        else:
            self.client.connect(host, username=username, password=password, timeout=10)
        
        self.shell = self.client.invoke_shell(term='vt100', width=120, height=40)
        self.sftp = self.client.open_sftp()
        self.is_connected = True

    def execute(self, command):
        if self.client and self.is_connected:
            return self.client.exec_command(command)
        raise Exception("Not connected to SSH")

    def disconnect(self):
        self.is_connected = False
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
