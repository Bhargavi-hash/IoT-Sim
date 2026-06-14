"""
tools/benchmark.py

Performance benchmark runner.
Sweeps device counts and records:
  - Total wall time
  - Events per second (persistent + triggered separately)
  - Time per 1000 ticks
  - Peak tick throughput
  - Memory usage (peak RSS)
  - Scaling factor vs baseline

Usage:
    python tools/benchmark.py \
        --schema   examples/agriculture.sql \
        --rules    examples/agriculture.iotdl \
        --sim-end  7 --time-unit days --tick-unit minutes \
        --counts   10 50 100 500 1000 \
        --output   out/benchmark.json

To also plot the results (requires matplotlib):
    python tools/benchmark.py ... --plot
"""

import argparse
import json
import os
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.generate_devices import generate
from codegen.generator import CodeGenerator, DeviceRecord, PlotRecord, ZoneRecord
from engine.fast_simulator import FastSimulator
from lexer.tokenizer import Lexer
from parser.parser import Parser
from validator.semantic import SemanticValidator


# ── Single benchmark run ─────────────────────────────────────────────────────

def run_one(
    schema_text:  str,
    rules_text:   str,
    n_devices:    int,
    sim_end:      int,
    time_unit:    str,
    tick_unit:    str,
    seed:         int = 42,
) -> dict:
    """
    Run a single simulation with n_devices and return timing/throughput metrics.
    """
    # Parse
    prog = Parser(Lexer(schema_text).tokenize()).parse()
    if rules_text:
        rp = Parser(Lexer(rules_text).tokenize()).parse()
        prog.rules.extend(rp.rules)

    # Validate (suppress output)
    SemanticValidator(prog).validate()

    # Generate resource data
    res_data = generate(n_devices=n_devices, seed=seed)
    devices  = [DeviceRecord(**d) for d in res_data['devices']]
    plots    = [PlotRecord(
                    plot_id=p['plot_id'], zone_id=p['zone_id'],
                    plot_type=p.get('plot_type',''), soil_type=p.get('soil_type',''),
                    crop_type=p.get('crop_type',''))
                for p in res_data['plots']]
    zones    = [ZoneRecord(zone_id=z['zone_id'], zone_name=z['zone_name'],
                           zone_type=z['zone_type'])
                for z in res_data['zones']]

    # Compute ticks
    time_to_min  = {'seconds': 1/60, 'minutes': 1, 'hours': 60, 'days': 1440}
    tick_to_min  = {'seconds': 1/60, 'minutes': 1, 'hours': 60}
    duration_min = sim_end   * time_to_min[time_unit]
    min_per_tick = tick_to_min[tick_unit]
    end_tick     = int(duration_min / min_per_tick)
    ms_per_tick  = int(min_per_tick * 60 * 1000)

    sim_cfg = {
        'start_time':  0,
        'end_time':    end_tick,
        'time_unit':   'ticks',
        'tick_unit':   tick_unit,
        'ms_per_tick': ms_per_tick,
        'seed':        seed,
    }

    # Codegen
    import random
    random.seed(seed)
    cg      = CodeGenerator(prog, devices, plots, zones, sim_cfg)
    compiled = cg.generate()

    actual_devices = len(compiled.get('bindings', []))

    # Run simulator with memory tracking
    tracemalloc.start()
    t0 = time.perf_counter()

    tick_times = []      # wall time at each 10% milestone
    last_pct   = 0

    def on_tick_milestone(pct, wall_so_far):
        nonlocal last_pct
        if pct - last_pct >= 10:
            tick_times.append({'pct': pct, 'wall_s': round(wall_so_far, 4)})
            last_pct = pct

    event_counts = {'persistent': 0, 'triggered': 0}

    def count_ev(ev):
        if ev.get('triggered_by'): event_counts['triggered'] += 1
        else:                       event_counts['persistent'] += 1

    sim = FastSimulator(compiled=compiled, ms_per_tick=ms_per_tick, verbose=0)

    # Wrap run to capture milestone timings
    orig_run = sim.run

    class _TimedSim(type(sim)):
        pass

    # Run it
    sim.run(output_fn=count_ev)

    wall = time.perf_counter() - t0
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_events     = event_counts['persistent'] + event_counts['triggered']
    events_per_sec   = total_events / max(wall, 1e-6)
    ticks_total      = end_tick
    ms_per_1k_ticks  = (wall * 1000) / max(ticks_total, 1) * 1000

    return {
        'n_devices_requested': n_devices,
        'n_devices_actual':    actual_devices,
        'n_ticks':             ticks_total,
        'wall_seconds':        round(wall, 4),
        'events_total':        total_events,
        'events_persistent':   event_counts['persistent'],
        'events_triggered':    event_counts['triggered'],
        'events_per_second':   round(events_per_sec, 1),
        'ms_per_1k_ticks':     round(ms_per_1k_ticks, 2),
        'peak_memory_mb':      round(peak_mem / 1024 / 1024, 2),
        'dropped_failure':     sim.stats.get('dropped_failure', 0),
        'dropped_no_change':   sim.stats.get('dropped_no_change', 0),
    }


# ── Benchmark sweep ──────────────────────────────────────────────────────────

def run_benchmark(
    schema_text:  str,
    rules_text:   str,
    counts:       list,
    sim_end:      int,
    time_unit:    str,
    tick_unit:    str,
    seed:         int = 42,
) -> list:
    results = []
    baseline_wall = None
    baseline_evs  = None

    print(f'\n{"═"*72}')
    print(f'  PERFORMANCE BENCHMARK')
    print(f'  Sim duration: {sim_end} {time_unit}  |  Tick unit: {tick_unit}')
    print(f'{"═"*72}')
    print(f'  {"Devices":>8}  {"Wall(s)":>8}  {"Ev/s":>10}  '
          f'{"Total Ev":>10}  {"ms/1kTick":>10}  {"Mem MB":>7}  {"Scale":>6}')
    print(f'  {"─"*8}  {"─"*8}  {"─"*10}  '
          f'{"─"*10}  {"─"*10}  {"─"*7}  {"─"*6}')

    for n in counts:
        try:
            r = run_one(
                schema_text=schema_text, rules_text=rules_text,
                n_devices=n, sim_end=sim_end, time_unit=time_unit,
                tick_unit=tick_unit, seed=seed,
            )

            if baseline_wall is None:
                baseline_wall = r['wall_seconds']
                baseline_evs  = r['events_per_second']

            scale = r['wall_seconds'] / baseline_wall if baseline_wall > 0 else 1.0
            r['scale_vs_baseline'] = round(scale, 2)
            results.append(r)

            # Colour: green if near-linear scaling, yellow if degrading, red if bad
            G = '\033[92m'; Y = '\033[93m'; R = '\033[91m'; X = '\033[0m'
            expected_scale = r['n_devices_actual'] / results[0]['n_devices_actual']
            actual_scale   = scale
            ratio          = actual_scale / max(expected_scale, 1)
            sc = G if ratio < 1.3 else (Y if ratio < 2.0 else R)

            print(f'  {r["n_devices_actual"]:>8,}  {r["wall_seconds"]:>8.2f}  '
                  f'{r["events_per_second"]:>10,.0f}  '
                  f'{r["events_total"]:>10,}  {r["ms_per_1k_ticks"]:>10.2f}  '
                  f'{r["peak_memory_mb"]:>7.1f}  {sc}{scale:>5.1f}×{X}')

        except Exception as e:
            print(f'  {n:>8,}  ERROR: {e}')
            results.append({'n_devices_requested': n, 'error': str(e)})

    print(f'{"═"*72}')
    _print_scaling_analysis(results)
    return results


def _print_scaling_analysis(results: list):
    valid = [r for r in results if 'error' not in r and len(results) > 1]
    if len(valid) < 2:
        return

    print(f'\n  SCALING ANALYSIS')
    print(f'  {"─"*60}')

    # Fit log-log slope to estimate scaling exponent
    import math
    xs = [math.log(r['n_devices_actual']) for r in valid]
    ys = [math.log(r['wall_seconds'])     for r in valid]
    n  = len(xs)
    if n >= 2:
        sx  = sum(xs); sy = sum(ys)
        sxx = sum(x*x for x in xs)
        sxy = sum(x*y for x,y in zip(xs,ys))
        slope = (n*sxy - sx*sy) / max(n*sxx - sx*sx, 1e-9)
        print(f'  Scaling exponent: {slope:.2f}  '
              f'(1.0=linear  1.5=sublinear degradation  2.0=quadratic)')
        if slope < 1.15:
            print(f'  \033[92m✓ Near-linear scaling — engine handles load well\033[0m')
        elif slope < 1.5:
            print(f'  \033[93m⚠ Mild superlinear scaling — some bottleneck emerging\033[0m')
        else:
            print(f'  \033[91m✗ Superlinear scaling — bottleneck identified\033[0m')

    # Bottleneck hint
    first, last = valid[0], valid[-1]
    dev_ratio  = last['n_devices_actual'] / max(first['n_devices_actual'], 1)
    wall_ratio = last['wall_seconds']     / max(first['wall_seconds'], 1e-6)
    drop_first = first.get('dropped_no_change', 0) / max(first['events_total'], 1)
    drop_last  = last.get('dropped_no_change', 0)  / max(last['events_total'], 1)

    print(f'\n  Device count scaled {dev_ratio:.1f}×  →  Wall time scaled {wall_ratio:.1f}×')
    if wall_ratio > dev_ratio * 1.5:
        print(f'  Hint: Wall time grew faster than device count.')
        print(f'        Likely bottleneck: rule engine window store or heap re-push overhead.')
    else:
        print(f'  Hint: Wall time grew proportionally — no obvious single bottleneck.')

    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description='IoT Simulator Performance Benchmark')
    p.add_argument('--schema',     required=True)
    p.add_argument('--rules',      default=None)
    p.add_argument('--sim-end',    type=int,   default=7)
    p.add_argument('--time-unit',  default='days',
                   choices=['seconds','minutes','hours','days'])
    p.add_argument('--tick-unit',  default='minutes',
                   choices=['seconds','minutes','hours'])
    p.add_argument('--counts',     type=int, nargs='+',
                   default=[10, 50, 100, 250, 500, 1000],
                   help='Device counts to benchmark')
    p.add_argument('--seed',       type=int, default=42)
    p.add_argument('--output',     default='out/benchmark.json')
    p.add_argument('--plot',       action='store_true',
                   help='Plot results (requires matplotlib)')
    args = p.parse_args()

    schema_text = Path(args.schema).read_text()
    rules_text  = Path(args.rules).read_text() if args.rules else ''

    results = run_benchmark(
        schema_text=schema_text,
        rules_text=rules_text,
        counts=args.counts,
        sim_end=args.sim_end,
        time_unit=args.time_unit,
        tick_unit=args.tick_unit,
        seed=args.seed,
    )

    # Save JSON
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump({
            'config': {
                'sim_end': args.sim_end, 'time_unit': args.time_unit,
                'tick_unit': args.tick_unit, 'seed': args.seed,
            },
            'results': results,
        }, f, indent=2)
    print(f'\n  Benchmark results saved → {args.output}')

    if args.plot:
        _plot(results, args.output.replace('.json', '.png'))


def _plot(results: list, path: str):
    try:
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
    except ImportError:
        print('  matplotlib not available — skipping plot')
        return

    valid = [r for r in results if 'error' not in r]
    if not valid:
        return

    xs     = [r['n_devices_actual']  for r in valid]
    walls  = [r['wall_seconds']      for r in valid]
    evs    = [r['events_per_second'] for r in valid]
    mems   = [r['peak_memory_mb']    for r in valid]

    fig = plt.figure(figsize=(14, 9))
    fig.suptitle('IoT Simulator — Performance Benchmark', fontsize=14, fontweight='bold')
    gs  = gridspec.GridSpec(2, 2, figure=fig, hspace=0.4, wspace=0.35)

    # ── Wall time vs device count ──
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(xs, walls, 'o-', color='#2A6496', linewidth=2, markersize=6)
    # Ideal linear reference
    ax1.plot(xs, [walls[0] * x / xs[0] for x in xs],
             '--', color='#aaa', linewidth=1, label='Linear (ideal)')
    ax1.set_xlabel('Number of devices')
    ax1.set_ylabel('Wall time (seconds)')
    ax1.set_title('Wall time vs device count')
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    # ── Throughput vs device count ──
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.plot(xs, evs, 'o-', color='#27AE60', linewidth=2, markersize=6)
    ax2.set_xlabel('Number of devices')
    ax2.set_ylabel('Events per second')
    ax2.set_title('Throughput vs device count')
    ax2.grid(True, alpha=0.3)

    # ── Scaling exponent (log-log) ──
    import math
    ax3 = fig.add_subplot(gs[1, 0])
    log_x = [math.log10(x) for x in xs]
    log_w = [math.log10(w) for w in walls]
    ax3.plot(log_x, log_w, 'o-', color='#8E44AD', linewidth=2, markersize=6)
    # Reference lines
    for exp, label, color in [(1.0, 'Linear O(n)', '#27AE60'),
                               (1.5, 'O(n^1.5)',   '#F39C12'),
                               (2.0, 'Quadratic O(n²)', '#E74C3C')]:
        ys_ref = [log_w[0] + exp * (x - log_x[0]) for x in log_x]
        ax3.plot(log_x, ys_ref, '--', color=color, linewidth=1, label=label)
    ax3.set_xlabel('log₁₀(devices)')
    ax3.set_ylabel('log₁₀(wall seconds)')
    ax3.set_title('Log-log scaling plot')
    ax3.legend(fontsize=8)
    ax3.grid(True, alpha=0.3)

    # ── Memory usage ──
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.plot(xs, mems, 'o-', color='#E67E22', linewidth=2, markersize=6)
    ax4.set_xlabel('Number of devices')
    ax4.set_ylabel('Peak memory (MB)')
    ax4.set_title('Memory usage vs device count')
    ax4.grid(True, alpha=0.3)

    plt.savefig(path, dpi=140, bbox_inches='tight')
    print(f'  Plot saved → {path}')
    plt.close()


if __name__ == '__main__':
    main()
