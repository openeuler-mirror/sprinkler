"""升级目标凭据解密 —— 从 blade 行解密 BMC/OS 密码，转 ConnectionTarget。

blade 表的 bmc_password/os_password 在数据库中为 AES 加密的 base64 字符串
（由 importer.py 的 encrypt_password() 写入）。本模块复用 password_store.decrypt_password()
解密，不引入任何新的加解密逻辑。
"""

from sprinkler.utils.password_store import decrypt_password


def decrypt_blade_credentials(blade_row: dict) -> dict:
    """从 blade 行解密 BMC 和 OS 凭据，返回新 dict（不修改原行）。

    :param blade_row: blade 表行（dict 或 sqlite3.Row），含加密的 bmc_password/os_password
    :return: 新 dict，bmc_password/os_password 已解密为明文；空密码原样返回空串
    """
    result = dict(blade_row)
    if result.get("bmc_password"):
        result["bmc_password"] = decrypt_password(result["bmc_password"])
    if result.get("os_password"):
        result["os_password"] = decrypt_password(result["os_password"])
    return result


def blade_to_connection_target(blade_row: dict, kind: str = "cpu_bmc"):
    """将 blade 行转换为 ConnectionTarget（含密码解密）。

    :param blade_row: blade 表行（dict 或 sqlite3.Row）
    :param kind: 目标类型，决定使用 BMC 还是 OS 凭据：
        - ``cpu_bmc`` / ``npu_bmc`` / ``sfu_bmc`` / ``roce_bmc`` / ``cpu_1213`` / ``npu_1213`` → BMC 凭据
        - ``cpu_os`` → OS 凭据
    :return: sprinkler.connect.targets.ConnectionTarget
    """
    from sprinkler.connect.targets import ConnectionTarget

    decrypted = decrypt_blade_credentials(blade_row)

    # BMC 目标用 bmc_ip；OS 目标用 os_ip
    if kind.endswith("_os"):
        host = decrypted.get("os_ip", "")
        user = decrypted.get("os_user", "root")
        password = decrypted.get("os_password", "")
        port = decrypted.get("os_port", 22)
    else:
        host = decrypted.get("bmc_ip", "")
        user = decrypted.get("bmc_user", "Administrator")
        password = decrypted.get("bmc_password", "")
        port = 22

    return ConnectionTarget(
        name=host,
        host=host,
        port=port,
        user=user,
        password=password,
        meta={
            "kind": kind,
            "blade_id": blade_row.get("id"),
            "device_type": blade_row.get("device_type", ""),
            "cabinet_id": blade_row.get("cabinet_id"),
        },
    )
