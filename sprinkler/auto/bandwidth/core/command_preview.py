"""URMA command preview action."""

from types import SimpleNamespace

from sprinkler.auto.bandwidth.planning.eid_pairs import plan_eid_pairs
from sprinkler.auto.bandwidth.urma.show_resolver import UrmaShowResolver
from sprinkler.auto.bandwidth.urma.executor import UrmaCommandExecutor
from sprinkler.auto.bandwidth.urma.commands import UrmaCommandBuilder
from sprinkler.auto.bandwidth.common import display_reason, pair_side_text, execution_host, args_mode_list, unique_ints, XBOARD_MODES, UBOE_MODES, UBOE_XBOARD_MODE
from sprinkler.base.log import RunLog

CHECK_ID = "urma_cmd"

class UrmaCommandAction:
    def __init__(self, args, run_log):
        self.args = args
        self.run_log = run_log

    def _planned(self):
        args = self.args
        has_intra = bool(str(getattr(args, "pair_spec", "") or "").strip())
        has_xboard = bool(str(getattr(args, "xboard_pair_spec", "") or "").strip())
        if not (has_intra and has_xboard):
            return plan_eid_pairs(args, self.run_log)
        intra_args = SimpleNamespace(**{**vars(args), "mode": "d2d"})
        _, intra_ranks, intra_pairs = plan_eid_pairs(intra_args, self.run_log)
        modes = args_mode_list(args)
        xboard_mode = modes[0] if modes and modes[0] in XBOARD_MODES else "d2d"
        values = dict(vars(args))
        values["mode"] = xboard_mode
        values["pair_spec"] = ""
        _, xboard_ranks, xboard_pairs = plan_eid_pairs(SimpleNamespace(**values), self.run_log)
        return (None, unique_ints([*(intra_ranks or []), *(xboard_ranks or [])]),
                [*(intra_pairs or []), *(xboard_pairs or [])])

    def run(self):
        modes = args_mode_list(self.args)
        if modes and modes[0] in UBOE_MODES:
            if modes[0] == UBOE_XBOARD_MODE:
                commands, ranks, pairs = self.run_uboe_xboard()
            else:
                commands, ranks, pairs = self.run_uboe()
        else:
            _, ranks, pairs = self._planned()
            pairs = UrmaShowResolver(UrmaCommandExecutor(self.args)).fill(pairs)
            builder = UrmaCommandBuilder(self.args)
            commands = builder.plan_commands(pairs)
        self._emit(commands, ranks, pairs)

    def run_uboe(self):
        from sprinkler.auto.bandwidth.uboe.pairs import plan_uboe_pairs
        from sprinkler.auto.bandwidth.uboe.commands import UboeCommandBuilder
        ranks, pairs = plan_uboe_pairs(self.args)
        builder = UboeCommandBuilder(self.args)
        return builder.plan_commands(pairs), ranks, pairs

    def run_uboe_xboard(self):
        from sprinkler.auto.bandwidth.uboe.pairs import plan_uboe_xboard_pairs
        from sprinkler.auto.bandwidth.uboe.commands import UboeCommandBuilder
        ranks, pairs = plan_uboe_xboard_pairs(self.args)
        builder = UboeCommandBuilder(self.args)
        return builder.plan_commands(pairs), ranks, pairs

    def _emit(self, commands, ranks, pairs):
        self.run_log.write_results(commands)
        pair_text = ",".join(dict.fromkeys(str(pair.get("kind") or "") for pair in pairs if pair.get("kind")))
        host_text = ",".join(dict.fromkeys(execution_host(pair) for pair in pairs)) or "local"
        self.run_log.info(f"pair={pair_text or 'unknown'} host={host_text} "
                          f"rank={','.join(str(rank) for rank in ranks)} "
                          f"pair_total={len(pairs)} command_total={len(commands)}")
        for item in commands:
            level = "INFO" if item.get("ready") else "WARN"
            role_name = str(item.get("role") or "").lower()
            role = {"listener": "SERVER", "client": "CLIENT", "not_executable": "SKIP"}.get(role_name, "COMMAND")
            side = "server" if item.get("role") == "listener" else "client"
            target = pair_side_text(item, side) or str(item.get("command_target") or "")
            target = target.rsplit(":", 1)[-1]
            parts = [f"{role}:{target}"]
            reason = display_reason(item.get("reason") or item.get("stderr"))
            if reason:
                parts.append(f"reason={reason}")
            if item.get("cmd"):
                parts.append(f"cmd={item.get('cmd')}")
            self.run_log.log(level, " ".join(parts))
        passed = sum(1 for item in commands if self.run_log.formatter.result_ok(item))
        self.run_log.auto_details(CHECK_ID, passed, len(commands))
        self.run_log.finish_log_dir()
        return 0 if passed == len(commands) else 1

def urma_cmd_execute(args):
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    try:
        return UrmaCommandAction(args, run_log).run()
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)
