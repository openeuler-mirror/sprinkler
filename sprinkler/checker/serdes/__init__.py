"""Sprinkler checker 模块 —— SerDes 检查。

当前包含 NPU 端口 PN 极性查询（``-pn-check``）：在每台本机 NPU 的 OS 上
执行 ``serdestool.bin pn``，按“NPU × 端口 × tx/rx”渲染极性矩阵。

``serdestool.bin`` 约定位于 NPU OS 的 ``/home/serdestool.bin``；缺失时由
:mod:`sprinkler.checker.serdes.tool` 从 CPU OS 上传，源路径取
``-serdes-src`` / ``sprinkler_config/config.conf`` 的 ``SERDES_TOOL_PATH``
或交互式输入。

入口：

- CLI：``sprinkler -pn-check -udie 0 -port-mode x2 -ports 0-3``，即
  ``pn_check_execute(args)``，结果写入 results.json / results.csv，
  并在终端打印矩阵。
"""

from sprinkler.checker.serdes.pn import pn_check_execute

__all__ = ["pn_check_execute"]
