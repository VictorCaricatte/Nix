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
            

        if section == "CPU":
            if line.startswith("cpu "):
                cpu_lines.append(line)
            elif line.startswith("Mem"):
                mem_lines.append(line)
        elif section == "MEM":
            mem_lines.append(line)
        elif section == "NET":
            net_lines.append(line)
        elif section == "USERS":
            user_lines.append(line)
        elif section == "PROCS":
            proc_lines.append(line)
            

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
        mem_details['total'] = mem_info['MemTotal'] // 1024
        mem_details['available'] = mem_info['MemAvailable'] // 1024
        

    rx_total = 0
    tx_total = 0
    for l in net_lines:
        if ':' in l:
            if 'lo:' in l: 
                continue
            parts = l.split(':')[1].split()
            if len(parts) >= 9:
                try:
                    rx_total += int(parts[0])
                    tx_total += int(parts[8])
                except Exception:
                    pass
    net_bytes['rx'] = rx_total
    net_bytes['tx'] = tx_total
    

    for l in user_lines:
        parts = l.split()
        if len(parts) >= 1:
            user_str = parts[0]
            if len(parts) > 1:
                user_str += f" ({parts[1]})"
            users.append(user_str)
            

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
            pass
        
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

def count_remote_size(ssh_mgr, remote_path):
    """Conta o total de bytes em uma árvore de diretório remoto."""
    total = 0
    try:
        with ssh_mgr.lock:
            items = ssh_mgr.sftp.listdir_attr(remote_path)
        for item in items:
            rem = posixpath.join(remote_path, item.filename)
            if stat.S_ISDIR(item.st_mode):
                total += count_remote_size(ssh_mgr, rem)
            else:
                total += item.st_size or 0
    except Exception:
        pass
    return total

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

def download_directory_with_progress(ssh_mgr, remote_dir, local_dir, total_bytes, transferred_ref, progress_cb=None):
    """
    Download recursivo com rastreamento de progresso cumulativo correto.
    transferred_ref: lista [int] com bytes acumulados de arquivos já concluídos.
    total_bytes: tamanho total pré-calculado de toda a árvore remota.
    """
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)

    with ssh_mgr.lock:
        items = ssh_mgr.sftp.listdir_attr(remote_dir)

    for item in items:
        rem = posixpath.join(remote_dir, item.filename)
        loc = os.path.join(local_dir, item.filename)

        if stat.S_ISDIR(item.st_mode):
            download_directory_with_progress(ssh_mgr, rem, loc, total_bytes, transferred_ref, progress_cb)
        else:
            file_size = item.st_size or 0
            base = transferred_ref[0]

            def _file_cb(done, _tot, _base=base, _total=total_bytes):
                if progress_cb and _total > 0:
                    progress_cb(_base + done, _total)

            with ssh_mgr.lock:
                ssh_mgr.sftp.get(rem, loc, callback=_file_cb)

            transferred_ref[0] += file_size

def fetch_os_info(ssh_mgr):
    """
    Fetches detailed hardware and OS information from the remote server.
    """
    sections = []

    def run(cmd):
        try:
            _, stdout, _ = ssh_mgr.execute(cmd)
            return stdout.read().decode('utf-8', errors='replace').strip()
        except Exception:
            return ""

    uname  = run("uname -srm")
    osrel  = run("cat /etc/os-release 2>/dev/null | grep -E '^(PRETTY_NAME|VERSION)=' | sed 's/.*=//;s/\"//g'")
    uptime = run("uptime -p 2>/dev/null || uptime")
    hostname = run("hostname -f 2>/dev/null || hostname")

    os_block = ["━━━ OS / KERNEL ━━━"]
    if hostname: os_block.append(f"  Host    : {hostname}")
    if osrel:    os_block.append(f"  OS      : {osrel.splitlines()[0]}")
    if uname:    os_block.append(f"  Kernel  : {uname}")
    if uptime:   os_block.append(f"  Uptime  : {uptime.replace('up ', '')}")
    sections.append("\n".join(os_block))

    cpu_model  = run("grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs")
    cpu_cores  = run("nproc --all 2>/dev/null || grep -c ^processor /proc/cpuinfo")
    cpu_threads= run("grep -c ^processor /proc/cpuinfo 2>/dev/null")
    cpu_arch   = run("uname -m")
    cpu_freq   = run("grep -m1 'cpu MHz' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs")
    cpu_cache  = run("grep -m1 'cache size' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs")
    lscpu_virt = run("lscpu 2>/dev/null | grep -E 'Virtualization|Hypervisor' | head -2")

    cpu_block = ["━━━ CPU ━━━"]
    if cpu_model:   cpu_block.append(f"  Model   : {cpu_model}")
    if cpu_arch:    cpu_block.append(f"  Arch    : {cpu_arch}")
    if cpu_cores:   cpu_block.append(f"  Cores   : {cpu_cores}")
    if cpu_threads: cpu_block.append(f"  Threads : {cpu_threads}")
    if cpu_freq:    cpu_block.append(f"  Freq    : {float(cpu_freq):.0f} MHz" if cpu_freq.replace('.','').isdigit() else f"  Freq    : {cpu_freq}")
    if cpu_cache:   cpu_block.append(f"  Cache   : {cpu_cache}")
    if lscpu_virt:
        for line in lscpu_virt.splitlines():
            cpu_block.append(f"  {line.strip()}")
    sections.append("\n".join(cpu_block))

    mem_total = run("grep MemTotal /proc/meminfo 2>/dev/null | awk '{printf \"%.1f GB\", $2/1024/1024}'")
    mem_free  = run("grep MemAvailable /proc/meminfo 2>/dev/null | awk '{printf \"%.1f GB\", $2/1024/1024}'")
    swap_total= run("grep SwapTotal /proc/meminfo 2>/dev/null | awk '{printf \"%.1f GB\", $2/1024/1024}'")
    swap_free = run("grep SwapFree /proc/meminfo 2>/dev/null | awk '{printf \"%.1f GB\", $2/1024/1024}'")
    dmidecode_mem = run("sudo -n dmidecode -t memory 2>/dev/null | grep -E 'Size:|Type:|Speed:|Manufacturer:|Part Number:' | grep -v 'No Module' | head -20")

    mem_block = ["━━━ MEMORY ━━━"]
    if mem_total: mem_block.append(f"  Total   : {mem_total}")
    if mem_free:  mem_block.append(f"  Free    : {mem_free}")
    if swap_total:mem_block.append(f"  Swap    : {swap_total} total / {swap_free} free")
    if dmidecode_mem:
        mem_block.append("  --- DIMM slots ---")
        for line in dmidecode_mem.splitlines():
            mem_block.append(f"    {line.strip()}")
    sections.append("\n".join(mem_block))

    nvidia_smi = run("nvidia-smi --query-gpu=name,memory.total,driver_version,temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null")
    lspci_gpu  = run("lspci 2>/dev/null | grep -Ei 'vga|3d|display|gpu'")
    rocm_info  = run("rocminfo 2>/dev/null | grep -E 'Marketing Name|Device Type' | head -6")

    gpu_block = ["━━━ GPU ━━━"]
    found_gpu = False
    if nvidia_smi:
        found_gpu = True
        gpu_block.append("  [NVIDIA]")
        for line in nvidia_smi.splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 5:
                gpu_block.append(f"    Name    : {parts[0]}")
                gpu_block.append(f"    VRAM    : {parts[1]}")
                gpu_block.append(f"    Driver  : {parts[2]}")
                gpu_block.append(f"    Temp    : {parts[3]} °C")
                gpu_block.append(f"    Usage   : {parts[4]}")
    if rocm_info:
        found_gpu = True
        gpu_block.append("  [AMD ROCm]")
        for line in rocm_info.splitlines():
            gpu_block.append(f"    {line.strip()}")
    if lspci_gpu:
        found_gpu = True
        if not nvidia_smi and not rocm_info:
            gpu_block.append("  [PCIe GPUs]")
        else:
            gpu_block.append("  [PCIe Devices]")
        for line in lspci_gpu.splitlines():
            gpu_block.append(f"    {line.strip()}")
    if not found_gpu:
        gpu_block.append("  No dedicated GPU detected (or tools unavailable).")
    sections.append("\n".join(gpu_block))

    disk_info = run("df -h --output=source,size,used,avail,pcent,target 2>/dev/null | grep -v tmpfs | grep -v udev | head -20")
    lsblk_info= run("lsblk -d -o NAME,SIZE,ROTA,TYPE,MODEL 2>/dev/null | grep -v loop | head -15")

    disk_block = ["━━━ STORAGE ━━━"]
    if lsblk_info:
        disk_block.append("  Block devices:")
        for line in lsblk_info.splitlines():
            disk_block.append(f"    {line}")
    if disk_info:
        disk_block.append("  Filesystems:")
        for line in disk_info.splitlines():
            disk_block.append(f"    {line}")
    sections.append("\n".join(disk_block))

    ip_info  = run("ip -br addr show 2>/dev/null || ifconfig -a 2>/dev/null | grep -E 'inet |flags'")
    ip_route = run("ip route show default 2>/dev/null | head -3")
    dns_info = run("cat /etc/resolv.conf 2>/dev/null | grep nameserver | head -3")

    net_block = ["━━━ NETWORK ━━━"]
    if ip_info:
        net_block.append("  Interfaces:")
        for line in ip_info.splitlines():
            net_block.append(f"    {line}")
    if ip_route:
        net_block.append("  Default route:")
        net_block.append(f"    {ip_route.strip()}")
    if dns_info:
        net_block.append("  DNS:")
        for line in dns_info.splitlines():
            net_block.append(f"    {line.strip()}")
    sections.append("\n".join(net_block))

    pkg_count = run(
        "(dpkg -l 2>/dev/null | grep -c '^ii') || "
        "(rpm -qa 2>/dev/null | wc -l) || "
        "(pacman -Q 2>/dev/null | wc -l) || echo 'N/A'"
    )
    failed_svcs = run("systemctl --failed --no-legend 2>/dev/null | head -5")
    docker_info = run("docker info 2>/dev/null | grep -E 'Containers|Images|Server Version' | head -4")

    misc_block = ["━━━ SOFTWARE ━━━"]
    if pkg_count: misc_block.append(f"  Packages  : {pkg_count.strip()}")
    if failed_svcs:
        misc_block.append("  Failed services:")
        for line in failed_svcs.splitlines():
            misc_block.append(f"    {line.strip()}")
    else:
        misc_block.append("  Failed services: none")
    if docker_info:
        misc_block.append("  Docker:")
        for line in docker_info.splitlines():
            misc_block.append(f"    {line.strip()}")
    sections.append("\n".join(misc_block))

    return "\n\n".join(sections) if sections else "Could not fetch system information."
