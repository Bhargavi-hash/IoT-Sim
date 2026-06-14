"""
engine/simulator.py

Simulation engine that consumes the compiled profiles.json output
and drives event generation.

Each device:
  1. Rolls which tier to use (NORMAL / ABOVE / BELOW) based on probabilities
  2. Samples from the tier's statistical distribution
  3. Respects the arrival mode (PERIODIC / ON_CHANGE / ON_THRESHOLD)
  4. Applies the FAILURE probability to decide if the reading is transmitted
  5. Emits an event if transmitted

The rule engine then evaluates IoTDL rules against incoming events.
"""

from __future__ import annotations

import math
import random
import statistics
import time as wallclock
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# -----------------------------------------------------------------------
# Statistical samplers
# -----------------------------------------------------------------------

def sample(dist_type: str, params: Dict[str, float]) -> float:
    dt = dist_type.lower()
    if dt == 'normal':
        return random.gauss(params['mean'], params['std_dev'])
    elif dt == 'uniform':
        return random.uniform(params['low'], params['high'])
    elif dt == 'exponential':
        return random.expovariate(1.0 / params['mean']) if params['mean'] > 0 else 0.0
    elif dt == 'poisson':
        lam = params['mean']
        # Knuth algorithm for small lambda
        if lam < 30:
            L, k, p = math.exp(-lam), 0, 1.0
            while p > L:
                k += 1
                p *= random.random()
            return float(k - 1)
        else:
            return float(max(0, int(random.gauss(lam, math.sqrt(lam)))))
    elif dt == 'binomial':
        n, p = int(params['n']), params['p']
        return float(sum(1 for _ in range(n) if random.random() < p))
    else:
        return 0.0


# -----------------------------------------------------------------------
# Event
# -----------------------------------------------------------------------

@dataclass
class Event:
    event_type:   str
    event_time:   int               # simulation tick
    device_id:    str
    measure_name: str
    value:        float
    value2:       Optional[float]   = None
    value3:       Optional[float]   = None
    triggered_by: Optional[str]     = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            'event_type':   self.event_type,
            'event_time':   self.event_time,
            'device_id':    self.device_id,
            'measure_name': self.measure_name,
            'value':        round(self.value, 4),
            'value2':       round(self.value2, 4) if self.value2 is not None else None,
            'value3':       round(self.value3, 4) if self.value3 is not None else None,
            'triggered_by': self.triggered_by,
        }
        return {k: v for k, v in d.items() if v is not None}


# -----------------------------------------------------------------------
# Device simulator
# -----------------------------------------------------------------------

@dataclass
class DeviceSimulator:
    device_id:    str
    profile:      Dict[str, Any]
    ms_per_tick:  int

    _last_value:  Optional[float] = field(default=None, repr=False)
    _next_tick:   int             = field(default=0,    repr=False)

    def should_emit(self, t: int, current_value: float) -> bool:
        sampling = self.profile.get('sampling', {})
        mode     = sampling.get('mode', 'PERIODIC')

        if mode == 'PERIODIC':
            interval_ms = sampling.get('interval_ms', 60000)
            rate_ticks  = max(1, int(interval_ms / self.ms_per_tick))
            return t % rate_ticks == 0

        elif mode == 'ON_CHANGE':
            if self._last_value is None:
                return True
            threshold = sampling.get('threshold', 0.0)
            return abs(current_value - self._last_value) >= threshold

        elif mode == 'ON_THRESHOLD':
            threshold = sampling.get('threshold', 0.0)
            prev_above = (self._last_value or 0.0) >= threshold
            curr_above = current_value >= threshold
            return curr_above != prev_above   # fire on crossing

        return True

    def generate_value(self) -> float:
        """Pick a tier and sample from its distribution."""
        normal = self.profile.get('normal', {})
        above  = self.profile.get('above',  {})
        below  = self.profile.get('below',  {})

        prob_normal = normal.get('probability', 1.0)
        prob_above  = above.get('probability',  0.0)
        prob_below  = below.get('probability',  0.0)

        roll = random.random()
        if roll < prob_normal:
            tier = normal
        elif roll < prob_normal + prob_above:
            tier = above
        else:
            tier = below

        if not tier:
            return 0.0

        dist = tier.get('distribution', {})
        value = sample(dist.get('type', 'normal'), dist.get('params', {'mean': 0, 'std_dev': 1}))

        # Clamp to valid range
        vr = self.profile.get('valid_range', {})
        if vr:
            value = max(vr.get('min', float('-inf')), min(vr.get('max', float('inf')), value))

        return value

    def tick(self, t: int) -> Optional[Event]:
        value = self.generate_value()

        sampling      = self.profile.get('sampling', {})
        failure_prob  = sampling.get('failure', 0.0)

        if not self.should_emit(t, value):
            return None

        # Apply failure probability
        if random.random() < failure_prob:
            return None   # reading dropped — network failure

        self._last_value = value

        return Event(
            event_type=self.profile.get('event_type', 'sensor_reading'),
            event_time=t,
            device_id=self.device_id,
            measure_name=self.profile.get('measure_name', ''),
            value=value,
        )


# -----------------------------------------------------------------------
# Window store for rule engine
# -----------------------------------------------------------------------

class WindowStore:
    def __init__(self):
        self._store: Dict[str, Dict[tuple, List[Event]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def append(self, event: Event, group_key: tuple):
        self._store[event.event_type][group_key].append(event)

    def query(self, event_type: str, group_key: tuple,
              t: int, size: int, wtype: str) -> List[Event]:
        events = self._store[event_type][group_key]
        if wtype == 'sliding':
            return [e for e in events if t - size < e.event_time <= t]
        elif wtype == 'tumbling':
            ws = (t // size) * size
            return [e for e in events if ws <= e.event_time < ws + size]
        return list(events)

    def all_groups(self, event_type: str) -> List[tuple]:
        return list(self._store[event_type].keys())

    def prune(self, max_age: int, current_t: int):
        for et in self._store:
            for key in list(self._store[et].keys()):
                self._store[et][key] = [
                    e for e in self._store[et][key]
                    if current_t - e.event_time <= max_age
                ]


# -----------------------------------------------------------------------
# Rule engine
# -----------------------------------------------------------------------

AGG_FUNCS: Dict[str, Callable[[List[float]], float]] = {
    'avg':   lambda vs: statistics.mean(vs) if vs else 0.0,
    'min':   lambda vs: min(vs) if vs else 0.0,
    'max':   lambda vs: max(vs) if vs else 0.0,
    'sum':   sum,
    'count': lambda vs: float(len(vs)),
    'last':  lambda vs: vs[-1] if vs else 0.0,
}


class RuleEngine:
    def __init__(self, rules: List[Dict], store: WindowStore):
        self.rules = rules
        self.store = store

    def evaluate(self, t: int) -> List[Event]:
        fired = []
        for rule in self.rules:
            fired.extend(self._eval_rule(rule, t))
        return fired

    def _eval_rule(self, rule: Dict, t: int) -> List[Event]:
        wtype = rule['window_type']
        wsize = rule['window_size']
        fired = []

        if wtype == 'sliding':
            should_fire = t >= wsize
        elif wtype == 'tumbling':
            should_fire = t > 0 and t % wsize == 0
        else:
            should_fire = True

        if not should_fire:
            return fired

        if not rule.get('sources'):
            return fired

        src        = rule['sources'][0]
        source_et  = src['event_name']
        group_keys = self.store.all_groups(source_et)

        for gk in group_keys:
            events = self.store.query(source_et, gk, t, wsize, wtype)
            if not events:
                continue

            # Extract values for aggregation
            values  = [e.value  for e in events]
            values2 = [e.value2 for e in events if e.value2 is not None]
            values3 = [e.value3 for e in events if e.value3 is not None]

            # Check constraints
            if not self._check_constraints(rule.get('constraints', []), values):
                continue

            # Compute aggregations
            agg_results = {}
            for agg in rule.get('agg_functions', []):
                input_var = agg['input_var']
                if input_var == 'value':
                    vals = values
                elif input_var == 'value2':
                    vals = values2
                elif input_var == 'value3':
                    vals = values3
                else:
                    vals = values
                fn = AGG_FUNCS.get(agg['func'], lambda vs: 0.0)
                agg_results[agg['output_var']] = fn(vals)

            # Fire time
            s, w = t, wsize
            try:
                fire_t = int(eval(rule['fire_time_expr'],
                                  {'__builtins__': {}}, {'s': s, 'w': w}))
            except Exception:
                fire_t = t + 1

            # Build triggered event
            agg_value = list(agg_results.values())[0] if agg_results else 0.0
            ev = Event(
                event_type=rule['head_event'],
                event_time=fire_t,
                device_id=events[0].device_id if events else '',
                measure_name=rule['head_event'],
                value=round(agg_value, 4),
                triggered_by=rule['head_event'],
            )
            self.store.append(ev, gk)
            fired.append(ev)

        return fired

    def _check_constraints(self, constraints: List[Dict],
                           values: List[float]) -> bool:
        if not constraints or not values:
            return True

        # Evaluate each constraint against the most recent value
        latest = values[-1] if values else 0.0
        avg    = statistics.mean(values) if values else 0.0

        for con in constraints:
            try:
                lhs_val = avg   # constraint LHS is typically the aggregated value
                rhs_val = float(con['rhs'])
                op      = con['op']
                if   op in ('=', '==') and not (lhs_val == rhs_val): return False
                elif op == '!='        and not (lhs_val != rhs_val): return False
                elif op == '<'         and not (lhs_val <  rhs_val): return False
                elif op == '<='        and not (lhs_val <= rhs_val): return False
                elif op == '>'         and not (lhs_val >  rhs_val): return False
                elif op == '>='        and not (lhs_val >= rhs_val): return False
            except (ValueError, TypeError):
                pass

        return True


# -----------------------------------------------------------------------
# Main simulation loop
# -----------------------------------------------------------------------

class Simulator:
    def __init__(
        self,
        compiled: Dict[str, Any],
        ms_per_tick: int = 1000,
        verbose: int     = 0,
    ):
        self.compiled    = compiled
        self.ms_per_tick = ms_per_tick
        self.verbose     = verbose
        self.store       = WindowStore()

        sim = compiled.get('simulation', {})
        # When time_unit is 'ticks', start/end are already in ticks
        if sim.get('time_unit') == 'ticks':
            self._start_tick = int(sim.get('start_time', 0))
            self._end_tick   = int(sim.get('end_time', 1000))
            # Use ms_per_tick from compiled config if available
            ms_per_tick = sim.get('ms_per_tick', ms_per_tick)
        else:
            ms_per_unit = {
                'seconds': 1000, 'milliseconds': 1,
                'minutes': 60000, 'hours': 3600000
            }.get(sim.get('time_unit', 'seconds').lower(), 1000)
            self._start_tick = int(sim.get('start_time', 0)   * ms_per_unit / ms_per_tick)
            self._end_tick   = int(sim.get('end_time', 3600)  * ms_per_unit / ms_per_tick)

        # Build device simulators from bindings + profiles
        profiles = compiled.get('profiles', {})
        self._devices: List[DeviceSimulator] = []
        for binding in compiled.get('bindings', []):
            profile_name = binding.get('profile')
            profile      = profiles.get(profile_name)
            if profile is None:
                continue
            self._devices.append(DeviceSimulator(
                device_id=binding['device_id'],
                profile=profile,
                ms_per_tick=ms_per_tick,
            ))

        # Build rule engine
        rules = compiled.get('rules', [])
        self._rule_engine = RuleEngine(rules=rules, store=self.store)

        # Stats
        self.stats = {'persistent': 0, 'triggered': 0, 'dropped': 0}

    def run(self, output_fn: Callable[[Event], None] = None):
        """
        Run the simulation. output_fn is called for every emitted event.
        If None, events are collected and returned.
        """
        collected = []

        def emit(event: Event):
            if output_fn:
                output_fn(event)
            else:
                collected.append(event)

            if event.triggered_by:
                self.stats['triggered'] += 1
            else:
                self.stats['persistent'] += 1

        print(f"[Simulator] Ticks {self._start_tick} → {self._end_tick} "
              f"| Devices: {len(self._devices)} | ms_per_tick: {self.ms_per_tick}")

        t0 = wallclock.perf_counter()

        for t in range(self._start_tick, self._end_tick + 1):
            # Persistent events
            for dev in self._devices:
                ev = dev.tick(t)
                if ev:
                    self.store.append(ev, (dev.device_id,))
                    emit(ev)

            # Triggered events from rules
            for tev in self._rule_engine.evaluate(t):
                emit(tev)

            # Periodic pruning
            if t % 1000 == 0 and t > 0:
                self.store.prune(max_age=5000, current_t=t)

            if self.verbose > 0 and t % self.verbose == 0:
                wall = wallclock.perf_counter() - t0
                total = self.stats['persistent'] + self.stats['triggered']
                print(f'  [t={t}] events={total:,}  ({total/max(wall,0.001):,.0f} ev/s)')

        wall = wallclock.perf_counter() - t0
        total = self.stats['persistent'] + self.stats['triggered']
        print(f"[Simulator] Done: {total:,} events in {wall:.2f}s "
              f"({total/max(wall,0.001):,.0f} ev/s)")
        print(f"  Persistent: {self.stats['persistent']:,}  "
              f"Triggered: {self.stats['triggered']:,}")

        return collected
