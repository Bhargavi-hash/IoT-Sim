"""
validator/semantic.py

Semantic validation pass over the AST.
Checks:
  1. Device class integrity — each MEASURES references a valid event type
  2. Distribution integrity — measure_name exists in a device class,
     ZONE used only if ZONE ENUM * declared, probabilities sum to 1.0 per WHERE block
  3. Event stream integrity — measure names exist, failure 0.0-1.0
  4. Event type integrity — no duplicate attribute names
  5. Table integrity — REFERENCES point to existing tables and columns
  6. Rule integrity — source event types exist, aggregation vars referenced
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Set

from ast_nodes.nodes import Program


@dataclass
class ValidationError:
    message:  str
    severity: str   # ERROR | WARN
    line:     int   = 0


class SemanticValidator:
    def __init__(self, program: Program):
        self.prog   = program
        self.errors: List[ValidationError] = []

        # Indexes built during validation
        self._device_classes: Dict[str, set] = {}   # name → set of measure names
        self._device_zone:    Set[str]        = set()  # device classes with ZONE ENUM *
        self._event_types:    Set[str]        = set()
        self._tables:         Dict[str, set]  = {}   # table → set of columns
        self._all_measures:   Set[str]        = set()

    def validate(self) -> List[ValidationError]:
        self._index_device_classes()
        self._index_event_types()
        self._index_tables()
        self._validate_distributions()
        self._validate_event_streams()
        self._validate_tables()
        self._validate_rules()
        return self.errors

    @property
    def has_errors(self) -> bool:
        return any(e.severity == 'ERROR' for e in self.errors)

    def _err(self, msg: str, line: int = 0):
        self.errors.append(ValidationError(message=msg, severity='ERROR', line=line))

    def _warn(self, msg: str, line: int = 0):
        self.errors.append(ValidationError(message=msg, severity='WARN', line=line))

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def _index_device_classes(self):
        for dc in self.prog.device_classes:
            if dc.name in self._device_classes:
                self._err(f"Duplicate DEVICE_CLASS name '{dc.name}'")
            measures = {m.name for m in dc.measures}
            self._device_classes[dc.name] = measures
            self._all_measures |= measures
            if dc.zone_enum:
                self._device_zone.add(dc.name)

    def _index_event_types(self):
        for et in self.prog.event_types:
            if et.name in self._event_types:
                self._err(f"Duplicate EVENTTYPE name '{et.name}'")
            self._event_types.add(et.name)

            # Check no duplicate attributes
            seen = set()
            for attr in et.attributes:
                if attr.name in seen:
                    self._err(f"Duplicate attribute '{attr.name}' in EVENTTYPE '{et.name}'")
                seen.add(attr.name)

    def _index_tables(self):
        for tbl in self.prog.tables:
            cols = {c.name for c in tbl.columns}
            self._tables[tbl.name] = cols

    # ------------------------------------------------------------------
    # Distribution validation
    # ------------------------------------------------------------------

    def _validate_distributions(self):
        declared_measures = set()

        for dist in self.prog.distributions:
            mn = dist.measure_name

            # Measure must exist in some device class
            if mn not in self._all_measures:
                self._err(f"DISTRIBUTION FOR '{mn}': no DEVICE_CLASS declares "
                          f"MEASURES {mn}")

            # Check for duplicate distributions for same measure
            if mn in declared_measures:
                self._warn(f"Multiple DISTRIBUTION FOR '{mn}' — last one wins")
            declared_measures.add(mn)

            # Validate each WHERE block
            for wb in dist.where_blocks:
                # Probabilities must sum to 1.0
                total = sum(t.probability for t in wb.tiers)
                if abs(total - 1.0) > 0.001:
                    self._err(
                        f"DISTRIBUTION FOR '{mn}' WHERE zone={wb.zone_id}: "
                        f"probabilities sum to {total:.4f}, must be 1.0"
                    )

                # Must have exactly one NORMAL tier
                normals = [t for t in wb.tiers if t.tier == 'NORMAL']
                if len(normals) != 1:
                    self._err(
                        f"DISTRIBUTION FOR '{mn}' WHERE zone={wb.zone_id}: "
                        f"must have exactly one NORMAL tier, found {len(normals)}"
                    )

                # Validate distribution parameters
                for tier in wb.tiers:
                    self._validate_dist_spec(tier.distribution, mn, wb.zone_id)

    def _validate_dist_spec(self, spec, measure_name, zone_id):
        required = {
            'NORMAL':      {'mean', 'std_dev'},
            'UNIFORM':     {'low', 'high'},
            'EXPONENTIAL': {'mean'},
            'POISSON':     {'mean'},
            'BINOMIAL':    {'n', 'p'},
        }
        needed = required.get(spec.dist_type, set())
        missing = needed - set(spec.params.keys())
        if missing:
            self._err(
                f"DISTRIBUTION FOR '{measure_name}' zone={zone_id} "
                f"{spec.dist_type}: missing params {missing}"
            )

        # Sanity on values
        if spec.dist_type == 'NORMAL' and spec.params.get('std_dev', 1) <= 0:
            self._err(f"NORMAL std_dev must be > 0 in distribution for '{measure_name}'")
        if spec.dist_type == 'UNIFORM':
            if spec.params.get('low', 0) >= spec.params.get('high', 1):
                self._err(f"UNIFORM: low must be < high in distribution for '{measure_name}'")
        if spec.dist_type == 'BINOMIAL':
            p = spec.params.get('p', 0.5)
            if not (0 <= p <= 1):
                self._err(f"BINOMIAL: p must be in [0,1] for '{measure_name}'")

    # ------------------------------------------------------------------
    # Event stream validation
    # ------------------------------------------------------------------

    def _validate_event_streams(self):
        for es in self.prog.event_streams:
            # All referenced event types must exist
            for et_name in es.event_types:
                if et_name not in self._event_types:
                    self._err(f"EVENTSTREAM '{es.name}': references unknown "
                              f"EVENTTYPE '{et_name}'")

            # All measure names in ARRIVALS must exist
            for arr in es.arrivals:
                if arr.measure_name not in self._all_measures:
                    self._warn(f"EVENTSTREAM '{es.name}' ARRIVALS: "
                               f"'{arr.measure_name}' not found in any DEVICE_CLASS")

                # Failure probability must be 0.0 to 1.0
                if not (0.0 <= arr.failure_prob <= 1.0):
                    self._err(f"EVENTSTREAM '{es.name}' ARRIVALS '{arr.measure_name}': "
                              f"FAILURE must be 0.0–1.0, got {arr.failure_prob}")

                # PERIODIC must have interval
                if arr.mode == 'PERIODIC' and arr.interval_ms is None:
                    self._err(f"EVENTSTREAM '{es.name}' ARRIVALS '{arr.measure_name}': "
                              f"PERIODIC requires an interval")

                # ON_CHANGE and ON_THRESHOLD must have threshold
                if arr.mode in ('ON_CHANGE', 'ON_THRESHOLD') and arr.threshold is None:
                    self._err(f"EVENTSTREAM '{es.name}' ARRIVALS '{arr.measure_name}': "
                              f"{arr.mode} requires a threshold value")

    # ------------------------------------------------------------------
    # Table validation
    # ------------------------------------------------------------------

    def _validate_tables(self):
        for tbl in self.prog.tables:
            for col in tbl.columns:
                if col.references:
                    ref_table, ref_col = col.references
                    # DEVICE_CLASS is a schema construct, not a table - skip
                    if ref_table == 'DEVICE_CLASS':
                        continue
                    if ref_table not in self._tables:
                        self._err(f"Table '{tbl.name}' column '{col.name}': "
                                  f"REFERENCES unknown table '{ref_table}'")
                    elif ref_col not in self._tables[ref_table]:
                        self._err(f"Table '{tbl.name}' column '{col.name}': "
                                  f"REFERENCES unknown column '{ref_table}.{ref_col}'")

    # ------------------------------------------------------------------
    # Rule validation
    # ------------------------------------------------------------------

    def _validate_rules(self):
        for rule in self.prog.rules:
            # Source event types must exist
            for src in rule.sources:
                if src.event_name not in self._event_types:
                    self._warn(f"Rule '{rule.head_event}': source event type "
                               f"'{src.event_name}' not declared")

            # Window size must be positive
            if rule.window.size <= 0:
                self._err(f"Rule '{rule.head_event}': window size must be > 0")

            # Aggregation functions must be known
            known_aggs = {'avg', 'min', 'max', 'count', 'sum', 'last'}
            for agg in rule.agg_functions:
                if agg.func not in known_aggs:
                    self._err(f"Rule '{rule.head_event}': unknown aggregation "
                              f"function '{agg.func}'")
