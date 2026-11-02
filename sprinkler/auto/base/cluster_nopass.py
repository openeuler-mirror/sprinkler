"""
集群SSH免密配置模块
通过 -sp/-ca/-node 解析目标节点IP，直接批量完成SSH免密配置
支持 -node "IP;user;password" 格式直接读取密码
"""
import getpass
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

try:
    import paramiko
except ImportError:
    paramiko = None

from sprinkler.base.log import RunLog
from sprinkler.connect.targets import TargetContext


@dataclass
class NodeCred:
    host: str
    user: str
    password: str


print_lock = Lock()


def _print_log(run_log: RunLog, level: str, message: str):
    with print_lock:
        if level == "PASS":
            run_log.ok(message)
        elif level == "FAIL":
            run_log.fail(message)
        else:
            run_log.info(message)


def _check_paramiko_installed(run_log: RunLog) -> bool:
    if paramiko:
        return True
    run_log.fail("paramiko未安装，请执行: pip install paramiko")
    return False


def _resolve_targets(args: Any) -> list[NodeCred]:
    try:
        targets = TargetContext(args).get_targets("cpu_os")
    except FileNotFoundError as exc:
        raise ValueError(f"配置文件未找到: {exc}")
    except ValueError as exc:
        raise ValueError(f"参数解析错误: {exc}")

    result = []
    for t in targets:
        if not t.host:
            continue
        if not _validate_ip(t.host):
            continue
        result.append(NodeCred(host=t.host, user=t.user or "root", password=t.password or ""))

    if not result:
        raise ValueError("未解析到任何目标IP")

    return result


def _validate_ip(ip: str) -> bool:
    pattern = r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$"
    return bool(re.match(pattern, ip))


def _ensure_ssh_key(run_log: RunLog) -> str:
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)

    priv_key = ssh_dir / "id_rsa"
    pub_key = ssh_dir / "id_rsa.pub"

    if not pub_key.exists():
        run_log.info("[INFO] 生成SSH密钥对...")
        try:
            subprocess.run(["ssh-keygen", "-t", "rsa", "-b", "2048", "-f", str(priv_key), "-N", ""],
                           capture_output=True)
            run_log.ok("[OK]   密钥对生成完成")
        except Exception as e:
            run_log.fail(f"密钥对生成失败: {e}")
            raise

    return str(pub_key)


def _is_already_configured(ip: str, user: str, timeout: int = 5) -> bool:
    client = paramiko.SSHClient()
    try:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=ip, port=22, username=user, timeout=timeout,
                       look_for_keys=True, allow_agent=False, password=None)
        return True
    except paramiko.AuthenticationException:
        return False
    except (paramiko.SSHException, OSError, socket.timeout):
        return False
    finally:
        client.close()


def _ssh_copy_id(cred: NodeCred, pub_key_path: str, node_text: str = "",
                 run_log: RunLog = None) -> tuple[bool, str]:
    if not paramiko:
        return False, "paramiko未安装"

    if node_text and ";" in node_text and len(nodes := node_text.split(";")) >= 3:
        ip_part = nodes[0].strip()
        pwd_part = nodes[2].strip()
        is_single_ip = "," not in ip_part
        pwd_has_dollar = "$" in pwd_part and pwd_part.count("$") > 0
        cred_pwd_no_dollar = "$" not in cred.password
        if is_single_ip and pwd_has_dollar and cred_pwd_no_dollar:
            return False, "密码可能被shell解释，请使用单引号包裹参数"

    pub_key_content = Path(pub_key_path).read_text(encoding="utf-8").strip()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = None

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((cred.host, 22))

        client.connect(
            hostname=cred.host,
            port=22,
            username=cred.user,
            password=cred.password,
            timeout=10,
            auth_timeout=10,
            look_for_keys=False,
            allow_agent=False,
            sock=sock,
        )
    except paramiko.AuthenticationException:
        return False, "密码错误"
    except paramiko.SSHException as e:
        err_lower = str(e).lower()
        if "authentication" in err_lower or "auth" in err_lower:
            return False, "密码错误"
        if "refused" in err_lower:
            return False, "连接被拒绝(SSH服务未启动)"
        if "timeout" in err_lower:
            return False, "连接超时"
        return False, f"SSH错误: {e}"
    except (OSError, socket.timeout) as e:
        err_lower = str(e).lower()
        if "timeout" in err_lower or "timed out" in err_lower:
            return False, "连接超时"
        if "no route" in err_lower:
            return False, "网络不可达"
        if "refused" in err_lower:
            return False, "连接被拒绝(SSH服务未启动)"
        return False, f"网络错误: {e}"
    except Exception as e:
        return False, f"异常: {e}"

    try:
        cmd = (
            f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"grep -qF '{pub_key_content}' ~/.ssh/authorized_keys 2>/dev/null || "
            f"echo '{pub_key_content}' >> ~/.ssh/authorized_keys && "
            f"chmod 600 ~/.ssh/authorized_keys"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=10)
        exit_code = stdout.channel.recv_exit_status()

        if exit_code == 0:
            return True, ""
        else:
            err_msg = stderr.read().decode("utf-8", errors="replace").strip()
            return False, f"写入公钥失败: {err_msg}"
    except paramiko.SSHException as e:
        return False, f"执行命令失败: {e}"
    finally:
        if sock:
            sock.close()
        client.close()


def _process_ip(cred: NodeCred, idx: int, total: int, pub_key_path: str,
                node_text: str, run_log: RunLog) -> tuple[bool, bool, str]:
    status_ok = f"  [{idx}/{total}] {cred.host:<15} ({cred.user}) "
    if _is_already_configured(cred.host, cred.user):
        _print_log(run_log, "PASS", f"{status_ok}✓ 已配置")
        return True, False, ""

    success, err_msg = _ssh_copy_id(cred, pub_key_path, node_text, run_log)
    if success:
        _print_log(run_log, "PASS", f"{status_ok}✓ 配置成功")
        return True, True, ""
    else:
        detail = f" [{err_msg}]" if err_msg else ""
        _print_log(run_log, "FAIL", f"{status_ok}✗ 配置失败{detail}")
        return False, True, err_msg


def cluster_nopass(args: Any) -> None:
    run_log = RunLog(args, "cluster_nopass")

    run_log.info("=" * 50)
    run_log.info("集群SSH免密配置开始")
    run_log.info("=" * 50)

    if not _check_paramiko_installed(run_log):
        return

    node_text = str(getattr(args, "node", "") or "").strip()

    try:
        nodes = _resolve_targets(args)
    except ValueError as e:
        run_log.fail(f"参数解析错误: {e}")
        return

    run_log.info(f"解析到 {len(nodes)} 个目标节点")

    pub_key_path = _ensure_ssh_key(run_log)

    nodes_need_pwd = [n for n in nodes if not n.password]
    if nodes_need_pwd:
        if len(nodes_need_pwd) == len(nodes):
            password = getpass.getpass("请输入所有服务器的统一密码: ")
            print()
            unified_password = password
        else:
            unified_password = getpass.getpass("部分节点缺少密码，请输入统一密码（已有密码的节点将使用其自身密码）: ")
            print()
        for node in nodes:
            if not node.password:
                node.password = unified_password

    total_nodes = len(nodes)
    max_workers = min(getattr(args, "parallel", 8), total_nodes)
    skip_count = 0
    success_count = 0
    fail_count = 0
    results_lock = Lock()

    def process_node(idx: int, node: NodeCred):
        nonlocal skip_count, success_count, fail_count
        skipped, processed, _ = _process_ip(node, idx, total_nodes, pub_key_path, node_text, run_log)
        with results_lock:
            if skipped and not processed:
                skip_count += 1
            elif skipped and processed:
                success_count += 1
            else:
                fail_count += 1

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_node, idx, node) for idx, node in enumerate(nodes, 1)]
        for future in as_completed(futures):
            future.result()

    run_log.info("")
    run_log.info(f"总计: {len(nodes)}  跳过: {skip_count}  成功: {success_count}  失败: {fail_count}")
    run_log.ok("[DONE] 批量SSH免密配置完成")
