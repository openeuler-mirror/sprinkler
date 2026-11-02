"""URMA command executor and process sessions."""

import os
import re
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from sprinkler.auto.bandwidth.common import command_result, text_value
from sprinkler.connect.npu_access import is_local_cpu_host, npu_side_command
from sprinkler.connect.ssh import SSHOSConnector
from sprinkler.connect.targets import ConnectionTarget

class UrmaStreamLog:
    def __init__(self, path, role, side, command):
        self.path = Path(path) if path else None
        self.role = str(role or "")
        self.side = str(side or "")
        self.command = str(command or "")

    def start(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}] "
                   f"role={self.role} side={self.side}\n$ {self.command}\n")

    def append(self, text):
        if self.path is None or not text:
            return
        self.write(str(text))

    def finish(self, rc):
        if self.path is None:
            return
        self.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}] role={self.role} rc={rc}\n")

    def write(self, text):
        with self.path.open("a", encoding="utf-8", errors="replace") as fp:
            fp.write(text)

class UrmaProcessBuffer:
    PID_RE = re.compile(r"SPRINKLER_PROCESS_PID=(\d+)")
    RC_RE = re.compile(r"SPRINKLER_PROCESS_RC=(-?\d+)")

    def __init__(self, stream_log=None):
        self.stream_log = stream_log
        self.stdout = []
        self.stderr = []

    def add_stdout(self, data):
        text = text_value(data)
        self.stdout.append(text)
        if self.stream_log is not None:
            self.stream_log.append(text)
        tail = "".join(self.stdout[-4:])
        pid = self.PID_RE.search(tail)
        rc = self.RC_RE.search(tail)
        return pid.group(1) if pid else "", int(rc.group(1)) if rc else None

    def add_stderr(self, data):
        text = text_value(data)
        self.stderr.append(text)
        if self.stream_log is not None:
            self.stream_log.append(f"\n[stderr] {text}\n")

    @classmethod
    def clean_output(cls, text):
        lines = str(text or "").replace("\r", "").splitlines()
        return "\n".join(line for line in lines if not (cls.PID_RE.search(line) or cls.RC_RE.search(line)))

class UrmaLocalProcessHandle:
    def __init__(self, session):
        self.session = session
        self.process = None
        self.reader = None

    def start(self):
        kwargs = {"shell": True, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                  "stderr": subprocess.STDOUT, "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(self.session.shell_command(), **kwargs)
        self.session.pid = str(self.process.pid)
        self.session.started = True
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.process.stdout or []:
            self.session.add_stdout(line)
        self.session.rc = self.process.wait()

    def stop(self):
        process = self.running_process()
        if process is None:
            return
        if os.name == "nt":
            process.terminate()
            return
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        time.sleep(1)
        if process.poll() is None:
            self.kill()

    def wait(self):
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.kill()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if self.reader is not None:
            self.reader.join(timeout=5)

    def kill(self):
        process = self.running_process()
        if process is None:
            return
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)

    def running_process(self):
        return self.process if self.process is not None and self.process.poll() is None else None

    def return_code(self):
        return self.process.returncode if self.process is not None else None

class UrmaRemoteProcessHandle:
    def __init__(self, session):
        self.session = session
        self.client = None
        self.channel = None
        self.reader = None

    def start(self):
        self.client = self.session.executor.connect(self.connection_target())
        self.channel = self.client.get_transport().open_session()
        self.channel.set_combine_stderr(True)
        self.channel.exec_command(self.remote_command())
        self.session.started = True
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        deadline = time.time() + int(getattr(self.session.executor.args, "connect_timeout", 10) or 10)
        while time.time() < deadline and not self.session.pid and self.session.rc is None:
            time.sleep(0.05)

    def read(self):
        while self.channel is not None:
            try:
                if self.channel.recv_ready():
                    self.session.add_stdout(self.channel.recv(4096))
                if self.channel.exit_status_ready():
                    while self.channel.recv_ready():
                        self.session.add_stdout(self.channel.recv(4096))
                    self.session.rc = int(self.channel.recv_exit_status())
                    return
            except Exception as exc:
                self.session.add_stderr(str(exc))
                return
            time.sleep(0.02)

    def stop(self):
        if self.session.pid:
            self.session.executor.run(self.session.side, self.remote_stop_command())

    def wait(self):
        if self.reader is not None:
            self.reader.join(timeout=5)

    def close(self):
        for item in (self.channel, self.client):
            if item is not None:
                item.close()

    def connection_target(self):
        side = self.session.side
        meta = {"kind": "npu_os", "rank": side.get("rank"), "subsystem": "npu_os"}
        return ConnectionTarget(name=side.get("label") or side.get("host") or "npu", host=side.get("host") or "",
                                meta=meta)

    def remote_command(self):
        inner = (f"{{ {self.session.shell_command()}; }} 2>&1 & pid=$!; echo SPRINKLER_PROCESS_PID=$pid; "
                 f"wait $pid; rc=$?; echo SPRINKLER_PROCESS_RC=$rc; exit $rc")
        return f"sh -lc {shlex.quote(inner)}"

    def remote_stop_command(self):
        if not self.session.pid:
            return "sh -lc true"
        inner = (f"kill -TERM {self.session.pid} 2>/dev/null || true; sleep 1; "
                 f"kill -KILL {self.session.pid} 2>/dev/null || true")
        return f"sh -lc {shlex.quote(inner)}"

class UrmaProcessSession:
    def __init__(self, executor, side, command, role, stream_log=None):
        self.executor = executor
        self.side = dict(side or {})
        self.command = str(command or "")
        self.role = role
        self.stream_log = stream_log
        self.buffer = UrmaProcessBuffer(stream_log)
        self.pid = ""
        self.rc = None
        self.started = False
        self.stopped = False
        self.start_time = 0.0
        handle_cls = UrmaLocalProcessHandle if self.side.get("kind") == "local" else UrmaRemoteProcessHandle
        self.handle = handle_cls(self)

    def start(self):
        self.start_time = time.time()
        self.start_stream_log()
        try:
            self.handle.start()
            ok = bool(self.pid)
            reason = "" if ok else "process pid not reported"
            if not ok:
                self.finish_stream_log(255)
                self.close()
            return self.result(ok=ok, rc=0 if ok else 255, error_type="" if ok else "start", stderr=reason)
        except Exception as exc:
            self.add_stderr(str(exc))
            self.finish_stream_log(255)
            self.close()
            return self.result(ok=False, rc=255, error_type="start", stderr=str(exc))

    def stop(self):
        if not self.started or self.stopped:
            return
        self.stopped = True
        self.handle.stop()

    def shell_command(self):
        return npu_side_command(self.side, self.command)

    def collect(self):
        if self.started and not self.stopped and self.rc is None:
            self.stop()
        self.handle.wait()
        out = self.buffer.clean_output("".join(self.buffer.stdout))
        err = self.buffer.clean_output("".join(self.buffer.stderr))
        rc_value = self.rc if self.rc is not None else (getattr(self.handle, "return_code", None) or (lambda: None))()
        exited = rc_value is not None
        rc = int(rc_value) if exited else 255
        ok = rc == 0 or (exited and self.stopped and rc in {0, 124, 137, 143, 255, -9, -15})
        if not exited:
            note = "process did not exit after stop/kill (maybe stuck in driver io)"
            err = f"{err}\n{note}" if err else note
        result = self.result(ok=ok, rc=rc,
                             error_type="" if ok else "stop" if not exited else "command",
                             stdout=out, stderr=err)
        self.finish_stream_log(rc)
        self.close()
        return result

    def add_stdout(self, data):
        pid, rc = self.buffer.add_stdout(data)
        if pid:
            self.pid = pid
        if rc is not None:
            self.rc = rc

    def add_stderr(self, data):
        self.buffer.add_stderr(data)

    def result(self, ok, rc, error_type="", stdout="", stderr=""):
        return command_result(target=self.side.get("label") or "local", host=self.side.get("host") or "local",
                              cmd=self.command, ok=ok, rc=rc, error_type=error_type, stdout=stdout, stderr=stderr,
                              elapsed=time.time() - self.start_time, backend=self.side.get("kind") or "local",
                              stopped=self.stopped)

    def start_stream_log(self):
        if self.stream_log is None:
            return
        if not isinstance(self.stream_log, UrmaStreamLog):
            side = self.side.get("label") or self.side.get("host") or "local"
            self.stream_log = UrmaStreamLog(self.stream_log, self.role, side, self.command)
        self.buffer.stream_log = self.stream_log
        self.stream_log.start()

    def finish_stream_log(self, rc):
        if self.stream_log is not None:
            self.stream_log.finish(rc)

    def close(self):
        close = getattr(self.handle, "close", None)
        if close:
            close()

class UrmaCommandExecutor:
    def __init__(self, args=None):
        timeout = int(getattr(args, "timeout", 60) or 60)
        duration = int(getattr(args, "duration", 0) or 0)
        self.command_timeout = timeout if duration <= 0 else max(timeout, duration + 10)
        values = dict(vars(args)) if args is not None else {}
        values["timeout"] = self.command_timeout
        self.args = SimpleNamespace(**values)
        self.connector = SSHOSConnector(args=self.args, run_log=None)
    def run(self, side, command, timeout=None):
        if side.get("error"):
            return command_result(target=side.get("label") or "npu", host=side.get("host") or "",
                                  cmd=command, ok=False, rc=2, error_type="config", stderr=side.get("error"))
        actual_command = npu_side_command(side, command)
        if side.get("kind") == "local":
            return self.run_local(side, actual_command, display_command=command, timeout=timeout)
        meta = {"kind": "npu_os", "rank": side.get("rank"), "subsystem": "npu_os"}
        target = ConnectionTarget(name=side.get("label") or side.get("host") or "npu",
                                  host=side.get("host") or "", meta=meta)
        result = self._connector(timeout).run_target(target, [actual_command])[0]
        if actual_command != command:
            result["raw_cmd"] = result.get("cmd", "")
            result["cmd"] = command
        return result

    def _connector(self, timeout=None):
        if not timeout:
            return self.connector
        values = dict(vars(self.args))
        values["timeout"] = timeout
        return SSHOSConnector(args=SimpleNamespace(**values), run_log=None)

    def connect(self, target):
        return self.connector.connect_client(target)

    def run_local(self, side, command, display_command=None, timeout=None):
        start = time.time()
        try:
            completed = subprocess.run(command, shell=True, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       timeout=timeout or self.command_timeout)
            ok = completed.returncode == 0
            rc = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            error_type = ""
        except subprocess.TimeoutExpired as exc:
            ok = False
            rc = 255
            stdout = exc.stdout or ""
            stderr = exc.stderr or str(exc)
            error_type = "timeout"
        return command_result(target=side.get("label") or "local", host=side.get("host") or "local",
                              cmd=display_command or command,
                              ok=ok, rc=rc, error_type=error_type or ("" if ok else "command"),
                              stdout=stdout, stderr=stderr, elapsed=time.time() - start,
                              backend=side.get("kind") or "local")
