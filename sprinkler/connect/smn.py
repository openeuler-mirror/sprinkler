"""SMN serial port run command: SSH to PodManager, loginUBM/loginBMC to device serial port, execute commands in parallel."""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sprinkler.base.log import RunLog
from sprinkler.base.results import CommandResultView, ResultFormatter
from sprinkler.base.ranges import parse_range
from sprinkler.connect.base import _strip_ansi

CHECK_ID = "smn_run_cmd"

# Slot mapping per command type: (login_function, base_slots, count)
# cpu: index 0-3 -> slot 14-17; index 4-7 -> slot 28-31
# npu: index 0-3 -> slot 18-21; index 4-7 -> slot 23-26
# sfu: index 0-3 -> slot 61-64
CMD_TYPE_MAP = {
    "cpu_1213_smn":  "loginUBM",
    "npu_1213_smn":  "loginUBM",
    "sfu_com_smn":   "loginUBM",
    "cpu_bmc_smn":   "loginBMC",
    "npu_bmc_smn":   "loginBMC",
    "sfu_bmc_smn":   "loginBMC",
}

CMD_TYPE_MAX_INDEX = {
    "cpu_1213_smn":  7,
    "npu_1213_smn":  7,
    "sfu_com_smn":   3,
    "cpu_bmc_smn":   7,
    "npu_bmc_smn":   7,
    "sfu_bmc_smn":   3,
}

DEFAULT_CMDS = {
    "loginUBM": "dis int b | inc down",
    "loginBMC": "df -h",
}

def clean_terminal_text(text):
    return _strip_ansi(str(text or "")).replace("\r\n", "\n").replace("\r", "\n")


def get_slot(cmd_type: str, index: int) -> int:
    """Calculate the actual slot number based on command type and device index."""
    if cmd_type in ("cpu_1213_smn", "cpu_bmc_smn"):
        if 0 <= index <= 3:
            return 14 + index
        else:
            return 28 + (index - 4)
    elif cmd_type in ("npu_1213_smn", "npu_bmc_smn"):
        if 0 <= index <= 3:
            return 18 + index
        else:
            return 23 + (index - 4)
    elif cmd_type in ("sfu_com_smn", "sfu_bmc_smn"):
        return 61 + index
    raise ValueError(f"unknown cmd_type: {cmd_type}")


def detect_cmd_type(args):
    """Detect which SMN command was triggered. Returns the cmd_type string."""
    for key in CMD_TYPE_MAP:
        if getattr(args, key, False):
            return key
    return ""


def validate_args(args):
    """Validate arguments for SMN run command. Returns error string or empty string."""
    cmd_type = detect_cmd_type(args)
    if not cmd_type:
        return "no SMN serial port command specified (use -cpu-1213-smn, -npu-1213-smn, etc.)"

    ip_addr = getattr(args, "ip", None)
    if not ip_addr:
        return "-ip is required for SMN serial port commands"

    user = getattr(args, "user", None)
    if not user:
        return "-user is required for SMN serial port commands"

    index_text = getattr(args, "index", None)
    if index_text is not None:
        max_idx = CMD_TYPE_MAX_INDEX[cmd_type]
        try:
            indexes = parse_index(index_text, max_idx)
        except ValueError as exc:
            return str(exc)
        if any(idx < 0 or idx > max_idx for idx in indexes):
            return f"-index values must be 0-{max_idx} for {cmd_type}"

    return ""


def parse_index(value, max_count):
    """Parse -index argument.
    Supports: '0-7' (range), '0,3,7' (list), None/empty (all).
    Returns list of ints.
    """
    if value is None or str(value).strip() == "":
        return list(range(max_count + 1))

    indexes = parse_range(str(value))
    if not indexes:
        raise ValueError(f"-index contains no valid values: {value}")
    return indexes


def make_target(cmd_type, index, host, user, password):
    """Create a ConnectionTarget for a single device."""
    device_type = "cpu" if "cpu" in cmd_type else ("npu" if "npu" in cmd_type else "sfu")
    if "1213" in cmd_type or "com" in cmd_type:
        suffix = "1213" if "1213" in cmd_type else "COM"
        target_name = f"{device_type.upper()}{index}-{suffix}"
    else:
        target_name = f"{device_type.upper()}{index}-BMC"
    return ConnectionTarget(
        name=target_name,
        host=host,
        port=22,
        user=user,
        password=password,
        meta={"kind": cmd_type, "node": index, "source": "cli"},
    )


class SmnRunResultView(CommandResultView):
    """Result view controlling output display based on -show-output."""

    def summary(self, result: dict, level: str):
        ok = self.formatter.result_ok(result)
        target = result.get("target", "")
        cmd = result.get("cmd", "")
        parts = [f"target={target}"]
        if ok:
            parts.append(f"command executed successfully, cmd={cmd}")
        else:
            error_type = result.get("error_type", "unknown")
            stderr = result.get("stderr", "")
            if stderr:
                parts.append(f"FAILED - {stderr}")
            else:
                parts.append(f"FAILED - {error_type}")
        parts.append(f"elapsed={self.formatter.elapsed_text(result.get('elapsed'))}s")
        return " ".join(parts)

    def output_blocks(self, result: dict, ok: bool, show_output: bool):
        target = self.target(result)
        blocks = []
        if result.get("stdout"):
            blocks.append(("INFO", f"[{target}] ", result["stdout"], show_output))
        if result.get("stderr"):
            blocks.append(("INFO", f"[{target}][stderr] ", result["stderr"], show_output))
        if not ok and not show_output:
            excerpt = self.formatter.first_lines(result.get("stderr") or result.get("stdout") or "")
            if excerpt:
                blocks.append(("INFO", f"[{target}][detail] ", excerpt, True))
        return blocks


class SmnRunSession:
    """SSH interactive session for a single device serial port operation."""

    def __init__(self, cmd_type, index, command, args):
        self.cmd_type = cmd_type
        self.index = index
        self.command = command
        self.args = args
        self.connect_timeout = int(getattr(args, "connect_timeout", 10) or 10)
        self.command_timeout = int(getattr(args, "timeout", 120) or 120)
        self.client = None
        self.channel = None

    def connect(self, host, port, user, password):
        """SSH connect to PodManager with interactive shell."""
        import paramiko

        last_error = None
        passwords = [password] if password else []
        if password:
            from sprinkler.utils.password_store import get_default_passwords
            for pwd in get_default_passwords("bmc"):
                if pwd not in passwords:
                    passwords.append(pwd)

        for pwd in passwords or [""]:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    hostname=host, port=port, username=user,
                    password=pwd, timeout=self.connect_timeout,
                    banner_timeout=self.connect_timeout,
                    auth_timeout=self.connect_timeout,
                    allow_agent=False, look_for_keys=False,
                )
                self.client = client
                self.channel = client.invoke_shell(width=200, height=80)
                self.channel.settimeout(0.2)
                self.read_until_idle(2.0)
                return
            except Exception as exc:
                last_error = exc
                client.close()
        raise RuntimeError(last_error or "SSH login failed")

    UBM_PROMPT_RE = re.compile(r"<[A-Za-z0-9_-]+>\s*$")
    BMC_PROMPT_RE = re.compile(r"~\s*[#$]\s*$")
    GENERIC_PROMPT_RE = re.compile(r"[#$>]\s*$")
    PASSWORD_PROMPT_RE = re.compile(r"(?:p|P)assword:")

    def _is_login_ok(self, text, is_bmc):
        """Check if login succeeded by matching the target prompt pattern.

        For loginUBM: prompt is like <A07-CPU1>, <A08-CPU2>, <A07-NPU1>, <C02-A07-SFU1>
        For loginBMC: prompt is like ~ ~ $  or  ~ #
        """
        cleaned = clean_terminal_text(text).strip()
        if not cleaned:
            return False
        last_line = cleaned.splitlines()[-1].strip()
        if is_bmc:
            return bool(self.BMC_PROMPT_RE.search(last_line))
        else:
            return bool(self.UBM_PROMPT_RE.search(last_line))

    def _is_login_failed(self, text):
        """Check if login prompts for a password, which means failure."""
        return bool(self.PASSWORD_PROMPT_RE.search(str(text or "")))

    def login_target(self, slot):
        """Execute loginUBM -slot <slot> or loginBMC -slot <slot>.
        Returns (ok, output).

        Login success:
          - loginUBM: prompt changes to <设备名>, e.g. <A07-CPU1>
          - loginBMC: prompt changes to ~ ~ $  or  ~ #
        Login failure: prompts for Password:, or timeout.
        """
        login_func = CMD_TYPE_MAP.get(self.cmd_type, "loginUBM")
        is_bmc = login_func == "loginBMC"
        cmd = f"{login_func} -slot {slot}"
        self.sendline(cmd)

        start = time.time()
        buffer = ""
        while time.time() - start < self.command_timeout:
            chunk = self.recv_ready()
            if chunk:
                buffer += chunk
                # Check if prompted for password -> login failed
                if self._is_login_failed(buffer):
                    return False, f"{login_func} -slot {slot} requires password, skipped"
                # Check for target prompt -> login succeeded
                if self._is_login_ok(buffer, is_bmc):
                    # Flush any remaining banner output
                    time.sleep(0.3)
                    self.recv_ready()
                    return True, ""
            time.sleep(0.1)
        buffer += self.recv_ready()
        if self._is_login_ok(buffer, is_bmc):
            return True, ""
        return False, f"{login_func} -slot {slot} timed out (no target prompt received)"

    def _has_output_prompt(self, text):
        """Check if text ends with any known prompt pattern after command execution."""
        cleaned = clean_terminal_text(text).strip()
        if not cleaned:
            return False
        last_line = cleaned.splitlines()[-1].strip()
        return bool(
            self.UBM_PROMPT_RE.search(last_line) or
            self.BMC_PROMPT_RE.search(last_line) or
            self.GENERIC_PROMPT_RE.search(last_line)
        )

    def exec_command(self, cmd):
        """Execute a command on the logged-in 1213/BMC device.
        Returns (ok, output).
        """
        self.sendline(cmd)

        start = time.time()
        buffer = ""
        while time.time() - start < self.command_timeout:
            chunk = self.recv_ready()
            if chunk:
                buffer += chunk
                if self._has_output_prompt(buffer):
                    break
            time.sleep(0.1)
        buffer += self.recv_ready()

        output = clean_terminal_text(buffer)

        # Strip command echo and trailing prompt from output
        lines = output.splitlines()
        cleaned_lines = []
        echo_skipped = False
        for line in lines:
            if not echo_skipped and cmd.strip() and cmd.strip() in line.strip():
                echo_skipped = True
                continue
            cleaned_lines.append(line)

        # Remove trailing prompt line (e.g. <A07-CPU1>, ~ ~ $)
        if cleaned_lines:
            last = cleaned_lines[-1].strip()
            if (self.UBM_PROMPT_RE.search(last) or
                self.BMC_PROMPT_RE.search(last) or
                self.GENERIC_PROMPT_RE.search(last)):
                cleaned_lines.pop()

        result_output = "\n".join(cleaned_lines).strip()
        if not result_output:
            return True, "(no output)"

        return True, result_output

    def close(self):
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        if self.client is not None:
            self.client.close()
            self.client = None

    def sendline(self, text):
        self.send(str(text or "") + "\n")

    def send(self, text):
        if self.channel is None:
            raise RuntimeError("SSH channel is not connected")
        self.channel.send(text)

    def recv_ready(self):
        if self.channel is None:
            return ""
        chunks = []
        while self.channel.recv_ready():
            chunks.append(self.channel.recv(4096).decode("utf-8", errors="replace"))
        return "".join(chunks)

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


class SmnRunRunner:
    """Parallel executor for SMN serial port commands."""

    def __init__(self, args, run_log):
        self.args = args
        self.run_log = run_log
        self.processor = ResultProcessor()

    def run(self):
        cmd_type = detect_cmd_type(self.args)
        max_idx = CMD_TYPE_MAX_INDEX[cmd_type]
        indexes = parse_index(getattr(self.args, "index", None), max_idx)

        login_func = CMD_TYPE_MAP[cmd_type]
        cmd_list = getattr(self.args, "cmd", None)
        command = cmd_list[0] if cmd_list else DEFAULT_CMDS[login_func]

        host = getattr(self.args, "ip", "")
        user = getattr(self.args, "user", "root")
        password = getattr(self.args, "pwd", "")

        self.run_log.info(f"cmd_type={cmd_type} indexes={indexes} host={host} cmd={command} workers={len(indexes)}")

        results = []
        with ThreadPoolExecutor(max_workers=len(indexes)) as pool:
            future_map = {
                pool.submit(self.run_single, cmd_type, idx, command, host, user, password): idx
                for idx in indexes
            }
            for future in as_completed(future_map):
                try:
                    results.append(future.result())
                except Exception as exc:
                    idx = future_map[future]
                    target = make_target(cmd_type, idx, host, user, password)
                    results.append(self.processor.make(
                        target, command, False, 255, "exception", "", str(exc),
                        time.time(), CHECK_ID
                    ))

        results.sort(key=lambda r: r.get("target", ""))
        self.run_log.info("")
        self.run_log.info("<Here are the execution results of the command:>")
        return results

    def run_single(self, cmd_type, index, command, host, user, password):
        """Run full flow for a single device: SSH -> loginUBM/BMC -> exec command."""
        target = make_target(cmd_type, index, host, user, password)
        start = time.time()
        slot = get_slot(cmd_type, index)

        session = SmnRunSession(cmd_type, index, command, self.args)
        try:
            session.connect(host, 22, user, password)
        except Exception as exc:
            return self.processor.make(target, command, False, 255, "connect", "", str(exc), time.time() - start, CHECK_ID)

        try:
            ok, output = session.login_target(slot)
            if not ok:
                return self.processor.make(target, f"login slot {slot}", False, 255, "login", "", output,
                                           time.time() - start, CHECK_ID)

            ok, output = session.exec_command(command)
            if not ok:
                return self.processor.make(target, command, False, 255, "command", "", output,
                                           time.time() - start, CHECK_ID)

            return self.processor.make(target, command, True, 0, "", output, "",
                                       time.time() - start, CHECK_ID)
        except Exception as exc:
            return self.processor.make(target, command, False, 255, "exception", "", str(exc),
                                       time.time() - start, CHECK_ID)
        finally:
            session.close()


def smn_1213_bmc_execute(args):
    """Entry point for SMN 1213/BMC serial port run command."""
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")

    error = validate_args(args)
    if error:
        return run_log.fail_error(CHECK_ID, ValueError(error))

    try:
        runner = SmnRunRunner(args, run_log)
        results = runner.run()
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    return run_log.finish_results(CHECK_ID, results, SmnRunResultView(run_log.formatter))
