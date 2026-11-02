import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sprinkler.base.log import RunLog
from sprinkler.base.ranges import parse_range
from sprinkler.base.results import CommandResultView
from sprinkler.connect.base import ResultProcessor
from sprinkler.connect.connection_runner import normalize_commands
from sprinkler.connect.smn import SmnRunSession, get_slot, parse_index
from sprinkler.connect.targets import ConnectionTarget, TargetContext
from sprinkler.utils.password_store import get_default_passwords

CHECK_ID = "npu_sol"
DEFAULT_SOL_PORTS = "3,5,7,9,11,13,15,17"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\r")
PROMPT_RE = re.compile(r"(?:#|>|\$)\s*$")

def validate_args(args):
    if getattr(args, "local", None):
        return "-local is not supported for -npu-sol"
    if not getattr(args, "cmd", None) and not getattr(args, "file", None):
        return "-npu-sol requires at least one -cmd or -file"
    try:
        parse_sol_ports(getattr(args, "sol_ports", DEFAULT_SOL_PORTS))
    except ValueError as exc:
        return str(exc)
    return ""

def parse_sol_ports(value):
    ports = parse_range(str(value or DEFAULT_SOL_PORTS))
    if not ports:
        raise ValueError("-sol-ports cannot be empty")
    bad = [port for port in ports if port <= 0 or port > 65535]
    if bad:
        raise ValueError(f"bad -sol-ports value: {bad[0]}")
    return ports

def bmc_ssh_target(target):
    target = ConnectionTarget.from_value(target)
    return ConnectionTarget(name=target.name, host=target.host, port=22, user=target.user,
                            password=target.password, meta=target.meta, error=target.error)

def clean_terminal_text(text):
    return ANSI_RE.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")

def is_bmc_prompt(text):
    lines = [line.strip() for line in clean_terminal_text(text).splitlines() if line.strip()]
    return bool(lines and lines[-1].startswith("~") and lines[-1].endswith("#"))

def has_prompt(text):
    lines = [line.strip() for line in clean_terminal_text(text).splitlines() if line.strip()]
    return bool(lines and PROMPT_RE.search(lines[-1]))

def strip_command_echo(command, output):
    lines = clean_terminal_text(output).splitlines()
    cleaned = []
    echo_skipped = False
    for line in lines:
        if not echo_skipped and command.strip() and command.strip() in line.strip():
            echo_skipped = True
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()

class NpuSolResultView(CommandResultView):
    def summary(self, result: dict, level: str):
        ok = self.formatter.result_ok(result)
        parts = [f"target={result.get('target', '')}", f"host={result.get('host', '')}",
                 f"npu=NPU{result.get('npu_index', '')}", f"sol_port={result.get('sol_port', '')}",
                 f"backend={result.get('backend', '')}", f"rc={result.get('rc', '')}"]
        if not ok and result.get("error_type"):
            parts.append(f"error_type={result.get('error_type')}")
        parts.append(f"elapsed={self.formatter.elapsed_text(result.get('elapsed'))}s")
        parts.append(f"cmd={result.get('cmd', '')}")
        return " ".join(parts)

class NpuSolRunner:
    session_class = None

    def __init__(self, args, run_log):
        self.args = args
        self.run_log = run_log
        self.processor = ResultProcessor()

    def run(self):
        commands = normalize_commands(self.args)
        ports = parse_sol_ports(getattr(self.args, "sol_ports", DEFAULT_SOL_PORTS))
        targets = [bmc_ssh_target(target) for target in TargetContext(self.args).get_targets("npu_bmc")]
        if not targets:
            raise ValueError("no NPU BMC targets selected")

        results = []
        for target in targets:
            target_results = self.run_target(target, ports, commands)
            self.run_log.write_target_results(target.label(), target_results)
            results.extend(target_results)
        return results

    def run_target(self, target, ports, commands):
        session_class = self.session_class or NpuSolSession
        session = session_class(target, self.args, self.processor)
        try:
            return session.run(ports, commands)
        finally:
            session.close()

class NpuSolSession:
    def __init__(self, target, args, processor):
        self.target = target
        self.args = args
        self.processor = processor
        self.client = None
        self.channel = None
        self.connect_timeout = int(getattr(args, "connect_timeout", 10) or 10)
        self.command_timeout = int(getattr(args, "timeout", 60) or 60)

    def run(self, ports, commands):
        start = time.time()
        target_error = self.target.validate()
        if target_error:
            return [self.result(index, port, cmd, False, "", target_error, start, "target")
                    for index, port in enumerate(ports) for cmd in commands]
        try:
            self.connect()
        except Exception as exc:
            return [self.result(index, port, cmd, False, "", str(exc), start, self.connect_error_type(exc))
                    for index, port in enumerate(ports) for cmd in commands]

        results = []
        for index, port in enumerate(ports):
            ok, output = self.enter_sol(port)
            if not ok:
                results.extend(self.result(index, port, cmd, False, "", output, time.time(), "sol") for cmd in commands)
                self.exit_sol()
                continue
            for cmd in commands:
                results.append(self.run_command(index, port, cmd))
            self.exit_sol()
        return results

    def connect(self):
        import paramiko

        last_error = None
        passwords = [self.target.password] if self.target.password else []
        passwords.extend(password for password in get_default_passwords("bmc") if password not in passwords)
        for password in passwords or [""]:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(hostname=self.target.host, port=self.target.port, username=self.target.user,
                               password=password, timeout=self.connect_timeout, banner_timeout=self.connect_timeout,
                               auth_timeout=self.connect_timeout, allow_agent=False, look_for_keys=False)
                self.client = client
                self.channel = client.invoke_shell(width=200, height=80)
                self.channel.settimeout(0.2)
                self.read_until_idle(2.0)
                return
            except Exception as exc:
                last_error = exc
                client.close()
        raise RuntimeError(last_error or "SSH login failed")

    def connect_error_type(self, exc):
        text = str(exc or "").casefold()
        if "auth" in text or "password" in text or "permission denied" in text:
            return "auth"
        if "timed out" in text or "timeout" in text:
            return "timeout"
        if "refused" in text or "route" in text or "unreachable" in text:
            return "network"
        return "connect"

    def enter_sol(self, port):
        self.sendline("ipmcset -t sol -d deactivate -v 1")
        self.read_until_idle(2.0)
        self.sendline("ipmcset -t sol -d deactivate -v 2")
        self.read_until_idle(2.0)
        self.sendline("ipmcset -d ser -v 0")
        self.read_until_idle(1.0)
        self.sendline("source /etc/profile")
        self.read_until_idle(1.0)
        self.sendline("cd /data")
        self.read_until_idle(1.0)
        self.sendline(f"ipmcset -t sol -d act -v {port} 0")
        output = self.read_until_idle(min(self.command_timeout, 10))
        lowered = output.lower()
        if "maximum number of sol sessions" in lowered or "sol connection closed" in lowered:
            return False, output.strip() or "SOL activate failed"
        self.sendline("")
        prompt_output, matched = self.read_until_prompt(self.command_timeout)
        output += prompt_output
        if not matched or is_bmc_prompt(prompt_output):
            return False, output.strip() or "target shell prompt not found"
        return True, output

    def run_command(self, index, port, command):
        start = time.time()
        self.sendline(command)
        output, matched = self.read_until_prompt(self.command_timeout)
        if not matched:
            return self.result(index, port, command, False, "", output or "command timed out", start, "timeout")
        if is_bmc_prompt(output):
            return self.result(index, port, command, False, "", output, start, "sol")
        return self.result(index, port, command, True, strip_command_echo(command, output), "", start, "")

    def exit_sol(self):
        if self.channel is None:
            return
        self.send("\x1b(")
        self.read_until_idle(2.0)

    def result(self, index, port, command, ok, stdout, stderr, start, error_type):
        sol_target = ConnectionTarget(name=f"{self.target.label()}:NPU{index}", host=self.target.host,
                                      port=self.target.port, user=self.target.user, password=self.target.password,
                                      meta=self.target.meta)
        result = self.processor.make(sol_target, command, ok, 0 if ok else 255, error_type, stdout, stderr,
                                     time.time() - start, CHECK_ID)
        result["npu_index"] = index
        result["sol_port"] = port
        return result

    def sendline(self, text):
        self.send(str(text or "") + "\n")

    def send(self, text):
        if self.channel is None:
            raise RuntimeError("SSH channel is not connected")
        self.channel.send(text)

    def read_until_prompt(self, timeout):
        start = time.time()
        buffer = ""
        while time.time() - start < timeout:
            buffer += self.recv_ready()
            if has_prompt(buffer):
                return buffer, True
            time.sleep(0.1)
        buffer += self.recv_ready()
        return buffer, False

    def read_until_idle(self, timeout, idle=0.4):
        start = time.time()
        last_data = start
        buffer = ""
        while time.time() - start < timeout:
            chunk = self.recv_ready()
            if chunk:
                buffer += chunk
                last_data = time.time()
            elif buffer and time.time() - last_data >= idle:
                break
            time.sleep(0.1)
        return buffer

    def recv_ready(self):
        if self.channel is None:
            return ""
        chunks = []
        while self.channel.recv_ready():
            chunks.append(self.channel.recv(4096).decode("utf-8", errors="replace"))
        return "".join(chunks)

    def close(self):
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        if self.client is not None:
            self.client.close()
            self.client = None

def npu_sol_execute(args):
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    try:
        results = NpuSolRunner(args, run_log).run()
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)
    return run_log.finish_results(CHECK_ID, results, NpuSolResultView(run_log.formatter))


# ======================================================================
# -npu-sol-smn / -npu-bash-smn: SMN 方式登录 NPU BMC 后进入 SOL 执行命令
# ======================================================================
# 复用本文件头部的工具函数: clean_terminal_text, is_bmc_prompt, has_prompt,
# strip_command_echo, parse_sol_ports, ANSI_RE, PROMPT_RE, DEFAULT_SOL_PORTS
# 复用 smn.py 中的 SmnRunSession, get_slot, parse_index

SMN_CHECK_ID = "npu_sol_smn"


class NpuSolSmnSession(SmnRunSession):
    """SMN 方式登录 NPU BMC 后，进入 SOL 串口执行命令。

    继承 SmnRunSession 以复用 SSH 连接 PodManager 和 loginBMC 的能力。
    """

    def __init__(self, cmd_type, npu_index, command, args, processor=None):
        super().__init__(cmd_type, npu_index, command, args)
        self.processor = processor or ResultProcessor()

    def enter_sol(self, port):
        """激活 SOL 串口，返回 (成功, 输出信息)。"""
        self.sendline("ipmcset -t sol -d deactivate -v 1")
        self.read_until_idle(2.0)
        self.sendline("ipmcset -t sol -d deactivate -v 2")
        self.read_until_idle(2.0)
        self.sendline("ipmcset -d ser -v 0")
        self.read_until_idle(1.0)
        self.sendline("source /etc/profile")
        self.read_until_idle(1.0)
        self.sendline("cd /data")
        self.read_until_idle(1.0)
        self.sendline(f"ipmcset -t sol -d act -v {port} 0")
        output = self.read_until_idle(min(self.command_timeout, 10))
        lowered = output.lower()
        if "maximum number of sol sessions" in lowered or "sol connection closed" in lowered:
            return False, output.strip() or "SOL activate failed"
        self.sendline("")
        prompt_output, matched = self._sol_read_until_prompt(self.command_timeout)
        output += prompt_output
        if not matched or is_bmc_prompt(prompt_output):
            return False, output.strip() or "target shell prompt not found"
        return True, output

    def exit_sol(self):
        """退出 SOL 串口。"""
        self.send("\x1b(")
        self.read_until_idle(2.0)

    def _sol_read_until_prompt(self, timeout):
        """读取 channel 直到出现目标提示符。"""
        start = time.time()
        buffer = ""
        while time.time() - start < timeout:
            buffer += self.recv_ready()
            if has_prompt(buffer):
                return buffer, True
            time.sleep(0.1)
        buffer += self.recv_ready()
        return buffer, False

    def run_command(self, port, command):
        """在 SOL 串口中执行单条命令。"""
        start = time.time()
        self.sendline(command)
        output, matched = self._sol_read_until_prompt(self.command_timeout)
        if not matched:
            return self._sol_make_result(port, command, False, "",
                                         output or "command timed out", start, "timeout")
        if is_bmc_prompt(output):
            return self._sol_make_result(port, command, False, "",
                                         output, start, "sol")
        return self._sol_make_result(port, command, True,
                                     strip_command_echo(command, output), "", start, "")

    def _sol_make_result(self, port, command, ok, stdout, stderr, start, error_type):
        """构造结果字典。"""
        npu_idx = self.index
        target = ConnectionTarget(
            name=f"NPU{npu_idx}:SMN", host=self.args.ip, port=22,
            user=self.args.user, password=self.args.pwd,
            meta={"kind": "npu_bmc_smn", "node": npu_idx, "source": "cli"},
        )
        result = self.processor.make(target, command, ok, 0 if ok else 255,
                                     error_type, stdout, stderr,
                                     time.time() - start, SMN_CHECK_ID)
        result["npu_index"] = npu_idx
        result["sol_port"] = port
        return result

    def run(self, ports, commands):
        """遍历所有 SOL ports，逐个尝试进入并执行命令。（与 NpuSolSession.run 保持一致）"""
        if not ports:
            return [self._sol_make_result(0, cmd, False, "", "no SOL ports specified", time.time(), "sol")
                    for cmd in commands]

        results = []
        for index, port in enumerate(ports):
            ok, output = self.enter_sol(port)
            if not ok:
                results.extend(
                    self._sol_make_result(port, cmd, False, "", output, time.time(), "sol")
                    for cmd in commands
                )
                self.exit_sol()
                continue
            for cmd in commands:
                results.append(self.run_command(port, cmd))
            self.exit_sol()
        return results


class NpuSolSmnRunner:
    """并行调度器：对每个 NPU index 启动一个 SmnSession，通过 SMN 进入 SOL 执行命令。"""

    def __init__(self, args, run_log):
        self.args = args
        self.run_log = run_log
        self.processor = ResultProcessor()

    def run(self):
        indexes = parse_index(getattr(self.args, "index", None), 7)
        ports = parse_sol_ports(getattr(self.args, "sol_ports", DEFAULT_SOL_PORTS))
        commands = normalize_commands(self.args)
        host = getattr(self.args, "ip", "")
        user = getattr(self.args, "user", "root")
        password = getattr(self.args, "pwd", "")

        self.run_log.info(f"indexes={indexes} host={host} sol_ports={ports} "
                          f"commands={commands} workers={len(indexes)}")

        results = []
        with ThreadPoolExecutor(max_workers=len(indexes)) as pool:
            future_map = {
                pool.submit(self.run_single, idx, ports, commands, host, user, password): idx
                for idx in indexes
            }
            for future in as_completed(future_map):
                try:
                    results.extend(future.result())
                except Exception as exc:
                    idx = future_map[future]
                    target = ConnectionTarget(
                        name=f"NPU{idx}:SMN", host=host, port=22,
                        user=user, password=password,
                        meta={"kind": "npu_bmc_smn", "node": idx, "source": "cli"},
                    )
                    results.append(self.processor.make(
                        target, "npu_sol_smn", False, 255, "exception", "", str(exc),
                        0.0, SMN_CHECK_ID,
                    ))

        results.sort(key=lambda r: (r.get("target", ""), r.get("npu_index", 0)))
        self.run_log.info("")
        self.run_log.info("<Here are the execution results of the SOL commands:>")
        return results

    def run_single(self, index, ports, commands, host, user, password):
        """单个 NPU index 的完整执行流程。"""
        slot = get_slot("npu_bmc_smn", index)
        session = NpuSolSmnSession("npu_bmc_smn", index, commands[0] if commands else "", self.args,
                                   processor=self.processor)
        try:
            session.connect(host, 22, user, password)
        except Exception as exc:
            target = ConnectionTarget(
                name=f"NPU{index}:SMN", host=host, port=22,
                user=user, password=password,
                meta={"kind": "npu_bmc_smn", "node": index, "source": "cli"},
            )
            return [self.processor.make(target, "login", False, 255, "connect", "", str(exc), 0.0, SMN_CHECK_ID)]

        try:
            ok, output = session.login_target(slot)
            if not ok:
                target = ConnectionTarget(
                    name=f"NPU{index}:SMN", host=host, port=22,
                    user=user, password=password,
                    meta={"kind": "npu_bmc_smn", "node": index, "source": "cli"},
                )
                result = self.processor.make(target, f"loginBMC slot {slot}", False, 255, "login", "", output, 0.0, SMN_CHECK_ID)
                result["npu_index"] = index
                return [result]

            return session.run(ports, commands)
        except Exception as exc:
            target = ConnectionTarget(
                name=f"NPU{index}:SMN", host=host, port=22,
                user=user, password=password,
                meta={"kind": "npu_bmc_smn", "node": index, "source": "cli"},
            )
            return [self.processor.make(target, "npu_sol_smn", False, 255, "exception", "", str(exc), 0.0, SMN_CHECK_ID)]
        finally:
            session.close()


class NpuSolSmnResultView(CommandResultView):
    """NpuSolSmnRunner 的结果展示。"""

    def summary(self, result: dict, level: str):
        ok = self.formatter.result_ok(result)
        parts = [
            f"target={result.get('target', '')}",
            f"npu=NPU{result.get('npu_index', '')}",
            f"sol_port={result.get('sol_port', '')}",
            f"backend={result.get('backend', '')}",
            f"rc={result.get('rc', '')}",
        ]
        if not ok and result.get("error_type"):
            parts.append(f"error_type={result.get('error_type')}")
        parts.append(f"elapsed={self.formatter.elapsed_text(result.get('elapsed'))}s")
        parts.append(f"cmd={result.get('cmd', '')}")
        return " ".join(parts)


def npu_sol_smn_execute(args):
    """入口函数：-npu-sol-smn 和 -npu-bash-smn 的调度入口。"""
    run_log = RunLog(args, default_check_id=SMN_CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    try:
        results = NpuSolSmnRunner(args, run_log).run()
    except Exception as exc:
        return run_log.fail_error(SMN_CHECK_ID, exc)
    return run_log.finish_results(SMN_CHECK_ID, results, NpuSolSmnResultView(run_log.formatter))
