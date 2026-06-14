"""
compiler.py

Main entrypoint for the IoT schema compiler.

Usage:
    python compiler.py --schema agriculture.sql --rules agriculture.iotdl
                       --devices devices.json --output out/

Pipeline:
    1. Lex + Parse schema file   → AST
    2. Lex + Parse rules file    → rule AST nodes appended
    3. Semantic validation        → error/warn report
    4. Code generation            → profiles.json + bindings
    5. Run simulation             → events.jsonl
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lexer.tokenizer    import Lexer
from parser.parser      import Parser
from validator.semantic import SemanticValidator
from codegen.generator  import CodeGenerator, DeviceRecord, PlotRecord, ZoneRecord
from engine.simulator   import Simulator
from engine.fast_simulator import FastSimulator


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def parse_schema(text: str):
    tokens  = Lexer(text).tokenize()
    program = Parser(tokens).parse()
    return program


def load_resource_data(devices_json: str):
    """
    Load physical resource data from a JSON file.
    Expected format:
    {
        "devices": [{"device_id": "SM_001", "device_class": "SoilMoistureSensor",
                     "plot_id": 11, "depth_cm": 30}, ...],
        "plots":   [{"plot_id": 11, "zone_id": 3, ...}, ...],
        "zones":   [{"zone_id": 3, "zone_name": "North Field",
                     "zone_type": "IRRIGATED"}, ...]
    }
    """
    data    = json.loads(Path(devices_json).read_text())
    devices = [DeviceRecord(**d) for d in data.get('devices', [])]
    plots   = [PlotRecord(**p)   for p in data.get('plots',   [])]
    zones   = [ZoneRecord(**z)   for z in data.get('zones',   [])]
    return devices, plots, zones


# -----------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(description='IoT Schema Compiler & Simulator')
    p.add_argument('--schema',    required=True, help='Schema file (.sql)')
    p.add_argument('--rules',     required=False, default=None,
                   help='IoTDL rules file (.iotdl)')
    p.add_argument('--devices',   required=False, default=None,
                   help='Resource data JSON (devices, plots, zones)')
    p.add_argument('--output',    default='out',
                   help='Output directory (default: out/)')
    p.add_argument('--sim-start', type=int,   default=0,
                   help='Simulation start in --time-unit units (default: 0)')
    p.add_argument('--sim-end',   type=int,   default=90,
                   help='Simulation end in --time-unit units (default: 90)')
    p.add_argument('--time-unit', default='days',
                   choices=['seconds', 'minutes', 'hours', 'days'],
                   help='Unit for sim-start and sim-end (default: days)')
    p.add_argument('--tick-unit', default='minutes',
                   choices=['seconds', 'minutes', 'hours'],
                   help='Resolution of one simulation tick (default: minutes)')
    p.add_argument('--seed',      type=int,   default=None)
    p.add_argument('--verbose',   type=int,   default=0,
                   help='Print progress every N ticks')
    p.add_argument('--validate-only', action='store_true',
                   help='Run validation only, do not simulate')
    return p


def main():
    args    = build_arg_parser().parse_args()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Parse ────────────────────────────────────────────────────────
    print('[Compiler] Parsing schema …')
    schema_text = Path(args.schema).read_text()
    program     = parse_schema(schema_text)

    # Append rules if provided
    if args.rules:
        print('[Compiler] Parsing rules …')
        rules_text   = Path(args.rules).read_text()
        rules_prog   = parse_schema(rules_text)
        program.rules.extend(rules_prog.rules)

    print(f'  Device classes : {len(program.device_classes)}')
    print(f'  Tables         : {len(program.tables)}')
    print(f'  Event types    : {len(program.event_types)}')
    print(f'  Event streams  : {len(program.event_streams)}')
    print(f'  Distributions  : {len(program.distributions)}')
    print(f'  Rules          : {len(program.rules)}')

    # ── 2. Validate ─────────────────────────────────────────────────────
    print('\n[Compiler] Validating …')
    validator = SemanticValidator(program)
    errors    = validator.validate()

    for err in errors:
        prefix = '  ERROR' if err.severity == 'ERROR' else '  WARN '
        print(f'{prefix} (L{err.line}): {err.message}')

    if validator.has_errors:
        print(f'\n[Compiler] {sum(1 for e in errors if e.severity=="ERROR")} error(s) — '
              f'compilation aborted.')
        sys.exit(1)
    else:
        print(f'  Validation passed '
              f'({sum(1 for e in errors if e.severity=="WARN")} warnings)')

    if args.validate_only:
        return

    # ── 3. Load resource data ────────────────────────────────────────────
    devices, plots, zones = [], [], []
    if args.devices:
        print('\n[Compiler] Loading resource data …')
        devices, plots, zones = load_resource_data(args.devices)
        print(f'  Devices: {len(devices)}  Plots: {len(plots)}  Zones: {len(zones)}')
    else:
        print('\n[Compiler] No resource data provided — generating synthetic bindings …')
        # Auto-generate one device per distribution per zone_id
        for dc in program.device_classes:
            for dist in program.distributions:
                for wb in dist.where_blocks:
                    zone_id   = wb.zone_id
                    device_id = f'{dc.name[:6]}_{dist.measure_name[:4]}_z{zone_id}'
                    devices.append(DeviceRecord(device_id=device_id,
                                                device_class=dc.name,
                                                plot_id=zone_id * 10))
                    plots.append(PlotRecord(plot_id=zone_id * 10, zone_id=zone_id))
                    zones.append(ZoneRecord(zone_id=zone_id))
        # Deduplicate
        seen_plots = {}
        seen_zones = {}
        plots = list({p.plot_id: p for p in plots}.values())
        zones = list({z.zone_id: z for z in zones}.values())

    # ── 4. Code generation ───────────────────────────────────────────────
    print('\n[Compiler] Generating profiles …')
    # Convert sim duration to ticks
    time_unit_to_min  = {'seconds': 1/60, 'minutes': 1, 'hours': 60, 'days': 1440}
    tick_unit_to_min  = {'seconds': 1/60, 'minutes': 1, 'hours': 60}
    sim_duration_min  = args.sim_end   * time_unit_to_min[args.time_unit]
    sim_start_min     = args.sim_start * time_unit_to_min[args.time_unit]
    min_per_tick      = tick_unit_to_min[args.tick_unit]
    start_tick        = int(sim_start_min / min_per_tick)
    end_tick          = int(sim_duration_min / min_per_tick)
    ms_per_tick       = int(min_per_tick * 60 * 1000)

    print(f'[Compiler] Simulation: {args.sim_start}-{args.sim_end} {args.time_unit} ')
    print(f'           Tick unit : 1 {args.tick_unit} = {ms_per_tick} ms/tick')
    print(f'           Total ticks: {end_tick - start_tick:,}')

    sim_cfg = {
        'start_time':  start_tick,
        'end_time':    end_tick,
        'time_unit':   'ticks',
        'tick_unit':   args.tick_unit,
        'ms_per_tick': ms_per_tick,
        'seed':        args.seed,
    }

    import random
    if args.seed is not None:
        random.seed(args.seed)

    codegen  = CodeGenerator(program=program, devices=devices, plots=plots,
                              zones=zones, simulation_cfg=sim_cfg)
    compiled = codegen.generate()

    # Write profiles.json
    profiles_path = out_dir / 'profiles.json'
    profiles_path.write_text(json.dumps(compiled, indent=2))
    print(f'  profiles.json → {profiles_path}')
    print(f'  Profiles generated : {len(compiled["profiles"])}')
    print(f'  Bindings generated : {len(compiled["bindings"])}')

    # ── 5. Simulate ──────────────────────────────────────────────────────
    print('\n[Compiler] Running simulation …')
    events_path = out_dir / 'events.jsonl'

    with open(events_path, 'w') as f:
        def write_event(ev):
            d = ev if isinstance(ev, dict) else ev.to_dict()
            f.write(json.dumps(d) + '\n')

        sim = FastSimulator(compiled=compiled, ms_per_tick=ms_per_tick,
                            verbose=args.verbose)
        sim.run(output_fn=write_event)

    print(f'\n  events.jsonl → {events_path}')
    print('\n[Compiler] Done.')


if __name__ == '__main__':
    main()
