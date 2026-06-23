"""Single source for magic values scattered across the codebase: Thermostat
modes + cluster/attribute ids, sensor keys, color-temperature bounds, and the
Matter manual-pairing-code -> PIN extractor."""

# -- Thermostat SystemMode values (cluster 513, attr 28) for AC control ------
# 0=Off,1=Auto,3=Cool,4=Heat,5=EmergencyHeat,6=Precooling,7=FanOnly,8=Dry,9=Sleep
THERMO_MODE_OFF = 0
THERMO_MODE_AUTO = 1
THERMO_MODE_COOL = 3
THERMO_MODE_HEAT = 4
THERMO_MODE_EMERGENCY_HEAT = 5
THERMO_MODE_PRECOOLING = 6
THERMO_MODE_FAN_ONLY = 7
THERMO_MODE_DRY = 8
THERMO_MODE_SLEEP = 9

THERMO_VALID_MODES = {0, 1, 3, 4, 5, 6, 7, 8, 9}
# Modes whose setpoint is the heating setpoint (attr 18).
THERMO_HEAT_MODES = {THERMO_MODE_HEAT, THERMO_MODE_EMERGENCY_HEAT}
# Modes whose setpoint is the cooling setpoint (attr 17).
THERMO_COOL_MODES = {THERMO_MODE_COOL, THERMO_MODE_PRECOOLING}

# -- Thermostat cluster + attribute ids --------------------------------------
THERMOSTAT_CLUSTER = 513
ATTR_LOCAL_TEMPERATURE = 0
ATTR_COOLING_SETPOINT = 17
ATTR_HEATING_SETPOINT = 18
ATTR_SYSTEM_MODE = 28

# -- Sensors -----------------------------------------------------------------
SENSOR_KEYS = [
    "illuminance",
    "temperature",
    "pressure",
    "humidity",
    "occupancy",
    "contact",
    "rain",
]

# -- Color temperature -------------------------------------------------------
MIRED_MIN, MIRED_MAX = 153, 500  # Matter ColorControl spec range


def extract_matter_pin(setup_code: str) -> int:
    """Convert a Matter manual pairing code to a PIN."""
    clean = setup_code.replace("-", "").replace(" ", "")
    if len(clean) not in (11, 21) or not clean.isdigit():
        raise ValueError("Invalid manual pairing code format")
    return (int(clean[6:10]) << 14) | (int(clean[1:6]) & 0x3FFF)
