"""升级通道选择器 —— 根据组件类型、场景和策略选择升级通道。"""


def select_channel(component: str, scenario: str, strategy: str = "auto") -> str:
    """选择升级通道名称。

    :param component: 组件类型（ibmc/bios/cpld/npu_firmware/cc/cfg/telnet_crack）
    :param scenario: 场景（initial/daily/batch）
    :param strategy: 策略（auto/redfish/ssh_cli/sol）
    :return: 通道名称（redfish/ssh_cli/sol）

    规则（strategy=auto 时）：
    1. UBM 组件(cc/cfg) + 开局场景 → "sol"
    2. UBM 组件(cc/cfg) + 日常/批量 → "ssh_cli"（前台 SSH 直连，走命令通道）
    3. telnet_crack → "ssh_cli"（BMC SSH）
    4. BMC 组件 + 开局 → "ssh_cli"（开局时 BMC Redfish 可能未就绪）
    5. BMC 组件 + 日常/批量 → "redfish"
    """
    if strategy != "auto":
        return strategy

    if component in ("cc", "cfg"):
        return "sol" if scenario == "initial" else "ssh_cli"
    if component == "telnet_crack":
        return "ssh_cli"
    if scenario == "initial":
        return "ssh_cli"
    return "redfish"


def supports_fallback(component: str, scenario: str) -> bool:
    """判断该组件+场景组合是否支持 Redfish→SSH CLI 降级。

    仅 BMC 组件（ibmc/bios/cpld/csr/npu_firmware）在非开局场景下支持降级。
    """
    if component in ("cc", "cfg", "telnet_crack"):
        return False
    if scenario == "initial":
        return False
    return True
