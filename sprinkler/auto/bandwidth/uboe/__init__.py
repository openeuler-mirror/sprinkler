"""UBOE (ethernet parameter-plane) bandwidth backend.

Mirrors the `urma/` package layer structure: command building (`commands.py`),
execution-side resolution (`show_resolver.py`), device-side planning/discovery
(`pairs.py` / `discovery.py`), device-side pre-check (`env.py`), and orchestration
(`planner.py`). Generic process execution / bandwidth result parsing are reused from
`urma/executor.py` and `urma/results.py`; shared tools come from `common/`.
"""
