"""Connection target resolution from args/xlsx; this module does not execute SSH."""

from sprinkler.connect.targets._base import (
    CabinetRef,
    ConnectionTarget,
    NodeRef,
    _build_meta,
    _build_node_ref,
)
from sprinkler.connect.targets._builder import TargetBuilder
from sprinkler.connect.targets._context import TargetContext
from sprinkler.connect.targets._ip_spec import NodeIpSpec
from sprinkler.connect.targets._selector import TargetSelector

__all__ = [
    "CabinetRef",
    "ConnectionTarget",
    "NodeIpSpec",
    "NodeRef",
    "TargetBuilder",
    "TargetContext",
    "TargetSelector",
]
