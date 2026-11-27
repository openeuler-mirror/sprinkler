"""Redfish 通道 —— 通过 HTTPS + Redfish API 采集 BMC 信息。

Redfish 是 DMTF 标准化的服务器管理 API（基于 HTTPS + JSON）。本模块针对
BMC（基板管理控制器）实现版本查询，职责分层如下：

- ``RedfishChannel``：通道层。负责为目标建立已认证的 ``requests.Session``、
  按产品名子串识别 BMC 类型（CPU / NPU / UNKNOWN），并把原始采集结果产出
  为 ``RawResult``。
- ``RedfishReader``：单目标只读访问器。隐藏"会话 / 根地址 / 超时"等连接
  细节，对外只暴露 ``get(path)``，让解析器专心处理资源语义。
- ``RedfishQueryStrategy``：策略层。把 Redfish 通道与 CPU / NPU 解析器
  组合起来：通道识别出 ``bmc_type`` 后选对应解析器取值；无法识别时产出
  失败行（reason='cannot identify BMC type'）

安全说明：BMC 的 Redfish 端点使用自签名证书，因此 ``verify=False`` 是
必须的（HTTPS 传输的机密性仍由 BMC 硬件侧保证）；``InsecureRequestWarning``
在此被静默，而用户名 / 密码仍然通过 HTTPS 加密传输。这是有意的例外，
部署环境限定为公司内网管理面
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

from sprinkler.base.log import RunLog
from sprinkler.checker.version.query.base import QueryStrategy, RawResult

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:  # pragma: no cover
    pass


def _unidentified_reason(raw: RawResult) -> str:
    """构造 BMC 类型无法识别的诊断原因（含实际产品名 / 数据缺失情况）。

    识别请求（SystemOverview）本身失败的状态码已由
    ``RedfishChannel._record_request_error`` 处理；到达解析层时识别请求已
    成功，剩下的不确定性来自：产品名未命中任何识别模式（回显实际
    ProductName 便于排查新 BMC 型号），或 SystemOverview 缺少
    Systems / ProductName 数据。
    """
    reader = getattr(raw, "data", None)
    product = str(getattr(reader, "last_product_name", "") or "") if reader is not None else ""
    if product:
        return f"cannot identify BMC type: ProductName={product!r} 未命中任何识别模式"
    if reader is not None:
        return "cannot identify BMC type: SystemOverview 缺少 Systems/ProductName 数据"
    return "cannot identify BMC type: 无识别数据"


class RedfishReader:
    """单个 BMC 目标的 Redfish 只读访问器。

    封装已认证的 ``requests.Session`` 与 API 根地址（``https://<ip>``），
    对外仅暴露 ``get(path)``。解析器通过它读取任意 Redfish 资源，无需关心
    连接细节；读取完成后由策略层调用 ``close()`` 释放会话。
    """

    # Redfish 资源路径常量（供通道顶层识别 GET 使用）。
    PATH_SYSTEM_OVERVIEW = "/redfish/v1/SystemOverview"

    # Redfish 升级相关路径常量（供升级通道使用）。
    PATH_UPDATE_SERVICE = "/redfish/v1/UpdateService"
    PATH_SIMPLE_UPDATE = "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate"

    # 网络异常分类状态码（负数，与真实 HTTP 状态码 100-599 不冲突）：
    # 由 ``get`` 在请求未获得任何 HTTP 响应时返回，调用方据此细分失败原因。
    STATUS_TIMEOUT = -1   # 请求超时
    STATUS_CONNECT = -2   # 连接失败（TCP 不可达等）
    STATUS_TLS = -3       # TLS / 证书握手失败

    def __init__(self, session, base_url: str, timeout: int):
        """构造访问器。

        参数：
            session: 已认证的 requests.Session
            base_url: 形如 ``https://<ip>`` 的 API 根地址（不含路径）
            timeout: 单次请求超时秒数
        """
        self._session = session
        self._base_url = base_url
        self.timeout = timeout
        # 上次识别请求读到的产品名（无数据 / 缺字段时置空串），供无法识别
        # BMC 类型时回显实际型号，区分"新型号未登记"与"数据缺失"
        self.last_product_name = ""

    def close(self):
        """关闭底层会话，释放连接资源。"""
        self._session.close()

    def get(self, path: str) -> Tuple[Optional[dict], int]:
        """GET 一个 Redfish 资源；返回 ``(数据, 状态)`` 二元组。

        返回语义（调用方据此精确分类失败原因）：
        - HTTP 200：``(解析后的 JSON, 200)``
        - 其余 HTTP 状态码：``(None, 状态码)``，调用方按码区分认证失败
          （401 / 403）、资源缺失（404）、服务端错误（5xx）等类别
        - 网络异常：``(None, 负数分类码)`` —— ``STATUS_TIMEOUT`` /
          ``STATUS_CONNECT`` / ``STATUS_TLS``；0 表示其它未分类的
          ``requests.RequestException``。负数码与真实 HTTP 状态码
          （100-599）不冲突，调用方可放心用 ``status < 0`` 判定网络故障。

        仅捕获 ``requests`` 的网络异常并按类型分类（SSLError 是
        ConnectionError 子类，须先捕获），绝对不使用裸 ``Exception``，
        从而让编程错误（如键拼错）浮出水面而非被吞掉
        """
        try:
            resp = self._session.get(f"{self._base_url}{path}", timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json(), 200
            return None, resp.status_code
        except requests.exceptions.SSLError:
            return None, self.STATUS_TLS
        except requests.exceptions.Timeout:
            return None, self.STATUS_TIMEOUT
        except requests.exceptions.ConnectionError:
            return None, self.STATUS_CONNECT
        except requests.RequestException:
            return None, 0

    def post(self, path: str, body: dict) -> Tuple[Optional[dict], int, str]:
        """POST 一个 Redfish 资源；返回 ``(响应JSON, 状态码, task_monitor_uri)``。

        语义：
        - HTTP 200：同步成功，返回 ``(json, 200, "")``
        - HTTP 202：异步任务已接受，返回 ``(json或{}, 202, Location头值)``
        - 其余 HTTP 状态码：``(None, 状态码, "")``
        - 网络异常：``(None, 负数分类码, "")``，分类码与 ``get()`` 一致

        ``task_monitor_uri`` 非空时，调用方应轮询该 URI 获取异步任务状态。
        """
        try:
            resp = self._session.post(
                f"{self._base_url}{path}",
                json=body, timeout=self.timeout,
            )
            task_uri = resp.headers.get("Location", "")
            if resp.status_code in (200, 202):
                data = resp.json() if resp.text else {}
                return data, resp.status_code, task_uri
            return None, resp.status_code, ""
        except requests.exceptions.SSLError:
            return None, self.STATUS_TLS, ""
        except requests.exceptions.Timeout:
            return None, self.STATUS_TIMEOUT, ""
        except requests.exceptions.ConnectionError:
            return None, self.STATUS_CONNECT, ""
        except requests.RequestException:
            return None, 0, ""

    def patch(self, path: str, body: dict) -> Tuple[Optional[dict], int]:
        """PATCH 资源（如切换启动顺序）；返回 ``(响应JSON, 状态码)``。

        语义与 ``get()`` 一致，仅 HTTP 方法不同。HTTP 204 无内容时返回
        ``({}, 204)``。
        """
        try:
            resp = self._session.patch(
                f"{self._base_url}{path}",
                json=body, timeout=self.timeout,
            )
            if resp.status_code in (200, 204):
                return (resp.json() if resp.text else {}), resp.status_code
            return None, resp.status_code
        except requests.exceptions.SSLError:
            return None, self.STATUS_TLS
        except requests.exceptions.Timeout:
            return None, self.STATUS_TIMEOUT
        except requests.exceptions.ConnectionError:
            return None, self.STATUS_CONNECT
        except requests.RequestException:
            return None, 0

    def get_task_status(self, task_uri: str) -> Tuple[Optional[dict], int]:
        """轮询异步任务状态（GET TaskMonitor URI）。

        复用 ``get()`` 的异常处理逻辑。返回的 JSON 中包含：
        - TaskState: New / Starting / Running / Stopping / Completed /
          Cancelled / Exception / Service
        - TaskStatus: OK / Error / Warning / Critical

        ``task_uri`` 可以是完整 URL（``https://<ip>/redfish/v1/TaskService/...``）
        或相对路径（``/redfish/v1/TaskService/...``），本方法自动适配。
        """
        if task_uri.startswith("https://") or task_uri.startswith("http://"):
            try:
                resp = self._session.get(task_uri, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json(), 200
                return None, resp.status_code
            except requests.exceptions.SSLError:
                return None, self.STATUS_TLS
            except requests.exceptions.Timeout:
                return None, self.STATUS_TIMEOUT
            except requests.exceptions.ConnectionError:
                return None, self.STATUS_CONNECT
            except requests.RequestException:
                return None, 0
        return self.get(task_uri)


class RedfishChannel:
    """Redfish 通道：为目标建立会话，识别 BMC 类型并产出原始结果。

    通道只负责"建立连接 + 识别 + 产出原始数据"，版本字段的提取与成败判定
    交由解析层；识别产出的 ``device_type``（CPU / NPU / UNKNOWN）是解析层
    选择 CPU / NPU 解析器的依据。
    """

    # BMC 类型取值常量：CPU / NPU 为已识别类型，UNKNOWN 表示无法识别。
    CPU_BMC = "CPU"
    NPU_BMC = "NPU"
    UNKNOWN = "UNKNOWN"

    def __init__(self, bmc_patterns=(), timeout: int = 30, parallel: int = 1):
        """构造通道。

        参数：
            bmc_patterns: 有序 ``(产品名子串, BMC 类型)`` 元组序列，用于识别
                CPU / NPU；由工厂从 ``registry.bmc_identification_patterns()``
                注入，首个命中即返回（顺序即优先级）
            timeout: 单次 Redfish 请求超时秒数
            parallel: 批量采集并发数（>1 时使用线程池并行采集，默认串行）
        """
        self.bmc_patterns = tuple(bmc_patterns or ())
        self.timeout = timeout
        self.parallel = max(1, parallel)

    # ------------------------------------------------------------------
    # 通道采集协议（QueryStrategy 调用）
    # ------------------------------------------------------------------
    def collect(self, targets, args=None, run_log: RunLog = None, timeout: int = None) -> list:
        """对一批目标并行（或串行）采集，产出每目标一个 ``RawResult``。

        结果语义：
        - 识别成功：``device_type`` 记录 CPU / NPU，``data`` 为 ``RedfishReader``
        - 识别失败：``device_type=UNKNOWN``，由解析层判为无法识别
        - 建会话 / 取凭据异常：以 ``RawResult.error`` 记录，不中断其它目标；
          命中该字段的行直接判失败（不再进入解析）
        """
        tmo = timeout or self.timeout
        if self.parallel > 1 and len(targets) > 1:
            with ThreadPoolExecutor(max_workers=min(self.parallel, len(targets))) as pool:
                return list(pool.map(lambda t: self._collect_one(t, tmo), targets))
        return [self._collect_one(t, tmo) for t in targets]

    def _collect_one(self, target, timeout: int) -> RawResult:
        """采集单个目标：取凭据 → 建会话 → 建阅读器 → 识别类型。

        各步骤异常被就地捕获并转为 ``RawResult.error``（连接失败 / 凭据缺失 /
        requests 依赖缺失），保证单目标失败不抛到上层、不影响整批其它目标
        """
        row = RawResult(target=target)
        try:
            host, username, password = self._target_creds(target)
        except ValueError as exc:
            row.error = str(exc)
            return row
        if requests is None:
            row.error = "dependency: requests unavailable; pip install requests"
            return row
        sess = self._new_session(username, password)
        reader = RedfishReader(sess, f"https://{host}", timeout)
        row.data = reader
        row.target_type = self._target_kind(target)
        row.device_type, status = self._identify_bmc_type(reader)
        self._record_request_error(row, status)
        return row

    def _build_reader(self, target) -> RedfishReader:
        """为目标建立已认证的 RedfishReader（供子类升级通道复用）。

        :raises ValueError: 目标缺少 host 等必要凭据
        :raises RuntimeError: requests 依赖缺失
        :return: 已认证的 RedfishReader（调用方负责 close()）
        """
        if requests is None:
            raise RuntimeError("dependency: requests unavailable; pip install requests")
        host, username, password = self._target_creds(target)
        sess = self._new_session(username, password)
        return RedfishReader(sess, f"https://{host}", self.timeout)

    def _record_request_error(self, row: RawResult, status: int):
        """把识别请求的失败状态写入结果行错误（成功 / 数据缺失时保持空）。

        分类规则（与 ``RedfishReader.get`` 的返回语义对应）：
        - 401 / 403：认证 / 授权失败（HTTP 状态码）
        - 负数：网络异常（timeout / connect / tls / 其它未分类）
        - 404 / 5xx / 其它正数：Redfish 资源的 HTTP 类失败。

        识别请求（SystemOverview）失败意味着 BMC 类型与全部版本字段均不可得，
        因此这些失败直接判整体失败（``row.error`` 非空时不再进入解析层）
        ``status == 200`` 仅表示请求成功，可能仍因数据缺失返回 UNKNOWN，
        由解析层以携带上下文的 reason 处理
        """
        if status == 401:
            row.error = "auth: BMC 认证失败（账号或密码错误，HTTP 401）"
        elif status == 403:
            row.error = "auth: BMC 授权不足（HTTP 403）"
        elif status == RedfishReader.STATUS_TIMEOUT:
            row.error = f"timeout: BMC 请求超时（>{self.timeout}s 未响应）"
        elif status == RedfishReader.STATUS_CONNECT:
            row.error = "connect: BMC 连接失败（无法建立 TCP / HTTPS 连接）"
        elif status == RedfishReader.STATUS_TLS:
            row.error = "tls: BMC TLS / 证书握手失败"
        elif status == 0:
            row.error = "network: BMC 请求失败（未分类网络异常）"
        elif status == 404:
            row.error = "notfound: BMC Redfish 资源缺失（HTTP 404，BMC 可能不支持该资源）"
        elif status >= 500:
            row.error = f"server: BMC Redfish 服务端错误（HTTP {status}）"
        elif status == 200:
            pass  # 请求成功；可能数据缺失返回 UNKNOWN，由解析层处理
        elif status:
            row.error = f"http: BMC Redfish 请求失败（HTTP {status}）"

    def _target_creds(self, target) -> tuple:
        """从目标提取连接凭据 (host, user, pwd)。

        兼容两种目标形态：``ConnectionTarget`` 直接读取属性；dict 依次取
        ``host`` / ``ip``（以 host 优先），user 缺省为 BMC 默认账号
        ``Administrator``
        """
        if hasattr(target, "host"):
            host = str(getattr(target, "host", "") or "").strip()
            user = str(getattr(target, "user", "") or "Administrator").strip() or "Administrator"
            pwd = str(getattr(target, "password", "") or "")
        else:
            host = str(target.get("host") or target.get("ip") or "").strip()
            user = str(target.get("user") or "Administrator").strip()
            pwd = str(target.get("password") or "")
        if not host:
            raise ValueError("target host is required for BMC version query")
        return host, user, pwd

    def _target_kind(self, target) -> str:
        """从目标元数据提取类型标签（如 cpu_bmc/npu_bmc），缺省为空串。

        目标 meta 中的 ``kind`` 记录该目标的数据来源类型，回填到结果行
        的 ``type`` 字段，供报告与 hybrid 合并归类
        """
        meta = getattr(target, "meta", None)
        if isinstance(meta, dict):
            return str(meta.get("kind", ""))
        return ""

    def _new_session(self, username: str, password: str):
        """创建带基本认证的 requests.Session。

        关闭证书校验（BMC 自签名证书，见模块 docstring 安全说明），设置
        JSON 请求头，并允许复用空闲连接（传输层长连接，降低批量查询开销）
        """
        sess = requests.Session()
        sess.auth = (username, password)
        sess.verify = False  # 自签名 BMC 证书；详见模块 docstring
        sess.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
        return sess

    def _identify_bmc_type(self, reader: RedfishReader) -> tuple:
        """通过 ``/redfish/v1/SystemOverview`` 的 ProductName 识别 BMC 类型。

        过程：GET SystemOverview → 读取 ``Systems[0].ProductName`` → 依次用
        ``bmc_patterns`` 做子串匹配，首个命中即返回对应类型（CPU / NPU）。

        返回：
            ``(bmc_type, status)`` 二元组。``bmc_type`` 为 ``CPU`` / ``NPU``；
            无数据、数据缺失或全部模式未命中时为 ``UNKNOWN``；``status`` 为
            SystemOverview 请求的状态（认证失败 401 / 403、资源 404、服务端
            5xx 或负数网络分类码，由 ``_record_request_error`` 据此写通道级
            错误）。识别请求成功读到产品名时写入 ``reader.last_product_name``，
            供解析层在 UNKNOWN 时回显实际型号
        """
        data, status = reader.get(RedfishReader.PATH_SYSTEM_OVERVIEW)
        if not data:
            reader.last_product_name = ""
            return self.UNKNOWN, status
        systems = data.get("Systems") or []
        if not systems:
            reader.last_product_name = ""
            return self.UNKNOWN, status
        product_name = str(systems[0].get("ProductName", ""))
        reader.last_product_name = product_name
        # 模式按顺序、首匹配优先，来源于 registry（OCP：新增 BMC 产品只需在
        # registry 的 DeviceSpec.bmc_patterns 中追加，无需改动本通道）
        for pattern, bmc_type in self.bmc_patterns:
            if pattern in product_name:
                return bmc_type, status
        return self.UNKNOWN, status


class RedfishQueryStrategy(QueryStrategy):
    """Redfish BMC 查询策略：识别 BMC 类型后选择对应解析器。

    通道产出识别结果（``RawResult.device_type`` 为 CPU / NPU / UNKNOWN）；
    本策略据此从 ``parsers_by_type``（``{bmc_type: Parser}``）中选取解析器
    解析版本字段。无法识别（UNKNOWN）或对应该类型的解析器缺失时，返回
    失败行（reason='cannot identify BMC type'），不会中断对其它目标的查询。
    """

    def __init__(
        self,
        channel: RedfishChannel,
        parsers_by_type: dict,
        target_types: Tuple[str, ...] = ("cpu_bmc", "npu_bmc"),
        args=None,
        run_log: RunLog = None,
        timeout: int = 30,
    ):
        """构造策略。

        参数：
            channel: 已配置识别模式的 RedfishChannel
            parsers_by_type: ``{BMC 类型: Parser 实例}`` 映射，通常包含
                CPU 与 NPU 两个键，与 registry 中 redfish 通道的设备绑定一致
            target_types: Redfish 通道要收集的目标类型（默认 cpu_bmc /
                npu_bmc），目标收集时按 host 去重
            args / run_log / timeout: 透传给基类（运行期配置）
        """
        super().__init__(channel=channel, target_type=target_types[0] if target_types else "",
                         args=args, run_log=run_log, timeout=timeout)
        self.parsers_by_type = parsers_by_type
        self.target_types = tuple(target_types)

    def _collect_targets(self, target_ctx) -> list:
        """收集 Redfish 通道登记的全部目标类型并去重（同 host 只保留第一个）。

        CPU BMC 与 NPU BMC 可能共享同一个 BMC IP（整机管理口），此处按 host
        去重避免对同一设备重复建会话；某目标类型收集失败时仅告警并跳过，
        不阻塞其它类型
        """
        collected, seen_hosts = [], set()
        for target_type in self.target_types:
            try:
                for target in target_ctx.get_targets(target_type):
                    host = getattr(target, "host", None) or ""
                    if host and host in seen_hosts:
                        continue
                    seen_hosts.add(host)
                    collected.append(target)
            except Exception as exc:
                if self.run_log is not None:
                    self.run_log.warn(f"skip target type {target_type}: {exc}")
        return collected

    def query(self, target) -> dict:
        """查询单个目标并返回结果行；临时会话由基类流程自动释放。"""
        # 通道已为单目标采集；此处仅做正常渲染（会话释放由 collect/dispose 完成）
        return super().query(target)

    def _dispose(self, raw: RawResult):
        """释放 Redfish 会话；会话对象缺失或关闭异常时静默返回。

        Redfish 通道为每个目标新建 Session，若不关闭会泄漏连接；此处兜底
        保证无论解析成败都会释放，且不因关闭异常而中断流程
        """
        reader = getattr(raw, "data", None)
        if reader is not None and hasattr(reader, "close"):
            try:
                reader.close()
            except Exception:
                pass

    def _parse(self, raw: RawResult) -> tuple:
        """按识别出的 BMC 类型选择解析器；无法识别或解析器缺失时返回失败。

        规则：已知类型（CPU / NPU）且已注册对应解析器 → 交解析器取值；
        其他情况（UNKNOWN、类型未注册）→ ``ok=False``，reason 附带无法
        识别的具体上下文（实际 ProductName / 数据缺失情况）
        """
        parser = self.parsers_by_type.get(raw.device_type)
        if parser is None:
            return {}, False, _unidentified_reason(raw)
        return parser.parse(raw)
