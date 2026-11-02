"""前台交换机版本解析策略 —— 解析 ``display startup`` 输出。

一条 ``display startup`` 命令同时返回当前 CC 包（Startup system software）
与 CFG 配置（Startup saved-configuration file）的版本。1213（CPU/NPU）与
1260 F（SFU）前台交换机统一作为 ``switch`` 目标类型，复用同一解析逻辑；
连接方式（当前均为 SSH 直连）由 DeviceSpec 的 ``connection_type`` 决定。

返回内部字段 ``cc_version`` / ``cfg_version``；重命名为报告列
（switch_cc 等独立列）的工作发生在 hybrid 合并层
（见 ``registry.DeviceSpec.field_map``）
"""

import re

from sprinkler.checker.version.query.base import Parser, RawResult

# 前台交换机查询命令：一条 ``display startup`` 同时返回 CC 包与 CFG 配置版本。
DISPLAY_STARTUP_CMD = "display startup"


class FrontendParser(Parser):
    """把 ``display startup`` 输出解析为 cc_version / cfg_version 两个字段。"""

    # 通道采集所需的全部命令。
    commands = (DISPLAY_STARTUP_CMD,)

    def parse(self, raw: RawResult) -> tuple:
        """聚合单目标的 display startup 结果到字段字典 + 成败判定。

        ``raw.data`` 为命令通道返回的原始结果列表（每项是含
        cmd/stdout/ok/error_type/reason 的字典）
        """
        fields = {"cc_version": "", "cfg_version": ""}
        ok, reason = True, ""
        for result in raw.data or []:
            if not isinstance(result, dict):
                continue
            cmd = result.get("cmd", "")
            stdout = str(result.get("stdout", "") or "")
            if result.get("ok") and cmd == DISPLAY_STARTUP_CMD:
                fields.update(self._parse_display_startup(stdout))
            elif not result.get("ok"):
                error_type = result.get("error_type") or ""
                # 命令级错误不判整体失败；连接 / 认证类错误才判整体失败并记录原因
                if error_type != "command":
                    ok = False
                    reason = result.get("reason") or error_type or "connection failed"
        if not raw.data:
            ok, reason = False, "no results"
        return fields, ok, reason

    @staticmethod
    def _parse_display_startup(stdout: str) -> dict:
        """解析 ``display startup`` 输出中的 CC 包和 CFG 文件版本。

        示例：
            Startup system software:                  flash:/UBM-CPU-xxx.cc
            Startup saved-configuration file:         flash:/02-slot14-cpu1_20260510.cfg

        只匹配当前 ``Startup``（忽略 ``Next startup``）。返回
        ``{"cc_version": str, "cfg_version": str}``（未找到对应行时为空串）
        """
        cc = re.search(r"Startup system software:\s*flash:/(\S+)", stdout)
        cfg = re.search(r"Startup saved-configuration file:\s*flash:/(\S+)", stdout)
        return {"cc_version": cc.group(1).strip() if cc else "",
                "cfg_version": cfg.group(1).strip() if cfg else ""}
