import os
import re
import stat
import posixpath
import time
import shlex


# ---------------------------------------------------------------------------
#  Integração com o shell: o próprio shell informa usuário/pasta/screen
# ---------------------------------------------------------------------------
# Sequência invisível (OSC privado) que o hook imprime a cada prompt:
#   ESC ] 7770 ; token ; usuário ; host ; STY ; pasta BEL
# O token é aleatório por conexão: sem ele, um `cat arquivo_malicioso` poderia
# forjar o relatório e fazer o Explorer trocar de pasta ou elevar o SFTP.
_REPORT_PREFIX = "\x1b]7770;"
_REPORT_RE = re.compile(r"\x1b\]7770;([^\x07\x1b]*)\x07")
_REPORT_MAX_PENDING = 8192

_PASSWORD_PROMPT_RE = re.compile(
    r"(password|passwort|passphrase|pass phrase|senha|contrase[nñ]a|mot de passe)"
    r"[^:：]*[:：]\s*$", re.IGNORECASE)
# Prompt de shell. O caractere antes do '#' exclui o psql (postgres=#, -#, (#)
# e o antes do '%' exclui progresso de download ("45%"). '>' fica de fora de
# propósito: é o prompt de python, mysql, redis, sftp...
_SHELL_PROMPT_RE = re.compile(
    r"(?:(?:^|[^=\-('\"])#|\$|(?:^|[^\d])%|^\s*(?:➜|❯|λ).*)\s?$")


def extract_shell_reports(text):
    """Separa os relatórios do hook do fluxo do terminal.

    Devolve (texto_limpo, relatórios, resto). `resto` é uma sequência cortada
    no fim do chunk e deve ser prefixado ao próximo chunk. Cada relatório é uma
    tupla (token, usuário, host, sty, pasta)."""
    reports = []

    def grab(match):
        fields = match.group(1).split(";", 4)
        if len(fields) == 5:
            reports.append(tuple(fields))
        return ""

    clean = _REPORT_RE.sub(grab, text)
    idx = clean.rfind("\x1b")
    if idx >= 0:
        tail = clean[idx:]
        if _REPORT_PREFIX.startswith(tail) or (
                tail.startswith(_REPORT_PREFIX) and len(tail) < _REPORT_MAX_PENDING):
            return clean[:idx], reports, tail
    return clean, reports, ""


def is_password_prompt(line):
    """A linha do cursor é um prompt de senha (sudo, su, ssh...)?"""
    return bool(_PASSWORD_PROMPT_RE.search(line or ""))


def is_sudo_prompt(line):
    """Prompt de senha do sudo, isto é, a senha do PRÓPRIO usuário conectado."""
    return "[sudo]" in (line or "") and is_password_prompt(line)


def is_shell_prompt(line):
    """A linha do cursor parece um prompt de shell esperando comando?"""
    return bool(line and line.strip() and not is_password_prompt(line)
                and _SHELL_PROMPT_RE.search(line))


def is_initial_prompt(line):
    """Critério mais flexível, usado só logo após o login de bash/zsh: aceita
    temas sem $/# no fim, mas nunca uma pergunta (troca de senha, MFA...)."""
    text = (line or "").strip()
    return bool(text) and not is_password_prompt(line) and text[-1] not in ":?："


def build_shell_hook(token, erase_rows):
    """Linha enviada ao shell para ele passar a informar seu estado.

    Precisa ser analisável por bash, zsh e dash. Dentro do GNU screen a
    sequência vai embrulhada em DCS, senão o screen a descartaria. No fim a
    própria linha apaga seu eco (erase_rows linhas acima), de modo que funciona
    também através do screen, que mantém uma cópia própria da tela. O espaço
    inicial e o `history -d` a mantêm fora do histórico do bash."""
    return (
        " __nix_t=" + sh_quote(token) + ";__nix_u=$(id -un);__nix_h=$(uname -n);"
        "__nix_r(){ __nix_s=$?;__nix_f='\\033]7770;%s;%s;%s;%s;%s\\007';"
        "[ -z \"${STY:-}\" ]||__nix_f=\"\\033P$__nix_f\\033\\\\\\\\\";"
        "printf \"$__nix_f\" \"$__nix_t\" \"$__nix_u\" \"$__nix_h\" \"${STY:-}\" \"$PWD\";"
        "return $__nix_s;};"
        "if [ -n \"${BASH_VERSION:-}\" ];then history -d $HISTCMD 2>/dev/null;"
        "case \"${PROMPT_COMMAND:-}\" in *__nix_r*);;"
        "*)PROMPT_COMMAND=\"__nix_r;${PROMPT_COMMAND:-}\";;esac;"
        "elif [ -n \"${ZSH_VERSION:-}\" ];then "
        "eval 'precmd_functions=(${precmd_functions:#__nix_r} __nix_r)';fi;"
        f"printf '\\033[{int(erase_rows)}A\\r\\033[J';__nix_r"
    )


# Teto de profundidade nas descidas recursivas de diretório. Protege contra
# árvores muito fundas / ciclos por symlink no servidor remoto, que poderiam
# estourar a pilha (RecursionError) ou rodar indefinidamente.
MAX_RECURSION_DEPTH = 64


def sh_quote(value):
    """Escapa um valor para uso seguro como UM argumento em comando shell POSIX.

    Use SEMPRE isto ao interpolar input do usuário (nome de arquivo, usuário,
    nome de screen, porta, IP, path...) em comandos enviados ao servidor —
    evita command injection. shlex.quote envolve em aspas simples e trata o
    caso da própria aspa simples; string vazia vira ''.
    """
    return shlex.quote("" if value is None else str(value))

class TransferCancelled(Exception):
    """Levantada dentro do callback de progresso para abortar um upload/download."""


# ---------------------------------------------------------------------------
#  Autocompletar com Tab (estilo bash)
# ---------------------------------------------------------------------------
_CMD_WORDS = {"sudo", "time", "xargs", "env", "nohup", "exec", "watch", "command"}
_SHELL_SPECIALS = set(" \t\n!\"#$&'()*;<>?[\\]^`{|}~")


def split_completion_context(text):
    """Analisa o texto ANTES do cursor e devolve o contexto da palavra atual.

    Retorna dict: start (índice onde a palavra começa), value (palavra sem
    aspas/escapes — é o que se completa no servidor), quote (aspa aberta ou
    None) e cmd_pos (True se a palavra está em posição de comando: início da
    linha, depois de | ; && || ou depois de sudo/time/...).
    """
    tokens = []          # valores das palavras/separadores já fechados
    start = None
    value = []
    quote = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < n and text[i + 1] in '"\\$`':
                i += 1
                value.append(text[i])
            else:
                value.append(ch)
        elif ch == "\\" and i + 1 < n:
            if start is None:
                start = i
            i += 1
            value.append(text[i])
        elif ch in "'\"":
            if start is None:
                start = i
            quote = ch
        elif ch.isspace():
            if start is not None:
                tokens.append("".join(value))
                start, value = None, []
        elif ch in "|;&":
            if start is not None:
                tokens.append("".join(value))
                start, value = None, []
            tokens.append("|")   # todos os separadores equivalem a "novo comando"
        else:
            if start is None:
                start = i
            value.append(ch)
        i += 1
    if start is None:
        start = n
    prev = tokens[-1] if tokens else None
    cmd_pos = prev is None or prev == "|" or prev in _CMD_WORDS
    return {"start": start, "value": "".join(value), "quote": quote, "cmd_pos": cmd_pos}


def build_completion_script(cwd, word, cmd_mode):
    """Comando remoto que lista candidatos via `compgen` do bash (dirs com '/')."""
    return (
        "bash -c 'cd \"$1\" 2>/dev/null; "
        "if [ \"$3\" = cmd ]; then compgen -c -- \"$2\" | sort -u | head -n 300; "
        "else compgen -f -- \"$2\" | head -n 500 | while IFS= read -r f; do "
        "if [ -d \"$f\" ]; then printf \"%s/\\n\" \"$f\"; else printf \"%s\\n\" \"$f\"; fi; "
        "done; fi' _ " + sh_quote(cwd) + " " + sh_quote(word) + " "
        + ("cmd" if cmd_mode else "file")
    )


def escape_completion(word, quote):
    """Escapa `word` para inserção na linha de comando respeitando a aspa aberta."""
    if quote == "'":
        return word.replace("'", "'\\''")
    if quote == '"':
        return "".join("\\" + c if c in '"\\$`' else c for c in word)
    out = []
    for idx, c in enumerate(word):
        out.append("\\" + c if (c in _SHELL_SPECIALS and not (idx == 0 and c == "~")) else c)
    return "".join(out)


def compute_completion(ctx, matches, home=""):
    """Dado o contexto e os candidatos, calcula o que inserir.

    Retorna (texto_a_inserir, lista_de_opcoes_para_exibir). Se `texto_a_inserir`
    for None não há progresso possível (mostrar as opções).
    """
    value = ctx["value"]
    quote = ctx["quote"]
    tilde = value.startswith("~") and home
    if tilde:
        matches = [("~" + m[len(home):]) if m.startswith(home) else m for m in matches]
    matches = sorted(set(matches))
    if not matches:
        return None, []
    common = os.path.commonprefix(matches)
    if len(matches) == 1:
        m = matches[0]
        done = escape_completion(m, quote)
        if m.endswith("/"):
            return done, matches            # diretório: continua digitando
        return done + (quote or "") + " ", matches
    if len(common) > len(value):
        return escape_completion(common, quote), matches
    return None, matches


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
            user = parts[0]
            tty = parts[1] if len(parts) > 1 else ""
            # `who` mostra a origem da conexão (IP/host) entre parênteses no fim
            # da linha para logins remotos; logins locais não têm esse campo.
            origin = ""
            if "(" in l and ")" in l:
                origin = l[l.rfind("(") + 1: l.rfind(")")].strip()
            users.append({"user": user, "tty": tty, "origin": origin})
            

    for l in proc_lines:
        parts = l.split(None, 4) 
        if len(parts) == 5:
            procs.append(parts)
            
    return cpu_usage, mem_usage, procs, net_bytes, users, mem_details

def upload_recursive(ssh_mgr, local_path, remote_path, sudo_pwd_list, get_sudo_pwd_func, progress_cb=None, _depth=0):
    """
    Faz upload recursivo de arquivos ou pastas e aplica elevação via sudo
    caso receba "Permission denied".
    """
    if _depth > MAX_RECURSION_DEPTH:
        raise Exception(f"Profundidade máxima ({MAX_RECURSION_DEPTH}) excedida em {local_path}")
    if os.path.isdir(local_path) and not os.path.islink(local_path):
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
                progress_cb,
                _depth + 1,
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
                    
                cmd = f'sudo -S mv {sh_quote(tmp_path)} {sh_quote(remote_path)}'
                
                stdin, stdout, stderr = ssh_mgr.execute(cmd)
                stdin.write(sudo_pwd_list[0] + "\n")
                stdin.flush()
                
                err_out = stderr.read().decode().lower()
                if "incorrect password" in err_out or "senha incorreta" in err_out:
                    sudo_pwd_list[0] = None
                    raise Exception("Senha do sudo incorreta.")
            else:
                raise e

def count_remote_size(ssh_mgr, remote_path, _depth=0):
    """Conta o total de bytes em uma árvore de diretório remoto."""
    total = 0
    if _depth > MAX_RECURSION_DEPTH:
        return total
    try:
        with ssh_mgr.lock:
            items = ssh_mgr.sftp.listdir_attr(remote_path)
        for item in items:
            rem = posixpath.join(remote_path, item.filename)
            # Ignora symlinks: seguir links pode gerar ciclos / contagem dupla.
            if stat.S_ISLNK(item.st_mode):
                continue
            if stat.S_ISDIR(item.st_mode):
                total += count_remote_size(ssh_mgr, rem, _depth + 1)
            else:
                total += item.st_size or 0
    except Exception:
        pass
    return total

def download_directory_recursive(ssh_mgr, remote_dir, local_dir, progress_cb=None, _depth=0):
    """
    Faz download de diretórios inteiros via SFTP mantendo a estrutura local.
    """
    if _depth > MAX_RECURSION_DEPTH:
        raise Exception(f"Profundidade máxima ({MAX_RECURSION_DEPTH}) excedida em {remote_dir}")
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)

    with ssh_mgr.lock:
        items = ssh_mgr.sftp.listdir_attr(remote_dir)

    for item in items:
        rem_path = posixpath.join(remote_dir, item.filename)
        loc_path = os.path.join(local_dir, item.filename)

        if stat.S_ISLNK(item.st_mode):
            continue  # não segue symlinks: evita ciclos e escapes de path
        if stat.S_ISDIR(item.st_mode):
            download_directory_recursive(ssh_mgr, rem_path, loc_path, progress_cb, _depth + 1)
        else:
            with ssh_mgr.lock:
                ssh_mgr.sftp.get(rem_path, loc_path, callback=progress_cb)

def download_directory_with_progress(ssh_mgr, remote_dir, local_dir, total_bytes, transferred_ref, progress_cb=None, _depth=0):
    """
    Download recursivo com rastreamento de progresso cumulativo correto.
    transferred_ref: lista [int] com bytes acumulados de arquivos já concluídos.
    total_bytes: tamanho total pré-calculado de toda a árvore remota.
    """
    if _depth > MAX_RECURSION_DEPTH:
        raise Exception(f"Profundidade máxima ({MAX_RECURSION_DEPTH}) excedida em {remote_dir}")
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)

    with ssh_mgr.lock:
        items = ssh_mgr.sftp.listdir_attr(remote_dir)

    for item in items:
        rem = posixpath.join(remote_dir, item.filename)
        loc = os.path.join(local_dir, item.filename)

        if stat.S_ISLNK(item.st_mode):
            continue  # não segue symlinks: evita ciclos e escapes de path
        if stat.S_ISDIR(item.st_mode):
            download_directory_with_progress(ssh_mgr, rem, loc, total_bytes, transferred_ref, progress_cb, _depth + 1)
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
            # timeout evita que um comando de hardware travado (ex.: nvidia-smi
            # com driver quebrado) prenda a thread e vaze a sessão do servidor.
            _, stdout, _ = ssh_mgr.execute(cmd, timeout=15)
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
