"""Log collection using the SSH no-pass module and superpod-table credentials.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from sprinkler.connect.ssh.sol.npu import NPU1213Connector
from sprinkler.connect.ssh.sol.cpu import CPU1213Connector
from sprinkler.connect.targets import ConnectionTarget
from sprinkler.arguments import get_args
from sprinkler.base.log import RunLog
from sprinkler.config.cabinet_info import get_cabinet_data

COLLECTION_PATTERN = re.compile(r"^collection_(\d+)$")

CPU_LOG_SOURCES = [
    ("/var/log/messages", "CPU/var/log/messages"),
]

PLOG_DIRS = [
    ("/root/ascend/log", "PLOG/log"),
    ("/root/ascend/atrace", "PLOG/atrace"),
]

MNPUREPORT_SEARCH_ROOTS = ("/usr/local/Ascend", "/root/host")

# UB (管控面 1213) log files transferred from the 1213 backend to the host.
#  - src : remote file/glob on the 1213 backend
#  - pre : optional command run first (e.g. copy panic region to /home)
_UB_TRANSFER_BASE = [
    {"name": "cpdt_dmesg_current", "src": "/opt/vrpv8/var/log/cpdt_dmesg_current*"},
    {"name": "cpdt_journal", "src": "/opt/vrpv8/var/log/cpdt_journal*"},
    {"name": "panic", "src": "/home/panic", "pre": "cp /proc/kbox/regions/panic /home/"},
]

UB_TRANSFER_FILES = {
    "npu": list(_UB_TRANSFER_BASE),
    "cpu": _UB_TRANSFER_BASE + [
        {"name": "vbussw_self", "src": "/opt/vrpv8/home/vbussw_self.log"},
    ],
}

# Local directory on the pod where the 1213 backend scp's the UB logs.
UB_LOCAL_DIRS = {
    "npu": Path("/home/npu1213log"),
    "cpu": Path("/home/cpu1213log"),
}

# Generous timeout for one scp transfer over the SOL serial console.
UB_TRANSFER_TIMEOUT = 300

# ssh/scp options for the NPU log pull: key auth only (no-pass module owns
# the keys + ~/.ssh/config entries), never prompt interactively.
SSH_BASE_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
]

# The 5808 sheet is not an NPU/CPU cabinet: never feed it to get_cabinet_data.
NON_CABINET_SHEETS = {"5808"}
LOG_TYPES = ("cpu_os", "npu_os", "plog", "msnpureport", "npu_bmc", "cpu_bmc")

def _iter_superpod_sheets(sp_num=None):
    """Yield (path, sheet_name, df, ca) for each cabinet sheet of the local
    superpod table(s) at default_config_dir()/superpod_<sp>.xlsx.

    Replacement for the removed superpod_info._iter_superpod_sheets.
    `ca` is the sheet's position in the file, i.e. the cabinet number
    (sheets are ordered by cabinet 0-f with 5808 last), so skipped
    placeholder sheets cannot shift the numbering.  Sheets that fail
    preprocess_column_names (empty 'nullN' placeholders etc.) and the
    5808 sheet are skipped.
    """
    import pandas as pd
    from sprinkler.utils.utils import default_config_dir
    from sprinkler.config.superpod_info import preprocess_column_names

    conf_dir = default_config_dir()
    pattern = f"superpod_{sp_num}.xlsx" if sp_num else "superpod_*.xlsx"
    for path in sorted(conf_dir.glob(pattern)):
        try:
            xls = pd.ExcelFile(path)
        except Exception as exc:
            print(f"[WARN] 读取超节点表格 {path} 失败: {type(exc).__name__}: {exc}")
            continue
        try:
            for ca, sheet_name in enumerate(xls.sheet_names):
                if sheet_name in NON_CABINET_SHEETS:
                    continue
                try:
                    df = pd.read_excel(xls, sheet_name=sheet_name)
                    df = preprocess_column_names(df)
                except Exception:
                    # Empty placeholder sheet (nullN) or unexpected layout.
                    continue
                yield path, sheet_name, df, ca
        finally:
            xls.close()


class _UBSOLMixin:
    """Adds 1213-backend scp transfers on top of a SOL1213Connector.

    The base connector already handles: SSH to the BMC (password fallback via
    _connect_with_passwords), the BMC shell prompt wait, INIT_COMMANDS (SOL
    switch) and the 1213 SOL login (_login_sol, incl. get_default_passwords
    ("1213") fallback). This mixin only adds the log-collection-specific tail:
    sys -> diag -> l s into the backend shell, then scp transfers whose
    host-key / password prompts are answered interactively.

    Configure per instance: `transfers` (list of {name, src, cmd, pre}) and
    `host_passwords` (pod OS password candidates for the scp callback).
    """

    transfers = ()
    host_passwords = ("",)
    transfer_timeout = UB_TRANSFER_TIMEOUT

    async def _run_sol_commands(self, reader, writer, target, commands, on_result=None):
        results = []
        # sys -> diag -> l s: enter the 1213 backend shell ("TOR / #").
        for step in ("sys", "diag", "l s"):
            result = await self._run_telnet_command(reader, writer, target, step)
            results.append(result)
            self._emit(on_result, result)
        for item in self.transfers:
            result = await self._run_transfer(reader, writer, target, item)
            results.append(result)
            self._emit(on_result, result)
        return results

    async def _run_transfer(self, reader, writer, target, item):
        start = time.time()
        pre = item.get("pre")
        if pre:
            await self._run_telnet_command(reader, writer, target, pre)

        writer.write(item["cmd"] + "\r\n")
        await writer.drain()

        output = ""
        pwd_index = 0
        deadline = time.time() + self.transfer_timeout
        while time.time() < deadline:
            chunk = await self._read_until(
                reader,
                ["continue connecting", "password:", "tor / #"],
                timeout=5,
            )
            output += chunk
            lowered = chunk.lower()
            if "continue connecting" in lowered:
                writer.write("yes\r\n")
                await writer.drain()
                continue
            if "password:" in lowered:
                # Wrong password -> scp re-prompts -> try the next candidate.
                pwd = self.host_passwords[min(pwd_index, len(self.host_passwords) - 1)]
                pwd_index += 1
                writer.write(pwd + "\r\n")
                await writer.drain()
                continue
            if "tor / #" in lowered:
                return self.result_processor.make(
                    target, item["cmd"], True, 0, "", output, "",
                    time.time() - start, self.backend_name())
        return self.result_processor.make(
            target, item["cmd"], False, 255, "timeout", output,
            f"transfer timed out after {self.transfer_timeout}s",
            time.time() - start, self.backend_name())

    @staticmethod
    def _emit(on_result, result):
        if on_result:
            try:
                on_result(result)
            except Exception:
                pass


class _NPUBConnector(_UBSOLMixin, NPU1213Connector):
    """NPU BMC SOL session with UB log transfers (npu_1213 init sequence)."""


class _CPUBConnector(_UBSOLMixin, CPU1213Connector):
    """CPU BMC SOL session with UB log transfers (cpu_1213 init sequence)."""


UB_CONNECTORS = {"npu": _NPUBConnector, "cpu": _CPUBConnector}


class CollectResult:
    def __init__(self, collection_dir: Path, collection_id: str):
        self.collection_dir = collection_dir
        self.collection_id = collection_id
        self.files = []
        self.success = 0
        self.failed = 0
        self.errors = []

    def to_dict(self):
        return {
            "collection_id": self.collection_id,
            "collection_dir": str(self.collection_dir),
            "files": [str(f) for f in self.files],
            "success": self.success,
            "failed": self.failed,
            "errors": self.errors,
        }


class LogCollector:
    def __init__(self, args=None, collection_root=None, sp_num=None,
                 interactive=True, run_log=None, setup_nopass=True):
        self.args = args or get_args() or object()
        self.interactive = interactive
        self.setup_nopass = setup_nopass
        self.sp_num = sp_num or getattr(self.args, "sp", None) or None
        self.collection_root = Path(
            collection_root or getattr(self.args, "collect_root", None) or "./debug_log"
        )
        self.run_log = run_log or RunLog(self.args, default_check_id="log_collect")
        self.check_id = "log_collect"
        self.log_types = self._parse_log_types(getattr(self.args, "log_type", None))
        self._cabinets = None      # cached cabinet data (superpod tables)
        self._nopass_done = False  # no-pass setup runs at most once per collect()

    # ------------------------------------------------------------------ public
    def collect(self) -> CollectResult:
        """Run one log collection pass. Returns the collection result summary."""
        collection_dir, collection_id = self._new_collection_dir()
        result = CollectResult(collection_dir, collection_id)
        print("=" * 60)
        print(f"Log collection start, saving to: {collection_dir}")
        print(f"log-type: {','.join(sorted(self.log_types, key=LOG_TYPES.index))}")
        print("=" * 60)

        # host_device_pairs is needed by npu_os (ssh targets) and by npu_bmc
        # (BMC count trimming); skip it for BMC/plog-only runs.
        npu_pairs = []
        if self._want("npu_os") or self._want("npu_bmc"):
            npu_pairs = self._npu_pairs()

        # Same order as pod_collect_logs.sh: CPU -> no-pass -> NPU -> PLOG
        # -> msnpureport -> UB, each gated by -log-type.
        if self._want("cpu_os"):
            self._collect_local_cpu(result)

        if self._want("npu_os"):
            if not npu_pairs:
                result.errors.append("未获取到 NPU IP 列表（host_device_pairs 为空），跳过 NPU 日志收集")
            else:
                # Shell parity: run no_password.sh (no_pass.no_pass_check) ONCE
                # before touching any NPU.  Never re-run it mid-collection: its
                # run() deletes ~/.ssh/config and regenerates the keypair first.
                self._ensure_nopass()
            for dev_id, host in npu_pairs:
                self._collect_npu_host(result, dev_id, host)

        if self._want("plog"):
            self._collect_local_plog(result)
        if self._want("msnpureport"):
            self._collect_msnpureport(result)

        # Superpod-table parsing (with its RoCE sheet warnings) only happens
        # below, so non-BMC runs never touch the tables at all.
        if self._want("npu_bmc"):
            npu_ub = self._npu_ub_targets([host for _, host in npu_pairs])
            if not npu_ub:
                self._record(result, result.collection_dir / "UB" / "npu1213log", False,
                             "UB-npu: no NPU BMC info in superpod table")
            for bmc in npu_ub:
                self._collect_ub(result, bmc, "npu")
        if self._want("cpu_bmc"):
            cpu_bmc = self._cpu_ub_target()
            if cpu_bmc:
                self._collect_ub(result, cpu_bmc, "cpu")
            else:
                self._record(result, result.collection_dir / "UB" / "cpu1213log", False,
                             "UB-cpu: no CPU BMC info in superpod table")

        self._write_manifest(result)
        self._archive(result)
        self._print_summary(result)
        return result

    # ------------------------------------------------------- collection actions
    def _collect_local_cpu(self, result: CollectResult):
        dest_dir = result.collection_dir / "CPU" / "var" / "log"
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            with (dest_dir / "dmesg").open("w", encoding="utf-8", errors="replace") as fp:
                proc = subprocess.run(["dmesg", "-T"], check=False,
                                      stdout=fp, stderr=subprocess.STDOUT, timeout=60)
                self._record(result, dest_dir / "dmesg", proc.returncode == 0,
                             "local dmesg collect")
        except Exception as exc:
            self._record(result, dest_dir / "dmesg", False, f"local dmesg: {exc}")

        for src, rel in CPU_LOG_SOURCES:
            path = Path(src)
            if path.exists():
                self._copy_file(result, path, result.collection_dir / rel)
            else:
                self._record(result, result.collection_dir / rel, False, f"missing {src}")

    def _collect_local_plog(self, result: CollectResult):
        for src, rel in PLOG_DIRS:
            path = Path(src)
            if path.exists() and path.is_dir():
                self._copy_tree(result, path, result.collection_dir / rel)
            else:
                self._record(result, result.collection_dir / rel, False, f"missing dir {src}")

    @staticmethod
    def _parse_log_types(raw) -> frozenset:
        """Parse -log-type: comma-separated subset of LOG_TYPES; "all"/empty = all."""
        if raw is None:
            return frozenset(LOG_TYPES)
        tokens = [t.strip().lower() for t in str(raw).split(",") if t.strip()]
        if not tokens or "all" in tokens:
            return frozenset(LOG_TYPES)
        invalid = [t for t in tokens if t not in LOG_TYPES]
        if invalid:
            raise ValueError(
                f"无效的 -log-type: {','.join(invalid)}，可选: "
                f"{','.join(LOG_TYPES)} 或 all（可多选，逗号分隔）")
        return frozenset(tokens)

    def _want(self, log_type: str) -> bool:
        return log_type in self.log_types

    # --------------------------------------------------------------- no-pass
    def _ensure_nopass(self):
        """Configure SSH key auth for every NPU pair (no_password.sh parity).

        Delegates to the no-pass module (no_pass.no_pass_check): it generates
        ~/.ssh/id_ed25519_ipv6, writes per-pair ~/.ssh/config entries with
        Host aliases npu<dev_id>, and pushes the public key to each NPU.
        First run may prompt for POD_NPU_USER / POD_NPU_PASSWORD exactly like
        the shell's config_require_keys.

        Runs at most once per collect(): no_pass.run() starts with
        _cleanup_old_keys() (deletes ~/.ssh/config and the keypair), so
        re-running it would destroy the working configuration.
        """
        if self._nopass_done:
            return
        self._nopass_done = True
        if not self.setup_nopass:
            return
        print("\n[Collect] 配置 NPU SSH 免密 ...")
        try:
            from sprinkler.auto.base.no_pass import no_pass_check
            no_pass_check(self.args)
        except Exception as exc:
            print(f"[WARN] 免密配置执行失败: {type(exc).__name__}: {exc}")

    @staticmethod
    def _read_conf_value(key: str) -> str:
        """Non-interactive read of a key from the sprinkler config.conf."""
        try:
            from sprinkler.utils.utils import default_config_dir
            config_file = default_config_dir() / "config.conf"
            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as fp:
                    for line in fp:
                        line = line.strip()
                        if line.startswith(f"{key}="):
                            return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return ""

    # -------------------------------------------------------------- npu host
    def _collect_npu_host(self, result: CollectResult, dev_id, host: str):
        """Collect dmesg + /var/log/messages from one NPU via key-auth ssh/scp.
        Single attempt: if key auth fails, record FAIL and move on — the
        shell behaves the same after running no_password.sh once.
        """
        label = f"NPU{dev_id} ({host})"
        login = f"root@npu{dev_id}"
        print(f"\n[Collect] 正在收集 {label} 日志...")
        dmesg_dest = result.collection_dir / "NPU" / f"npu{dev_id}_dmesg.txt"
        messages_dest = result.collection_dir / "NPU" / f"npu{dev_id}_messages"

        # Best-effort: shell runs `ssh ... syslogd` to flush logs first.
        self._run_ssh(login, "syslogd", timeout=30)

        ok, err = self._fetch_remote_dmesg(login, dmesg_dest)
        if not ok:
            self._record(result, dmesg_dest, False, f"{label} dmesg: {err}")
            result.errors.append(f"{label}: {err}")
            return
        self._record(result, dmesg_dest, True, f"{label} dmesg")

        dl_ok, dl_err = self._download_remote_messages(login, messages_dest)
        self._record(result, messages_dest, dl_ok, f"{label} messages")
        if not dl_ok:
            result.errors.append(f"{label}: messages 下载失败: {dl_err}")

    def _run_ssh(self, alias: str, command: str, timeout: int = 60):
        """Run a command on the NPU via its ssh-config alias. Returns (ok, error)."""
        try:
            proc = subprocess.run(
                ["ssh", *SSH_BASE_OPTS, alias, command],
                check=False, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"ssh timeout after {timeout}s"
        except Exception as exc:
            return False, f"ssh: {exc}"
        if proc.returncode != 0:
            return False, (proc.stderr or "").strip() or f"exit {proc.returncode}"
        return True, ""

    def _fetch_remote_dmesg(self, alias: str, dest: Path):
        """ssh <alias> dmesg > dest.  Returns (ok, error)."""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("w", encoding="utf-8", errors="replace") as fp:
                proc = subprocess.run(
                    ["ssh", *SSH_BASE_OPTS, alias, "dmesg"],
                    check=False, stdout=fp, stderr=subprocess.PIPE,
                    text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return False, "ssh dmesg timeout"
        except Exception as exc:
            return False, f"ssh dmesg: {exc}"
        if proc.returncode != 0:
            return False, (proc.stderr or "").strip() or f"exit {proc.returncode}"
        try:
            if dest.stat().st_size == 0:
                return False, "dmesg 输出为空"
        except OSError as exc:
            return False, f"stat {dest}: {exc}"
        return True, ""

    def _download_remote_messages(self, alias: str, dest: Path):
        """scp -O <alias>:/var/log/messages dest.  Returns (ok, error)."""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                ["scp", "-O", *SSH_BASE_OPTS,
                 f"{alias}:/var/log/messages", str(dest)],
                check=False, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return False, "scp timeout"
        except Exception as exc:
            return False, f"scp: {exc}"
        if proc.returncode != 0:
            return False, (proc.stderr or "").strip() or f"exit {proc.returncode}"
        try:
            if not dest.exists() or dest.stat().st_size == 0:
                return False, "messages 为空"
        except OSError as exc:
            return False, f"stat {dest}: {exc}"
        return True, ""

    # ------------------------------------------------------------------- npu
    def _npu_pairs(self) -> list:
        """[(dev_id, target_ip)] from host_device_pairs (the no_pass approach,
        replacing the legacy npuip.sh /lib/route.conf parsing).

        get_host_and_device_pairs() reads /proc/asdrv_ub/pair_info per device
        and falls back to STATIC_NPU_IPS; the remote (device) EID is normalized
        exactly as no_pass.run() does, and dev_id matches the npu<dev_id>
        aliases the no-pass module writes into ~/.ssh/config.
        """
        from sprinkler.config.host_device_pairs import get_host_and_device_pairs
        from sprinkler.auto.base.no_pass import _is_ipv6, _normalize_ipv6

        pairs = []
        host_and_device_pairs = get_host_and_device_pairs()
        for dev_id in sorted(host_and_device_pairs):
            values = host_and_device_pairs[dev_id] or []
            target = values[1] if len(values) > 1 else ""
            if target and not _is_ipv6(target):
                target = _normalize_ipv6(target)
            if target:
                pairs.append((dev_id, target))
        if not pairs:
            print("[WARN] host_device_pairs 未获取到 NPU IP，跳过 NPU 日志收集")
        return pairs

    # ----------------------------------------------------------- msnpureport
    def _collect_msnpureport(self, result: CollectResult):
        """msnpureport logs: run the tool (outer/TS:debug), then -f, then copy
        the newest collected date-folder into msnpureport/."""
        label = "msnpureport"
        marker = result.collection_dir / label
        tool = self._find_msnpureport()
        if not tool:
            self._record(result, marker, False, f"{label}: tool not found")
            return
        log_root = Path("/root/ascend/log")
        try:
            subprocess.run([tool, "-d", "0", "-m", "TS:debug"], check=False,
                           capture_output=True, text=True, timeout=180)
            subprocess.run([tool, "-f"], cwd=str(log_root), check=False,
                           capture_output=True, text=True, timeout=180)
        except Exception as exc:
            self._record(result, marker, False, f"{label} run: {exc}")
            return
        latest = self._latest_date_dir(log_root)
        if latest is None:
            self._record(result, marker, False,
                         f"{label}: no collected log dir under {log_root}")
            return
        self._copy_tree(result, latest, result.collection_dir / label / latest.name)

    def _find_msnpureport(self) -> str:
        """Locate the msnpureport binary (same search order as the shell)."""
        for base in MNPUREPORT_SEARCH_ROOTS:
            try:
                for path in Path(base).rglob("msnpureport"):
                    if path.is_file():
                        return str(path)
            except Exception:
                continue
        try:
            proc = subprocess.run(["find", "/", "-name", "msnpureport", "-type", "f"],
                                  check=False, capture_output=True, text=True, timeout=60)
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line and Path(line).is_file():
                    return line
        except Exception:
            pass
        return ""

    @staticmethod
    def _latest_date_dir(root: Path):
        """Newest dir under root matching the msnpureport date format."""
        if not root.is_dir():
            return None
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$")
        candidates = [d for d in root.iterdir() if d.is_dir() and pattern.fullmatch(d.name)]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.name)

    # ---------------------------------------------------------------------- ub
    def _cabinet_data(self):
        """Cached structured cabinet data (see cabinet_info.get_cabinet_data)
        for every cabinet sheet in the local superpod table(s).

        Follows the current superpod_info layout (one xlsx per superpod
        number under default_config_dir(), sheets preprocessed with
        preprocess_column_names).  get_cabinet_data is called with the new
        6-argument signature (default_user/default_password appended), same
        as superpod_info.read_cabinet.
        """
        if self._cabinets is None:
            cabinets = []
            sheets_seen = 0
            for path, sheet_name, df, ca in _iter_superpod_sheets(self.sp_num):
                sheets_seen += 1
                sp = path.stem.rsplit("_", 1)[-1]
                try:
                    cabinets.append(get_cabinet_data(df, sheet_name, sp, ca, "root", ""))
                except Exception as exc:
                    print(f"[WARN] 解析页签 {path.name}/{sheet_name} 失败: "
                          f"{type(exc).__name__}: {exc}")
            if sheets_seen == 0:
                print(f"[WARN] 未找到超节点表格"
                      f"（superpod_{self.sp_num or '*'}.xlsx），UB 日志将不可收集")
            self._cabinets = cabinets
        return self._cabinets

    def _local_cabinet(self):
        """The cabinet sheet containing this host (matched by local CPU OS IP)."""
        local_ip = self._local_ip()
        for cabinet in self._cabinet_data():
            for cpu in cabinet.get("cpu", []):
                if cpu.get("host") == local_ip:
                    return cabinet
        return None

    def _local_cpu_password(self) -> str:
        """Pod OS password from the local CPU row's 密码 column in the
        superpod table (matched by OS IP).

        Replaces the removed superpod_info.find_host_credentials: the 1213
        backend scp's UB logs back to this host and needs its OS password
        to answer the password prompt."""
        local_ip = self._local_ip()
        for cabinet in self._cabinet_data():
            for cpu in cabinet.get("cpu", []):
                if cpu.get("host") == local_ip:
                    for key in ("password", "os_password", "passwd"):
                        value = cpu.get(key)
                        if value:
                            return str(value)
        return ""

    def _npu_ub_targets(self, npu_hosts):
        """NPU BMC targets for UB collection, scoped to the local cabinet.

        Matching by cabinet (via local CPU OS IP) instead of flat-list order:
        the superpod table spans many cabinets and host_device_pairs enumerates
        only the local pod's NPUs, so a global [:len(npu_hosts)] slice would
        pick the wrong cabinet's NPUs."""
        cabinet = self._local_cabinet()
        if cabinet is None:
            print("[WARN] 未能在超节点表格中定位本机所在机柜（本机 CPU OS IP 无匹配），"
                  "退回全表 NPU BMC 列表，UB 可能采到别的机柜！")
            bmc_list = self._npu_bmc_hosts()
        else:
            bmc_list = []
            for npu in cabinet.get("npu", []):
                host = npu.get("bmc_host")
                if host:
                    bmc_list.append({
                        "host": host,
                        "user": "Administrator",
                        "password": npu.get("bmc_password", ""),
                    })
            print(f"[INFO] 本机柜 {cabinet.get('cabinet_name')}: "
                  f"{len(bmc_list)} 个 NPU BMC 待采集 UB 日志")
        if npu_hosts and len(bmc_list) > len(npu_hosts):
            # host_device_pairs saw fewer NPUs than the table lists: trim, but say so.
            print(f"[WARN] host_device_pairs 只发现 {len(npu_hosts)} 个 NPU，"
                  f"表格本柜有 {len(bmc_list)} 个，按前者数量截取")
            return bmc_list[:len(npu_hosts)]
        return bmc_list

    def _npu_bmc_hosts(self):
        """NPU BMC list from the superpod tables (npu[].bmc_host/bmc_password)."""
        result = []
        for cabinet in self._cabinet_data():
            for npu in cabinet.get("npu", []):
                host = npu.get("bmc_host")
                if host:
                    result.append({
                        "host": host,
                        "user": "Administrator",
                        "password": npu.get("bmc_password", ""),
                    })
        return result

    def _cpu_ub_target(self):
        """CPU BMC whose OS IP matches the local host; fall back to first CPU BMC."""
        hosts = self._cpu_bmc_hosts()
        if not hosts:
            return None
        local_ip = self._local_ip()
        for entry in hosts:
            if entry.get("os_ip") == local_ip:
                return entry
        return hosts[0]

    def _cpu_bmc_hosts(self):
        """CPU BMC list from the superpod tables (cpu[].cpu_bmc_host ...)."""
        result = []
        for cabinet in self._cabinet_data():
            for cpu in cabinet.get("cpu", []):
                host = cpu.get("cpu_bmc_host")
                if host:
                    result.append({
                        "host": host,
                        "user": "Administrator",
                        "password": cpu.get("cpu_bmc_password", ""),
                        "os_ip": cpu.get("host", ""),
                    })
        return result

    def _collect_ub(self, result: CollectResult, bmc: dict, kind: str):
        """Collect 1213 UB logs through the BMC SOL session (reusing
        NPU1213Connector / CPU1213Connector + _UBSOLMixin), then copy the
        files the 1213 backend scp's to the pod-local staging directory.

        Success is judged by files actually arriving in the staging dir; the
        SOL session itself is best-effort (a clean session run without
        transferred files is still a failure)."""
        label = f"UB-{kind} ({bmc['host']})"
        print(f"\n[Collect] {label} 1213 日志...")
        # Per-BMC subdirectory: _copy_tree replaces dest, so sharing one dest
        # dir across multiple NPU BMCs would leave only the last one's logs.
        dest_dir = result.collection_dir / "UB" / f"{kind}1213log" / bmc["host"]
        staging = UB_LOCAL_DIRS[kind]
        # Clean staging first: stale files from previous runs (or previous
        # NPUs sharing /home/npu1213log) must not count as this run's success.
        self._reset_staging(staging)

        params = self._ub_params()
        connector = UB_CONNECTORS[kind](args=self.args)
        connector.transfers = self._ub_transfers(kind, params)
        connector.host_passwords = params["host_passwords"]
        target = ConnectionTarget(
            name=f"{kind}_1213", host=bmc["host"], port=22,
            user=bmc.get("user") or "Administrator",
            password=bmc.get("password") or "",
            meta={"kind": f"{kind}_1213", "node": 0, "cabinet_name": "",
                  "raw_name": bmc["host"], "sp": "", "ca": "", "source": "log_collect"},
        )
        try:
            results = connector.run_target(target, ["ub-log-transfer"])
        except Exception as exc:
            self._record(result, dest_dir, False, f"{label}: {exc}")
            return
        for r in results:
            if not r.get("ok"):
                result.errors.append(f"{label}: {r.get('cmd')}: {self._result_error(r)}")

        self._harvest_ub_staging(result, staging, dest_dir, label)

    @staticmethod
    def _reset_staging(staging: Path):
        """Empty (and create) the pod-local UB staging dir before a session."""
        try:
            if staging.exists():
                for entry in staging.iterdir():
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
            staging.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            print(f"[WARN] 清理 {staging} 失败: {exc}")

    def _harvest_ub_staging(self, result: CollectResult, staging: Path,
                            dest_dir: Path, label: str):
        try:
            has_files = staging.is_dir() and any(staging.iterdir())
        except Exception:
            has_files = False
        if has_files:
            self._copy_tree(result, staging, dest_dir)
        else:
            self._record(result, dest_dir, False, f"{label}: no files in {staging}")

    def _ub_transfers(self, kind, params):
        dest_dir = f"/home/{kind}1213log"
        dest = f"{params['dest_user']}@{params['dest_host']}:{dest_dir}"
        transfers = []
        for item in UB_TRANSFER_FILES[kind]:
            cmd = f"scp -P 22 {item['src']} {dest}/"
            transfers.append({
                "name": item["name"],
                "src": item["src"],
                "cmd": cmd,
                "pre": item.get("pre"),
            })
        return transfers

    def _ub_params(self):
        """Resolve the pod-local SSH destination used by the 1213 backend scp.

        Host OS password candidates, in priority order:
          1. SPRINKLER_UB_HOST_PASSWORD env var
          2. the local CPU row's 密码 in the superpod table (matched by OS IP;
             replaces the removed find_host_credentials)
          3. POD_PASSWORD from config.conf (what the shell's
             config_require_keys collects)
        """
        local_ip = self._local_ip()
        passwords = []
        env_pw = os.environ.get("SPRINKLER_UB_HOST_PASSWORD")
        if env_pw:
            passwords.append(env_pw)
        table_pw = self._local_cpu_password()
        if table_pw:
            passwords.append(table_pw)
        conf_pw = self._read_conf_value("POD_PASSWORD")
        if conf_pw:
            passwords.append(conf_pw)
        passwords.append("")
        return {
            "dest_host": os.environ.get("SPRINKLER_UB_DEST_HOST") or local_ip,
            "dest_user": os.environ.get("SPRINKLER_UB_DEST_USER") or "root",
            "host_passwords": list(dict.fromkeys(passwords)),
        }

    def _local_ip(self) -> str:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
            finally:
                sock.close()
        except Exception:
            return socket.gethostbyname(socket.gethostname())

    # ------------------------------------------------------------- directory
    def _new_collection_dir(self):
        self.collection_root.mkdir(parents=True, exist_ok=True)
        max_index = 0
        for entry in self.collection_root.iterdir():
            match = COLLECTION_PATTERN.fullmatch(entry.name)
            if match and entry.is_dir():
                max_index = max(max_index, int(match.group(1)))
        index = max_index + 1
        return self.collection_root / f"collection_{index:03d}", index

    # ------------------------------------------------------------- recording
    def _record(self, result: CollectResult, path, ok: bool, label: str):
        entry = str(path)
        if ok:
            result.files.append(entry)
            if Path(entry).exists():
                result.success += 1
        else:
            result.failed += 1
        self.run_log.log("PASS" if ok else "FAIL", f"{label}: {entry}")
        print(f"  [{'OK' if ok else 'FAIL'}] {label}: {entry}")

    def _copy_file(self, result: CollectResult, src: Path, dest: Path):
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            self._record(result, dest, True, f"copy {src.name}")
        except Exception as exc:
            self._record(result, dest, False, f"copy {src}: {exc}")

    def _copy_tree(self, result: CollectResult, src: Path, dest: Path):
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            self._record(result, dest, True, f"copy tree {src.name}")
        except Exception as exc:
            self._record(result, dest, False, f"copy tree {src}: {exc}")

    @staticmethod
    def _result_error(result: dict) -> str:
        return str(result.get("stderr") or result.get("error_type") or "unknown")

    # --------------------------------------------------------------- summary
    def _write_manifest(self, result: CollectResult):
        manifest = result.to_dict()
        manifest["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        manifest_path = result.collection_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        result.files.append(str(manifest_path))

    def _archive(self, result: CollectResult):
        """Tar the collection dir (parity with the shell's tar -czf step)."""
        try:
            archive = shutil.make_archive(str(result.collection_dir), "gztar",
                                          root_dir=result.collection_dir)
            result.files.append(archive)
            print(f"日志包: {archive}")
        except Exception as exc:
            result.errors.append(f"打包失败: {exc}")

    def _print_summary(self, result: CollectResult):
        print("\n" + "=" * 60)
        print(f"Collection {result.collection_id} finished: "
              f"{result.success} files collected, {result.failed} failed")
        print(f"Directory: {result.collection_dir}")
        if result.errors:
            print("Errors:")
            for error in result.errors:
                print(f"  - {error}")
        print("=" * 60)


def collect_logs():
    """Entry point for ``sprinkler -collect-logs`` (needs get_args())."""
    args = get_args()
    collector = LogCollector(args=args)
    return collector.collect()
