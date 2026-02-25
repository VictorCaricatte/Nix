import os
import stat
import posixpath
import time

def parse_monitor_output(output):
    lines = output.split('\n')
    cpu_lines = [line for line in lines if line.startswith('cpu ')]
    cpu_usage = 0.0
    
    if len(cpu_lines) >= 2:
        def get_cpu_times(line):
            v = [int(x) for x in line.split()[1:]]
            return v[3] + (v[4] if len(v)>4 else 0), sum(v[0:3]) + sum(v[5:8]) + v[3] + (v[4] if len(v)>4 else 0)
        
        i1, t1 = get_cpu_times(cpu_lines[0])
        i2, t2 = get_cpu_times(cpu_lines[1])
        if t2 - t1 > 0: 
            cpu_usage = max(0.0, min(100.0, (((t2 - t1) - (i2 - i1)) / (t2 - t1)) * 100))
    
    mem_info = {l.split(':')[0]: int(l.split()[1]) for l in lines if l.startswith('Mem')}
    mem_usage = 0.0
    if 'MemTotal' in mem_info and 'MemAvailable' in mem_info: 
        mem_usage = ((mem_info['MemTotal'] - mem_info['MemAvailable']) / mem_info['MemTotal']) * 100
    
    procs = []
    capture = False
    for line in lines:
        if line.strip() == "==PROCS==": 
            capture = True
            continue
        if capture and line.strip(): 
            parts = line.split(maxsplit=3)
            if len(parts) == 4: 
                procs.append(parts)
            
    return cpu_usage, mem_usage, procs

def fetch_os_info(ssh_mgr):
    try:
        # Usando Raw String (r"") para evitar problemas do python interpretando escapes do bash (\n) e cifrões
        cmd = r"""
        if command -v neofetch >/dev/null 2>&1; then
            neofetch --stdout
        elif command -v fastfetch >/dev/null 2>&1; then
            fastfetch --pipe
        else
            OS_STR=$(cat /etc/os-release 2>/dev/null | grep '^PRETTY_NAME=' | cut -d= -f2 | tr -d '"' || uname -s)
            HOST_STR=$(cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null || hostname)
            KERNEL_STR=$(uname -r)
            UPTIME_STR=$(uptime -p 2>/dev/null | sed 's/up //')
            
            PKGS="Unknown"
            if command -v dpkg-query >/dev/null 2>&1; then PKGS="$(dpkg-query -f '.\n' -W | wc -l) (dpkg)";
            elif command -v rpm >/dev/null 2>&1; then PKGS="$(rpm -qa | wc -l) (rpm)";
            elif command -v pacman >/dev/null 2>&1; then PKGS="$(pacman -Q | wc -l) (pacman)"; fi
            
            SHELL_STR=$(basename "$SHELL" 2>/dev/null || echo "$SHELL")
            RES_STR=$(xrandr 2>/dev/null | awk '/\*/ {print $1}' | head -n 1 || echo 'N/A (Headless/SSH)')
            
            CPU_STR=$(awk -F: '/^[mM]odel name/ {print $2; exit}' /proc/cpuinfo | xargs)
            if [ -z "$CPU_STR" ]; then CPU_STR=$(lscpu 2>/dev/null | grep -i 'model name' | cut -d: -f2 | xargs); fi
            if [ -z "$CPU_STR" ]; then CPU_STR="Unknown"; fi
            
            GPU_STR=$(lspci 2>/dev/null | grep -iE 'vga|3d|display' | cut -d: -f3 | xargs)
            if [ -z "$GPU_STR" ]; then GPU_STR="Unknown"; fi
            
            MEM_STR=$(free -h 2>/dev/null | awk 'NR==2 {print $3 " / " $2}')
            if [ -z "$MEM_STR" ]; then MEM_STR="Unknown"; fi
            
            echo "OS: $OS_STR"
            echo "Host: $HOST_STR"
            echo "Kernel: $KERNEL_STR"
            echo "Uptime: $UPTIME_STR"
            echo "Packages: $PKGS"
            echo "Shell: $SHELL_STR"
            echo "Resolution: $RES_STR"
            echo "DE: ${XDG_CURRENT_DESKTOP:-N/A (Headless)}"
            echo "WM: ${GDMSESSION:-N/A (Headless)}"
            echo "Theme: ${GTK_THEME:-Unknown}"
            echo "Terminal: ${TERM:-Unknown}"
            echo "CPU: $CPU_STR"
            echo "GPU: $GPU_STR"
            echo "Memory: $MEM_STR"
        fi
        """
        stdin, stdout, stderr = ssh_mgr.execute(cmd)
        return stdout.read().decode('utf-8').strip()
    except Exception:
        return "Could not fetch OS info."

def upload_recursive(ssh_mgr, local_path, remote_path, sudo_pwd_list, get_sudo_cb, progress_cb=None):
    if os.path.isdir(local_path):
        try:
            with ssh_mgr.lock:
                try:
                    ssh_mgr.sftp.stat(remote_path)
                    dir_exists = True
                except IOError:
                    dir_exists = False
                    
                if not dir_exists:
                    ssh_mgr.sftp.mkdir(remote_path)
        except Exception as e:
            if "Permission" in str(e) or "denied" in str(e).lower():
                if not sudo_pwd_list[0]:
                    sudo_pwd_list[0] = get_sudo_cb()
                if not sudo_pwd_list[0]: 
                    raise Exception("Operação cancelada (SUDO necessário).")
                
                safe_remote = remote_path.replace('"', '\\"')
                cmd = f'sudo -S mkdir -p "{safe_remote}"'
                stdin, stdout, stderr = ssh_mgr.execute(cmd)
                stdin.write(sudo_pwd_list[0] + "\n")
                stdin.flush()
                if "incorrect password" in stderr.read().decode().lower():
                    sudo_pwd_list[0] = None
                    raise Exception("Senha do sudo incorreta.")
            else:
                raise e
                
        for item in os.listdir(local_path):
            upload_recursive(ssh_mgr, os.path.join(local_path, item), posixpath.join(remote_path, item), sudo_pwd_list, get_sudo_cb, progress_cb)
    else:
        try:
            with ssh_mgr.lock:
                ssh_mgr.sftp.put(local_path, remote_path, callback=progress_cb)
        except Exception as e:
            if "Permission" in str(e) or "denied" in str(e).lower():
                if not sudo_pwd_list[0]:
                    sudo_pwd_list[0] = get_sudo_cb()
                if not sudo_pwd_list[0]: 
                    raise Exception("Operação cancelada (SUDO necessário).")
                
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
                if "incorrect password" in err_out:
                    sudo_pwd_list[0] = None
                    raise Exception("Senha do sudo incorreta.")
            else:
                raise e

def download_directory_recursive(ssh_mgr, remote_dir, local_dir, progress_cb=None):
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
