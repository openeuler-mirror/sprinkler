"""OS 版本解析策略 —— 解析 SSH 命令输出。

对 cpu_os 目标执行三条只读命令并解析版本字段：

- ``cat /etc/os-release``  -> OS 发行版版本（os_version）。
- ``npu-smi info``         -> NPU 固件版本（npu_firmware）。
- ``cat /usr/local/Ascend/version.info`` -> HDK 版本（hdk_version）。

判定规则（与命令通道的语义对齐）：

- 命令级错误（``error_type == "command"``，如某命令不存在 / 非零退出）不判
  整体失败，仅该字段缺失；连接 / 认证类错误才判整体失败。
- 命令成功但无输出：该字段保持空串；命令失败：对应字段保持空串。
- 一条命令失败不掩盖其它有效命令解析出的字段。
- 通道返回空结果（undo 无任何命令输出）时判失败，reason='no results'
"""

import re

from sprinkler.checker.version.query.base import Parser, RawResult

# 待执行命令常量（同名的解析函数登记在 _cmd_parsers 中）。
# - cat /etc/os-release：OS 发行版信息（PRETTY_NAME / NAME+VERSION）。
# - npu-smi info：NPU 设备与固件信息（各 Version: 行）。
# - cat /usr/local/Ascend/version.info：HDK（硬件开发套件）版本（version= 行）。
OS_RELEASE_CMD = "cat /etc/os-release"
NPU_SMI_CMD = "npu-smi info"
HDK_VERSION_CMD = "cat /usr/local/Ascend/version.info"


class OsParser(Parser):
    """把 OS 命令输出解析为 os_version / npu_firmware / hdk_version 三个字段。"""

    # 通道采集所需的全部命令。
    commands = (OS_RELEASE_CMD, NPU_SMI_CMD, HDK_VERSION_CMD)

    def __init__(self):
        """构造解析器并登记 命令 -> 解析函数 的分发表（OCP：新增命令即追加）。"""
        self._cmd_parsers = {
            OS_RELEASE_CMD: self._parse_os_release,
            NPU_SMI_CMD: self._parse_npu_smi,
            HDK_VERSION_CMD: self._parse_hdk_version,
        }

    def parse(self, raw: RawResult) -> tuple:
        """聚合单目标的多条命令结果到字段字典 + 成败判定。

        ``raw.data`` 为命令通道返回的原始结果列表（每项是含
        cmd/stdout/ok/error_type/reason 的字典）
        """
        fields = {"os_version": "", "npu_firmware": "", "hdk_version": ""}
        ok, reason = True, ""
        for result in raw.data or []:
            if not isinstance(result, dict):
                continue
            cmd = result.get("cmd", "")
            stdout = str(result.get("stdout", "") or "")
            if result.get("ok"):
                parser = self._cmd_parsers.get(cmd)
                if parser:
                    fields.update(parser(stdout))
            else:
                error_type = result.get("error_type") or ""
                # 命令级错误不判整体失败，仅对应字段缺失；连接/认证等
                # 其它错误才判整体失败并记录原因
                if error_type != "command":
                    ok = False
                    reason = (result.get("reason") or
                              _stderr_first_line(result.get("stderr", "")) or
                              error_type or
                              "ssh failed")
        if not raw.data:
            ok, reason = False, "no results"
        return fields, ok, reason

    @staticmethod
    def _parse_os_release(stdout: str) -> dict:
        """解析 /etc/os-release，返回 ``{"os_version": str}``。

        优先级：``PRETTY_NAME``（带引号或不带引号）；缺失时退回
        ``NAME`` + ``VERSION``（两者均有则空格拼接）
        """
        pretty = re.search(r'^PRETTY_NAME="(.+)"', stdout, re.MULTILINE)
        if pretty:
            return {"os_version": pretty.group(1).strip()}
        name = re.search(r'^NAME="?(.+?)"?$', stdout, re.MULTILINE)
        ver = re.search(r'^VERSION="?(.+?)"?$', stdout, re.MULTILINE)
        name_val = name.group(1).strip() if name else ""
        ver_val = ver.group(1).strip() if ver else ""
        return {"os_version": " ".join(p for p in (name_val, ver_val) if p)}

    @staticmethod
    def _parse_npu_smi(stdout: str) -> dict:
        """解析 npu-smi info 输出，返回 ``{"npu_firmware": str}``。

        聚合所有 ``Version:`` / ``Version：`` 行的版本号（兼容中英文冒号），
        去重后按出现顺序以逗号拼接（多 NPU 设备各自报告一行 Version）
        """
        versions = []
        for line in stdout.splitlines():
            match = re.search(r"Version\s*[:：]\s*(\S+)", line)
            if match and match.group(1) not in versions:
                versions.append(match.group(1))
        return {"npu_firmware": ", ".join(versions)}

    @staticmethod
    def _parse_hdk_version(stdout: str) -> dict:
        """解析 HDK version.info，返回 ``{"hdk_version": str}``。

        取 ``version=`` 后的值并去除尾部空白；无匹配行时保持空串
        """
        match = re.search(r"version=(.+)", stdout)
        return {"hdk_version": match.group(1).strip() if match else ""}


def _stderr_first_line(stderr: str) -> str:
    """取 stderr 首行（去掉尾部空白）作为后备原因；空串时返回空串。

    当 result 缺失 reason 且 error_type 不足以描述故障时，stderr 首行
    通常携带最多的诊断上下文（如 "network: host unreachable"）
    """
    if stderr and stderr.strip():
        return stderr.strip().splitlines()[0].strip()
    return ""
