"""PRBS 全量打流。

全量打流分以下步骤：
  a) 确保 NPU OS 免密可用，并向每张卡下发 serdestool.bin
  b) NPU 侧所有 macro/lane 开启 prbs31
  c) 5808 前台对所有端口开启 prbs31
  d) 读 NPU 侧误码率结果
  e) 读 5808 侧误码率结果
  f) 关闭 NPU 侧 prbs
  g) 关闭 5808 侧 prbs

当前入口：``sprinkler -prbs``。
"""

from sprinkler.auto.prbs.runner import prbs_exec

__all__ = ["prbs_exec"]
