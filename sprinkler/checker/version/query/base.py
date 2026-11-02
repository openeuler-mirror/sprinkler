"""版本查询策略基础设施 —— 公共模型与模板方法骨架。

本模块定义版本查询的三大核心抽象，是"通道 × 解析器"策略组合的公共基础：

1. ``RawResult``：一次"目标 + 通道采集"的原始结果载体。携带目标引用、
   通道原始数据（Redfish 阅读器 / 命令结果列表）与通道级错误，是通道层与
   解析层之间的唯一信使。
2. ``Parser``：设备解析策略抽象。把通道原始数据解析为版本字段并判定成败；
   同类设备（如 CPU BMC 与 NPU BMC）因为字段提取规则不同而各自实现一个
   解析器。
3. ``QueryStrategy``：查询策略模板方法。用统一的处理骨架把
   『收集目标 → 通道采集 → 解析 → 归一化结果行』编排起来；通道与解析器
   通过构造参数注入，子类只需覆写少数钩子即可接入新设备类型。

模板方法骨架（``query_all`` 的执行流水线）：
1. ``_collect_targets``：按设备目标类型收集目标（可覆写，如 Redfish 通道
   需要跨多种目标类型去重）。
2. ``channel.collect``：通道批量采集，产出逐目标的 ``RawResult``。
3. ``_render``：命中通道级错误时直接生成失败行；否则交给解析策略取值 /
   判定（``_parse`` 钩子，Redfish 策略会覆写为按识别类型选择解析器）。
4. ``_build_row``：归一化通用字段（target/host/ip/type/ok/reason）并合并
   解析出的版本字段。
5. ``_dispose``：释放本次采集占用的资源（如 Redfish 会话），默认为空操作。

设计收益：新增设备类型时，只需新增一个 Parser（解析规则）+ 一个 DeviceSpec
（注册表声明），由工厂负责把合适的通道与解析器组合成 QueryStrategy，本模块
的模板骨架无需改动（OCP：对扩展开放、对修改关闭）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from sprinkler.base.log import RunLog
from sprinkler.connect.targets import TargetContext, ConnectionTarget


@dataclass
class RawResult:
    """某目标的一次通道采集结果。

    属性：
        target: 归一化后的 ``ConnectionTarget``（经 ``ConnectionTarget.from_value``
            转换，统一暴露 name/host/port/user/password/meta 等属性）
        data: 通道原始数据，交给解析器解读：
            - Redfish 通道：``RedfishReader``（单目标只读访问器）
            - 命令通道：命令结果字典列表（每项含 cmd/stdout/ok/error_type/reason）
            通道级采集失败（连接错误等）时可为 None
        error: 通道级错误描述（连接失败、凭据缺失、依赖缺失等）；空串表示
            采集成功进入解析阶段。命中该字段时上层直接产出失败行，不再调解析器
        target_type: 结果行中的类型标识（如 cpu_bmc / cpu_os / switch），
            用于报告与 hybrid 合并
        device_type: 通道识别出的设备细类（Redfish 通道的 CPU / NPU / UNKNOWN），
            供策略层选择对应解析器；命令通道通常不设置。
    """

    target: ConnectionTarget
    data: Any = None
    error: str = ""
    target_type: str = ""
    device_type: str = ""


class Parser(ABC):
    """设备解析策略：把通道原始数据解析为版本字段并判定成败。

    实现约定：
    - ``raw.data`` 的形态由配对通道决定（Redfish 阅读器或命令结果列表），
      解析器需按通道契约读取。
    - 解析必须是"尽力而为"的：某子资源缺失 / 某命令失败时对应字段保持空值或
      空串，不抛异常；只有连接 / 认证等整体性错误才判定整体失败。
    - ``commands`` 类属性声明设备需要执行的命令集（命令通道采集依赖）。
    """

    @abstractmethod
    def parse(self, raw: RawResult) -> tuple:
        """解析一次采集结果。

        参数：
            raw: 通道采集产出的原始结果。

        返回：
            (字段字典, 是否成功 bool, 原因字符串)。失败时字段可为空字典，
            成功时原因应为空串
        """


class QueryStrategy(ABC):
    """版本查询策略 —— 模板方法统一『收集 → 采集 → 解析 → 归一化』骨架。

    通过 ``channel``（通道策略）与 ``parser``（解析策略）可插拔组合出针对
    某一设备目标类型的查询能力；运行期配置（args / run_log / timeout）由
    工厂在构造时注入，策略自身不绑定具体传输实现。

    子类扩展点：
    - ``_collect_targets``：自定义目标收集（如 Redfish 跨类型去重）。
    - ``_parse``：自定义解析分发（如按识别出的设备类型选择解析器）。
    - ``_dispose``：自定义资源释放（如关闭 Redfish 会话）。
    """

    def __init__(
        self,
        channel: Any,
        parser: Optional[Parser] = None,
        target_type: str = "",
        args=None,
        run_log: Optional[RunLog] = None,
        timeout: int = 30,
        target_collector=None,
    ):
        """构造策略。

        参数：
            channel: 通道策略实例（提供 ``collect(targets, ...)`` 采集能力）
            parser: 设备解析策略实例；Redfish 策略可以在运行期按识别结果
                自行选择解析器，此时该参数可为 None（见 RedfishQueryStrategy）
            target_type: 本策略服务的设备目标类型（registry 中登记的
                target_type 名），用于默认的 ``_collect_targets`` 收集
            args: 顶层 CLI 参数（透传给通道 / 底层连接器，如凭据、超时等）
            run_log: 日志对象；可为 None（不记日志）
            timeout: 单次交互（Redfish 请求 / 命令执行）超时秒数
            target_collector: 可选的目标收集器（``callable(args) -> list``）
                提供时 ``_collect_targets`` 优先调用它（替代默认的
                ``target_ctx.get_targets``），适用于目标来源不在
                TargetContext 能力范围内的设备（如前交换机 switch 需从
                Excel 的 frontend_host / com_host 跨设备族收集）
        """
        self.channel = channel
        self.parser = parser
        self.target_type = target_type
        self.args = args
        self.run_log = run_log
        self.timeout = timeout
        self.target_collector = target_collector

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def query_all(self, target_ctx: TargetContext = None) -> list:
        """对所有适用目标执行版本查询，返回结果行列表（每目标一行）。

        流程即模板骨架：收集目标 → 通道批量采集 → 逐 RawResult 渲染行 →
        逐 RawResult 释放资源。目标为空时直接返回空列表（不产生结果文件）
        """
        if target_ctx is None:
            target_ctx = TargetContext(self.args)
        targets = self._collect_targets(target_ctx)
        if not targets:
            return []
        raw_results = self.channel.collect(targets, args=self.args,
                                           run_log=self.run_log, timeout=self.timeout)
        rows = [self._render(raw) for raw in raw_results]
        for raw in raw_results:
            self._dispose(raw)
        return rows

    def query(self, target) -> dict:
        """查询单个目标（ConnectionTarget 或 dict），返回结果行。

        供单点查询使用（如 IT 用例、故障排查）：内部会归一化目标、
        采集、渲染，并在 finally 中确保释放本次会话资源
        """
        conn_target = ConnectionTarget.from_value(target)
        raw = self.channel.collect([conn_target], args=self.args,
                                   run_log=self.run_log, timeout=self.timeout)[0]
        try:
            return self._render(raw)
        finally:
            self._dispose(raw)

    def _dispose(self, raw: RawResult):
        """释放本次采集占用的资源（如关闭 Redfish 会话）；默认为空操作，可覆写。

        由 ``query_all`` / ``query`` 自动调用，保证长会话（Redfish Session）
        不被泄漏；命令通道无持久连接，无需处理
        """

    # ------------------------------------------------------------------
    # 模板步骤（可覆写）
    # ------------------------------------------------------------------
    def _collect_targets(self, target_ctx: TargetContext) -> list:
        """按设备目标类型收集目标；无目标或取目标异常时返回空列表。

        默认实现即 ``target_ctx.get_targets(self.target_type)``；取目标抛异常
        时仅告警（不中断调用方），并返回空列表让上层安全跳过。

        当构造时注入了 ``target_collector``（可调用，接收 args 返回目标列表）
        时优先使用它，替代 TargetContext 收集（如 switch 需要跨设备族从
        Excel 的 frontend_host / com_host 收集）
        """
        if self.target_collector is not None:
            try:
                collected = self.target_collector(self.args)
                return list(collected or [])
            except Exception as exc:
                if self.run_log is not None:
                    self.run_log.warn(f"no {self.target_type} targets: {exc}")
                return []
        try:
            return target_ctx.get_targets(self.target_type)
        except Exception as exc:
            if self.run_log is not None:
                self.run_log.warn(f"no {self.target_type} targets: {exc}")
            return []

    def _render(self, raw: RawResult) -> dict:
        """把一次采集结果渲染为结果行：通道级错误优先，否则交给解析策略。

        规则：
        - ``raw.error`` 非空（连接 / 凭据 / 依赖缺失）：直接产出失败行
        - 否则调用 ``_parse`` 解析，用其 (字段, ok, reason) 产出结果行
        """
        if raw.error:
            return self._build_row(raw, {}, False, raw.error)
        fields, ok, reason = self._parse(raw)
        return self._build_row(raw, fields, ok, reason)

    def _parse(self, raw: RawResult) -> tuple:
        """解析入口（默认按单一解析器），可被 Redfish 通道覆写为按识别类型选择。

        默认实现直接调用 ``self.parser.parse(raw)``；未配置解析器时返回
        统一失败原因。RedfishQueryStrategy 会覆写本方法，依据
        ``raw.device_type`` 从 ``parsers_by_type`` 中挑选 CPU / NPU 解析器
        """
        if self.parser is None:
            return {}, False, "no parser configured for device"
        return self.parser.parse(raw)

    def _build_row(self, raw: RawResult, fields: dict, ok: bool, reason: str) -> dict:
        """归一化结果行：通用字段（target/host/ip/type/ok/reason） + 解析字段。

        - ``target`` 取目标标签（label，即 name 或 host）
        - ``host`` / ``ip`` 均填目标 host，保证合并层与报告层取哪名都能用
        - ``type`` 取 ``raw.target_type``，缺省回退到策略声明的 ``target_type``
        - 最后合并解析器产出的版本字段（键值对覆盖写入）
        """
        target = raw.target
        row = {
            "target": target.label(),
            "host": target.host,
            "ip": target.host,
            "type": raw.target_type or self.target_type,
            "ok": ok,
            "reason": reason,
        }
        row.update(fields or {})
        return row
