# UBOE 包文件结构与函数说明

> sprinkler/auto/bandwidth/uboe/

本包负责 **UBOE 打流**（以太参数面 bandwidth）的核心编排：device 侧目标发现、组对规划、命令构建、执行侧解析、前置环境检查、流量编排与结果接入。分层结构镜像 `urma/` 包；**通用执行层（进程/ssh）与结果解析直接复用 `urma/executor.py` 和 `urma/results.py`**，不重复实现。

---

## 目录

- [1. `__init__.py`](#1-__init__py)
- [2. `commands.py` — UBOE listener/client 命令构建](#2-commandspy--uboe-listenerclient-命令构建)
- [3. `show_resolver.py` — 执行侧寻址解析](#3-show_resolverpy--执行侧寻址解析)
- [4. `pairs.py` — UBOE 组对规划](#4-pairspy--uboe-组对规划)
- [5. `discovery.py` — device 侧目标自动发现](#5-discoverypy--device-侧目标自动发现)
- [6. `env.py` — device 侧前置检查与 `-uboe-env-check`](#6-envpy--device-侧前置检查与-uboe-env-check)
- [7. `planner.py` — UBOE 打流编排入口](#7-plannerpy--uboe-打流编排入口)
- [依赖关系图](#依赖关系图)

---

## 背景：UBOE 与 URMA 的关键差异

- **UBOE ip（bond ip）只在 device↔device 之间互通**，host 无法直接连。因此进 device 用 `execute_eid`（该卡的 **eid32**，系统 `ssh -tt -o BatchMode=yes root@<eid32>` 经 ~/.ssh/config 解析），在 device 内读 `ethc0d1e3`/bond 拿到 **bond_ip（uboe ip）** 填 `--sip/--dip/-S`。
- 打流参数只需传 **npu 编号**（`-pair 0-1` / `-rank`），bond ip 自动发现，`-bond-ip` 可覆盖。
- 打流前置检查在 **device 侧** 执行（`-uboe-env-check` 独立命令 + 打流前预检），不是 host 侧 ping。

---

## 1. `__init__.py`

空包标识文件，仅含包级 docstring，无任何内容。

---

## 2. `commands.py` — UBOE listener/client 命令构建

### 常量

| 常量 | 作用 |
|---|---|
| `UBOE_PORT_DEFAULT = 62600` | 默认端口（wiki 示例端口；经 `-uboe-port` 覆盖） |
| `UBOE_PORT_MIN / UBOE_PORT_MAX` | 端口合法范围 1~65535 |
| `UBOE_REQUIRED_FIELDS` | 命令可执行所必需的 pair 字段（client_udma/server_udma/client_bond_ip/server_bond_ip） |

### `UboePortPlanner`

端口规划器。UBOE 端口为**扁平 base+index 区间**（无 URMA 的 kind 偏移）。

| 方法 | 作用 |
|---|---|
| `port(pair, index)` | 计算端口号（port_base + index） |
| `port_in_range(port)` | 检查端口是否在 1~65535 范围内 |

### `UboeCommandBuilder`

将 UBOE pair 构建为 `urma_perftest` 的 listener/client 命令行，命令带 `--uboe`、`-P`、`--sip/--dip`。

| 方法 | 作用 |
|---|---|
| `listener(pair, port)` | 构建 **server** 侧监听命令（**无 `-S`**）：`urma_perftest {mode} -d {server_udma} -P {port} --uboe {opts} --sip {server_bond_ip} --dip {client_bond_ip}` |
| `client(pair, port)` | 构建 **client** 侧命令（加 `-S {server_bond_ip}`） |
| `not_ready_reason(pair, index)` | 检查 pair 是否就绪，返回 `(reason, port)`；reason 为空表示可执行 |
| `command_item(...)` | 封装单条命令结果，带 `uboe=True`/`uboe_port`/`uboe_mode`/`uboe_duration`/`ready`/`reason` 等元数据 |
| `plan_commands(pairs)` | 对 pairs 生成完整命令项列表（listener + client 或 not_executable） |

> 黄金命令形状（用户确认标准）：
> ```
> # server/listener（无 -S）
> urma_perftest write_bw -d <server_udma> -P <port> --uboe <opts> --sip <server_bond_ip> --dip <client_bond_ip>
> # client
> urma_perftest write_bw -d <client_udma> -S <server_bond_ip> -P <port> --uboe <opts> --sip <client_bond_ip> --dip <server_bond_ip>
> ```

---

## 3. `show_resolver.py` — 执行侧寻址解析

### `UboeShowResolver`

镜像 `urma/show_resolver.py` 的 `fill`+`side` 接口，使通用 `urma/planner.py` 的 `UrmaTrafficRunner` 注入复用。UBOE 的 per-card 字段（udma=`udmac0d1e3`、execute_eid、bond_ip）已在规划阶段填好，故 `fill` 为透传（no-op）。

| 方法 | 作用 |
|---|---|
| `fill(pairs)` | 透传返回 pairs（字段已规划，无需解析） |
| `side(pair, role)` | 构造 side 描述字典：`kind="local"`、`host/execute_eid` = 该角色 eid32；供 `UrmaProcessSession` 走**系统 ssh 本地执行**（经 `npu_side_command` → `ssh root@<eid32>`） |

---

## 4. `pairs.py` — UBOE 组对规划

### 常量

| 常量 | 作用 |
|---|---|
| `UBOE_DEFAULT_UDMA = "udmac0d1e3"` | 参数面 DMA 通道（eth3 bond 项） |

### 函数

| 函数 | 作用 |
|---|---|
| `uboe_side(rank, target, machine="local")` | 构造单侧字典：`execute_eid`（登录地址）+ `bond_ip`（uboe ip）+ `udma` |
| `build_uboe_pair(client_rank, server_rank, targets)` | 组一个 client→server pair；缺 bond ip 标 skipped |
| `plan_uboe_pairs(args)` | **主入口**：自动发现 targets → 返回 `(ranks, pairs)`；支持 `-pair 0-1` 或 `-rank`（默认全部已发现 rank 依次相连） |
| `parse_pair_spec_pairs(pair_spec)` | 解析 `0-1,2-3` 组对规格 |

---

## 5. `discovery.py` — device 侧目标自动发现

### 核心机制

`collect_uboe_targets(args)` 返回 `{rank: {"execute_eid", "npu_ip", "bond_ip"}}`：
1. 对本机 `get_host_and_device_pairs()` 每 rank 的 `remote_eid`：`execute_eid_of` 取 **eid32**（登录地址，系统 ssh 用），`eid_to_ipv4` 解尾部 8 hex 得 npu_ip（仅作报告）。
2. 用 `run_on_device(login, ...)` **系统 ssh 进 device**，`ip addr show dev ethc0d1e3` / bond master 读该卡 **uboe ip**；`-bond-ip` 可覆盖。
3. 无 bond ip 的 rank 不参与。

> **注意**：登录走 **eid32 + 系统 ssh**（`npu_os_command`），**不要**用解码 IPv4 + paramiko（已证实会超时）。

### 函数

| 函数 | 作用 |
|---|---|
| `eid_normalize(eid)` | 去冒号/0x/转小写，规整 eid |
| `eid_to_ipv4(eid)` | 取尾部 8 hex → IPv4（如 `...dfdf0890 → 223.223.8.144`） |
| `execute_eid_of(remote_eid)` | 返回 `RouteConf.format_eid32(remote_eid)`（device 登录地址） |
| `discover_bond_ip(login)` | ssh 进 device，解析 ethc0d1e3 或其 bond master 的 IPv4（uboe ip） |
| `collect_uboe_targets(args)` | **主入口**：返回 `{rank: {execute_eid, npu_ip, bond_ip}}` |

---

## 6. `env.py` — device 侧前置检查与 `-uboe-env-check`

镜像 `planning/ping_check.py` 的预检角色，但**在 device 侧**执行。每张卡只输出两条 UBOE 关键信息：匹配到的参数面**网卡名**（`ethc0d1e3/e4`）与 **uboe bond ip**；打流前置检查另给出每对的 `ping` 命令。

两类消费者：
1. `-uboe-env-check`（独立命令）：每 npu 一行 网卡名 + bond ip；
2. `-urma-bandwidth -mode uboe` 打流前 `precheck_pairs_for_traffic` 门控。

每个参与 npu **只查一次（去重）**，再对每对做 ping。

### 常量

| 常量 | 作用 |
|---|---|
| `DEVICE_TIMEOUT / PING_TIMEOUT` | device 命令 / ping 超时 |
| `PING_OPTIONS` | 默认 `ping -c 3 -W 2 -w 6` |

### 函数

| 函数 | 作用 |
|---|---|
| `run_on_device(connect_addr, command)` | **系统 ssh** 进 device（`npu_os_command` + subprocess），返回 `{ok,rc,stdout,stderr,elapsed}` |
| `card_info(rank, connect_addr, bond_ip)` | 读 device `ifconfig` grep `ethc0d1e[34]`，产出 `{target:npuR, ifaces, bond_ip}`；`ok` = 有网卡且 bond ip 存在 |
| `card_info_text(result)` | 一行展示：`npuR iface=... bond_ip=...` |
| `connectivity_check(client_eid, server_bond_ip)` | client device ping server bond-ip，0% 丢包为通；返回 `(ok, reason, loss, cmd)` |
| `precheck_pairs_for_traffic(args, pairs, run_log)` | **门控**：`_involved_ranks` 去重后每 npu `card_info` 一次，再每对 ping 一次；打 `[PASS/WARN/FAIL] npuR iface=... bond_ip=...` 与 `ping ... cmd=...`，返回 `(ready_pairs, failed_results)` |
| `_involved_ranks(pairs)` | 收集 pairs 两端 rank → `(execute_eid, bond_ip)` 映射，去重 |
| `env_failed_result(pair, reason)` | 构造 env 失败结果（`bandwidth_phase="uboe"`、`execution_status="env_failed"`） |
| `run_uboe_env(args)` | 独立命令逻辑：发现 targets（支持 `-rank` 过滤）→ 每 npu `card_info` |

### `UboeEnvResultView`

`-uboe-env-check` 的日志视图：`npuR iface=... bond_ip=...`，不含原始 stdout。

| 方法 | 作用 |
|---|---|
| `summary(result, level)` | 复用 `card_info_text` 组装一行 |
| `output_blocks(...)` | 过滤含 `[detail]` 的输出块 |

### `uboe_env_execute(args)`

顶层入口：`RunLog` → `run_uboe_env` → `finish_results`。

---

## 7. `planner.py` — UBOE 打流编排入口

镜像 `urma/planner.py` + `core/runner.py` 的编排角色，经**注入 `UrmaTrafficRunner`** 复用通用打流执行/带宽解析/报告。

### 常量

| 常量 | 作用 |
|---|---|
| `UBOE_TRAFFIC_FIELDS` | 参与打流的 pair 必需字段（client_udma/server_udma/client_bond_ip/server_bond_ip） |

### `run_uboe_bandwidth(args, run_log)`

| 步骤 | 作用 |
|---|---|
| 1 | `plan_uboe_pairs` 规划 pairs |
| 2 | `env.precheck_pairs_for_traffic(..., run_log)` device 侧预检，只让就绪 pair 打流 |
| 3 | 过滤缺失 `UBOE_TRAFFIC_FIELDS` 的 pair |
| 4 | `UrmaTrafficRunner(args, run_log, command_builder=UboeCommandBuilder(args), show_resolver=UboeShowResolver(executor)).run(...)` 打流（内部打印命令 + 计时 + collect） |
| 5 | 返回 `env_failed + traffic_results`（tag `bandwidth_phase="uboe"`、`ping=True`） |

> UBOE 从各卡 device 侧打流，**不从 host 预 ping bond ip**（避免误判门槛）。

---

## 依赖关系图

```
commands.py
  └── common (command_result, pair_context)
  └── utils (positive_int_or_default)

show_resolver.py
  └── (无 uboe 包内依赖，仅构造 side 字典)

 discovery.py
   └── env.py (run_on_device)
   └── common (parse_bond_ip_map, RouteConf)

pairs.py
  └── discovery.py (collect_uboe_targets)
  └── common (uboe_pair_result, parse_pair_spec, unique_ints)

env.py
  └── common (command_result, pair_context, role_execute_eid, execution_host)
  └── connect/npu_access (npu_os_command)
  └── base/log + base/results

planner.py
  └── pairs.py (plan_uboe_pairs)
  └── env.py (precheck_pairs_for_traffic)
  └── commands.py (UboeCommandBuilder)
  └── show_resolver.py (UboeShowResolver)
  └── urma/executor.py (UrmaCommandExecutor)   ← 复用
  └── urma/planner.py  (UrmaTrafficRunner)     ← 复用（注入）
```

## 复用边界

| 层 | 归属 | 说明 |
|---|---|---|
| 命令构建 / 解析 / 组对 / 发现 / 预检 | **uboe 自包含** | 差异化逻辑 |
| 进程执行 / 带宽解析 / 报告 | **复用 `urma/`** | `urma/executor.py`、`urma/results.py`、`report.py` |
| 共享工具 | **复用 `common/`** | `common/utils.py`、`common/pairs.py`、`common/route_conf.py` |
