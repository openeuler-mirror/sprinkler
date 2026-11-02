from sprinkler.base.log import RunLog
from sprinkler.auto.runtime.check.check_env import CheckEnv, resolve_env
from sprinkler.auto.runtime.check.check_runner import CheckRunner


class CheckBuilder:
    """建造者：组装 CheckEnv + check_items → CheckRunner 实例。

    用法：
        checker = (CheckBuilder()
            .env(ENV_NPU1213)
            .args(args)
            .run_log(run_log)
            .check_items(["card", "port"])
            .build())
        results = checker.check_batch()
    """

    def __init__(self):
        self._env: CheckEnv | None = None
        self._args = None
        self._run_log: RunLog | None = None
        self._item_names: list[str] | None = None

    def env(self, env: CheckEnv) -> "CheckBuilder":
        self._env = env
        return self

    def env_name(self, name: str) -> "CheckBuilder":
        self._env = resolve_env(name)
        return self

    def args(self, args) -> "CheckBuilder":
        self._args = args
        return self

    def run_log(self, run_log: RunLog) -> "CheckBuilder":
        self._run_log = run_log
        return self

    def check_items(self, item_names: list[str]) -> "CheckBuilder":
        self._item_names = item_names
        return self

    def build(self) -> CheckRunner:
        if self._env is None:
            raise ValueError("必须通过 .env() 或 .env_name() 指定检查环境")
        return CheckRunner(
            env=self._env,
            args=self._args,
            run_log=self._run_log,
            check_items=self._item_names,
        )
