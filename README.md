# IoTSim

**A General Framework for Generating Pseudo-Real Sensor Streams with Causal Event Chains**

Advisor: Dr Jianwen Su (UCSB Computer Science Dept)

Developing IoT stream-processing systems is often constrained by a lack of realistic sensor event stream data that captures statistical signal behaviour, device heterogeneity, network unreliability, and the causal relationships between physical readings and derived or triggered events. Generating such data today typically requires writing custom simulation scripts tailored to a specific deployment, with no reusable specification layer that separates what sensors measure from how they behave statistically, how they transmit, and what events they trigger.


This project presents IoT-Sim, a declarative language framework for specifying and simulating (pseudo) realistic IoT sensor streams, and an event data generator that accurately produces such pseudo real data. The language separates five orthogonal concerns — hardware characteristics, resource topology, streaming event schemas, network transmission behaviour, and statistical signal models — into distinct composable constructs, so that the same compiler and engine serve any IoT domain without modification. Each sensor measure follows a declared statistical distribution with probabilistic tiers representing normal operation, anomalous high conditions, and anomalous low conditions. A companion rule language allows users to define arbitrary window-based computations over incoming sensor readings — expressing sliding aggregations, threshold conditions, and multi-source correlations that produce new derived event streams tailored to the semantics of their specific application. The schema itself serves as a formal, human-readable specification of the IoT deployment it models, cleanly separating what a sensor physically is from how it behaves, where it is deployed, and what events it participates in.


---

## Table of Contents

1. [Overview](#1-overview)
2. [Why IoTSim](#2-why-iotsim)
3. [Architecture](#3-architecture)
4. [Installation](#4-installation)
5. [Quick Start](#5-quick-start)
6. [Language Reference](#6-language-reference)
   - 6.1 [CREATE DEVICE CLASS](#61-create-device-class)
   - 6.2 [CREATE EVENTTYPE](#62-create-eventtype)
   - 6.3 [CREATE EVENTSTREAM](#63-create-eventstream)
   - 6.4 [DISTRIBUTION FOR](#64-distribution-for)
   - 6.5 [IoTDL Rules](#65-iotdl-rules)
7. [Compiler](#7-compiler)
   - 7.1 [Pipeline Stages](#71-pipeline-stages)
   - 7.2 [CLI Reference](#72-cli-reference)
   - 7.3 [Output Files](#73-output-files)
8. [Simulation Engine](#8-simulation-engine)
   - 8.1 [Heap Scheduler](#81-heap-scheduler)
   - 8.2 [Value Generation](#82-value-generation)
   - 8.3 [Rule Engine](#83-rule-engine)
   - 8.4 [Time Model](#84-time-model)
9. [Correctness Validation](#9-correctness-validation)
   - 9.1 [Running the Analyser](#91-running-the-analyser)
   - 9.2 [Mixture Statistics](#92-mixture-statistics)
   - 9.3 [Validation Metrics](#93-validation-metrics)
10. [Performance Benchmarks](#10-performance-benchmarks)
    - 10.1 [Running Benchmarks](#101-running-benchmarks)
    - 10.2 [Results](#102-results)
11. [Debugging Rules with IoTSim](#11-debugging-rules-with-iotsim)
12. [Examples](#12-examples)
    - 12.1 [Agriculture](#121-agriculture)
    - 12.2 [Hospital](#122-hospital)
13. [Project Structure](#13-project-structure)
14. [Future Work](#14-future-work)
15. [References](#15-references)

---

## 1. Overview

Testing and developing IoT stream-processing systems requires realistic sensor data that captures:

- Statistical signal behaviour per deployment zone
- Device heterogeneity across sensor types
- Network unreliability and transmission failure
- Causal event chains — alerts that fire from readings, reports that follow alerts

IoTSim addresses this by providing a declarative language with five constructs, each covering one independent concern. The same compiler and engine work for any IoT domain — agriculture, hospital, smart building, logistics — without modification. Only the schema changes.

```
schema.sql  +  rules.iotdl
        │
        ▼
   IoTSim Compiler
   ├── Lexer + Parser
   ├── Semantic Validator
   └── Code Generator  ──→  profiles.json
                                  │
                                  ▼
                         Simulation Engine  ──→  events.jsonl
                                                 correctness report
```

---

## 2. Why IoTSim

| Problem | IoTSim's answer |
|---|---|
| Custom simulation scripts are hardcoded to one deployment | Declarative schema separates specification from simulation code |
| No reuse across domains | Same compiler and engine; only schema changes |
| No statistical structure in synthetic data | Per-zone parameter models with NORMAL / ABOVE / BELOW tiers |
| No causal event chains | IoTDL rule language for window-based triggered events |
| No verifiability | Automated correctness analyser using mixture statistics |
| Rare conditions hard to test | Tune tier probabilities in schema to force any condition |

---

## 3. Architecture

IoTSim separates five orthogonal concerns into distinct constructs:

| Construct | Concern |
|---|---|
| `CREATE DEVICE CLASS` | Physical hardware — what a sensor measures |
| `CREATE EVENTTYPE` | Schema of a streaming event |
| `CREATE EVENTSTREAM` | Transmission behaviour and network failure |
| `DISTRIBUTION FOR` | Statistical parameter model per zone |
| `IoTDL Rules` | Causal event chain logic |

The compiler reads the schema and rules, joins device instances against the enterprise database to resolve zone-to-distribution bindings, compiles rules into executable objects, and writes `profiles.json`. The simulation engine consumes `profiles.json` and generates events using a heap-based scheduler.

---

## 4. Installation

**Requirements:**
- Python 3.9+
- No external dependencies for the core compiler and engine

```bash
git clone https://github.com/bhargavi-hash/IoT-Sim.git
cd IoT-Sim
pip install -r requirements.txt   # optional: matplotlib for benchmark plots
```

**Optional — benchmark plotting:**
```bash
pip install matplotlib
```

---

## 5. Quick Start

**Step 1 — Run the compiler and simulation:**

```bash
python3 compiler.py \
    --schema    examples/agriculture/agriculture.sql \
    --rules     examples/agriculture/agriculture.iotdl \
    --sim-start 0 \
    --sim-end   90 \
    --time-unit days \
    --tick-unit minutes \
    --seed      42 \
    --output    out/
```

**Step 2 — Run the correctness analyser:**

```python
from eval.correctness import analyse, print_report, save_json

results = analyse('out/events.jsonl', 'out/profiles.json')
print_report(results)
save_json(results, 'out/correctness.json')
```

**Step 3 — Run performance benchmarks:**

```bash
python3 tools/benchmark.py \
    --schema    examples/agriculture/agriculture.sql \
    --rules     examples/agriculture/agriculture.iotdl \
    --sim-end   1 --time-unit days --tick-unit minutes \
    --counts    10 50 100 250 500 1000 \
    --output    out/benchmark.json \
    --plot
```

---

## 6. Language Reference

IoTSim schemas are written in a SQL-like declarative syntax. A schema file (`.sql`) contains the five construct types. A rules file (`.iotdl`) contains IoTDL rules.

### 6.1 CREATE DEVICE CLASS

Describes physical hardware — what it measures, units, range, and resolution. Says nothing about where the device is deployed, how it samples, or what values it produces statistically.

The `ZONE ENUM *` declaration marks the zone field as the distribution discriminator. The compiler uses this to resolve which parameter model applies to each physical device by joining against the enterprise database.

```sql
CREATE DEVICE CLASS SoilMoistureSensor (
    MEASURES Moisture
        UNIT        "percent_VWC"
        RANGE       (0.0, 100.0)
        RESOLUTION  0.1,
    zone ENUM *
)

-- Multi-measure sensor
CREATE DEVICE CLASS NPKSensor (
    MEASURES Nitrogen   UNIT "mg_per_kg" RANGE (0.0, 999.0) RESOLUTION 1.0,

    MEASURES Phosphorus UNIT "mg_per_kg" RANGE (0.0, 999.0) RESOLUTION 1.0,

    MEASURES Potassium  UNIT "mg_per_kg" RANGE (0.0, 999.0) RESOLUTION 1.0,

    zone ENUM *
)
```

**Multi-measure sensors** store readings in order: `value` = first measure, `value2` = second, `value3` = third.

### 6.2 CREATE EVENTTYPE

Defines the schema of a streaming event. The `device_id` field uses `fieldname: TableName(column) FK` syntax to reference the enterprise device table — the compiler reads this to know where to resolve device-to-zone mappings.

Only `sensor_reading` events enter the stream. Computed events (`metric_avg`, `alert`, `report`) are produced by the rule engine only.

```sql
CREATE EVENTTYPE sensor_reading (
    EVENTTIME T    INTEGER,
    INGESTTIME I   MAXDELAY 5 MIN,
    device_id:     Device(device_id) FK,
    measure_type   ENUM,
    value          FLOAT,
    value2         FLOAT,   -- optional: second measure
    value3         FLOAT    -- optional: third measure
)

CREATE EVENTTYPE alert (
    EVENTTIME T    INTEGER,
    device_id:     Device(device_id) FK,
    alert_type     ENUM,
    value          FLOAT
)

CREATE EVENTTYPE report (
    EVENTTIME T    INTEGER,
    device_id:     Device(device_id) FK,
    report_type    ENUM
)
```

**INGESTTIME / MAXDELAY** — models the delay between sensor emission time (`EVENTTIME`) and engine ingestion time. `MAXDELAY 5 MIN` means events arriving more than 5 minutes late are flagged as stale.

### 6.3 CREATE EVENTSTREAM

Models the physical transmission channel. Declares which event type flows through the stream, and for each measure: arrival mode, and failure probability.

```sql
CREATE EVENTSTREAM agriculture_stream (
    EVENTTYPE  sensor_reading,

    ARRIVALS (
        Moisture      ON_CHANGE   (2.0)      FAILURE 0.10,
        AirTemp       PERIODIC    (5 MIN)    FAILURE 0.05,
        Nitrogen      PERIODIC    (1 DAY)    FAILURE 0.12,
        Rainfall      ON_THRESHOLD(0.2)      FAILURE 0.07,
        WindSpeed     PERIODIC    (1 MIN)    FAILURE 0.04
    )
)
```

**Arrival modes:**

| Mode | Behaviour |
|---|---|
| `PERIODIC (N UNIT)` | Fixed clock interval. Units: `MS` `SEC` `MIN` `HOURS` `DAYS` |
| `ON_CHANGE (threshold)` | Emit only when value shifts by ≥ threshold |
| `ON_THRESHOLD (value)` | Emit only when value crosses the declared boundary |

**FAILURE** — probability of dropping any given reading (network loss, sensor fault). Modelled independently per reading.

### 6.4 DISTRIBUTION FOR

Maps a device class measure to a statistical parameter model, per zone. The `WHERE ZONE = N` blocks are matched against the zone resolved from the enterprise database for each physical device.

Each WHERE block has three tiers:
- `NORMAL` — regular operating conditions
- `ABOVE` — values exceeding normal range (heatwave, tachycardia, flooding)
- `BELOW` — values beneath normal range (frost, hypoxia, drought)

Probabilities within each WHERE block must sum to 1.0 (validated at compile time).

**STICKY** at tier level means transitions into or out of that tier happen gradually rather than abruptly. Tiers without STICKY can switch instantly — useful for phenomena like rainfall that starts and stops suddenly.

```sql
DISTRIBUTION FOR AirTemp (
    WHERE ZONE = 1 (
        NORMAL (mean = 22.0, std_dev = 4.0)   PROB 0.85  STICKY,
        ABOVE (
            NORMAL (mean = 40.0, std_dev = 3.0) PROB 0.08  STICKY
            -- heatwave builds and fades gradually
        )
        BELOW (
            NORMAL (mean = 1.0,  std_dev = 1.5) PROB 0.07  STICKY
            -- frost also gradual
        )
    )
    WHERE ZONE = 2 (
        NORMAL (mean = 24.0, std_dev = 3.0)   PROB 0.92  STICKY,
        ABOVE (
            NORMAL (mean = 38.0, std_dev = 2.0) PROB 0.05  STICKY
        )
        BELOW (
            NORMAL (mean = 2.0,  std_dev = 1.0) PROB 0.03  STICKY
        )
    )
)

-- Rainfall: no STICKY — can start and stop abruptly
DISTRIBUTION FOR Rainfall (
    WHERE ZONE = 1 (
        NORMAL (mean = 0.0, std_dev = 0.0)   PROB 0.85,
        ABOVE (
            EXPONENTIAL (mean = 8.0)          PROB 0.15
        )
    )
)
```

**Supported distributions:** `NORMAL`, `UNIFORM`, `EXPONENTIAL`, `POISSON`, `BINOMIAL`

### 6.5 IoTDL Rules

IoTDL rules define triggered events — window-based computations over sensor readings that produce derived event streams. Rules are declared alongside the schema so the causal structure of the deployment is specified and verified in one place.

**Syntax:**

```
new event_type[window?](grouping_vars, (aggregations?)) @ (fire_time) :-
    source_clause @ time_var,
    constraint?;
```

**Window types:**
- `sliding(s, N)` — N most recent readings
- `tumbling(s, N)` — non-overlapping windows of size N
- _(no window)_ — fires immediately on each matching event

**Aggregation functions:** `avg`, `min`, `max`, `sum`, `count`, `last`

```sql
-- Sliding window average
new metric_avg[sliding(s, 6)](device_id, MOISTURE,
    (avg_value = avg(value))) @ (s + 7) :-
    sensor_reading(device_id, MOISTURE, value, _, _) @ z;

-- Tumbling window with threshold constraint
new alert[tumbling(s, 30 MIN)](device_id, DROUGHT_STRESS,
    (value = avg(value))) @ (s + 31 MIN) :-
    sensor_reading(device_id, MOISTURE, value, _, _) @ z,
    value < 25;

-- No window — fires immediately on each matching event
new alert(device_id, FROST_RISK) @ (T + 1) :-
    sensor_reading(device_id, AIR_TEMP, value, _) @ T,
    value < 3;

-- Chained rule — fires on a triggered alert event
new report[tumbling(s, 60 MIN)](device_id, IRRIGATION_NEEDED) @ (s + 61 MIN) :-
    alert(device_id, DROUGHT_STRESS, value) @ z,
    value < 25;
```

The `measure_type` field must be specified in rule bodies (e.g. `MOISTURE`, `AIR_TEMP`) to prevent rules from matching readings from the wrong sensor type.

---

## 7. Compiler

### 7.1 Pipeline Stages

| Stage | File | Description |
|---|---|---|
| Lexer | `lexer/tokenizer.py` | Tokenizes schema source. Handles keywords, identifiers, quoted strings, numeric literals, `--` comments. |
| Parser | `parser/parser.py` | Recursive-descent parser. Builds a typed AST from all five construct types plus IoTDL rules. |
| Semantic Validator | `validator/semantic.py` | Checks probability sums, distribution parameter completeness, referential integrity, measure name existence. |
| Code Generator | `codegen/generator.py` | Joins devices → enterprise DB → zones. Assigns distribution profiles per device. Compiles rules to structured objects. Writes `profiles.json`. |
| Simulation Engine | `engine/fast_simulator.py` | Consumes `profiles.json`. Runs the simulation. Writes `events.jsonl`. |

### 7.2 CLI Reference

```bash
python3 compiler.py [OPTIONS]

Options:
  --schema      PATH   Path to .sql schema file                    [required]
  --rules       PATH   Path to .iotdl rules file                   [optional]
  --sim-start   INT    Simulation start time                       [default: 0]
  --sim-end     INT    Simulation end time                         [required]
  --time-unit   STR    Unit for sim-start/end: seconds|minutes|hours|days
  --tick-unit   STR    Simulation resolution: seconds|minutes|hours
  --seed        INT    Random seed for reproducibility             [default: 42]
  --output      PATH   Output directory for profiles.json + events.jsonl
```

**Tick unit guidance:** set to the fastest sensor interval in your deployment. For agriculture with 1-minute wind sensors, use `--tick-unit minutes`. A 90-day simulation at 1-minute resolution = 129,600 ticks.

### 7.3 Output Files

| File | Description |
|---|---|
| `out/profiles.json` | Compiled profiles: device bindings, parameter models, rules, stream config |
| `out/events.jsonl` | Simulation output — one JSON object per event, newline-delimited |
| `out/correctness.json` | Correctness analysis results (if analyser is run) |
| `out/benchmark.json` | Benchmark sweep results (if benchmark runner is used) |

---

## 8. Simulation Engine

### 8.1 Heap Scheduler

The engine maintains a min-heap of scheduled device fire times. At each step it pops the device with the earliest tick, generates a value, applies failure probability, emits the event, then pushes the device back with its next fire tick. Cost is O(log N) per event where N is the number of devices — never O(T × D).

### 8.2 Value Generation

For each emission:
1. Roll a random number to select NORMAL, ABOVE, or BELOW tier based on declared probabilities.
2. Sample from that tier's statistical distribution.
3. Clamp to the declared RANGE from the device class.
4. Tag the tier name on the event (used by the correctness analyser).

When STICKY is active on a tier, the engine maintains tier state per device and interpolates the mean gradually during transitions rather than jumping directly.

### 8.3 Rule Engine

- **Tumbling window rules** — evaluated when the current tick coincides with a window boundary.
- **Sliding window rules** — evaluated on each event emit.
- **No-window rules** — fire immediately on each matching event.

Triggered events are stored in the window store so downstream chained rules can reference them.

### 8.4 Time Model

Simulation time is expressed in user-friendly units at the CLI and converted to ticks internally.

| Duration | Ticks (1-min tick unit) |
|---|---|
| 1 minute | 1 |
| 5 minutes | 5 |
| 1 hour | 60 |
| 1 day | 1,440 |
| 90 days | 129,600 |

---

## 9. Correctness Validation

The correctness analyser in `eval/correctness.py` compares observed simulation output against declared parameter models.

### 9.1 Running the Analyser

```python
from eval.correctness import analyse, print_report, save_json

results = analyse(
    events_jsonl  = 'out/events.jsonl',
    profiles_json = 'out/profiles.json',
    tolerance_mean = 0.15,   # ±15% of mixture mean
    tolerance_std  = 0.20,   # ±20% of mixture std
    tolerance_tier = 0.05,   # ±5 percentage points on tier rates
)

print_report(results)
save_json(results, 'out/correctness.json')
```

**Multi-measure sensors (NPK):** the analyser reads `value` for Nitrogen, `value2` for Phosphorus, `value3` for Potassium. This is configured via `MULTI_MEASURE_ORDER` in `eval/correctness.py`.

### 9.2 Mixture Statistics

Observed mean and std are compared against **mixture statistics** — the combined values across all tiers — not against the NORMAL tier alone.

```
mixture_mean = Σ pᵢ × μᵢ

mixture_var  = Σ pᵢ × (σᵢ² + (μᵢ − mixture_mean)²)
mixture_std  = √(mixture_var)
```

For AirTemp Zone 1 (NORMAL mean=22 prob=0.85, ABOVE mean=40 prob=0.08, BELOW mean=1 prob=0.07):
- `mixture_mean = 21.97`
- `mixture_std  = 8.44` (not 4.0 — between-tier variance dominates)

Comparing observed std against the NORMAL-only std of 4.0 would always fail even when the simulation is correct.

### 9.3 Validation Metrics

| Check | Description | Tolerance |
|---|---|---|
| Tier rates | Fraction of events in NORMAL / ABOVE / BELOW vs declared probabilities | ±5 percentage points |
| Mean | Observed mean vs mixture mean | ±15% of mixture mean |
| Std dev | Observed std vs mixture std | ±20% of mixture std |
| Range | Any reading outside declared device RANGE | Zero violations |
| Transition rate | Observed tier switch rate vs theoretical `1 − Σpᵢ²` | ±5 percentage points |

**Agriculture results (90-day, 48 devices, 1.85M events): 8/8 device/zone combinations PASS. Zero range violations.**

---

## 10. Performance Benchmarks

### 10.1 Running Benchmarks

```bash
python3 tools/benchmark.py \
    --schema    examples/agriculture/agriculture.sql \
    --rules     examples/agriculture/agriculture.iotdl \
    --sim-end   1 \
    --time-unit days \
    --tick-unit minutes \
    --counts    10 50 100 250 500 1000 \
    --seed      42 \
    --output    out/benchmark.json \
    --plot                          # requires matplotlib
```

Generates resource data at each device count using `tools/generate_devices.py` with realistic sensor mix ratios (50% SoilMoisture, 20% AirTemp, 15% Humidity, 15% NPK).

### 10.2 Results

1-day simulation, 1-minute ticks:

| Devices | Wall time (s) | Events/s |
|---|---|---|
| 14 | 0.14 | 30,657 |
| 64 | 0.88 | 29,749 |
| 130 | 1.55 | 30,099 |
| 326 | 4.04 | 29,714 |
| 650 | 7.54 | 29,571 |
| 1,300 | 16.72 | 27,958 |

**Scaling exponent: 1.03** (near-linear). Throughput stays at ~30K events/second from 14 to 1300 devices. The 9% drop is the O(log N) heap cost as device count grows.

**Full 90-day run:** 1.85 million events in 55 seconds at ~34K events/second. Output: ~275 MB JSON Lines.

**Output size estimates:**

| Scenario | Devices | Duration | Est. Events | Est. Size (JSONL) |
|---|---|---|---|---|
| Agriculture (baseline) | 48 | 90 days | 1.85M | 275 MB |
| Agriculture (large) | 500 | 90 days | 19M | ~2.8 GB |
| Hospital (200 beds) | 800 | 90 days | 625M | ~94 GB |

---

## 11. Debugging Rules with IoTSim

The schema is a controllable knob. By temporarily tuning tier probabilities you can force specific sensor conditions to isolate rule bugs without waiting for rare events to appear naturally.

**Scenario 1 — Force a tier (unit test a rule)**

HEATWAVE alert never fires during testing. Is the rule wrong or does heatwave data never appear?

```sql
DISTRIBUTION FOR AirTemp (
    WHERE ZONE = 1 (
        NORMAL (...) PROB 0.00,
        ABOVE  ( NORMAL (mean = 40.0, std_dev = 3.0) PROB 1.00 )
        -- force all readings into heatwave tier
    )
)
```

If the alert still does not fire, the bug is in the rule logic, not the data. Restore original probabilities once confirmed.

**Scenario 2 — Push to the boundary (test threshold logic)**

Drought alert fires inconsistently near the declared 25% moisture threshold.

```sql
DISTRIBUTION FOR Moisture (
    WHERE ZONE = 1 (
        BELOW ( NORMAL (mean = 25.0, std_dev = 0.5) PROB 0.99 )
        NORMAL (...) PROB 0.01
        -- cluster readings right at the decision boundary
    )
)
```

Exposes off-by-one errors and window size issues that would take days to appear in real data.

**Scenario 3 — Reproduce a rare co-occurrence (test composite rules)**

NPK_CRITICAL report requires both nitrogen and phosphorus deficiency simultaneously — statistically rare in a realistic simulation.

```sql
DISTRIBUTION FOR Nitrogen (
    WHERE ZONE = 1 (
        BELOW ( NORMAL (mean = 40.0, std_dev = 10.0) PROB 0.90 )
        NORMAL (...) PROB 0.10
    )
)
DISTRIBUTION FOR Phosphorus (
    WHERE ZONE = 1 (
        BELOW ( NORMAL (mean = 15.0, std_dev = 5.0) PROB 0.90 )
        NORMAL (...) PROB 0.10
    )
)
```

Co-occurring deficiencies appear in every window. Verify the composite rule then restore realistic probabilities.

---

## 12. Examples

### 12.1 Agriculture

Located in `examples/agriculture/`. Models a precision agriculture farm with three zone types.

**Device classes (10):** SoilMoistureSensor, SoilTemperatureSensor, AirTemperatureSensor, HumiditySensor, SolarRadiationSensor, RainfallSensor, WindSensor, CO2Sensor, NPKSensor, PHSensor

**Zone types:** IRRIGATED, NON_IRRIGATED, INDOOR

**IoTDL rules (14):** Drought stress, frost risk, heatwave, disease risk index, CO2 high/low, storm warning, NPK deficiency (N, P, K separately), pH alert, salinity alert, irrigation report, NPK composite report

**Run:**
```bash
python3 compiler.py \
    --schema examples/agriculture/agriculture.sql \
    --rules  examples/agriculture/agriculture.iotdl \
    --sim-end 90 --time-unit days --tick-unit minutes \
    --output out/
```

### 12.2 Hospital

Located in `examples/hospital/`. Models a multi-ward hospital. Demonstrates IoTSim generalisability — the same compiler and engine, zero modification.

**Device classes (7):** PulseOximeter, ECGMonitor, NIBPCuff, ThermometerProbe, RespiratoryBelt, ICPMonitor, RFIDReader

**Zone types:** ICU, CICU, NEURO_ICU, GENERAL, ED, TRAUMA

**Notable distributions:**
- `NEURO_ICU` SpO2 uses permissive hypertension baseline (mean systolic 138 mmHg) — clinical protocol for TBI patients
- `TRAUMA` models haemodynamic instability as the NORMAL state, recovery as ABOVE

**IoTDL rules:** Hypoxia alert, tachycardia alert, hypertension alert, bradycardia alert, deterioration report (requires hypoxia + tachycardia simultaneously), ICP critical

**Run:**
```bash
python3 compiler.py \
    --schema examples/hospital/hospital.sql \
    --rules  examples/hospital/hospital.iotdl \
    --sim-end 7 --time-unit days --tick-unit seconds \
    --output out/
```

---

## 13. Project Structure

```
iotsim/
├── compiler.py                  # CLI entrypoint
│
├── lexer/
│   └── tokenizer.py             # Tokenizer — keywords, literals, comments
│
├── parser/
│   └── parser.py                # Recursive-descent parser → AST
│
├── ast_nodes/
│   └── nodes.py                 # Typed AST node dataclasses
│
├── validator/
│   └── semantic.py              # Semantic validation
│                                # - probability sums per WHERE block
│                                # - distribution parameter completeness
│                                # - referential integrity
│                                # - measure name existence
│
├── codegen/
│   └── generator.py             # Code generator
│                                # - device → plot → zone binding resolution
│                                # - rule compilation
│                                # - profiles.json writer
│
├── engine/
│   ├── fast_simulator.py        # Primary: heap-based scheduler + rule engine
│   └── simulator.py             # Original simulator (slower, for reference)
│
├── eval/
│   └── correctness.py           # Correctness analyser
│                                # - mixture statistics (mean + std)
│                                # - tier rate analysis
│                                # - transition rate analysis
│                                # - range compliance
│
├── tools/
│   ├── generate_devices.py      # Resource JSON generator at arbitrary scale
│   └── benchmark.py             # Performance benchmark sweep
│
├── examples/
│   ├── agriculture/
│   │   ├── agriculture.sql      # Full agriculture schema
│   │   └── agriculture.iotdl   # Agriculture rules
│   └── hospital/
│       ├── hospital.sql         # Full hospital schema
│       └── hospital.iotdl      # Hospital rules
│
└── out/                         # Default output directory
    ├── profiles.json            # Compiled profiles (generated)
    ├── events.jsonl             # Simulation events (generated)
    ├── correctness.json         # Correctness results (generated)
    └── benchmark.json           # Benchmark results (generated)
```

---

## 14. Future Work

| Item | Description |
|---|---|
| Multiple event streams | Currently only one stream per simulation. Multiple parallel streams with independent failure rates and arrival configurations. |
| Custom distribution functions | User-defined Python callables as distribution implementations alongside the five built-in families. |
| Anomaly injection with temporal correlation | Per-device state machine that holds a tier for a declared duration, introducing genuine temporal correlation between consecutive readings. |
| Circadian and seasonal modulation | Sinusoidal layer over base parameters — daily cycles for temperature and heart rate, seasonal cycles for soil temperature. |
| Compact output format | Switch from JSON Lines (~150 bytes/event) to Parquet or binary MessagePack (5-10× smaller, faster analytics). |
| Real-time mode | Throttle the engine to wall-clock speed so IoTSim can feed a live stream processor during development. |
| Multi-source rules | Rules that join two event streams — e.g. hypoxia AND tachycardia on the same patient within a window. |
| Visual schema editor | Graphical tool for configuring device classes, zones, and parameter models without writing schema syntax. |

---

## 15. References

- Tyler et al. — CEPAL event type specification (FK syntax in EVENTTYPE adapted from this work)
- NEXMark Benchmark — Tucker et al. (motivating example of hardcoded data generators)
- DEBS Grand Challenge datasets (motivating example of domain-locked simulators)

---

## License

MIT License. See `LICENSE` for details.

---
<!-- 
## Citation

If you use IoTSim in your research, please cite:

```
@mastersthesis{iotsim2026,
  title   = {IoTSim: A Language Framework for Pseudo-Realistic IoT Sensor Stream Generation with Causal Event Chains},
  author  = {Your Name},
  school  = {Your University},
  year    = {2026}
}
``` -->