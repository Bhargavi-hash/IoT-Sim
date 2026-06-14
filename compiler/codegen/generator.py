"""
codegen/generator.py

Takes a validated AST and generates:
  1. profiles.json  — simulation config, profiles, eventstream config
  2. bindings       — device_id → profile mapping (resolved from resource tables)
  3. rules.json     — compiled IoTDL rules for the engine

The code generator also resolves which distribution applies to each
physical device by joining:
    devices.device_class + zones.zone_id → DISTRIBUTION WHERE zone_id
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ast_nodes.nodes import (
    Program, DeviceClassNode, DistributionNode,
    WhereBlock, TierSpec, DistributionSpec,
    ArrivalSpec, RuleNode,
)


# -----------------------------------------------------------------------
# Resource data (populated from parsed table rows at runtime)
# -----------------------------------------------------------------------

@dataclass
class DeviceRecord:
    device_id:    str
    device_class: str
    plot_id:      int
    depth_cm:     Optional[int] = None


@dataclass
class PlotRecord:
    plot_id:   int
    zone_id:   int
    plot_type: str  = ''
    soil_type: str  = ''
    crop_type: str  = ''


@dataclass
class ZoneRecord:
    zone_id:   int
    zone_name: str  = ''
    zone_type: str  = ''


# -----------------------------------------------------------------------
# Code generator
# -----------------------------------------------------------------------

class CodeGenerator:
    """
    Parameters
    ----------
    program         : validated AST
    devices         : list of DeviceRecord (from actual resource table data)
    plots           : list of PlotRecord
    zones           : list of ZoneRecord
    simulation_cfg  : dict with start_time, end_time, time_unit, seed
    """

    def __init__(
        self,
        program:        Program,
        devices:        List[DeviceRecord],
        plots:          List[PlotRecord],
        zones:          List[ZoneRecord],
        simulation_cfg: Dict[str, Any],
    ):
        self.prog    = program
        self.sim_cfg = simulation_cfg

        # Build lookup indexes
        self._devices: Dict[str, DeviceRecord] = {d.device_id: d for d in devices}
        self._plots:   Dict[int, PlotRecord]   = {p.plot_id:   p for p in plots}
        self._zones:   Dict[int, ZoneRecord]   = {z.zone_id:   z for z in zones}

        # Index device class → { measure_name → MeasuresDef }
        self._dc_measures: Dict[str, Dict[str, Any]] = {}
        for dc in program.device_classes:
            self._dc_measures[dc.name] = {m.name: m for m in dc.measures}

        # Index distributions: measure_name → DistributionNode
        self._distributions: Dict[str, DistributionNode] = {
            d.measure_name: d for d in program.distributions
        }

        # Index arrival specs: measure_name → ArrivalSpec
        self._arrivals: Dict[str, ArrivalSpec] = {}
        for es in program.event_streams:
            for arr in es.arrivals:
                self._arrivals[arr.measure_name] = arr

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate(self) -> Dict[str, Any]:
        """Returns a dict that can be JSON-serialised to profiles.json"""
        return {
            'simulation':   self.sim_cfg,
            'profiles':     self._generate_profiles(),
            'bindings':     self._generate_bindings(),
            'event_streams': self._generate_streams(),
            'rules':        self._generate_rules(),
        }

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def _generate_profiles(self) -> Dict[str, Any]:
        """
        One profile entry per (device_class, zone_id) combination.
        Profile name: <DeviceClass>_<measure_name>_zone<zone_id>
        """
        profiles = {}

        for dc in self.prog.device_classes:
            for measure in dc.measures:
                dist_node = self._distributions.get(measure.name)
                if dist_node is None:
                    continue

                for wb in dist_node.where_blocks:
                    profile_name = f'{dc.name}_{measure.name}_zone{wb.zone_id}'
                    profiles[profile_name] = self._build_profile(
                        dc_name=dc.name,
                        measure_name=measure.name,
                        measure=measure,
                        where_block=wb,
                    )

        return profiles

    def _build_profile(self, dc_name: str, measure_name: str,
                       measure: Any, where_block: WhereBlock) -> Dict[str, Any]:
        # Find eventtype for this device class
        event_type = self._find_event_type(dc_name)

        # Find arrival spec for this measure
        arrival = self._arrivals.get(measure_name)

        # Build tier signals
        normal_tier = next((t for t in where_block.tiers if t.tier == 'NORMAL'), None)
        above_tier  = next((t for t in where_block.tiers if t.tier == 'ABOVE'),  None)
        below_tier  = next((t for t in where_block.tiers if t.tier == 'BELOW'),  None)

        profile: Dict[str, Any] = {
            'event_type':   event_type,
            'measure_name': measure_name,
            'unit':         measure.unit,
            'valid_range':  {'min': measure.range_min, 'max': measure.range_max},
            'resolution':   measure.resolution,
            'zone_id':      where_block.zone_id,
        }

        # Signal tiers
        if normal_tier:
            profile['normal'] = {
                'distribution': self._dist_to_dict(normal_tier.distribution),
                'probability':  normal_tier.probability,
            }
        if above_tier:
            profile['above'] = {
                'distribution': self._dist_to_dict(above_tier.distribution),
                'probability':  above_tier.probability,
            }
        if below_tier:
            profile['below'] = {
                'distribution': self._dist_to_dict(below_tier.distribution),
                'probability':  below_tier.probability,
            }

        # Sampling from event stream
        if arrival:
            profile['sampling'] = {
                'mode':        arrival.mode,
                'interval_ms': arrival.interval_ms,
                'threshold':   arrival.threshold,
                'failure':     arrival.failure_prob,
            }

        return profile

    def _dist_to_dict(self, spec: DistributionSpec) -> Dict[str, Any]:
        return {
            'type':   spec.dist_type.lower(),
            'params': spec.params,
        }

    def _find_event_type(self, dc_name: str) -> str:
        """Find which event type a device class produces."""
        # Match device class name fragments to event type names
        dc_lower = dc_name.lower()
        for et in self.prog.event_types:
            et_lower = et.name.lower()
            if any(fragment in et_lower for fragment in
                   ['soil', 'environment', 'vital', 'location', 'plant']):
                # Map by device class category
                if any(s in dc_lower for s in
                       ['moisture', 'temperature', 'npk', 'ph', 'salinity', 'insect',
                        'soil']):
                    if 'soil' in et_lower:
                        return et.name
                elif any(s in dc_lower for s in
                         ['air', 'humidity', 'co2', 'solar', 'wind', 'rain']):
                    if 'environment' in et_lower:
                        return et.name
                elif any(s in dc_lower for s in ['oximeter', 'ecg', 'nibp', 'thermo',
                                                  'respiratory', 'icp']):
                    if 'vital' in et_lower:
                        return et.name
                elif 'rfid' in dc_lower:
                    if 'location' in et_lower:
                        return et.name
        # Fallback: return first event type
        return self.prog.event_types[0].name if self.prog.event_types else 'sensor_reading'

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def _generate_bindings(self) -> List[Dict[str, Any]]:
        """
        For each physical device, resolve which profile to use:
          device.device_class + device.plot_id → plot.zone_id
          → profile name <DeviceClass>_<measure_name>_zone<zone_id>
        """
        bindings = []

        for device_id, dev in self._devices.items():
            plot = self._plots.get(dev.plot_id)
            if plot is None:
                continue
            zone_id = plot.zone_id

            dc_measures = self._dc_measures.get(dev.device_class, {})
            for measure_name in dc_measures:
                profile_name = f'{dev.device_class}_{measure_name}_zone{zone_id}'

                # Verify this profile was generated
                binding: Dict[str, Any] = {
                    'device_id':    device_id,
                    'device_class': dev.device_class,
                    'profile':      profile_name,
                    'zone_id':      zone_id,
                }
                if dev.depth_cm is not None:
                    binding['depth_cm'] = dev.depth_cm

                bindings.append(binding)

        return bindings

    # ------------------------------------------------------------------
    # Event streams
    # ------------------------------------------------------------------

    def _generate_streams(self) -> List[Dict[str, Any]]:
        result = []
        for es in self.prog.event_streams:
            result.append({
                'name':        es.name,
                'event_types': es.event_types,
                'arrivals': [
                    {
                        'measure':     a.measure_name,
                        'mode':        a.mode,
                        'interval_ms': a.interval_ms,
                        'threshold':   a.threshold,
                        'failure':     a.failure_prob,
                    }
                    for a in es.arrivals
                ],
            })
        return result

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def _generate_rules(self) -> List[Dict[str, Any]]:
        result = []
        for rule in self.prog.rules:
            result.append({
                'head_event':    rule.head_event,
                'window_type':   rule.window.window_type,
                'window_size':   rule.window.size,
                'slide_var':     rule.window.slide_var,
                'grouping_vars': rule.grouping_vars,
                'agg_functions': [
                    {
                        'output_var': a.output_var,
                        'func':       a.func,
                        'input_var':  a.input_var,
                    }
                    for a in rule.agg_functions
                ],
                'fire_time_expr': rule.fire_time_expr,
                'sources': [
                    {
                        'event_name': s.event_name,
                        'bound_vars': s.bound_vars,
                        'time_var':   s.time_var,
                    }
                    for s in rule.sources
                ],
                'constraints': [
                    {'lhs': c.lhs, 'op': c.op, 'rhs': c.rhs}
                    for c in rule.constraints
                ],
            })
        return result
