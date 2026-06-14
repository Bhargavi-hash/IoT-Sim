"""
ast_nodes/nodes.py

AST node dataclasses for every construct in the IoT schema language.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------------------------------------------------
# Base
# -----------------------------------------------------------------------

@dataclass
class ASTNode:
    pass


# -----------------------------------------------------------------------
# Device Class
# -----------------------------------------------------------------------

@dataclass
class MeasuresDef(ASTNode):
    name:       str          # e.g. "Moisture"
    unit:       str          # e.g. "percent_VWC"
    range_min:  float        # physical minimum
    range_max:  float        # physical maximum
    resolution: float        # smallest detectable change


@dataclass
class DeviceClassNode(ASTNode):
    name:       str
    measures:   List[MeasuresDef]
    zone_enum:  bool          # True if ZONE ENUM * is declared
                              # signals that ZONE is the distribution discriminator


# -----------------------------------------------------------------------
# Table
# -----------------------------------------------------------------------

@dataclass
class ColumnDef(ASTNode):
    name:        str
    dtype:       str          # INTEGER | FLOAT | STRING | ENUM
    primary_key: bool = False
    references:  Optional[Tuple[str, str]] = None   # (table, column)
    nullable:    bool = True


@dataclass
class TableNode(ASTNode):
    name:    str
    columns: List[ColumnDef]


# -----------------------------------------------------------------------
# Event Type
# -----------------------------------------------------------------------

@dataclass
class AttributeDef(ASTNode):
    name:  str
    dtype: str


@dataclass
class FunctionalDep(ASTNode):
    lhs: List[str]
    rhs: List[str]


@dataclass
class EventTypeNode(ASTNode):
    name:          str
    eventtime_attr: str                      # name of the EVENTTIME attribute
    attributes:    List[AttributeDef]
    func_deps:     List[FunctionalDep]


# -----------------------------------------------------------------------
# Event Stream
# -----------------------------------------------------------------------

@dataclass
class ArrivalSpec(ASTNode):
    measure_name: str                        # e.g. "Moisture"
    mode:         str                        # PERIODIC | ON_CHANGE | ON_THRESHOLD
    interval_ms:  Optional[float]            # for PERIODIC — converted to ms
    threshold:    Optional[float]            # for ON_CHANGE and ON_THRESHOLD
    failure_prob: float                      # 0.0 to 1.0


@dataclass
class EventStreamNode(ASTNode):
    name:        str
    event_types: List[str]                   # list of event type names
    arrivals:    List[ArrivalSpec]


# -----------------------------------------------------------------------
# Distribution
# -----------------------------------------------------------------------

@dataclass
class DistributionSpec(ASTNode):
    """One statistical distribution with its parameters."""
    dist_type: str                           # NORMAL | UNIFORM | EXPONENTIAL | POISSON | BINOMIAL
    params:    Dict[str, float]              # e.g. {"mean": 65.0, "std_dev": 8.0}


@dataclass
class TierSpec(ASTNode):
    """NORMAL / ABOVE / BELOW tier within a WHERE block."""
    tier:          str                       # NORMAL | ABOVE | BELOW
    distribution:  DistributionSpec
    probability:   float


@dataclass
class WhereBlock(ASTNode):
    """WHERE zone_id = N ( ... ) block."""
    zone_id:   int                           # the zone primary key value
    tiers:     List[TierSpec]                # NORMAL + optional ABOVE + optional BELOW


@dataclass
class DistributionNode(ASTNode):
    measure_name: str                        # matches a MEASURES name in a DEVICE_CLASS
    where_blocks: List[WhereBlock]           # one per zone


# -----------------------------------------------------------------------
# IoTDL Rules
# -----------------------------------------------------------------------

@dataclass
class AggFunction(ASTNode):
    output_var: str                          # e.g. "avg_value"
    func:       str                          # avg | min | max | count | sum | last
    input_var:  str                          # e.g. "value"


@dataclass
class WindowSpec(ASTNode):
    window_type: str                         # sliding | tumbling | landmark
    slide_var:   str                         # s
    size:        int                         # window size in ticks


@dataclass
class SourceClause(ASTNode):
    event_name: str
    bound_vars: List[str]                    # positional bindings
    time_var:   str                          # z


@dataclass
class Constraint(ASTNode):
    lhs: str
    op:  str                                 # < | > | <= | >= | == | !=
    rhs: str


@dataclass
class RuleNode(ASTNode):
    head_event:    str
    window:        WindowSpec
    grouping_vars: List[str]
    agg_functions: List[AggFunction]
    fire_time_expr: str
    sources:       List[SourceClause]
    constraints:   List[Constraint]


# -----------------------------------------------------------------------
# Top-level program
# -----------------------------------------------------------------------

@dataclass
class Program(ASTNode):
    device_classes: List[DeviceClassNode]
    tables:         List[TableNode]
    event_types:    List[EventTypeNode]
    event_streams:  List[EventStreamNode]
    distributions:  List[DistributionNode]
    rules:          List[RuleNode]
