"""
SSH免密配置模块 - Python实现
实现多设备对的SSH免密登录配置
"""
import shlex
import socket
import subprocess
import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

from sprinkler.logging import Colors

# 免密最终总是配置给 root：非 root 登录只用于 su 到 root 写公钥。
SSH_TARGET_USER = "root"


@dataclass
class SSHConfig:
    """SSH配置数据结构"""
    user: str
    password: str
    root_password: str = ""
    ssh_dir: Path = field(default_factory=lambda: Path.home() / ".ssh")
    results_dir: Path = field(default_factory=lambda: Path.home() / "ssh_batch_results")


@dataclass
class ProcessResult:
    """处理结果统计"""
    total_count: int = 0
    ping_success_count: int = 0
    ping_fail_count: int = 0
    ssh_success_count: int = 0
    ssh_fail_count: int = 0
    ping_fail_pairs: List[str] = field(default_factory=list)
    ssh_success_hosts: List[str] = field(default_factory=list)
    ssh_fail_hosts: List[str] = field(default_factory=list)

def add_colon_every_four(s: str) -> str:
    return ':'.join(s[i:i+4] for i in range(0, len(s), 4))

def _is_ipv4_mapped(s: str) -> bool:
    return bool(s.count(".") == 3 and s.count(":") == 1)

def _strip_ipv4_mapped_prefix(s: str) -> str:
    if s.startswith(":") and _is_ipv4_mapped(s):
        return s[1:]
    return s

def _is_ipv6(s: str) -> bool:
    if not s:
        return False
    if "::" in s:
        return True
    if _is_ipv4_mapped(s):
        return False
    return ":" in s

def _normalize_ipv6(s: str) -> str:
    if not s:
        return s
    if _is_ipv4_mapped(s):
        return _strip_ipv4_mapped_prefix(s)
    # 兼容纯hex EID: 无冒号时每4位补冒号成IPv6形式。
    # 16位(如 0000000100000002 -> 0000:0001:0000:0002)与32位EID(如 00000000007f020000100000df2a6b01 -> 0000:0000:007f:0200:0010:0000:df2a:6b01)
    if ":" not in s and len(s) in (16, 32) and all(c in "0123456789abcdefABCDEF" for c in s):
        return add_colon_every_four(s)
    return s

def _is_ipv4(s: str) -> bool:
    """判断字符串是否为合法IPv4地址(如 192.168.2.199)。"""
    parts = str(s or "").strip().split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or not 0 <= int(part) <= 255:
            return False
    return True

class NoPassConfigurator:
    """SSH免密配置器"""

    def __init__(self, config: SSHConfig):
        self.config = config
        self.result = ProcessResult()
        self.results_file = self._init_results_file()
        self.key_file = Path.home() / ".ssh" / "id_ed25519_ipv6"

    def _init_results_file(self) -> Path:
        """初始化结果文件"""
        self.config.results_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.config.results_dir / f"nopass_{timestamp}.log"

    def _log(self, message: str, print_console: bool = True, color: str = ""):
        """记录日志"""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"{timestamp} - {message}"

        with open(self.results_file, 'a', encoding='utf-8') as f:
            f.write(log_line + '\n')

        if print_console:
            if color:
                print(f"{color}{log_line}{Colors.RESET}")
            else:
                print(log_line)

    def _ensure_ssh_key(self) -> bool:
        """确保SSH密钥存在"""
        if self.key_file.exists():
            self._log(f"密钥已存在: {self.key_file}")
            return True

        self._log(f"生成ed25519密钥: {self.key_file}")
        try:
            subprocess.run(
                ['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', str(self.key_file)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self._log("密钥生成成功", color=Colors.GREEN)
            return True
        except subprocess.CalledProcessError:
            self._log("密钥生成失败", color=Colors.RED)
            return False

    def _add_ssh_config_entry(self, host_alias: str, target_ip: str,
                               source_ip: str):
        """添加SSH配置条目"""
        ssh_config = self.config.ssh_dir / "config"
        self.config.ssh_dir.mkdir(parents=True, exist_ok=True)
        ssh_config.touch(exist_ok=True)

        self._remove_ssh_config_entry(host_alias, target_ip)

        address_family = "inet6" if _is_ipv6(target_ip) else "inet"
        # IPv6 需要显式指定源地址/接口; IPv4 交给系统自动路由(避免强制错误源IP导致连不上)
        bind_address = f"    BindAddress {source_ip}\n" if _is_ipv6(target_ip) else ""
        # 打流(npu_os_command)用裸IP/EID ssh root@<target_ip> 直连, 把目标地址挂到别名同一块,
        # 使裸地址也能继承同套 IdentityFile(及IPv6的 BindAddress/AddressFamily) 配置。
        host_match = f"{host_alias} {target_ip}"

        config_entry = f"""
Host {host_match}
    HostName {target_ip}
    User {SSH_TARGET_USER}
    IdentityFile {self.key_file}
{bind_address}    AddressFamily {address_family}
    IdentitiesOnly yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
"""

        with open(ssh_config, 'a', encoding='utf-8') as f:
            f.write(config_entry)

        ssh_config.chmod(0o600)
        self._log(f"写入ssh config: Host {host_alias}")

    def _remove_ssh_config_entry(self, host_alias: str, target_ip: str):
        """移除SSH配置条目"""
        ssh_config = self.config.ssh_dir / "config"
        if not ssh_config.exists():
            return

        with open(ssh_config, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        new_lines = []
        skip = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"Host {host_alias} ") or stripped == f"Host {host_alias}":
                skip = True
            elif skip and line.strip() == "":
                skip = False
                continue
            elif skip and not line.startswith(" "):
                skip = False

            if not skip:
                new_lines.append(line)

        with open(ssh_config, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

    def _quick_ssh_check(self, host_alias: str) -> bool:
        """快速检查SSH连接"""
        try:
            subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3',
                 '-o', 'PasswordAuthentication=no', host_alias, 'echo', 'connected'],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def _setup_ssh(self, host_alias: str, target_ip: str, source_ip: str) -> bool:
        """使用paramiko配置SSH免密，将公钥追加到远端authorized_keys"""
        import paramiko

        user_host = f"{SSH_TARGET_USER}@{host_alias}"
        if self._quick_ssh_check(host_alias):
            self._log("SSH已配置(二次检查确认)", color=Colors.GREEN)
            return True

        if self.config.user != SSH_TARGET_USER:
            self._log(f"登录用户: {self.config.user}")
        self._log(f"配置SSH免密: {user_host}")

        pub_key_path = Path(str(self.key_file) + ".pub")
        if not pub_key_path.exists():
            self._log(f"公钥文件不存在: {pub_key_path}", color=Colors.RED)
            return False
        pub_key_content = pub_key_path.read_text(encoding="utf-8").strip()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            family = socket.AF_INET6 if _is_ipv6(target_ip) else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(5)
            # IPv6 需要显式指定源地址/接口; IPv4 交由系统自动路由(避免强制错误源IP导致超时)
            if _is_ipv6(target_ip):
                sock.bind((source_ip, 0))
            sock.connect((target_ip, 22))

            client.connect(
                hostname=target_ip,
                port=22,
                username=self.config.user,
                password=self.config.password,
                timeout=5,
                auth_timeout=5,
                look_for_keys=False,
                allow_agent=False,
                sock=sock,
            )
        except paramiko.AuthenticationException:
            self._log("认证失败: 密码错误", color=Colors.RED)
            client.close()
            return False
        except paramiko.SSHException as e:
            self._log(f"SSH连接失败: {e}", color=Colors.RED)
            client.close()
            return False
        except (OSError, TimeoutError) as e:
            self._log(f"网络连接失败: {e}", color=Colors.RED)
            client.close()
            return False

        try:
            need_su = self.config.user != SSH_TARGET_USER
            if need_su and not self.config.root_password:
                self._log("缺少root密码(root_password)，无法su切root写公钥", color=Colors.RED)
                return False

            if need_su:
                # 非root登录(如HwHiAiUser): 逐条exec_command执行, 每条都是
                # `echo 密码 | su - root -c '<简单命令>'`, 命令体简单以避免引号嵌套问题。
                def su_run(cmd_body, timeout=15):
                    cmd = (f"echo {shlex.quote(self.config.root_password)} | "
                           f"su - root -c {shlex.quote(cmd_body)}")
                    _, so, se = client.exec_command(cmd, timeout=timeout)
                    rc = so.channel.recv_exit_status()
                    return rc, se.read().decode("utf-8", errors="replace").strip()

                # 1) 把公钥写进 /root/.ssh/authorized_keys
                write_key = (
                    "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
                    f"grep -qF '{pub_key_content}' /root/.ssh/authorized_keys 2>/dev/null || "
                    f"echo '{pub_key_content}' >> /root/.ssh/authorized_keys && "
                    "chmod 600 /root/.ssh/authorized_keys"
                )
                rc, err = su_run(write_key)
                if rc != 0:
                    self._log(f"写入root公钥失败: {err or rc}", color=Colors.RED)
                    return False

                # 2) 放开root登录: PermitRootLogin yes + PasswordAuthentication yes, 允许root用密码
                #    或公钥登录。分两步: 先sed改配置(返回码可靠, 据此判断成败), 再pkill -HUP
                #    让配置生效(HUP会波及当前连接, 故不以其返回值为准)。
                enable_root = (
                    "sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config; "
                    "sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config"
                )
                rc, err = su_run(enable_root)
                if rc != 0:
                    self._log(f"放开root SSH登录失败: {err or rc}", color=Colors.RED)
                    return False

                # 让新配置生效(pkill -HUP sshd)。HUP可能扰动当前连接, 忽略其返回与输出。
                su_run("pkill -HUP sshd 2>/dev/null; true")

                self._log("已写入root公钥并放开root登录", color=Colors.GREEN)
                return True
            else:
                # root登录: 直接写密钥即可
                cmd = (
                    f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                    f"grep -qF '{pub_key_content}' ~/.ssh/authorized_keys 2>/dev/null || "
                    f"echo '{pub_key_content}' >> ~/.ssh/authorized_keys && "
                    f"chmod 600 ~/.ssh/authorized_keys"
                )
                _, stdout, stderr = client.exec_command(cmd, timeout=8)
                exit_code = stdout.channel.recv_exit_status()
                if exit_code == 0:
                    self._log("公钥写入成功", color=Colors.GREEN)
                    return True
                else:
                    err_msg = stderr.read().decode("utf-8", errors="replace").strip()
                    self._log(f"公钥写入失败: {err_msg}", color=Colors.RED)
                    return False
        except paramiko.SSHException as e:
            self._log(f"执行远程命令失败: {e}", color=Colors.RED)
            return False
        finally:
            client.close()

    def _process_pair(self, dev_id, source_ip, target_ip):
        """处理单个设备对"""
        self.result.total_count += 1
        host_alias = f"npu{dev_id}"

        self._log("=" * 50, print_console=False)
        print(f"\n{Colors.BOLD}{'=' * 50}{Colors.RESET}")
        self._log(f"处理第 {self.result.total_count} 对 (Pair{dev_id})")
        self._log(f"源IP: {source_ip}, 目的IP: {target_ip}")

        if not source_ip or not target_ip:
            self._log(f"跳过无效对: {source_ip} -> {target_ip}")
            return

        if not self._ensure_ssh_key():
            return

        self._add_ssh_config_entry(host_alias, target_ip,
                                    source_ip)

        if self._quick_ssh_check(host_alias):
            self._log("SSH已配置，跳过", color=Colors.GREEN)
            self.result.ssh_success_count += 1
            self.result.ssh_success_hosts.append(
                f"{SSH_TARGET_USER}@{host_alias}|Pair{dev_id}"
            )
            return

        self._log("快速ping检查...")

        if _is_ipv6(target_ip):
            ping_cmd = ['ping6', '-c', '3', '-W', '2', '-w', '10', target_ip, '-I', source_ip]
        else:
            ping_cmd = ['ping', '-c', '3', '-W', '2', '-w', '10', target_ip]
        try:
            subprocess.run(
                ping_cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15
            )
            self._log("网络连通", color=Colors.GREEN)
            self.result.ping_success_count += 1

            if self._setup_ssh(host_alias, target_ip, source_ip):
                self.result.ssh_success_count += 1
                self.result.ssh_success_hosts.append(
                    f"{SSH_TARGET_USER}@{host_alias}|npu{dev_id}"
                )
            else:
                self.result.ssh_fail_count += 1
                self.result.ssh_fail_hosts.append(
                    f"{SSH_TARGET_USER}@{host_alias}|npu{dev_id}"
                )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self._log("网络不通", color=Colors.RED)
            self.result.ping_fail_count += 1
            self.result.ping_fail_pairs.append(
                f"{source_ip}->{target_ip}|npu{dev_id}"
            )

    def _show_summary(self):
        """显示结果汇总"""
        print(f"\n{Colors.BOLD}{'=' * 50}{Colors.RESET}")
        self._log("结果汇总")
        print(f"{Colors.BOLD}{'=' * 50}{Colors.RESET}")
        self._log(f"总处理: {self.result.total_count}")
        self._log(f"Ping成功: {self.result.ping_success_count}", color=Colors.GREEN)
        self._log(f"Ping失败: {self.result.ping_fail_count}", color=Colors.RED if self.result.ping_fail_count else "")
        self._log(f"SSH成功: {self.result.ssh_success_count}", color=Colors.GREEN)
        self._log(f"SSH失败: {self.result.ssh_fail_count}", color=Colors.RED if self.result.ssh_fail_count else "")

        if self.result.ping_fail_pairs:
            print(f"\n{Colors.BOLD}Ping失败:{Colors.RESET}")
            for pair in self.result.ping_fail_pairs:
                ips, info = pair.split('|') if '|' in pair else (pair, '')
                self._log(f"  {ips}", color=Colors.RED)

        if self.result.ssh_fail_hosts:
            print(f"\n{Colors.BOLD}SSH失败:{Colors.RESET}")
            for host in self.result.ssh_fail_hosts:
                h, info = host.split('|') if '|' in host else (host, '')
                self._log(f"  {h}", color=Colors.RED)

        if self.result.ssh_success_hosts:
            print(f"\n{Colors.BOLD}SSH成功:{Colors.RESET}")
            for host in self.result.ssh_success_hosts:
                h, info = host.split('|') if '|' in host else (host, '')
                self._log(f"  {h}", color=Colors.GREEN)

        print(f"{Colors.BOLD}{'=' * 50}{Colors.RESET}")

    def _cleanup_old_keys(self):
        """清理旧的SSH密钥和配置文件"""
        files_to_remove = [
            self.config.ssh_dir / "config",
            self.key_file,
            Path(str(self.key_file) + ".pub"),
        ]
        for f in files_to_remove:
            if f.exists():
                try:
                    f.unlink()
                    self._log(f"已删除: {f}")
                except OSError as e:
                    self._log(f"删除失败: {f} ({e})", color=Colors.RED)

    def run(self):
        """执行免密配置"""
        from sprinkler.config.host_device_pairs import get_host_and_device_pairs

        self._cleanup_old_keys()
        host_and_device_pairs = get_host_and_device_pairs()
        pair_device_num = len(host_and_device_pairs)
        self._log(f"开始时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log("开始批量配置...")

        for dev_id in host_and_device_pairs.keys():
            pair = host_and_device_pairs[dev_id]
            source_ip, target_ip = pair[0], pair[1]
            if source_ip and not _is_ipv6(source_ip):
                source_ip = _normalize_ipv6(source_ip)
            if target_ip and not _is_ipv6(target_ip):
                target_ip = _normalize_ipv6(target_ip)
            self._process_pair(dev_id, source_ip, target_ip)

        self._show_summary()


def _config_file_path() -> Path:
    """免密配置使用的 config.conf 路径（与 shell 工具共用）。"""
    from sprinkler.utils.utils import default_config_dir
    return default_config_dir() / "config.conf"


def _get_config_value(key: str, prompt: str, default: str = "", secret: bool = False) -> str:
    """获取配置值"""
    config_file = _config_file_path()
    config_dir = config_file.parent

    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(f"{key}="):
                        value = line.split('=', 1)[1].strip()
                        if value:
                            return value
        except Exception:
            pass
    
    print(f"\n配置项: {key}")
    if default:
        print(f"{prompt} (默认: {default}): ", end='', flush=True)
    else:
        print(f"{prompt}: ", end='', flush=True)
    
    if secret:
        import getpass
        value = getpass.getpass('')
    else:
        value = input().strip()
    
    if not value and default:
        value = default
    
    if not value:
        return ""
    
    config_dir.mkdir(parents=True, exist_ok=True)
    with open(config_file, 'a', encoding='utf-8') as f:
        f.write(f"{key}={value}\n")
    print(f"已写入配置: {config_file} ({key})")

    return value


def no_pass_check(args):
    """免密配置入口函数"""
    config_file = _config_file_path()
    status = "已存在" if config_file.exists() else "不存在，将交互输入并自动创建"
    print(f"{Colors.GREEN}配置文件: {config_file} ({status}){Colors.RESET}")
    user = _get_config_value("POD_NPU_USER", "请输入连接NPU用户名", default="root")
    password = _get_config_value("POD_NPU_PASSWORD", "请输入登录NPU密码(输入不回显)", secret=True)
    
    if not user or not password:
        print("错误: 用户名和密码不能为空")
        return

    # 非root登录时需要root密码, 用于su切到root把公钥写入 /root/.ssh/authorized_keys
    root_password = ""
    if user != SSH_TARGET_USER:
        root_password = _get_config_value("POD_NPU_ROOT_PASSWORD",
                                          "请输入root密码(用于su切root写公钥, 输入不回显)",
                                          secret=True)
    
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    if known_hosts.exists():
        try:
            with open(known_hosts, 'r', encoding='utf-8') as f:
                lines = [line for line in f if not line.startswith('|1|')]
            with open(known_hosts, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        except Exception:
            pass
    
    config = SSHConfig(user=user, password=password, root_password=root_password)
    configurator = NoPassConfigurator(config)
    configurator.run()

