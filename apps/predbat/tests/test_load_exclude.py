"""
Unit tests for load_exclude_entities history subtraction.
"""

from datetime import datetime, timedelta, timezone

from tests.test_infra import TestHAInterface

TEST_NOW = datetime(2024, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
HISTORY_POINTS = 10
FILTER_MINUTE = 24 * 60 - 5
FILTER_STEP = 5
TOLERANCE = 0.0001


def _prepare_predbat(my_predbat):
    """Reset the small slice of Predbat state used by these tests."""
    assert my_predbat is not None, "test_load_exclude requires the shared my_predbat instance"

    my_predbat.now_utc = TEST_NOW
    my_predbat.minutes_now = 12 * 60
    my_predbat.max_days_previous = 1
    my_predbat.days_previous = [1]
    my_predbat.days_previous_weight = [1]
    my_predbat.load_minutes_age = 1
    my_predbat.base_load = 0
    my_predbat.car_charging_hold = False
    my_predbat.car_charging_energy = {}
    my_predbat.car_charging_threshold = 999
    my_predbat.car_charging_rate = [0]
    my_predbat.iboost_energy_subtract = True
    my_predbat.iboost_energy_today = {}
    my_predbat.load_exclude_subtract = True
    my_predbat.load_exclude_energy = {}
    my_predbat.args.pop("load_exclude_entities", None)
    my_predbat.ha_interface = TestHAInterface()
    return my_predbat


def _set_sensor(my_predbat, entity_id, unit=None, state="0"):
    """Add a synthetic HA sensor state to the TestHAInterface dummy store."""
    item = {"state": state}
    if unit is not None:
        item["unit_of_measurement"] = unit
    my_predbat.ha_interface.dummy_items[entity_id] = item


def _build_cumulative_minutes(points, per_minute_kwh):
    """Build cumulative minute data where minute 0 is now and larger keys are older."""
    return {minute: (points - minute) * per_minute_kwh for minute in range(points + 1)}


def _build_constant_minutes(points, value):
    """Build per-minute data with the same value at every minute offset."""
    return {minute: value for minute in range(points + 1)}


def _build_history(now_utc, minute_values, unit=None):
    """Convert minute-offset values into Home Assistant history ordered oldest to newest."""
    history = []
    for minute in sorted(minute_values.keys(), reverse=True):
        item = {
            "state": str(minute_values[minute]),
            "last_updated": (now_utc - timedelta(minutes=minute)).isoformat(),
        }
        if unit is not None:
            item["attributes"] = {"unit_of_measurement": unit}
        history.append(item)
    return history


def _patch_history_store(my_predbat, history_store):
    """Patch get_history_wrapper to return entity-specific synthetic histories."""
    original_get_history_wrapper = my_predbat.get_history_wrapper

    def mock_get_history_wrapper(entity_id, days=30, required=True, tracked=True):
        """Return synthetic history for the requested entity."""
        return history_store.get(entity_id, [])

    my_predbat.get_history_wrapper = mock_get_history_wrapper
    return original_get_history_wrapper


def _capture_logs(my_predbat):
    """Replace Predbat logging with a list capture and return the original logger."""
    messages = []
    original_log = my_predbat.log

    def capture_log(message, *args, **kwargs):
        """Capture a log message for assertions."""
        messages.append(str(message))

    my_predbat.log = capture_log
    return messages, original_log


def _filtered_load(my_predbat, load_minutes):
    """Run the historical load filter at a minute that maps to recent synthetic data."""
    return my_predbat.get_filtered_load_minute(load_minutes, FILTER_MINUTE, historical=True, step=FILTER_STEP)


def _assert_close(actual, expected, message):
    """Assert that two floating point values are close enough for minute-data tests."""
    assert abs(actual - expected) <= TOLERANCE, "{} expected {:.6f}, got {:.6f}".format(message, expected, actual)


def _snapshot_predbat(my_predbat):
    """Capture Predbat state mutated by the load exclusion tests."""
    attribute_names = [
        "now_utc",
        "minutes_now",
        "max_days_previous",
        "days_previous",
        "days_previous_weight",
        "load_minutes_age",
        "base_load",
        "car_charging_hold",
        "car_charging_energy",
        "car_charging_threshold",
        "car_charging_rate",
        "iboost_energy_subtract",
        "iboost_energy_today",
        "load_exclude_subtract",
        "load_exclude_energy",
    ]
    return {
        "attributes": {name: getattr(my_predbat, name, None) for name in attribute_names},
        "args": dict(my_predbat.args),
        "ha_interface": my_predbat.ha_interface,
        "log": my_predbat.log,
        "get_history_wrapper": my_predbat.get_history_wrapper,
    }


def _restore_predbat(my_predbat, snapshot):
    """Restore Predbat state captured before the aggregate test ran."""
    for name, value in snapshot["attributes"].items():
        setattr(my_predbat, name, value)
    my_predbat.args.clear()
    my_predbat.args.update(snapshot["args"])
    my_predbat.ha_interface = snapshot["ha_interface"]
    my_predbat.log = snapshot["log"]
    my_predbat.get_history_wrapper = snapshot["get_history_wrapper"]


def test_load_exclude_disabled_by_default(my_predbat=None):
    """Verify missing load_exclude_entities leaves helper output empty and filtering unchanged."""
    my_predbat = _prepare_predbat(my_predbat)
    load_minutes = _build_cumulative_minutes(HISTORY_POINTS, 0.2)
    baseline = _filtered_load(my_predbat, load_minutes)

    result = my_predbat.load_exclude_data(my_predbat.now_utc)
    filtered = _filtered_load(my_predbat, load_minutes)

    assert result == {}, "Missing load_exclude_entities should return an empty dict"
    assert my_predbat.load_exclude_energy == {}, "Missing load_exclude_entities should leave load_exclude_energy empty"
    assert filtered == baseline, "Filtering should be unchanged when load_exclude_data has no configured entities"


def test_load_exclude_empty_list(my_predbat=None):
    """Verify an explicit empty entity list returns empty data without warnings."""
    my_predbat = _prepare_predbat(my_predbat)
    my_predbat.args["load_exclude_entities"] = []
    log_messages, original_log = _capture_logs(my_predbat)

    try:
        result = my_predbat.load_exclude_data(my_predbat.now_utc)
    finally:
        my_predbat.log = original_log

    assert result == {}, "Empty load_exclude_entities should return an empty dict"
    assert my_predbat.load_exclude_energy == {}, "Empty load_exclude_entities should leave load_exclude_energy empty"
    assert not any("Warn:" in message for message in log_messages), "Empty load_exclude_entities should not log warnings"


def test_load_exclude_kwh_sensor_subtracts(my_predbat=None):
    """Verify a cumulative kWh exclusion sensor is loaded and subtracted from historical load."""
    my_predbat = _prepare_predbat(my_predbat)
    entity_id = "sensor.fake_miner_kwh"
    miner_per_minute = 0.1
    load_per_minute = 0.3
    _set_sensor(my_predbat, entity_id, unit="kWh")
    history_store = {entity_id: [_build_history(my_predbat.now_utc, _build_cumulative_minutes(HISTORY_POINTS, miner_per_minute), unit="kWh")]}
    original_get_history_wrapper = _patch_history_store(my_predbat, history_store)

    try:
        my_predbat.args["load_exclude_entities"] = [entity_id]
        result = my_predbat.load_exclude_data(my_predbat.now_utc)
    finally:
        my_predbat.get_history_wrapper = original_get_history_wrapper

    assert result, "kWh exclusion sensor should return minute data"
    _assert_close(my_predbat.load_exclude_energy[5], 0.5, "Cumulative miner kWh at sample minute")

    load_minutes = _build_cumulative_minutes(HISTORY_POINTS, load_per_minute)
    load_yesterday, load_yesterday_raw = _filtered_load(my_predbat, load_minutes)

    expected_baseline = load_per_minute * FILTER_STEP
    expected_miner = miner_per_minute * FILTER_STEP
    _assert_close(load_yesterday_raw, expected_baseline, "Raw historical load")
    _assert_close(load_yesterday, max(0, expected_baseline - expected_miner), "Filtered load after kWh exclusion")


def test_load_exclude_power_sensor_auto_detected(my_predbat=None):
    """Verify a W power sensor is auto-detected, integrated to kWh, and subtracted."""
    my_predbat = _prepare_predbat(my_predbat)
    entity_id = "sensor.fake_miner_power"
    power_watts = 1000.0
    load_per_minute = 0.1
    expected_per_minute = power_watts / 60000.0
    _set_sensor(my_predbat, entity_id, unit="W")
    history_store = {entity_id: [_build_history(my_predbat.now_utc, _build_constant_minutes(HISTORY_POINTS, power_watts), unit="W")]}
    original_get_history_wrapper = _patch_history_store(my_predbat, history_store)

    try:
        my_predbat.args["load_exclude_entities"] = [entity_id]
        result = my_predbat.load_exclude_data(my_predbat.now_utc)
    finally:
        my_predbat.get_history_wrapper = original_get_history_wrapper

    assert result, "W exclusion sensor should return integrated minute data"
    assert my_predbat.load_exclude_energy[0] > my_predbat.load_exclude_energy[1], "Integrated power data should grow towards minute 0"
    _assert_close(my_predbat.load_exclude_energy[0] - my_predbat.load_exclude_energy[1], expected_per_minute, "Integrated kWh per minute")

    load_minutes = _build_cumulative_minutes(HISTORY_POINTS, load_per_minute)
    load_yesterday, load_yesterday_raw = _filtered_load(my_predbat, load_minutes)

    expected_baseline = load_per_minute * FILTER_STEP
    expected_power = expected_per_minute * FILTER_STEP
    _assert_close(load_yesterday_raw, expected_baseline, "Raw historical load")
    _assert_close(load_yesterday, max(0, expected_baseline - expected_power), "Filtered load after W exclusion")


def test_load_exclude_unsupported_unit_skipped(my_predbat=None):
    """Verify a configured entity with an unsupported unit is skipped with a warning."""
    my_predbat = _prepare_predbat(my_predbat)
    entity_id = "sensor.fake_temperature"
    _set_sensor(my_predbat, entity_id, unit="\u00b0C")
    my_predbat.args["load_exclude_entities"] = [entity_id]
    log_messages, original_log = _capture_logs(my_predbat)

    try:
        result = my_predbat.load_exclude_data(my_predbat.now_utc)
    finally:
        my_predbat.log = original_log

    assert result == {}, "Unsupported unit should be skipped"
    assert my_predbat.load_exclude_energy == {}, "Unsupported unit should not populate load_exclude_energy"
    assert len([message for message in log_messages if "unsupported unit_of_measurement" in message]) == 1, "Unsupported unit should log one warning"
    assert not any("active for" in message for message in log_messages), "Unsupported unit should not log any used sensors"


def test_load_exclude_missing_unit_attribute_skipped(my_predbat=None):
    """Verify a configured entity without unit_of_measurement is skipped gracefully."""
    my_predbat = _prepare_predbat(my_predbat)
    entity_id = "sensor.fake_missing_unit"
    _set_sensor(my_predbat, entity_id)
    my_predbat.args["load_exclude_entities"] = [entity_id]

    result = my_predbat.load_exclude_data(my_predbat.now_utc)

    assert result == {}, "Missing unit_of_measurement should be skipped"
    assert my_predbat.load_exclude_energy == {}, "Missing unit_of_measurement should not populate load_exclude_energy"


def test_load_exclude_switch_off_no_subtraction(my_predbat=None):
    """Verify load_exclude_subtract disables subtraction even when exclusion data exists."""
    my_predbat = _prepare_predbat(my_predbat)
    my_predbat.args["load_exclude_entities"] = ["sensor.fake_miner_kwh"]
    my_predbat.load_exclude_energy = _build_cumulative_minutes(HISTORY_POINTS, 0.3)
    my_predbat.load_exclude_subtract = False

    load_minutes = _build_cumulative_minutes(HISTORY_POINTS, 0.4)
    load_yesterday, load_yesterday_raw = _filtered_load(my_predbat, load_minutes)

    expected_baseline = 0.4 * FILTER_STEP
    _assert_close(load_yesterday_raw, expected_baseline, "Raw historical load")
    _assert_close(load_yesterday, expected_baseline, "Filtered load with load_exclude_subtract off")


def test_load_exclude_negative_clamped_to_zero(my_predbat=None):
    """Verify exclusion energy larger than load is clamped to zero rather than negative."""
    my_predbat = _prepare_predbat(my_predbat)
    my_predbat.load_exclude_energy = _build_cumulative_minutes(HISTORY_POINTS, 0.3)

    load_minutes = _build_cumulative_minutes(HISTORY_POINTS, 0.1)
    load_yesterday, load_yesterday_raw = _filtered_load(my_predbat, load_minutes)

    _assert_close(load_yesterday_raw, 0.1 * FILTER_STEP, "Raw historical load")
    _assert_close(load_yesterday, 0.0, "Filtered load clamped to zero")


def test_load_exclude_multiple_entities_summed(my_predbat=None):
    """Verify multiple kWh exclusion sensors are summed into one cumulative dictionary."""
    my_predbat = _prepare_predbat(my_predbat)
    entity_one = "sensor.fake_miner_one_kwh"
    entity_two = "sensor.fake_miner_two_kwh"
    first_per_minute = 0.1
    second_per_minute = 0.25
    _set_sensor(my_predbat, entity_one, unit="kWh")
    _set_sensor(my_predbat, entity_two, unit="kWh")
    history_store = {
        entity_one: [_build_history(my_predbat.now_utc, _build_cumulative_minutes(HISTORY_POINTS, first_per_minute), unit="kWh")],
        entity_two: [_build_history(my_predbat.now_utc, _build_cumulative_minutes(HISTORY_POINTS, second_per_minute), unit="kWh")],
    }
    original_get_history_wrapper = _patch_history_store(my_predbat, history_store)

    try:
        my_predbat.args["load_exclude_entities"] = [entity_one, entity_two]
        result = my_predbat.load_exclude_data(my_predbat.now_utc)
    finally:
        my_predbat.get_history_wrapper = original_get_history_wrapper

    assert result, "Multiple kWh exclusion sensors should return minute data"
    expected_sample = (first_per_minute + second_per_minute) * 5
    expected_delta = first_per_minute + second_per_minute
    _assert_close(my_predbat.load_exclude_energy[5], expected_sample, "Summed cumulative exclusion at sample minute")
    _assert_close(my_predbat.get_from_incrementing(my_predbat.load_exclude_energy, 5), expected_delta, "Summed exclusion increment at sample minute")


def test_load_exclude(my_predbat=None):
    """Run all load_exclude_entities subtraction tests for the custom unit-test registry."""
    assert my_predbat is not None, "test_load_exclude requires the shared my_predbat instance"
    sub_tests = [
        test_load_exclude_disabled_by_default,
        test_load_exclude_empty_list,
        test_load_exclude_kwh_sensor_subtracts,
        test_load_exclude_power_sensor_auto_detected,
        test_load_exclude_unsupported_unit_skipped,
        test_load_exclude_missing_unit_attribute_skipped,
        test_load_exclude_switch_off_no_subtraction,
        test_load_exclude_negative_clamped_to_zero,
        test_load_exclude_multiple_entities_summed,
    ]

    snapshot = _snapshot_predbat(my_predbat)
    try:
        for sub_test in sub_tests:
            sub_test(my_predbat)
    finally:
        _restore_predbat(my_predbat, snapshot)

    return False
