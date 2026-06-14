"""
tools/generate_devices.py

Generates a realistic resource JSON (zones, plots, devices) at arbitrary scale.
Used as input to the compiler for performance benchmarking.

Usage:
    python tools/generate_devices.py --devices 500 --output resources_500.json
    python tools/generate_devices.py --devices 1000 --zones 5 --plots-per-zone 40

Scale model:
    - N devices are distributed across zones and plots
    - Each plot gets a proportional share of devices
    - Device classes are assigned proportionally to real farm sensor ratios:
        50% SoilMoistureSensor  (most common — multiple per plot, at 2 depths)
        20% AirTemperatureSensor
        15% HumiditySensor
        10% NPKSensor
         5% other (CO2, wind, etc — represented as AirTemp for now)
"""

import argparse
import json
import math
import random
from pathlib import Path


# Sensor mix ratios — should sum to 1.0
SENSOR_MIX = [
    ('SoilMoistureSensor',    0.50),
    ('AirTemperatureSensor',  0.20),
    ('HumiditySensor',        0.15),
    ('NPKSensor',             0.15),
]

ZONE_TYPES  = ['IRRIGATED', 'NON_IRRIGATED', 'INDOOR']
PLOT_TYPES  = ['OPEN_FIELD', 'GREENHOUSE', 'ORCHARD']
CROP_TYPES  = ['wheat', 'barley', 'tomato', 'maize', 'soybean', 'lettuce']
SOIL_TYPES  = ['CLAY', 'LOAM', 'SANDY', 'SILT']


def generate(
    n_devices:        int,
    n_zones:          int = 3,
    plots_per_zone:   int = None,   # auto if None
    seed:             int = 42,
) -> dict:
    rng = random.Random(seed)

    # Auto-scale plots per zone
    if plots_per_zone is None:
        # Roughly 5-8 devices per plot on average
        total_plots = max(n_zones, n_devices // 6)
        plots_per_zone = max(1, total_plots // n_zones)

    # ── Zones ──────────────────────────────────────────────────────────────
    zones = []
    for z in range(1, n_zones + 1):
        zone_type = ZONE_TYPES[(z - 1) % len(ZONE_TYPES)]
        zones.append({
            'zone_id':   z,
            'zone_name': f'Zone_{z}',
            'zone_type': zone_type,
            'area_ha':   round(rng.uniform(5.0, 50.0), 1),
        })

    # ── Plots ───────────────────────────────────────────────────────────────
    plots = []
    plot_id = 101
    for zone in zones:
        for p in range(plots_per_zone):
            plots.append({
                'plot_id':   plot_id,
                'zone_id':   zone['zone_id'],
                'plot_name': f'Plot_{plot_id}',
                'area_ha':   round(rng.uniform(1.0, 10.0), 1),
                'plot_type': rng.choice(PLOT_TYPES),
                'soil_type': rng.choice(SOIL_TYPES),
                'crop_type': rng.choice(CROP_TYPES),
            })
            plot_id += 1

    # ── Devices ─────────────────────────────────────────────────────────────
    # Assign devices to plots round-robin, respecting sensor mix ratios
    devices        = []
    device_counter = {cls: 1 for cls, _ in SENSOR_MIX}

    # Build weighted list of device classes
    classes = []
    for cls, ratio in SENSOR_MIX:
        count = max(1, round(n_devices * ratio))
        classes.extend([cls] * count)
    rng.shuffle(classes)
    classes = classes[:n_devices]  # trim to exact count

    plot_cycle = [p['plot_id'] for p in plots]
    for i, device_class in enumerate(classes):
        plot_id_assigned = plot_cycle[i % len(plot_cycle)]

        # Soil sensors get a depth
        depth_cm = None
        if 'Soil' in device_class:
            depth_cm = rng.choice([10, 20, 30, 40])

        prefix = device_class[:3].upper()
        num    = device_counter[device_class]
        device_counter[device_class] += 1
        device_id = f'{prefix}_{num:04d}'

        devices.append({
            'device_id':    device_id,
            'device_class': device_class,
            'plot_id':      plot_id_assigned,
            'depth_cm':     depth_cm,
        })

    return {
        'zones':   zones,
        'plots':   plots,
        'devices': devices,
    }


def print_summary(data: dict):
    zones   = data['zones']
    plots   = data['plots']
    devices = data['devices']

    from collections import Counter
    zone_types   = Counter(z['zone_type'] for z in zones)
    device_types = Counter(d['device_class'] for d in devices)

    print(f'  Zones   : {len(zones)}  {dict(zone_types)}')
    print(f'  Plots   : {len(plots)}  ({len(plots)//len(zones)} per zone avg)')
    print(f'  Devices : {len(devices)}')
    for cls, cnt in sorted(device_types.items()):
        print(f'    {cls:<28} {cnt:>5}  ({100*cnt//len(devices)}%)')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Generate resource JSON for IoT simulator')
    p.add_argument('--devices',        type=int, default=100,  help='Total device count')
    p.add_argument('--zones',          type=int, default=3,    help='Number of zones')
    p.add_argument('--plots-per-zone', type=int, default=None, help='Plots per zone (auto if omitted)')
    p.add_argument('--seed',           type=int, default=42)
    p.add_argument('--output',         default=None,           help='Output file path (stdout if omitted)')
    args = p.parse_args()

    data = generate(
        n_devices=args.devices,
        n_zones=args.zones,
        plots_per_zone=args.plots_per_zone,
        seed=args.seed,
    )

    print(f'Generated resource data:', flush=True)
    print_summary(data)

    out = json.dumps(data, indent=2)
    if args.output:
        Path(args.output).write_text(out)
        print(f'Saved → {args.output}')
    else:
        print(out)
