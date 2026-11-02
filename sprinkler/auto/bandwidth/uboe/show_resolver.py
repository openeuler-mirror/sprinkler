"""Execution-side resolver for UBOE pairs.

Mirrors `urma/show_resolver.py`'s interface (`fill` + `side`) so the generic
`urma/planner.py` `UrmaTrafficRunner` can be reused unchanged apart from injection.

Unlike URMA (which resolves udma/eid_idx from `urma_admin show`), UBOE's per-card
fields (udma = `udmac0d1e3`, execute_eid = the card's eid32, bond_ip = the uboe ip)
are already filled during planning (`uboe/pairs.py` / `uboe/discovery.py`). So `fill()`
is a no-op pass-through; `side()` just yields the side dict used to enter each card's
device. Local cards (no peer `cpu_host`) run system `ssh root@<execute_eid>` locally
(kind="local"); peer cards in `-mode uboe-xboard` carry a peer `cpu_host`, so their side
is kind="ssh" and `UrmaCommandExecutor` drives them from the peer cpu OS (matching the
proven URMA xboard path).
"""

from sprinkler.connect.npu_access import is_local_cpu_host


class UboeShowResolver:
    def __init__(self, executor):
        self.executor = executor

    def fill(self, pairs):
        # Fields (udma / bond ip / execute_eid) are already planned; nothing to resolve.
        return [dict(pair) for pair in pairs]

    def side(self, pair, role):
        target = str(pair.get(f"{role}_target") or "")
        rank = pair.get(f"{role}_rank")
        execute_eid = str(pair.get(f"{role}_execute_eid") or "").strip()
        cpu_host = str(pair.get(f"{role}_cpu_host") or "").strip()
        if cpu_host and not is_local_cpu_host(cpu_host):
            kind, host = "ssh", cpu_host
        else:
            kind, host = "local", execute_eid or cpu_host or str(pair.get(f"{role}_bond_ip") or "").strip()
        error = "" if host else f"{target} execute_eid missing"
        return {"kind": kind, "label": target, "host": host, "rank": rank,
                "execute_eid": execute_eid, "error": error}
