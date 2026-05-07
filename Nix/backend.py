import os
import stat
import posixpath
import time

def parse_monitor_output(output):
    """
    Decodifica a saída do comando de monitoramento unificado.
    Suporta os blocos: CPU, MEM, NET, USERS e PROCS.
    """
    lines = output.split('\n')
    
    cpu_usage = 0.0
    mem_usage = 0.0
    procs = []
    net_bytes = {'rx': 0, 'tx': 0}
    users = []
    mem_details = {'total': 0, 'available': 0}
    
    section = "CPU"
    
    cpu_lines = []
    mem_lines = []
    net_lines = []
    user_lines = []
    proc_lines = []
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        # Identificadores de blocos
        if line == "==MEM==":
            section = "MEM"
            continue
        elif line == "==NET==":
            section = "NET"
            continue
        elif line == "==USERS==":
            section = "USERS"
            continue
        elif line == "==PROCS==":
            section = "PROCS"
            continue
            
        # Distribuição das linhas
        if section == "CPU":
            if line.startswith("cpu "):
                cpu_lines.append(line)
            elif line.startswith("Mem"):
                mem_lines.append(line) # Compatibilidade caso o marcador falhe
        elif section == "MEM":
            mem_lines.append(line)
        elif section == "NET":
            net_lines.append(line)
        elif section == "USERS":
            user_lines.append(line)
        elif section == "PROCS":
            proc_lines.append(line)
            
    # 1. Analisando CPU
    if len(cpu_lines) >= 2:
        def get_cpu_times(line):
            v = [int(x) for x in line.split()[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            total = sum(v[0:3]) + sum(v[5:8]) + idle
            return idle, total
        
        try:
            i1, t1 = get_cpu_times(cpu_lines[0])
            i2, t2 = get_cpu_times(cpu_lines[1])
            if t2 - t1 > 0:
                cpu_usage = max(0.0, min(100.0, (((t2 - t1) - (i2 - i1)) / (t2 - t1)) * 100))
        except Exception:
            pass
            
    # 2. Analisando Memória
    mem_info = {}
    for l in mem_lines:
        if ':' in l:
            parts = l.split(':')
            try:
                mem_info[parts[0].strip()] = int(parts[1].split()[0])
            except Exception:
                pass
            
    if 'MemTotal' in mem_info and 'MemAvailable' in mem_info:
        mem_usage = ((mem_info['MemTotal'] - mem_info['MemAvailable']) / mem_info['MemTotal']) * 100
        mem_details['total'] = mem_info['MemTotal'] // 1024       # Converte KB para MB
        mem_details['available'] = mem_info['MemAvailable'] // 1024 # Converte KB para MB
        
    # 3. Analisando Tráfego de Rede (/proc/net/dev)
    rx_total = 0
    tx_total = 0
    for l in net_lines:
        if ':' in l:
            if 'lo:' in l: 
                continue # Ignora rede de loopback local
            parts = l.split(':')[1].split()
            if len(parts) >= 9:
                try:
                    rx_total += int(parts[0]) # Receive
                    tx_total += int(parts[8]) # Transmit
                except Exception:
                    pass
    net_bytes['rx'] = rx_total
    net_bytes['tx'] = tx_total
    
    # 4. Analisando Usuários Logados
    for l in user_lines:
        parts = l.split()
        if len(parts) >= 1:
            user_str = parts[0]
            if len(parts) > 1:
                user_str += f" ({parts[1]})" # Adiciona o terminal (tty)
            users.append(user_str)
            
    # 5. Analisando Processos (PID, User, CPU, Mem, Comm)
    for l in proc_lines:
        parts = l.split(None, 4) 
        if len(parts) == 5:
            procs.append(parts)
            
    return cpu_usage, mem_usage, procs, net_bytes, users, mem_details


def upload_recursive(ssh_mgr, local_path, remote_path, sudo_pwd_list, get_sudo_pwd_func, progress_cb=None):
    """
    Faz upload recursivo de arquivos ou pastas e aplica elevação via sudo 
    caso receba "Permission denied".
    """
    if os.path.isdir(local_path):
        try:
            with ssh_mgr.lock:
                ssh_mgr.sftp.mkdir(remote_path)
        except IOError:
            pass # Diretório já existe ou sem permissão temporária
        
        for item in os.listdir(local_path):
            upload_recursive(
                ssh_mgr, 
                os.path.join(local_path, item), 
                posixpath.join(remote_path, item), 
                sudo_pwd_list, 
                get_sudo_pwd_func, 
                progress_cb
            )
    else:
        try:
            with ssh_mgr.lock:
                ssh_mgr.sftp.put(local_path, remote_path, callback=progress_cb)
        except Exception as e:
            if "Permission" in str(e) or "denied" in str(e).lower() or getattr(e, 'errno', None) == 13:
                # Caso a permissão falhe, tenta injetar e mover por meio de SUDO e da pasta TMP
                if sudo_pwd_list[0] is None:
                    sudo_pwd_list[0] = get_sudo_pwd_func()
                if sudo_pwd_list[0] is None:
                    raise Exception("SUDO cancelado pelo usuário.")
                    
                tmp_path = posixpath.join("/tmp", f"nebula_up_{int(time.time())}_{os.path.basename(local_path)}")
                with ssh_mgr.lock:
                    ssh_mgr.sftp.put(local_path, tmp_path, callback=progress_cb)
                    
                safe_tmp = tmp_path.replace('"', '\\"')
                safe_remote = remote_path.replace('"', '\\"')
                cmd = f'sudo -S mv "{safe_tmp}" "{safe_remote}"'
                
                stdin, stdout, stderr = ssh_mgr.execute(cmd)
                stdin.write(sudo_pwd_list[0] + "\n")
                stdin.flush()
                
                err_out = stderr.read().decode().lower()
                if "incorrect password" in err_out or "senha incorreta" in err_out:
                    sudo_pwd_list[0] = None
                    raise Exception("Senha do sudo incorreta.")
            else:
                raise e


def download_directory_recursive(ssh_mgr, remote_dir, local_dir, progress_cb=None):
    """
    Faz download de diretórios inteiros via SFTP mantendo a estrutura local.
    """
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)
        
    with ssh_mgr.lock:
        items = ssh_mgr.sftp.listdir_attr(remote_dir)
        
    for item in items:
        rem_path = posixpath.join(remote_dir, item.filename)
        loc_path = os.path.join(local_dir, item.filename)
        
        if stat.S_ISDIR(item.st_mode):
            download_directory_recursive(ssh_mgr, rem_path, loc_path, progress_cb)
        else:
            with ssh_mgr.lock:
                ssh_mgr.sftp.get(rem_path, loc_path, callback=progress_cb)


def fetch_os_info(ssh_mgr):
    """
    Busca os detalhes do sistema operacional remoto para preencher a aba "OS Info".
    """
    try:
        cmd = "uname -a && echo '\n--- OS Release ---' && cat /etc/os-release"
        stdin, stdout, stderr = ssh_mgr.execute(cmd)
        return stdout.read().decode('utf-8', errors='replace').strip()
    except Exception:
        return "Não foi possível resgatar as informações do Sistema Operacional."
