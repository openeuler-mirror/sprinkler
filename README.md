# Sprinkler

洒水车，面向NPU集群的自动化巡检与运维工具框架，让集群运行的道路保持干净，扫除一切障碍。

## 覆盖机型
PoD、Server、PC16、A+X 大禹/银河、850E和QEMU 仿真平台等

## 目录树
Sprinkler

健康检测[全流程](https://open.codehub.huawei.com/innersource/LingquInfra_G/Sprinkler/wiki?categoryId=8&sn=WIKI2026071611892713)：
  - [urma打流](https://open.codehub.huawei.com/innersource/LingquInfra_G/Sprinkler/wiki?categoryId=8&sn=WIKI2026062411586084)
  - [ubmem打流](https://wiki.huawei.com/domains/186822/wiki/395174/WIKI2026070911794519)
  - [集群hccl test](https://open.codehub.huawei.com/innersource/LingquInfra_G/Sprinkler/wiki?categoryId=8&sn=WIKI2026060311325596)
  - [torchrun通信验证](https://open.codehub.huawei.com/innersource/LingquInfra_G/Sprinkler/wiki?categoryId=8&sn=WIKI2026062511589929)

## 健康检查命令
```shell
# 安装
pip install -e Sprinkler

sprinkler -env-check
sprinkler -device-online
sprinkler -ping-check
sprinkler -urma-bandwidth
sprinkler -cluster-bw
sprinkler -cluster-mpi
```

## 快速定位命令
```shell
sprinkler -npu-bash       # 解npu登录root权限
sprinkler -npu-ip         # 打印npu的ip
sprinkler -no-pass        # 配置NPU免密
sprinkler -pairs          # 打印eid对
sprinkler -urma-cmd h2d 0-7   # urma打流命令快速生成
```
