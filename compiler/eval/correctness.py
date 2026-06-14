"""
eval/correctness.py

Correctness analysis — observed vs declared for a completed simulation run.

Fixes:
  1. Std comparison uses MIXTURE std (computed from all tiers):
         Var_mix = sum_i[ p_i * (sigma_i^2 + (mu_i - mu_mix)^2) ]
     The declared std_dev inside each tier is within-tier spread only.

  2. Multi-value sensors (NPK) read value/value2/value3 per measure position.
"""

import json, math, statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# ── Measure → value field mapping ────────────────────────────────────────────

MULTI_MEASURE_ORDER = {
    'NPKSensor':  ['Nitrogen', 'Phosphorus', 'Potassium'],
    'NIBPCuff':   ['Systolic', 'Diastolic'],
    'WindSensor': ['WindSpeed', 'WindDirection'],
}
VALUE_FIELDS = ['value', 'value2', 'value3']

def value_field(device_class: str, measure_name: str) -> str:
    order = MULTI_MEASURE_ORDER.get(device_class, [])
    if measure_name in order:
        idx = order.index(measure_name)
        return VALUE_FIELDS[idx] if idx < len(VALUE_FIELDS) else 'value'
    return 'value'

# ── Mixture statistics ────────────────────────────────────────────────────────

def tier_dicts(profile: Dict) -> List[Dict]:
    out = []
    for name in ['normal', 'above', 'below']:
        t = profile.get(name, {})
        if not t: continue
        p = t.get('distribution', {}).get('params', {})
        out.append({
            'name': name.upper(),
            'mean': p.get('mean', 0.0),
            'std':  p.get('std_dev', p.get('std', 1.0)),
            'prob': t.get('probability', 0.0),
        })
    return out

def mix_mean(tiers): return sum(t['prob'] * t['mean'] for t in tiers)

def mix_std(tiers):
    mu = mix_mean(tiers)
    var = sum(t['prob'] * (t['std']**2 + (t['mean'] - mu)**2) for t in tiers)
    return math.sqrt(var)

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TierResult:
    tier: str
    declared_prob: float
    observed_prob: float
    observed_count: int
    total_count: int
    within_tolerance: bool
    tolerance: float = 0.05

    @property
    def deviation_pp(self): return abs(self.observed_prob - self.declared_prob) * 100

@dataclass
class MeasureResult:
    device_id: str
    device_class: str
    measure_name: str
    zone_id: int
    n_events: int
    declared_failure: float
    observed_mean: float
    declared_mean: float
    mean_ok: bool
    observed_std: float
    declared_std: float
    std_ok: bool
    tier_results: List[TierResult]
    n_transitions: int
    transition_rate: float
    expected_transition_rate: float
    n_out_of_range: int
    range_ok: bool

    @property
    def overall_pass(self):
        return (self.mean_ok and self.std_ok and self.range_ok
                and all(t.within_tolerance for t in self.tier_results))

    @property
    def transition_ok(self):
        return abs(self.transition_rate - self.expected_transition_rate) < 5.0

# ── Core analysis ─────────────────────────────────────────────────────────────

def analyse(events_jsonl, profiles_json,
            tolerance_mean=0.15, tolerance_std=0.20, tolerance_tier=0.05):

    data     = json.loads(Path(profiles_json).read_text())
    profiles = data['profiles']
    bindings = data['bindings']

    # Index persistent sensor readings by device
    by_device: Dict[str, List[dict]] = defaultdict(list)
    with open(events_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            ev = json.loads(line)
            if ev.get('event_type') == 'sensor_reading' and not ev.get('triggered_by'):
                by_device[ev['device_id']].append(ev)

    results = []
    seen = set()   # deduplicate device_class+measure+zone

    for binding in bindings:
        did          = binding['device_id']
        pname        = binding['profile']
        prof         = profiles.get(pname, {})
        if not prof: continue

        zone_id      = binding.get('zone_id', 0)
        measure_name = prof.get('measure_name', '')
        device_class = binding.get('device_class', '')
        key          = (device_class, measure_name, zone_id)
        if key in seen: continue
        seen.add(key)

        sampling = prof.get('sampling', {})
        declared_failure = sampling.get('failure', 0.0)
        valid_range = prof.get('valid_range', {})

        device_events = by_device.get(did, [])
        n_events = len(device_events)
        if n_events < 5: continue

        vfield = value_field(device_class, measure_name)
        values = [e.get(vfield) for e in device_events]
        values = [v for v in values if v is not None]
        if len(values) < 5: continue

        tiers_obs = [e.get('tier', 'NORMAL') for e in device_events]

        # Mixture statistics
        tds = tier_dicts(prof)
        if not tds: continue

        decl_mix_mean = mix_mean(tds)
        decl_mix_std  = mix_std(tds)
        obs_mean      = statistics.mean(values)
        obs_std       = statistics.stdev(values) if len(values) > 1 else 0.0

        tol_mean = abs(decl_mix_mean) * tolerance_mean if decl_mix_mean != 0 else 1.0
        tol_std  = decl_mix_std * tolerance_std

        mean_ok = abs(obs_mean - decl_mix_mean) <= tol_mean
        std_ok  = abs(obs_std  - decl_mix_std)  <= tol_std

        # Tier rates
        tier_counts = defaultdict(int)
        for t in tiers_obs: tier_counts[t] += 1

        tier_results = []
        for td in tds:
            tname = td['name']
            dp    = td['prob']
            op    = tier_counts.get(tname, 0) / n_events
            tier_results.append(TierResult(
                tier=tname, declared_prob=dp, observed_prob=op,
                observed_count=tier_counts.get(tname, 0), total_count=n_events,
                within_tolerance=abs(op - dp) <= tolerance_tier,
            ))

        # Transition rate
        n_trans = sum(1 for i in range(1, len(tiers_obs))
                      if tiers_obs[i] != tiers_obs[i-1])
        trans_rate  = 100.0 * n_trans / max(n_events, 1)
        exp_trans   = 100.0 * (1.0 - sum(td['prob']**2 for td in tds))

        # Range compliance
        rmin = valid_range.get('min', float('-inf'))
        rmax = valid_range.get('max', float('inf'))
        n_out = sum(1 for v in values if not (rmin <= v <= rmax))

        results.append(MeasureResult(
            device_id=did, device_class=device_class,
            measure_name=measure_name, zone_id=zone_id,
            n_events=n_events, declared_failure=declared_failure,
            observed_mean=obs_mean, declared_mean=decl_mix_mean, mean_ok=mean_ok,
            observed_std=obs_std,   declared_std=decl_mix_std,   std_ok=std_ok,
            tier_results=tier_results,
            n_transitions=n_trans, transition_rate=trans_rate,
            expected_transition_rate=exp_trans,
            n_out_of_range=n_out, range_ok=(n_out == 0),
        ))

    return results

# ── Report renderer ───────────────────────────────────────────────────────────

def print_report(results: List[MeasureResult]):
    G='\033[92m'; R='\033[91m'; Y='\033[93m'; B='\033[1m'; D='\033[2m'; X='\033[0m'
    ok = lambda f: f'{G}PASS{X}' if f else f'{R}FAIL{X}'

    print(f'\n{B}{"═"*80}{X}')
    print(f'{B}  CORRECTNESS REPORT  —  Observed vs Declared{X}')
    print(f'{B}{"═"*80}{X}')
    print(f'  {D}Std compared against mixture std (all tiers).  NPK: N=value P=value2 K=value3{X}')

    by_measure = defaultdict(list)
    for r in results: by_measure[r.measure_name].append(r)

    total_pass = total_fail = 0

    for measure, mrs in sorted(by_measure.items()):
        mrs = sorted(mrs, key=lambda x: x.zone_id)
        print(f'\n{B}  ── {measure} ──{X}')

        # Distribution table
        print(f'  {"Zone":>4}  {"n":>7}  {"Obs μ":>7} {"Mix μ":>7}  '
              f'{"Obs σ":>6} {"Mix σ":>6}  {"μ":>4} {"σ":>4} {"Rng":>4} {"✓":>4}')
        print(f'  {"─"*4}  {"─"*7}  {"─"*7} {"─"*7}  {"─"*6} {"─"*6}  '
              f'{"─"*4} {"─"*4} {"─"*4} {"─"*4}')
        for r in mrs:
            total_pass += r.overall_pass
            total_fail += (not r.overall_pass)
            print(f'  {r.zone_id:>4}  {r.n_events:>7,}  '
                  f'{r.observed_mean:>7.2f} {r.declared_mean:>7.2f}  '
                  f'{r.observed_std:>6.2f} {r.declared_std:>6.2f}  '
                  f'{ok(r.mean_ok):>4} {ok(r.std_ok):>4} '
                  f'{ok(r.range_ok):>4} {ok(r.overall_pass):>4}')

        # Tier rates table
        print()
        print(f'  {"Zone":>4}  {"NORMAL  obs/decl":>20}  '
              f'{"ABOVE  obs/decl":>18}  {"BELOW  obs/decl":>18}  '
              f'{"Transitions  obs/exp":>24}')
        print(f'  {"─"*4}  {"─"*20}  {"─"*18}  {"─"*18}  {"─"*24}')

        for r in mrs:
            td = {t.tier: t for t in r.tier_results}
            def tfmt(tier, width=18):
                if tier not in td: return f'{"—":{width}}'
                t = td[tier]
                s = f'{t.observed_prob*100:4.1f}% / {t.declared_prob*100:4.1f}%'
                c = G if t.within_tolerance else R
                return f'{c}{s:{width}}{X}'
            tc = G if r.transition_ok else Y
            ts = f'{tc}{r.n_transitions:,} ({r.transition_rate:.1f}%/{r.expected_transition_rate:.1f}%){X}'
            print(f'  {r.zone_id:>4}  {tfmt("NORMAL",20):>20}  '
                  f'{tfmt("ABOVE"):>18}  {tfmt("BELOW"):>18}  {ts}')

    n_total = total_pass + total_fail
    print(f'\n{B}{"═"*80}{X}')
    print(f'{B}  SUMMARY  ({n_total} device/zone combinations){X}')
    print(f'  PASS : {G}{total_pass}{X}  ({100*total_pass//max(n_total,1)}%)')
    print(f'  FAIL : {(R if total_fail else "")}{total_fail}{(X if total_fail else "")}')
    print(f'\n  Tolerances: mean ±15% of mix μ  |  std ±20% of mix σ  |  tier ±5 pp')
    print(f'{"═"*80}\n')


def save_json(results: List[MeasureResult], path: str):
    out = [{
        'device_id':    r.device_id,
        'device_class': r.device_class,
        'measure_name': r.measure_name,
        'zone_id':      r.zone_id,
        'n_events':     r.n_events,
        'mean':   {'observed': round(r.observed_mean,4), 'declared': round(r.declared_mean,4), 'pass': r.mean_ok},
        'std':    {'observed': round(r.observed_std,4),  'declared': round(r.declared_std,4),  'pass': r.std_ok},
        'tiers':  [{'tier': t.tier, 'observed_prob': round(t.observed_prob,4),
                    'declared_prob': round(t.declared_prob,4), 'pass': t.within_tolerance,
                    'deviation_pp': round(t.deviation_pp,2)} for t in r.tier_results],
        'transitions': {'count': r.n_transitions, 'observed_rate': round(r.transition_rate,2),
                        'expected_rate': round(r.expected_transition_rate,2), 'pass': r.transition_ok},
        'range':  {'n_out_of_range': r.n_out_of_range, 'pass': r.range_ok},
        'overall_pass': r.overall_pass,
    } for r in results]
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f'[Correctness] Saved → {path}')
