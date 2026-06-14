"""
engine/fast_simulator.py

Heap-based event scheduler — only wakes devices on their actual fire tick.
Each emitted event is tagged with the tier that was selected (NORMAL/ABOVE/BELOW)
so the correctness analyser can track tier transitions and compare observed
rates against declared probabilities.
"""

import heapq, json, math, random, statistics, time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional


def sample(dist_type: str, params: Dict) -> float:
    dt = dist_type.lower()
    if dt == 'normal':
        return random.gauss(params['mean'], params['std_dev'])
    elif dt == 'uniform':
        return random.uniform(params['low'], params['high'])
    elif dt == 'exponential':
        return random.expovariate(1.0 / max(params['mean'], 1e-9))
    elif dt == 'poisson':
        lam = params['mean']
        if lam <= 0: return 0.0
        if lam < 30:
            L, k, p = math.exp(-lam), 0, 1.0
            while p > L:
                k += 1
                p *= random.random()
            return float(k - 1)
        return float(max(0, int(random.gauss(lam, math.sqrt(lam)))))
    elif dt == 'binomial':
        return float(sum(1 for _ in range(int(params['n']))
                         if random.random() < params['p']))
    return 0.0


def pick_tier(profile: Dict):
    """
    Roll against tier probabilities and return (tier_name, tier_dict).
    Returns ('NORMAL', ...) | ('ABOVE', ...) | ('BELOW', ...)
    """
    normal = profile.get('normal', {})
    above  = profile.get('above',  {})
    below  = profile.get('below',  {})
    prob_n = normal.get('probability', 1.0)
    prob_a = above.get('probability',  0.0)
    roll   = random.random()
    if roll < prob_n:
        return 'NORMAL', normal
    elif roll < prob_n + prob_a:
        return 'ABOVE', above
    else:
        return 'BELOW', below


def generate_value(profile: Dict):
    """Returns (value, tier_name)."""
    tier_name, tier = pick_tier(profile)
    if not tier:
        return 0.0, 'NORMAL'
    dist  = tier.get('distribution', {})
    value = sample(dist.get('type', 'normal'),
                   dist.get('params', {'mean': 0, 'std_dev': 1}))
    vr    = profile.get('valid_range', {})
    if vr:
        value = max(vr.get('min', float('-inf')),
                    min(vr.get('max', float('inf')), value))
    return value, tier_name


class FastSimulator:
    def __init__(self, compiled: Dict, ms_per_tick: int = 60000, verbose: int = 0):
        self.compiled    = compiled
        self.verbose     = verbose

        sim = compiled.get('simulation', {})
        self._start = int(sim.get('start_time', 0))
        self._end   = int(sim.get('end_time',   1000))
        self.ms_per_tick = int(sim.get('ms_per_tick', ms_per_tick))

        profiles = compiled.get('profiles', {})

        # Build heap: (next_tick, device_id, interval_ticks, mode)
        self._heap: list = []
        self._profiles:   Dict[str, Dict]  = {}
        self._last_value: Dict[str, float] = {}
        self._last_tier:  Dict[str, str]   = {}   # track previous tier per device
        self._failure:    Dict[str, float] = {}

        for binding in compiled.get('bindings', []):
            did   = binding['device_id']
            pname = binding['profile']
            prof  = profiles.get(pname)
            if prof is None:
                continue
            self._profiles[did] = prof
            sampling = prof.get('sampling', {})
            mode     = sampling.get('mode', 'PERIODIC')
            self._failure[did] = sampling.get('failure', 0.0)

            if mode == 'PERIODIC':
                iticks = max(1, int(sampling['interval_ms'] / self.ms_per_tick))
                heapq.heappush(self._heap, (self._start, did, iticks, mode))
            else:
                heapq.heappush(self._heap, (self._start, did, 1, mode))

        self._rules = compiled.get('rules', [])

        # Window store: event_type → group_key → [{'event_time', 'value'}]
        self._store: Dict[str, Dict] = defaultdict(lambda: defaultdict(list))

        self.stats = {
            'persistent': 0, 'triggered': 0,
            'dropped_failure': 0, 'dropped_no_change': 0,
        }

        # Tier tracking per device: {did: {'NORMAL':n, 'ABOVE':n, 'BELOW':n, 'transitions':[]}}
        self.tier_stats: Dict[str, Dict] = {}

    def run(self, output_fn: Callable = None) -> List:
        collected = []

        def emit(ev: dict):
            if output_fn:
                output_fn(ev)
            else:
                collected.append(ev)
            if ev.get('triggered_by'):
                self.stats['triggered'] += 1
            else:
                self.stats['persistent'] += 1

        heap  = self._heap
        store = self._store
        t0    = time.perf_counter()
        last_log = self._start

        print(f'[FastSim] Ticks {self._start}→{self._end} | '
              f'Devices: {len(self._profiles)} | ms/tick: {self.ms_per_tick}')

        # Pre-schedule tumbling rule fire times
        rule_next: Dict[str, int] = {
            r['head_event']: self._start + r['window_size']
            for r in self._rules if r['window_type'] == 'tumbling'
        }

        while heap:
            tick, did, interval, mode = heapq.heappop(heap)
            if tick > self._end:
                break

            prof    = self._profiles[did]
            value, tier_name = generate_value(prof)
            failure = self._failure[did]
            sampling = prof.get('sampling', {})
            last_v   = self._last_value.get(did)
            prev_tier = self._last_tier.get(did, 'NORMAL')
            should_emit = False

            if mode == 'PERIODIC':
                should_emit = True
            elif mode == 'ON_CHANGE':
                threshold = sampling.get('threshold', 0.0)
                should_emit = (last_v is None or abs(value - last_v) >= threshold)
            elif mode == 'ON_THRESHOLD':
                threshold  = sampling.get('threshold', 0.0)
                prev_above = (last_v or 0.0) >= threshold
                curr_above = value >= threshold
                should_emit = (curr_above != prev_above)

            if should_emit:
                if random.random() < failure:
                    self.stats['dropped_failure'] += 1
                else:
                    self._last_value[did] = value
                    # Track tier transitions
                    if did not in self.tier_stats:
                        self.tier_stats[did] = {
                            'NORMAL': 0, 'ABOVE': 0, 'BELOW': 0,
                            'transitions': []
                        }
                    self.tier_stats[did][tier_name] += 1
                    if tier_name != prev_tier:
                        self.tier_stats[did]['transitions'].append({
                            'tick': tick,
                            'from': prev_tier,
                            'to':   tier_name,
                        })
                    self._last_tier[did] = tier_name

                    et = prof.get('event_type', 'sensor_reading')
                    ev = {
                        'event_type':   et,
                        'event_time':   tick,
                        'device_id':    did,
                        'measure_name': prof.get('measure_name', ''),
                        'value':        round(value, 4),
                        'tier':         tier_name,    # which tier produced this value
                    }
                    emit(ev)
                    store[et][(did,)].append({
                        'event_time': tick,
                        'value':      value,
                        'tier':       tier_name,
                    })
                    if len(store[et][(did,)]) > 1000:
                        store[et][(did,)] = store[et][(did,)][-1000:]
            else:
                self.stats['dropped_no_change'] += 1

            # Re-schedule device
            next_tick = tick + interval
            if next_tick <= self._end:
                heapq.heappush(heap, (next_tick, did, interval, mode))

            # Evaluate tumbling rules
            for rule in self._rules:
                if rule['window_type'] != 'tumbling':
                    continue
                rname     = rule['head_event']
                wsize     = rule['window_size']
                next_fire = rule_next.get(rname, self._start + wsize)
                if tick < next_fire:
                    continue
                rule_next[rname] = next_fire + wsize

                for src in rule.get('sources', []):
                    for gk, events in store[src['event_name']].items():
                        ws     = next_fire - wsize
                        window = [e for e in events
                                  if ws <= e['event_time'] < next_fire]
                        if not window:
                            continue
                        vals = [e['value'] for e in window]
                        avg_v = statistics.mean(vals)
                        ok = True
                        for con in rule.get('constraints', []):
                            try:
                                rhs = float(con['rhs'])
                                op  = con['op']
                                if op == '<'  and avg_v >= rhs: ok = False
                                elif op == '>' and avg_v <= rhs: ok = False
                                elif op == '<=' and avg_v > rhs: ok = False
                                elif op == '>=' and avg_v < rhs: ok = False
                            except Exception:
                                pass
                        if not ok:
                            continue
                        agg_results = {}
                        for agg in rule.get('agg_functions', []):
                            fn = {'avg': statistics.mean, 'min': min, 'max': max,
                                  'sum': sum, 'count': lambda v: float(len(v)),
                                  'last': lambda v: v[-1]}.get(agg['func'], statistics.mean)
                            agg_results[agg['output_var']] = fn(vals)
                        agg_v = list(agg_results.values())[0] if agg_results else 0.0
                        tev = {
                            'event_type':   rname,
                            'event_time':   next_fire,
                            'device_id':    gk[0],
                            'measure_name': rname,
                            'value':        round(agg_v, 4),
                            'triggered_by': rname,
                        }
                        emit(tev)
                        store[rname][gk].append({
                            'event_time': next_fire, 'value': agg_v
                        })

            if self.verbose > 0 and tick - last_log >= self.verbose:
                wall  = time.perf_counter() - t0
                total = self.stats['persistent'] + self.stats['triggered']
                pct   = 100 * (tick - self._start) / max(self._end - self._start, 1)
                print(f'  [t={tick:>7}] {pct:5.1f}%  events={total:,}  '
                      f'{total/max(wall, 0.001):,.0f} ev/s')
                last_log = tick

        wall  = time.perf_counter() - t0
        total = self.stats['persistent'] + self.stats['triggered']
        print(f'[FastSim] Done: {total:,} events in {wall:.2f}s '
              f'({total/max(wall,0.001):,.0f} ev/s)')
        print(f'  Persistent : {self.stats["persistent"]:,}  '
              f'Triggered  : {self.stats["triggered"]:,}')
        print(f'  Dropped (failure): {self.stats["dropped_failure"]:,}  '
              f'Dropped (no change): {self.stats["dropped_no_change"]:,}')
        return collected
