"""固件包仓库管理 —— 文件系统 + 数据库双重管理。

固件包物理文件存储在 sprinkler/checker/upgrade/firmware/<component>/ 目录下，
元数据登记到 firmware_package 表。

仓库根目录由 FIRMWARE_DIR 常量定义，默认为 sprinkler/checker/upgrade/firmware/。
"""

import hashlib
import shutil
from pathlib import Path

from sprinkler.db import dao
from sprinkler.db.models import COMPONENT_TYPES


FIRMWARE_DIR = Path(__file__).resolve().parent / "firmware"


def ensure_firmware_dirs():
    """确保固件包仓库目录结构存在（幂等）。"""
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    for component in COMPONENT_TYPES:
        (FIRMWARE_DIR / component).mkdir(exist_ok=True)


def compute_md5(file_path, chunk_size: int = 65536) -> str:
    """计算文件 MD5。

    :param file_path: 文件路径（str 或 Path）
    :param chunk_size: 分块读取大小
    :return: 32 位十六进制 MD5 摘要
    """
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def register_firmware_package(conn, local_path: str, component: str,
                              device_type: str = "all", version: str = "",
                              description: str = "", copy: bool = True) -> int:
    """登记固件包到数据库并（可选）复制到仓库目录。

    :param conn: sqlite3.Connection
    :param local_path: 本地固件包文件路径
    :param component: 组件类型（须在 COMPONENT_TYPES 中）
    :param device_type: 适用设备（cpu/npu/sfu/roce/server/all）
    :param version: 目标版本号
    :param description: 版本说明
    :param copy: True 则复制文件到仓库目录，False 则仅记录路径
    :return: package_id
    :raises FileNotFoundError: local_path 不存在
    :raises ValueError: component 不合法（由 DAO 抛出）
    """
    if component not in COMPONENT_TYPES:
        raise ValueError(f"非法 component: {component}")
    ensure_firmware_dirs()
    src = Path(local_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"固件包文件不存在: {local_path}")

    if copy:
        dst = FIRMWARE_DIR / component / src.name
        if not dst.exists():
            shutil.copy2(str(src), str(dst))
        rel_path = str(dst.relative_to(FIRMWARE_DIR.parent))
    else:
        rel_path = str(src)

    md5 = compute_md5(src)
    file_size = src.stat().st_size

    return dao.create_firmware_package(
        conn, name=src.name, component=component, device_type=device_type,
        version=version, file_path=rel_path, file_size=file_size,
        md5_sum=md5, description=description,
    )


def resolve_firmware_path(conn, package_id: int) -> str:
    """从 package_id 解析固件包的本地绝对路径。

    :param conn: sqlite3.Connection
    :param package_id: firmware_package.id
    :return: 绝对路径
    :raises ValueError: package 不存在或文件缺失
    """
    pkg = dao.get_firmware_package(conn, package_id)
    if pkg is None:
        raise ValueError(f"firmware package not found: id={package_id}")
    # file_path 可能是相对路径（相对于 sprinkler/checker/upgrade/）或绝对路径
    path = Path(pkg["file_path"])
    if not path.is_absolute():
        path = FIRMWARE_DIR.parent / path
    if not path.exists():
        raise ValueError(f"firmware file missing: {path}")
    return str(path)


def list_packages_for_components(conn, components: list) -> dict:
    """列出指定组件类型的可用固件包。

    :param conn: sqlite3.Connection
    :param components: 组件类型列表
    :return: {component: [package_dict, ...]}
    """
    result = {}
    for comp in components:
        result[comp] = dao.list_firmware_packages(conn, component=comp)
    return result
