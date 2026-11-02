"""升级前后版本验证器 —— 复用 RedfishReader + CpuBmcParser/NpuBmcParser 查询版本。

与 checker/version/query 的关系：
- version_query.py 是"批量查询 + 报告生成"的 CLI 入口
- UpgradeValidator 是"单目标查询 + 组件版本提取"的内部工具
- 两者共享 RedfishReader / Parser，但调用路径不同
"""

from sprinkler.checker.version.query.channels.redfish import RedfishReader
from sprinkler.checker.version.query.parsers.cpu_bmc import CpuBmcParser
from sprinkler.checker.version.query.parsers.npu_bmc import NpuBmcParser
from sprinkler.checker.version.query.base import RawResult
from sprinkler.checker.version.query.registry import bmc_identification_patterns


class UpgradeValidator:
    """升级前后版本验证器。

    复用现有版本查询框架（RedfishReader + Parser），不引入新的采集逻辑。
    BMC 识别模式从 registry 读取（与 RedfishChannel 共享单一事实来源），
    Parser 通过可注入映射支持扩展。
    """

    # 组件 → 解析器输出字段名的映射（BmcVersionInfo.to_dict() 的实际 key）
    # CpuBmcParser 输出：active_ibmc / bios_version / cpu_board_cpld / cpu_board_csr
    # NpuBmcParser 输出：active_ibmc / npu_firmware / npu_csr
    COMPONENT_FIELD_MAP = {
        "ibmc":         ("active_ibmc",),
        "bios":         ("bios_version",),
        "cpld":         ("cpu_board_cpld", "exp_cpld"),
        "csr":          ("cpu_board_csr", "exp_csr", "npu_csr"),
        "npu_firmware": ("npu_firmware",),
    }

    # 默认 BMC 类型 → Parser 映射（可由构造函数注入以支持扩展）
    DEFAULT_PARSERS = {
        "CPU": CpuBmcParser,
        "NPU": NpuBmcParser,
    }

    def __init__(self, timeout: int = 30, parsers=None,
                 bmc_patterns=None):
        """:param parsers: 可选的 {bmc_type: Parser} 映射，默认使用 DEFAULT_PARSERS
        :param bmc_patterns: 可选的 (pattern, bmc_type) 序列，默认从 registry 读取
        """
        self.timeout = timeout
        self._parsers = parsers or dict(self.DEFAULT_PARSERS)
        self._bmc_patterns = bmc_patterns if bmc_patterns is not None else bmc_identification_patterns()

    def query_component_version(self, reader: RedfishReader,
                                component: str) -> str:
        """通过已有的 RedfishReader 查询单个组件的当前版本。

        复用 RedfishReader 的 get() 方法采集 Redfish 资源，
        复用 Parser 解析版本字段。

        :param reader: 已认证的 RedfishReader（调用方负责生命周期）
        :param component: 组件类型
        :return: 版本号字符串（采集失败返回空串）
        """
        if component not in self.COMPONENT_FIELD_MAP:
            return ""

        # 采集 SystemOverview 识别 BMC 类型
        overview_data, status = reader.get(RedfishReader.PATH_SYSTEM_OVERVIEW)
        if status != 200 or overview_data is None:
            return ""

        bmc_type = self._identify_bmc_type(overview_data)

        # 选择解析器
        parser_cls = self._parsers.get(bmc_type)
        if parser_cls is None:
            return ""

        # 构造 RawResult 供 Parser 使用
        from sprinkler.connect.targets import ConnectionTarget
        raw = RawResult(target=ConnectionTarget(name="", host=reader._base_url.replace("https://", "")),
                        data=reader)
        raw.device_type = bmc_type

        fields, ok, _ = parser_cls().parse(raw)
        if not ok:
            return ""

        # 提取目标字段
        field_names = self.COMPONENT_FIELD_MAP[component]
        for fname in field_names:
            if fname in fields and fields[fname]:
                return str(fields[fname])
        return ""

    def verify_upgrade(self, reader: RedfishReader, component: str,
                       pre_version: str = "",
                       expected_version: str = "") -> dict:
        """升级后验证。

        :return: {
            "ok": bool,
            "version_before": str,
            "version_after": str,
            "changed": bool,
            "error": str,
        }
        """
        post_version = self.query_component_version(reader, component)
        changed = (post_version != pre_version) if pre_version else bool(post_version)
        if expected_version:
            ok = post_version == expected_version
            error = "" if ok else f"version mismatch: expected={expected_version}, actual={post_version}"
        else:
            ok = bool(post_version)
            error = "" if ok else "version query returned empty"
        return {
            "ok": ok,
            "version_before": pre_version,
            "version_after": post_version,
            "changed": changed,
            "error": error,
        }

    def _identify_bmc_type(self, overview_data: dict) -> str:
        """从 SystemOverview 数据识别 BMC 类型（CPU/NPU/UNKNOWN）。

        模式从 registry 读取（与 RedfishChannel 共享单一事实来源），
        首匹配优先。
        """
        if not isinstance(overview_data, dict):
            return "UNKNOWN"
        try:
            systems = overview_data.get("Systems", [])
            if not systems:
                return "UNKNOWN"
            product = str(systems[0].get("ProductName", "") or "")
        except (KeyError, IndexError, TypeError):
            return "UNKNOWN"

        for pattern, bmc_type in self._bmc_patterns:
            if pattern in product:
                return bmc_type
        return "UNKNOWN"
