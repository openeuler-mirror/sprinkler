"""Sprinkler checker 模块 —— 版本查询（Version Query）。

版本查询能力位于 ``sprinkler.checker.version.query`` 子包（本文件仅作
包入口与模块级文档）。该子包通过 Redfish / SSH / Frontend 三种通道
批量查询设备固件与软件版本，并具备 HTML 报告生成与预期版本合规检查能力。

业务能力概览：

- **Redfish 通道**：HTTPS + Redfish API 直连 BMC，识别 CPU / NPU 型 BMC
  并采集 iBMC / BIOS / NPU 固件 / CPLD / CSR 等版本字段。
- **SSH 通道**：SSH 连接 CPU 的 OS，采集 /etc/os-release、npu-smi info、
  /usr/local/Ascend/version.info 对应的 OS 版本 / NPU 固件 / HDK 版本。
- **Frontend 通道**：SSH 直连前台交换机（switch，1213 / 1260 F 统一，
  不再区分型号）执行 ``display startup``，解析 CC / CFG 版本。
- **hybrid 模式**：三条通道统一执行后按 IP 合并/分组为结果行，便于整机核对。

架构说明：本模块采用"**注册表驱动 + 策略模式**"设计（详见
``query/registry.py`` 与 ``query/factory.py``）：

- ``query/registry.py``   —— 通道 / 设备 / 字段声明式元数据（单一事实来源）。
- ``query/factory.py``    —— 依据注册表组装"通道 × 解析器"查询策略。
- ``query/base.py``       —— ``QueryStrategy`` 模板方法 / ``Parser`` 抽象。
- ``query/channels/``     —— Redfish 与命令（SSH）通道实现。
- ``query/parsers/``      —— 各设备类型（CPU BMC / NPU BMC / OS / 前台）解析策略。
- ``query/version_query.py`` —— ``-version-query`` 编排入口与 CLI 实现。
- ``query/report.py``     —— HTML 报告生成与合规检查器。

使用方式：命令行 ``sprinkler -version-query <目标参数> [选项]``，或直接
调用 ``query.version_query_execute(args)``。
"""
