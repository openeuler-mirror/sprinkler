"""Sprinkler 升级功能包。

子模块：
- ``credentials`` — 凭据解密工具：从 blade 行解密 BMC/OS 密码、转 ConnectionTarget
- ``channels``    — 升级通道（Redfish / SSH CLI / SOL）
- ``firmware``    — 固件包仓库与校验
- ``orchestrator`` — 升级编排引擎
- ``scheduler``   — 调度与并发控制
- ``strategy``    — 升级策略
- ``validator``   — 参数与兼容性校验
"""
