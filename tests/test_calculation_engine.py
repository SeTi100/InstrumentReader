import math
import pytest
from instrument_reader.core.calculation import (
    CalculationChannel,
    CalculationEngine,
    calculate_linear_regression,
    preprocess_formula,
    get_standard_presets,
)


def test_linear_regression():
    # Perfect line with slope -0.5
    points = [(0.0, 100.0), (10.0, 95.0), (20.0, 90.0)]
    res = calculate_linear_regression(points)
    assert res is not None
    slope, intercept = res
    assert pytest.approx(slope, rel=1e-5) == -0.5
    assert pytest.approx(intercept, rel=1e-5) == 100.0


def test_linear_regression_insufficient_points():
    assert calculate_linear_regression([]) is None
    assert calculate_linear_regression([(1.0, 50.0)]) is None
    # Extremely short time interval < 0.05s
    assert calculate_linear_regression([(1.0, 50.0), (1.01, 49.9)]) is None


def test_preprocess_formula():
    f, ref_map = preprocess_formula("{Waage 1} * 2 + {Thermo}")
    assert "_roi_ref_0" in f
    assert "_roi_ref_1" in f
    assert ref_map["_roi_ref_0"] == "Waage 1"
    assert ref_map["_roi_ref_1"] == "Thermo"

    f2, _ = preprocess_formula("10^3")
    assert f2 == "10**3"


def test_calculation_engine_rate_and_presets():
    engine = CalculationEngine()

    # Feed mass history: mass starts at 1000g and drops by 0.25 g/s over 20s
    t_start = 100.0
    for i in range(21):
        t = t_start + float(i)
        mass = 1000.0 - 0.25 * float(i)
        engine.add_reading("Waage", mass, timestamp=t)

    # Calculate rate over 10s window at t=120
    rate = engine.calculate_rate("Waage", window_seconds=10.0, current_time=120.0)
    assert rate is not None
    assert pytest.approx(rate, rel=1e-3) == 0.25

    slope = engine.calculate_slope("Waage", window_seconds=10.0, current_time=120.0)
    assert slope is not None
    assert pytest.approx(slope, rel=1e-3) == -0.25

    # Test Massenstrom in g/s
    ch_gs = CalculationChannel(
        id="c1",
        name="Massenstrom g/s",
        unit="g/s",
        formula="rate(m, window_s)",
        variables={"m": "Waage"},
        constants={"window_s": 10.0},
        decimal_places=3,
    )
    res_gs = engine.evaluate_channel(ch_gs, {}, current_time=120.0)
    assert res_gs.is_valid
    assert pytest.approx(res_gs.value, rel=1e-3) == 0.25
    assert res_gs.formatted_value == "0.250"

    # Test Massenstrom in g/h (0.25 g/s * 3600 = 900 g/h)
    ch_gh = CalculationChannel(
        id="c2",
        name="Massenstrom g/h",
        unit="g/h",
        formula="rate(m, window_s) * 3600",
        variables={"m": "Waage"},
        constants={"window_s": 10.0},
        decimal_places=1,
    )
    res_gh = engine.evaluate_channel(ch_gh, {}, current_time=120.0)
    assert res_gh.is_valid
    assert pytest.approx(res_gh.value, rel=1e-3) == 900.0
    assert res_gh.formatted_value == "900.0"


def test_gas_equation_concentration():
    engine = CalculationEngine()

    # Test concentration at operating temperature:
    # m_dot = 900 g/h
    # V_norm = 20 m3/h
    # T = 200 °C (473.15 K)
    # p = 1013.25 hPa
    # V_actual = 20 * (473.15 / 273.15) * 1 = 34.644 m3/h
    # c = 900 / 34.644 = 25.978 g/m3
    ch_conc = CalculationChannel(
        id="c3",
        name="Abgaskonzentration (g/m³)",
        unit="g/m³",
        formula="m_dot / (V_norm * ((T + 273.15) / 273.15) * (1013.25 / p))",
        variables={"m_dot": "Massenstrom", "T": "Thermo_1"},
        constants={"V_norm": 20.0, "p": 1013.25},
        decimal_places=3,
    )

    current_values = {
        "Massenstrom": 900.0,
        "Thermo_1": 200.0,
    }

    res = engine.evaluate_channel(ch_conc, current_values)
    assert res.is_valid
    expected_v_act = 20.0 * (473.15 / 273.15)
    expected_c = 900.0 / expected_v_act
    assert pytest.approx(res.value, rel=1e-3) == expected_c


def test_standard_presets():
    presets = get_standard_presets()
    assert len(presets) >= 8
    preset_ids = [p.id for p in presets]
    assert "mass_flow_gs" in preset_ids
    assert "mass_flow_gh" in preset_ids
    assert "concentration_operating" in preset_ids
    assert "concentration_norm" in preset_ids
    assert "concentration_ppm" in preset_ids
    assert "ghsv" in preset_ids
    assert "conversion_rate" in preset_ids
    assert "total_volume_flow" in preset_ids


def test_conversion_rate_preset():
    engine = CalculationEngine()
    ch = CalculationChannel(
        id="conv",
        name="Umsatzgrad η (%)",
        unit="%",
        formula="((c_in - c_out) / c_in) * 100",
        variables={"c_in": "VOC_Ein", "c_out": "VOC_Aus"},
        decimal_places=1,
    )
    current_values = {"VOC_Ein": 100.0, "VOC_Aus": 5.0}
    res = engine.evaluate_channel(ch, current_values)
    assert res.is_valid
    assert pytest.approx(res.value, rel=1e-3) == 95.0
    assert res.formatted_value == "95.0"


def test_channel_dependency_chain():
    engine = CalculationEngine()

    # Feed scale data
    for i in range(15):
        engine.add_reading("Waage", 500.0 - 0.1 * i, timestamp=10.0 + i)

    # Ch1: m_dot_gs = rate(m, 10)
    ch1 = CalculationChannel(
        id="ch1",
        name="m_dot_gs",
        unit="g/s",
        formula="rate(m, 10)",
        variables={"m": "Waage"},
        decimal_places=2,
    )
    # Ch2: m_dot_gh = m_dot_gs * 3600
    ch2 = CalculationChannel(
        id="ch2",
        name="m_dot_gh",
        unit="g/h",
        formula="m_dot_gs * 3600",
        variables={},
        decimal_places=1,
    )
    # Ch3: c_norm = m_dot_gh / V_norm
    ch3 = CalculationChannel(
        id="ch3",
        name="c_norm",
        unit="g/Nm³",
        formula="m_dot_gh / V_norm",
        constants={"V_norm": 10.0},
        decimal_places=2,
    )

    engine.add_channel(ch1)
    engine.add_channel(ch2)
    engine.add_channel(ch3)

    results = engine.calculate_all(current_time=24.0)
    assert len(results) == 3

    res1 = next(r for r in results if r.channel_id == "ch1")
    res2 = next(r for r in results if r.channel_id == "ch2")
    res3 = next(r for r in results if r.channel_id == "ch3")

    assert res1.is_valid and pytest.approx(res1.value, rel=1e-2) == 0.1
    assert res2.is_valid and pytest.approx(res2.value, rel=1e-2) == 360.0
    assert res3.is_valid and pytest.approx(res3.value, rel=1e-2) == 36.0


def test_safe_evaluator_security_and_errors():
    engine = CalculationEngine()

    # Division by zero
    ch_div_zero = CalculationChannel(id="err1", formula="10 / 0")
    res = engine.evaluate_channel(ch_div_zero, {})
    assert not res.is_valid
    assert "Division durch Null" in res.error_message

    # Syntax error
    ch_syntax = CalculationChannel(id="err2", formula="10 + * 2")
    res = engine.evaluate_channel(ch_syntax, {})
    assert not res.is_valid

    # Disallowed attribute access / code execution attempt
    ch_exploit = CalculationChannel(id="err3", formula="().__class__.__bases__[0]")
    res = engine.evaluate_channel(ch_exploit, {})
    assert not res.is_valid

    # Unknown variable
    ch_var = CalculationChannel(id="err4", formula="unknown_var * 2")
    res = engine.evaluate_channel(ch_var, {})
    assert not res.is_valid
    assert "unknown_var" in res.error_message


def test_reverse_order_multi_channel_dependency_chain():
    """Verify that multi-level dependencies resolve regardless of channel insertion order."""
    engine = CalculationEngine()

    for i in range(15):
        engine.add_reading("Waage", 500.0 - 0.1 * i, timestamp=10.0 + i)

    # Ch4 depends on Ch3, Ch3 depends on Ch2, Ch2 depends on Ch1, Ch1 depends on raw ROI
    ch4 = CalculationChannel(id="ch4", name="c_ppm", formula="(c_norm * 22.414 / 78.11) * 1000")
    ch3 = CalculationChannel(id="ch3", name="c_norm", formula="m_dot_gh / 10.0")
    ch2 = CalculationChannel(id="ch2", name="m_dot_gh", formula="m_dot_gs * 3600")
    ch1 = CalculationChannel(id="ch1", name="m_dot_gs", formula="rate(m, 10)", variables={"m": "Waage"})

    # Add in strictly reverse dependency order
    engine.add_channel(ch4)
    engine.add_channel(ch3)
    engine.add_channel(ch2)
    engine.add_channel(ch1)

    results = engine.calculate_all(current_time=24.0)
    assert len(results) == 4

    r1 = next(r for r in results if r.channel_id == "ch1")
    r2 = next(r for r in results if r.channel_id == "ch2")
    r3 = next(r for r in results if r.channel_id == "ch3")
    r4 = next(r for r in results if r.channel_id == "ch4")

    assert r1.is_valid and pytest.approx(r1.value, rel=1e-2) == 0.1
    assert r2.is_valid and pytest.approx(r2.value, rel=1e-2) == 360.0
    assert r3.is_valid and pytest.approx(r3.value, rel=1e-2) == 36.0
    assert r4.is_valid and pytest.approx(r4.value, rel=1e-2) == (36.0 * 22.414 / 78.11) * 1000


def test_nan_and_inf_handling():
    engine = CalculationEngine()

    # Poison attempt: adding NaN or Inf into history should be ignored
    engine.add_reading("Waage", float("nan"), timestamp=100.0)
    engine.add_reading("Waage", float("inf"), timestamp=100.5)
    assert len(engine.history.get("Waage", [])) == 0

    # Add valid readings
    engine.add_reading("Waage", 500.0, timestamp=101.0)
    engine.add_reading("Waage", 499.0, timestamp=102.0)
    slope = engine.calculate_slope("Waage", 10.0, current_time=102.0)
    assert slope is not None and not math.isnan(slope) and not math.isinf(slope)

    # Direct evaluation producing NaN or Inf
    ch_nan = CalculationChannel(id="c_nan", formula="val * 2", variables={"val": "v"})
    res_nan = engine.evaluate_channel(ch_nan, {"v": float("nan")})
    assert not res_nan.is_valid
    assert "NaN/Inf" in (res_nan.error_message or "")
    assert res_nan.formatted_value == "-"

    ch_inf = CalculationChannel(id="c_inf", formula="val * 2", variables={"val": "v"})
    res_inf = engine.evaluate_channel(ch_inf, {"v": float("inf")})
    assert not res_inf.is_valid
    assert "NaN/Inf" in (res_inf.error_message or "")
    assert res_inf.formatted_value == "-"


def test_rotameter_mass_flow_presets():
    engine = CalculationEngine()
    presets = {p.id: p for p in get_standard_presets()}
    assert "mass_flow_rotameter_gh" in presets
    assert "mass_flow_rotameter_gs" in presets

    p_gh = presets["mass_flow_rotameter_gh"]
    ch_gh = CalculationChannel(
        id="rot_gh",
        name=p_gh.name,
        formula=p_gh.formula,
        variables=dict(p_gh.default_variables),
        constants=dict(p_gh.default_constants),
    )
    # V_rot = 2.5 l/h, rho = 1000.0 g/l -> 2500 g/h
    res_gh = engine.evaluate_channel(ch_gh, {"Rotameter": 2.5})
    assert res_gh.is_valid
    assert pytest.approx(res_gh.value, rel=1e-3) == 2500.0

    p_gs = presets["mass_flow_rotameter_gs"]
    ch_gs = CalculationChannel(
        id="rot_gs",
        name=p_gs.name,
        formula=p_gs.formula,
        variables=dict(p_gs.default_variables),
        constants=dict(p_gs.default_constants),
    )
    # V_rot = 3.6 l/h, rho = 1000.0 g/l -> 3600 g/h / 3600 = 1.0 g/s
    res_gs = engine.evaluate_channel(ch_gs, {"Rotameter": 3.6})
    assert res_gs.is_valid
    assert pytest.approx(res_gs.value, rel=1e-3) == 1.0


def test_none_propagation_waiting_for_values():
    engine = CalculationEngine()
    ch = CalculationChannel(
        id="wait1",
        name="Sum",
        formula="a + b",
        variables={"a": "ROI1", "b": "ROI2"},
    )
    # ROI2 has not arrived yet (None)
    res = engine.evaluate_channel(ch, {"ROI1": 10.0, "ROI2": None})
    assert not res.is_valid
    assert "Warte auf Messwerte" in res.error_message
    assert res.formatted_value == "-"

