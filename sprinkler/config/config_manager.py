"""
配置管理模块
功能: 管理MPICH、CANN、PTA测试等路径配置,支持从config.conf读取和交互式输入
配置文件格式: KEY=VALUE (简单键值对)
"""

import sys
from pathlib import Path
from typing import Optional

from sprinkler.utils.utils import default_config_dir


class _BaseConfigManager:
    """配置管理器基类, 提供config.conf的读写和缓存能力"""

    CONFIG_FILE_NAME = "config.conf"

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = default_config_dir()
        self.config_dir = config_dir
        self.config_file = self.config_dir / self.CONFIG_FILE_NAME
        self._config_cache: dict[str, str] = {}

    def _load_config(self) -> bool:
        if not self.config_file.exists():
            return False

        try:
            self._config_cache.clear()

            with self.config_file.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    if "=" in line:
                        key, value = line.split("=", 1)
                        self._config_cache[key.strip()] = value.strip()

            return True
        except Exception:
            return False

    def _save_path_to_config(self, key: str, path: str):
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self._load_config()
        self._config_cache[key] = path

        lines = []
        for k, v in self._config_cache.items():
            lines.append(f"{k}={v}")

        with self.config_file.open("w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")

        print(f"配置已保存到: {self.config_file}")

    def show_config(self):
        if not self.config_file.exists():
            print(f"配置文件不存在: {self.config_file}")
            return

        print(f"\n配置文件: {self.config_file}")
        print("=" * 60)

        self._load_config()

        for key, value in self._config_cache.items():
            print(f"{key}={value}")


class MpirunConfigManager(_BaseConfigManager):
    """MPirun配置管理器"""

    KEY_CANN_PATH = "CANN_PATH"
    KEY_MPIRUN_PATH = "Mpirun_PATH"
    KEY_CLUSTER_CONF = "CLUSTER_CONF_PATH"

    def get_mpich_path(self) -> str:
        return self._get_path(self.KEY_MPIRUN_PATH, "Mpirun")

    def get_cann_path(self) -> str:
        return self._get_path(self.KEY_CANN_PATH, "CANN")

    def get_cluster_conf(self) -> str:
        return self._get_path(self.KEY_CLUSTER_CONF, "集群配置文件")

    def _get_path(self, key: str, display_name: str) -> str:
        if self._load_config():
            path = self._config_cache.get(key)
            if path:
                return path

        path = self._prompt_for_path(display_name)
        self._save_path_to_config(key, path)
        return path

    def _prompt_for_path(self, display_name: str) -> str:
        print(f"\n{'='*60}")
        print(f"需要配置 {display_name} 路径")
        print(f"{'='*60}")

        while True:
            path_input = input(f"请输入 {display_name} 安装路径: ").strip()

            if not path_input:
                print("错误: 路径不能为空,请重新输入")
                continue

            path = Path(path_input).expanduser()

            if not path.exists():
                print(f"警告: 路径不存在: {path}")
                confirm = input("是否仍要使用此路径? (y/n): ").strip().lower()
                if confirm != "y":
                    continue

            return str(path)

    def update_mpich_path(self, path: str):
        self._save_path_to_config(self.KEY_MPIRUN_PATH, path)

    def update_cann_path(self, path: str):
        self._save_path_to_config(self.KEY_CANN_PATH, path)

    def update_cluster_conf(self, path: str):
        self._save_path_to_config(self.KEY_CLUSTER_CONF, path)


class PtaConfigManager(_BaseConfigManager):
    """PTA测试配置管理器"""

    KEY_ENV_PATH = "ENV_PATH"

    def get_env_path(self) -> Optional[str]:
        if self._load_config():
            path = self._config_cache.get(self.KEY_ENV_PATH)
            if path:
                return path

        return self._prompt_for_path()

    def _prompt_for_path(self) -> Optional[str]:
        print(f"\n{'='*60}")
        print("需要配置 PTA 环境使能脚本路径 (ENV_PATH)")
        print("如无需环境使能, 直接回车跳过")
        print(f"{'='*60}")

        path_input = input("请输入环境使能脚本路径(留空跳过): ").strip()
        if not path_input:
            return None

        path = Path(path_input).expanduser()

        if not path.exists():
            print(f"警告: 路径不存在: {path}")
            confirm = input("是否仍要使用此路径? (y/n): ").strip().lower()
            if confirm != "y":
                return None

        result = str(path)
        self._save_path_to_config(self.KEY_ENV_PATH, result)
        return result

    def update_env_path(self, path: str):
        self._save_path_to_config(self.KEY_ENV_PATH, path)


class HixlConfigManager(_BaseConfigManager):
    """HIXL 基准测试配置管理器"""

    KEY_HIXL_PATH = "HIXL_PATH"

    HIXL_CLONE_HELP = """
HIXL (Huawei Xfer Library) 是面向昇腾芯片的单边通信库。

使用 HIXL Benchmark 前需要先获取源码并编译：
  git clone https://gitcode.com/cann/hixl.git
  cd hixl
  source /usr/local/Ascend/cann/set_env.sh   # 加载 CANN 环境
  bash build.sh --examples                     # 编译 Release 版（含 benchmark）

编译产物路径：
  build/benchmarks/comm_benchmark/hixl_comm_bench

脚本路径：
  benchmarks/comm_benchmark/scripts/run_comm_benchmark.py

请输入 hixl 仓库根目录路径（即包含 build.sh 的目录）。
"""

    def get_hixl_path(self) -> str:
        """获取 HIXL 路径，未配置时交互式提示用户输入"""
        return self._get_path(self.KEY_HIXL_PATH, "HIXL")

    def _get_path(self, key: str, display_name: str) -> str:
        if self._load_config():
            path = self._config_cache.get(key)
            if path:
                return path

        path = self._prompt_for_path(display_name)
        self._save_path_to_config(key, path)
        return path

    def _prompt_for_path(self, display_name: str) -> str:
        print(self.HIXL_CLONE_HELP)
        print(f"{'='*60}")
        print(f"需要配置 {display_name} 仓库路径")
        print(f"{'='*60}")

        while True:
            path_input = input(f"请输入 hixl 仓库根目录路径 (留空退出): ").strip()

            if not path_input:
                print("操作已取消")
                sys.exit(1)

            path = Path(path_input).expanduser().resolve()

            # 验证路径下是否存在 build.sh（hixl 仓库标识）
            build_sh = path / "build.sh"
            run_script = path / "benchmarks" / "comm_benchmark" / "scripts" / "run_comm_benchmark.py"

            if not path.exists():
                print(f"错误: 路径不存在: {path}")
                continue

            if not build_sh.exists():
                print(f"警告: 路径下未找到 build.sh，可能不是有效的 hixl 仓库根目录: {path}")
                confirm = input("是否仍要使用此路径? (y/n): ").strip().lower()
                if confirm != "y":
                    continue

            if not run_script.exists():
                print(f"警告: 未找到 benchmark 脚本: {run_script}")
                print("请确认已经执行了 git clone 以及 bash build.sh --examples")
                confirm = input("是否仍要使用此路径? (y/n): ").strip().lower()
                if confirm != "y":
                    continue

            return str(path)

    def update_hixl_path(self, path: str):
        self._save_path_to_config(self.KEY_HIXL_PATH, path)


def get_mpirun_config() -> tuple[str, str]:
    manager = MpirunConfigManager()
    mpich_path = manager.get_mpich_path()
    cann_path = manager.get_cann_path()
    return mpich_path, cann_path
