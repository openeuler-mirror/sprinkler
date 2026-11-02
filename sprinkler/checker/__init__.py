"""Sprinkler checker 包 —— 设备检查（Checker）相关功能集合。

本包承载面向设备的检查 / 巡检能力，当前纳入的第一个业务是**版本查询**
（``version`` 子包）：通过多种通道协议批量采集设备固件与软件版本，用于
产线与运维侧的版本核对。

版本查询支持三种通道：

- ``Redfish``：HTTPS + Redfish API，直连 BMC（Baseboard Management
  Controller，基板管理控制器），采集 iBMC / BIOS / NPU 固件 / CPLD / CSR
  等固件版本。
- ``SSH``：连接到服务器侧的操作系统（OS），采集 OS 发行版、NPU 固件
  （npu-smi info）与 HDK（硬件开发套件）版本。
- ``Frontend``：SSH 直连前台交换机（Frontend Switch，1213 / 1260 F 统一
  为 switch 目标类型），执行 ``display startup`` 解析 CC（启动软件包）与
  CFG（启动配置）版本。

模块内部实现要点：

- 架构采用"**注册表驱动 + 策略模式**"：``registry.py`` 声明通道 / 设备 /
  字段三类元数据（单一事实来源），``factory.py`` 依据注册表把"通道 × 解析器"
  组合为可执行策略，查询编排（``version_query.py``）与报告 / 合规
  （``report.py``）均从注册表读取，从而对新增设备 / 通道保持开放。
- 对外统一入口为 ``-version-query`` 命令行参数，入口函数为
  ``version_query.version_query_execute``，结果会生成 results.json / CSV、
  HTML 报告，并可配合 ``-expected-versions`` 做合规比对。

本包当前包含：

- ``version``：版本查询（Redfish / SSH / Frontend 三种通道）。
- ``status``：设备状态检查。
- ``upgrade``：固件升级通道、编排与校验。
"""
