-- ═══════════════════════════════════════════════
-- DEVICE CLASSES
-- ═══════════════════════════════════════════════

CREATE DEVICE_CLASS SoilMoistureSensor (
    MEASURES    Moisture       UNIT  "percent_VWC"   RANGE (0.0, 100.0)   RESOLUTION 0.1,
    ZONE        ENUM *
)

CREATE DEVICE_CLASS AirTemperatureSensor (
    MEASURES    AirTemp        UNIT  "celsius"        RANGE (-20.0, 60.0)  RESOLUTION 0.1,
    ZONE        ENUM *
)

CREATE DEVICE_CLASS HumiditySensor (
    MEASURES    Humidity       UNIT  "percent_RH"     RANGE (0.0, 100.0)   RESOLUTION 0.5,
    ZONE        ENUM *
)

CREATE DEVICE_CLASS NPKSensor (
    MEASURES    Nitrogen       UNIT  "mg_per_kg"      RANGE (0.0, 999.0)   RESOLUTION 1.0,
    MEASURES    Phosphorus     UNIT  "mg_per_kg"      RANGE (0.0, 999.0)   RESOLUTION 1.0,
    MEASURES    Potassium      UNIT  "mg_per_kg"      RANGE (0.0, 999.0)   RESOLUTION 1.0,
    ZONE        ENUM *
)

-- ═══════════════════════════════════════════════
-- TABLES
-- ═══════════════════════════════════════════════

CREATE TABLE zones (
    zone_id    INTEGER PRIMARY KEY,
    zone_name  STRING,
    zone_type  ENUM,
    area_ha    FLOAT
)

CREATE TABLE plots (
    plot_id    INTEGER PRIMARY KEY,
    zone_id    INTEGER REFERENCES zones(zone_id),
    plot_name  STRING,
    crop_type  STRING
)

CREATE TABLE devices (
    device_id    STRING PRIMARY KEY,
    device_class STRING REFERENCES DEVICE_CLASS,
    plot_id      INTEGER REFERENCES plots(plot_id),
    depth_cm     INTEGER
)

-- ═══════════════════════════════════════════════
-- EVENT TYPES
-- ═══════════════════════════════════════════════

CREATE EVENTTYPE sensor_reading (
    EVENTTIME T      INTEGER,
    device_id        STRING,
    measure_type     ENUM,
    value            FLOAT,
    value2           FLOAT,
    value3           FLOAT,
    depth_cm         INTEGER
)

CREATE EVENTTYPE metric_avg (
    EVENTTIME T      INTEGER,
    device_id        STRING,
    measure_type     ENUM,
    avg_value        FLOAT
)

CREATE EVENTTYPE alert (
    EVENTTIME T      INTEGER,
    device_id        STRING,
    alert_type       ENUM,
    value            FLOAT
)

CREATE EVENTTYPE report (
    EVENTTIME T      INTEGER,
    device_id        STRING,
    report_type      ENUM
)

-- ═══════════════════════════════════════════════
-- EVENT STREAM
-- Only sensor_reading goes into the stream.
-- metric_avg, alert, report are computed by the
-- rule engine and never transmitted physically.
-- ═══════════════════════════════════════════════

CREATE EVENTSTREAM agriculture_stream (
    EVENTTYPE  sensor_reading,

    ARRIVALS (
        Moisture   ON_CHANGE (2.0)    FAILURE 0.10,
        AirTemp    PERIODIC (5 MIN)   FAILURE 0.05,
        Humidity   PERIODIC (5 MIN)   FAILURE 0.05,
        Nitrogen   PERIODIC (1 DAYS)  FAILURE 0.12,
        Phosphorus PERIODIC (1 DAYS)  FAILURE 0.12,
        Potassium  PERIODIC (1 DAYS)  FAILURE 0.12
    )
)

-- ═══════════════════════════════════════════════
-- DISTRIBUTIONS
-- ═══════════════════════════════════════════════

DISTRIBUTION FOR Moisture (
    WHERE ZONE = 1 (
        NORMAL (mean = 65.0, std_dev = 8.0)       PROB 0.90
        ABOVE (
            NORMAL (mean = 88.0, std_dev = 4.0)   PROB 0.03
        )
        BELOW (
            NORMAL (mean = 18.0, std_dev = 5.0)   PROB 0.07
        )
    )
    WHERE ZONE = 2 (
        NORMAL (mean = 32.0, std_dev = 10.0)      PROB 0.80
        ABOVE (
            NORMAL (mean = 62.0, std_dev = 5.0)   PROB 0.05
        )
        BELOW (
            NORMAL (mean = 8.0, std_dev = 3.0)    PROB 0.15
        )
    )
)

DISTRIBUTION FOR AirTemp (
    WHERE ZONE = 1 (
        NORMAL (mean = 22.0, std_dev = 4.0)       PROB 0.85
        ABOVE (
            NORMAL (mean = 40.0, std_dev = 3.0)   PROB 0.08
        )
        BELOW (
            NORMAL (mean = 1.0, std_dev = 1.5)    PROB 0.07
        )
    )
    WHERE ZONE = 2 (
        NORMAL (mean = 24.0, std_dev = 3.0)       PROB 0.92
        ABOVE (
            NORMAL (mean = 38.0, std_dev = 2.0)   PROB 0.05
        )
        BELOW (
            NORMAL (mean = 2.0, std_dev = 1.0)    PROB 0.03
        )
    )
)

DISTRIBUTION FOR Humidity (
    WHERE ZONE = 1 (
        NORMAL (mean = 55.0, std_dev = 12.0)      PROB 0.82
        ABOVE (
            NORMAL (mean = 88.0, std_dev = 5.0)   PROB 0.12
        )
        BELOW (
            NORMAL (mean = 12.0, std_dev = 4.0)   PROB 0.06
        )
    )
    WHERE ZONE = 2 (
        NORMAL (mean = 72.0, std_dev = 6.0)       PROB 0.93
        ABOVE (
            NORMAL (mean = 92.0, std_dev = 3.0)   PROB 0.04
        )
        BELOW (
            NORMAL (mean = 42.0, std_dev = 5.0)   PROB 0.03
        )
    )
)

DISTRIBUTION FOR Nitrogen (
    WHERE ZONE = 1 (
        NORMAL (mean = 180.0, std_dev = 30.0)     PROB 0.85
        ABOVE (
            NORMAL (mean = 340.0, std_dev = 30.0) PROB 0.05
        )
        BELOW (
            NORMAL (mean = 55.0, std_dev = 15.0)  PROB 0.10
        )
    )
    WHERE ZONE = 2 (
        NORMAL (mean = 220.0, std_dev = 20.0)     PROB 0.90
        ABOVE (
            NORMAL (mean = 370.0, std_dev = 20.0) PROB 0.04
        )
        BELOW (
            NORMAL (mean = 60.0, std_dev = 12.0)  PROB 0.06
        )
    )
)

DISTRIBUTION FOR Phosphorus (
    WHERE ZONE = 1 (
        NORMAL (mean = 80.0, std_dev = 18.0)      PROB 0.85
        ABOVE (
            NORMAL (mean = 165.0, std_dev = 15.0) PROB 0.05
        )
        BELOW (
            NORMAL (mean = 25.0, std_dev = 8.0)   PROB 0.10
        )
    )
    WHERE ZONE = 2 (
        NORMAL (mean = 100.0, std_dev = 14.0)     PROB 0.90
        ABOVE (
            NORMAL (mean = 195.0, std_dev = 12.0) PROB 0.04
        )
        BELOW (
            NORMAL (mean = 28.0, std_dev = 7.0)   PROB 0.06
        )
    )
)

DISTRIBUTION FOR Potassium (
    WHERE ZONE = 1 (
        NORMAL (mean = 160.0, std_dev = 28.0)     PROB 0.85
        ABOVE (
            NORMAL (mean = 300.0, std_dev = 25.0) PROB 0.05
        )
        BELOW (
            NORMAL (mean = 40.0, std_dev = 12.0)  PROB 0.10
        )
    )
    WHERE ZONE = 2 (
        NORMAL (mean = 200.0, std_dev = 18.0)     PROB 0.90
        ABOVE (
            NORMAL (mean = 338.0, std_dev = 18.0) PROB 0.04
        )
        BELOW (
            NORMAL (mean = 42.0, std_dev = 10.0)  PROB 0.06
        )
    )
)
