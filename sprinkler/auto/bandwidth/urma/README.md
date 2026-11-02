# URMA 包文件结构与函数说明

> sprinkler/auto/bandwidth/urma/

本包负责 URMA 打流的核心编排：命令构建、show 数据解析、EID 解析、进程执行、流量运行、结果解析。

---

## 1. `__init__.py`

空包标识文件，无任何内容。

---

## 2. `commands.py` — URMA  listener/client 命令构建

### `UrmaPortPlanner`

端口规划器，根据 pair 的 kind（h2d/d2h/d2d/xboard）计算监听端口号。

| 方法 | 作用 |
|---|---|
| `port(pair, index)` | 根据 kind 和索引计算实际端口号（port_base + kind_offset + index） |
| `port_in_range(port)` | 检查端口是否在 1~65535 范围内 |
| `kind(pair)` | 从 pair 的 kind/scope 字段推断流量类型（h2d/d2h/d2d/xboard） |

### `UrmaCommandBuilder`

将 EID pair 构建为 urma_perftest 的 listener/client 命令行。

| 方法 | 作用 |
|---|---|
| `listener(pair, port)` | 构建 server 侧监听命令（urma_perftest -d ... --port ... --eid_idx ...） |
| `client(pair, port)` | 构建 client 侧连接命令（urma_perftest -d ... -S ... --port ... --eid_idx ... --bind_ip ...） |
| `plan_commands(pairs)` | 对 pairs 列表生成完整的命令项列表（含 listener 和 client） |
| `command_items(pair, index)` | 为单个 pair 返回 1~2 条命令项；不可执行时返回 not_executable 项 |
| `not_ready_reason(pair, index)` | 检查 pair 是否就绪，返回 (reason, port)；reason 为空表示可执行 |
| `command_item(...)` | 封装单条命令结果，带 ready/reason 等元数据 |
| `not_executable(pair, reason, port)` | 生成不可执行 pair 的错误结果 |
| `missing_command_fields(pair)` | 检查命令所必需的 UDMA/EID 字段是否缺失 |
| `command_extra(pair, port, ready, reason)` | 构造命令项的额外字段（urma_port/urma_mode/duration 等） |
| `opts_for_pair(pair)` | 处理 urma_opts 参数，-single 模式下去除 -B 选项 |

---

## 3. `show_parser.py` — 解析 `urma_admin show` 文本输出

### `UrmaShowParser`

将 `urma_admin show` 的原始输出解析为 EID→UDMA/EID_idx/EID32 的映射字典。

| 方法 | 作用 |
|---|---|
| `parse(text)` | 接收原始文本，清理 ANSI/Control 字符后逐行解析，返回 `{eid32: {udma, eid_idx, eid32, line}}` 字典 |

正则说明：
- `UDMA_RE`：匹配 udma 开头的网卡名
- `EID_RE`：匹配 eid 开头的字段名
- `EID_IDX_RE`：匹配 eid_idx 后跟的数字
- `ADDR_RE`：匹配 IPv6 格式的 EID 地址
- `ANSI_RE`/`CTRL_RE`：清理终端颜色码和控制字符

---

## 4. `show_resolver.py` — 通过 urma_admin show 解析 EID 数据并填充 pair

### `UrmaShowResolver`

对每个 pair 的 client/server 两侧执行 `urma_admin show`，提取 UDMA 和 EID_idx 信息并回填到 pair 字典中。

| 方法 | 作用 |
|---|---|
| `fill(pairs)` | 主入口：遍历 pairs，对每个 ok 的 pair 调用 show_map 解析两侧 EID 数据并更新 pair 的 udma/eid_idx 字段 |
| `show_map(side)` | 对单个 side（client 或 server）执行 show 命令，结果缓存；失败记录到 failures |
| `side(pair, role)` | 根据 pair 和角色（client/server）构造 side 描述字典，含 kind（local/ssh）、label、host、rank、execute_eid 等 |

---

## 5. `executor.py` — URMA 进程管理和本地/远程执行

### `UrmaStreamLog`

将进程 stdout/stderr 写入日志文件。

| 方法 | 作用 |
|---|---|
| `start()` | 写入启动时间戳和命令 |
| `append(text)` | 追加输出文本 |
| `finish(rc)` | 写入结束时间戳和返回码 |

### `UrmaProcessBuffer`

收集进程 stdout/stderr，从输出中提取 PID 和 RC。

| 方法 | 作用 |
|---|---|
| `add_stdout(data)` | 追加 stdout，检测并提取 PID 和 RC |
| `add_stderr(data)` | 追加 stderr |
| `clean_output(text)` | 去除输出中的 SPRINKLER_PROCESS_PID/RC 行 |

### `UrmaLocalProcessHandle`

在本地 shell 中启动子进程并异步读取输出。

| 方法 | 作用 |
|---|---|
| `start()` | 通过 subprocess.Popen 启动本地进程 |
| `read()` | 异步读取 stdout 直到进程结束 |
| `stop()` | 发送 SIGTERM 终止进程 |
| `wait()` | 等待进程退出 |
| `kill()` | 强制 kill 进程 |

### `UrmaRemoteProcessHandle`

通过 SSH 在远端 NPU 上启动进程。

| 方法 | 作用 |
|---|---|
| `start()` | 建立 SSH session 并 exec_command 启动进程 |
| `read()` | 异步读取 channel 输出，检测退出状态 |
| `stop()` | 通过 kill 命令终止远端 PID |
| `wait()` | 等待 reader 线程结束 |
| `close()` | 关闭 channel 和 client |

### `UrmaProcessSession`

封装单次命令执行的生命周期（start/stop/collect）。

| 方法 | 作用 |
|---|---|
| `start()` | 调用 handle.start() 并返回启动结果 |
| `stop()` | 调用 handle.stop() 停止进程 |
| `collect()` | 等待进程结束，收集 stdout/stderr/rc，构造最终 command_result |
| `result(...)` | 构造标准 command_result 字典 |

### `UrmaCommandExecutor`

对外执行 `urma_admin show` 等管理命令。

| 方法 | 作用 |
|---|---|
| `run(side, command)` | 根据 side.kind 分发到 run_local 或 SSH 远程执行 |
| `connect(target)` | 建立到远端的 SSH 连接 |
| `run_local(side, command, display_command)` | subprocess.run 本地执行命令，带超时控制 |

---

## 6. `planner.py` — URMA 打流编排（流量测量顶层调度）

### 工具函数

| 函数 | 作用 |
|---|---|
| `log_traffic_plan(run_log, ...)` | 打印打流计划表（按 group/role 分组），供日志查阅 |
| `traffic_plan_rows(command_builder, pair, ...)` | 为单个 pair 生成 server/client 两条 plan row |
| `traffic_plan_row(...)` | 构造单条 plan row 字典 |
| `stream_log_path(run_log)` | 从 run_log.path 推出 streams 子目录 |
| `create_stream_log(run_log, ...)` | 为指定 session 创建 UrmaStreamLog 对象 |

### `UrmaFlowSession`

封装单个 pair 的打流生命周期（server 启动→client 启动→等待时长→collect 结果）。

| 方法 | 作用 |
|---|---|
| `__init__()` | 检查 pair 是否就绪，决定是否直接标记为 fail |
| `start(role)` | 启动 server 或 client 进程，记录结果 |
| `stop()` | 停止 client 和 server 进程 |
| `collect()` | 收集两端进程结果，构造 traffic_pair_result |

### `UrmaTrafficRunner`

打流顶层编排器，协调 show_resolver、command_builder、executor、flow_sessions。

| 方法 | 作用 |
|---|---|
| `__init__()` | 初始化 executor/command_builder/show_resolver，设置 link_mode 和 stagger |
| `run(pairs)` | 主流程：fill 解析 → log 计划 → start_sessions → sleep duration → stop + collect → 返回结果列表 |
| `start_sessions(sessions)` | 先启动所有 server，再启动所有 client，按 host 分组并发 |
| `run_start_step(sessions, role)` | 按 host 分组并发启动指定 role 的进程 |
| `run_concurrent(items, action)` | 使用 ThreadPoolExecutor 并发执行 action |
| `record_step(step, side, ...)` | 记录单步结果到 run_log（start/stop/collect 各阶段的详细日志） |

---

## 7. `results.py` — URMA 带宽结果解析与展示

### 正则常量

| 常量 | 作用 |
|---|---|
| `BANDWIDTH_NUMBER_RE` | 匹配浮点数 |
| `BANDWIDTH_HEADER_RE` | 匹配 "BW_average" 表头行 |
| `URMA_FATAL_RE` | 匹配致命错误关键词 |
| `URMA_BENIGN_WARNING_RE` | 匹配可忽略的警告信息 |
| `URMA_STOP_DIAGNOSTIC_RE` | 匹配不应中断的诊断信息行 |

### 结果函数

| 函数 | 作用 |
|---|---|
| `bandwidth_summary(text, tail)` | 从 urma_perftest 输出中提取所有 BW 样本，计算平均值/最小/最大 |
| `bandwidth_sample_average(line)` | 解析单行输出，提取第 4 个数值作为带宽样本值 |
| `fatal_output_reason(*texts, has_bandwidth)` | 在 stdout/stderr 中搜索致命错误，排除 benign 警告 |
| `machine_pair(pair)` | 从 pair 提取 client_machine 和 server_machine 组成 "A->B" 字符串 |
| `fail_pair(pair, reason, link_mode)` | 构造 pair 失败时的结果（未启动/配置错误） |
| `empty_process_result(reason, rc)` | 构造进程未启动的空结果 |
| `traffic_pair_result(pair, command, ok, client_result, ...)` | **核心**：合并 client/server 的 stdout，解析带宽数据，判断 traffic_ok，构造最终结果 |
| `merge_bw(client_bw, server_bw)` | 合并两端的带宽统计（加权平均） |
| `bw_fields(prefix, summary)` | 生成 bw_samples/average/min/max 字段字典 |
| `side_outputs(*items)` | 将 client/server 的输出合并为带分隔符的字符串 |

### `UrmaBandwidthResultView`

将 traffic_pair_result 转换为适合日志格式的展示文本。

| 方法 | 作用 |
|---|---|
| `summary(result, level)` | 流量结果走 traffic_summary，命令结果走 command_view.summary |
| `output_blocks(result, ok, show_output)` | 过滤掉含 [detail] 的块，返回 (level, prefix, text, ...) 列表 |
| `traffic_summary(result)` | 构造人可读的打流结果摘要行，含 kind/link/ping/flow/client/server 带宽数据 |
| `traffic_label(kind, ...)` | 将 kind（xboard_d2d/intra_d2d/h2d/d2h）映射为中文标签 |

---

## 依赖关系图

```
commands.py
  └── planner.py (UrmaCommandBuilder)
  └── show_resolver.py (UrmaShowResolver)
  └── executor.py (UrmaCommandExecutor)

show_parser.py
  └── show_resolver.py (UrmaShowParser 实例)

show_resolver.py
  └── executor.py (UrmaCommandExecutor.run)
  └── show_parser.py

executor.py
  └── (无 urma 包内依赖，纯底层进程管理)

planner.py
  └── commands.py (UrmaCommandBuilder, UrmaPortPlanner)
  └── show_resolver.py (UrmaShowResolver)
  └── executor.py (UrmaProcessSession, UrmaStreamLog, UrmaCommandExecutor)
  └── results.py (traffic_pair_result, fail_pair, empty_process_result, machine_pair, ...)

results.py
  └── (无 urma 包内依赖，纯结果处理)
```
