import ast
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any


@dataclass
class CalculationChannel:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    unit: str = ""
    formula: str = ""
    # Maps formula variable name (e.g. 'm', 'T') to ROI name or source
    variables: Dict[str, str] = field(default_factory=dict)
    # Maps formula variable name (e.g. 'V_norm', 'p') to fixed numeric value
    constants: Dict[str, float] = field(default_factory=dict)
    window_seconds: float = 10.0
    decimal_places: int = 2
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "unit": self.unit,
            "formula": self.formula,
            "variables": dict(self.variables),
            "constants": dict(self.constants),
            "window_seconds": self.window_seconds,
            "decimal_places": self.decimal_places,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CalculationChannel":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", ""),
            unit=data.get("unit", ""),
            formula=data.get("formula", ""),
            variables=dict(data.get("variables", {})),
            constants={k: float(v) for k, v in data.get("constants", {}).items()},
            window_seconds=float(data.get("window_seconds", 10.0)),
            decimal_places=int(data.get("decimal_places", 2)),
            description=data.get("description", ""),
        )


@dataclass
class CalculationResult:
    channel_id: str
    name: str
    value: Optional[float]
    unit: str
    formula: str
    is_valid: bool
    error_message: Optional[str] = None
    formatted_value: str = "-"


@dataclass
class CalculationPreset:
    id: str
    name: str
    category: str
    description: str
    formula: str
    unit: str
    default_variables: Dict[str, str]
    variable_descriptions: Dict[str, str]
    default_constants: Dict[str, float]
    constant_descriptions: Dict[str, str]
    window_seconds: float = 10.0
    decimal_places: int = 2


def get_standard_presets() -> List[CalculationPreset]:
    """Returns standard engineering presets for process engineering and exhaust gas catalysis."""
    return [
        CalculationPreset(
            id="mass_flow_gs",
            name="Massenstrom Waage (g/s)",
            category="Massenstrom",
            description="Massenstrom aus Waage (-dm/dt mittels linearer Regression über Zeitfenster)",
            formula="rate(m, window_s)",
            unit="g/s",
            default_variables={"m": "Waage"},
            variable_descriptions={"m": "Messwert Waage (Masse in g)"},
            default_constants={"window_s": 10.0},
            constant_descriptions={"window_s": "Zeitfenster für lineare Regression (s)"},
            window_seconds=10.0,
            decimal_places=3,
        ),
        CalculationPreset(
            id="mass_flow_gh",
            name="Massenstrom Waage (g/h)",
            category="Massenstrom",
            description="Massenstrom aus Waage in g/h (-dm/dt * 3600)",
            formula="rate(m, window_s) * 3600",
            unit="g/h",
            default_variables={"m": "Waage"},
            variable_descriptions={"m": "Messwert Waage (Masse in g)"},
            default_constants={"window_s": 10.0},
            constant_descriptions={"window_s": "Zeitfenster für lineare Regression (s)"},
            window_seconds=10.0,
            decimal_places=2,
        ),
        CalculationPreset(
            id="mass_flow_smooth",
            name="Massenstrom glatt (stufenbereinigt)",
            category="Massenstrom",
            description="Stufenbereinigter Massenstrom aus Waage (-dm_corr/dt * 3600)",
            formula="rate(Waage_korrigiert, 10) * 3600",
            unit="g/h",
            default_variables={"Waage_korrigiert": "Waage_korrigiert"},
            variable_descriptions={"Waage_korrigiert": "Stufenbereinigte Masse (g)"},
            default_constants={},
            constant_descriptions={},
            window_seconds=10.0,
            decimal_places=2,
        ),
        CalculationPreset(
            id="voc_evaporated_cumulative",
            name="VOC verdampft (kumuliert)",
            category="Massenstrom",
            description="Kumulierte verdampfte VOC-Masse (driftfrei und stufenbereinigt)",
            formula="VOC_verdampft",
            unit="g",
            default_variables={"VOC_verdampft": "VOC_verdampft"},
            variable_descriptions={"VOC_verdampft": "Kumulierte Verdampfungsmasse (g)"},
            default_constants={},
            constant_descriptions={},
            window_seconds=10.0,
            decimal_places=2,
        ),
        CalculationPreset(
            id="concentration_operating",
            name="Abgaskonzentration Betriebszustand (g/m³)",
            category="Konzentration & Abgas",
            description="Schadstoffkonzentration bei Betriebstemperatur T und Druck p mit thermischer Ausdehnung V(T,p)",
            formula="m_dot / (V_norm * ((T + 273.15) / 273.15) * (1013.25 / p))",
            unit="g/m³",
            default_variables={"m_dot": "Massenstrom Waage (g/h)", "T": "Thermo_1"},
            variable_descriptions={
                "m_dot": "Massenstrom in g/h (Kanal oder ROI)",
                "T": "Betriebstemperatur in °C (Thermocouple ROI oder Fixwert)",
            },
            default_constants={"V_norm": 20.0, "p": 1013.25},
            constant_descriptions={
                "V_norm": "Hauptabgasstrom Norm (m³/h bei 0 °C, 1013.25 hPa)",
                "p": "Betriebsdruck (hPa)",
            },
            window_seconds=10.0,
            decimal_places=3,
        ),
        CalculationPreset(
            id="concentration_norm",
            name="Normkonzentration (g/Nm³)",
            category="Konzentration & Abgas",
            description="Schadstoffkonzentration bezogen auf Norm-Volumenstrom V_norm (0 °C, 1013.25 hPa)",
            formula="m_dot / V_norm",
            unit="g/Nm³",
            default_variables={"m_dot": "Massenstrom Waage (g/h)"},
            variable_descriptions={"m_dot": "Massenstrom in g/h"},
            default_constants={"V_norm": 20.0},
            constant_descriptions={"V_norm": "Norm-Hauptvolumenstrom (m³/h)"},
            window_seconds=10.0,
            decimal_places=3,
        ),
        CalculationPreset(
            id="concentration_ppm",
            name="Schadstoffanteil ppmV (Ideales Gas)",
            category="Konzentration & Abgas",
            description="Volumenanteil in ppmV aus Normkonzentration c_norm (g/Nm³) und Molmasse M (g/mol)",
            formula="(c_norm * 22.414 / M) * 1000",
            unit="ppmV",
            default_variables={"c_norm": "Normkonzentration (g/Nm³)"},
            variable_descriptions={"c_norm": "Normkonzentration in g/Nm³"},
            default_constants={"M": 78.11},
            constant_descriptions={"M": "Molmasse Schadstoff (g/mol, z.B. Benzol: 78.11, Toluol: 92.14)"},
            window_seconds=10.0,
            decimal_places=1,
        ),
        CalculationPreset(
            id="ghsv",
            name="Katalysator-Raumgeschwindigkeit GHSV (1/h)",
            category="Katalyse & Reaktion",
            description="Gas Hourly Space Velocity (GHSV) bezogen auf Katalysatorbettvolumen",
            formula="(V_norm * 1000) / V_cat",
            unit="1/h",
            default_variables={},
            variable_descriptions={},
            default_constants={"V_norm": 20.0, "V_cat": 0.5},
            constant_descriptions={
                "V_norm": "Norm-Volumenstrom (m³/h)",
                "V_cat": "Katalysatorvolumen (Liter)",
            },
            window_seconds=10.0,
            decimal_places=0,
        ),
        CalculationPreset(
            id="conversion_rate",
            name="Katalysator-Umsatzgrad η (%)",
            category="Katalyse & Reaktion",
            description="Katalytischer Umsatzgrad / Abscheidegrad in %: (c_in - c_out) / c_in * 100",
            formula="((c_in - c_out) / c_in) * 100",
            unit="%",
            default_variables={"c_in": "Konzentration_Ein", "c_out": "Konzentration_Aus"},
            variable_descriptions={
                "c_in": "Schadstoffkonzentration Einlass",
                "c_out": "Schadstoffkonzentration Auslass",
            },
            default_constants={},
            constant_descriptions={},
            window_seconds=10.0,
            decimal_places=1,
        ),
        CalculationPreset(
            id="total_volume_flow",
            name="Gesamtvolumenstrom mit Rotameter (m³/h)",
            category="Volumenstrom",
            description="Gesamtvolumenstrom aus Hauptstrom (m³/h) und Rotameter-Zudosierung (l/h)",
            formula="V_main + (V_rotameter / 1000)",
            unit="m³/h",
            default_variables={"V_rotameter": "Rotameter"},
            variable_descriptions={"V_rotameter": "Zudosierter Volumenstrom (l/h)"},
            default_constants={"V_main": 20.0},
            constant_descriptions={"V_main": "Hauptvolumenstrom (m³/h)"},
            window_seconds=10.0,
            decimal_places=2,
        ),
        CalculationPreset(
            id="mass_flow_rotameter_gh",
            name="Massenstrom Rotameter (g/h)",
            category="Massenstrom",
            description="Massenstrom aus Rotameter-Volumenstrom V_dot (l/h) und Flüssigkeitsdichte rho (g/l)",
            formula="V_rot * rho",
            unit="g/h",
            default_variables={"V_rot": "Rotameter"},
            variable_descriptions={"V_rot": "Volumenstrom aus Rotameter (l/h)"},
            default_constants={"rho": 1000.0},
            constant_descriptions={"rho": "Dichte des dosierten Mediums (g/l bzw. kg/m³, z.B. Wasser: 1000, Toluol: 867)"},
            window_seconds=10.0,
            decimal_places=2,
        ),
        CalculationPreset(
            id="mass_flow_rotameter_gs",
            name="Massenstrom Rotameter (g/s)",
            category="Massenstrom",
            description="Massenstrom aus Rotameter-Volumenstrom in g/s ((V_dot * rho) / 3600)",
            formula="(V_rot * rho) / 3600",
            unit="g/s",
            default_variables={"V_rot": "Rotameter"},
            variable_descriptions={"V_rot": "Volumenstrom aus Rotameter (l/h)"},
            default_constants={"rho": 1000.0},
            constant_descriptions={"rho": "Dichte des dosierten Mediums (g/l bzw. kg/m³)"},
            window_seconds=10.0,
            decimal_places=3,
        ),
    ]


def calculate_linear_regression(points: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """
    Given a list of (t, y) points, returns (slope, intercept).
    t is timestamp in seconds, y is measured value.
    Returns None if fewer than 2 points or if time span < 0.05s.
    """
    if len(points) < 2:
        return None

    t0 = points[0][0]
    t_span = points[-1][0] - t0
    if t_span < 0.05:
        return None

    n = len(points)
    t_rel = [p[0] - t0 for p in points]
    y = [p[1] for p in points]

    mean_t = sum(t_rel) / n
    mean_y = sum(y) / n

    s_tt = sum((t - mean_t) ** 2 for t in t_rel)
    s_ty = sum((t - mean_t) * (val - mean_y) for t, val in zip(t_rel, y))

    if s_tt < 1e-12:
        return None

    slope = s_ty / s_tt
    intercept = mean_y - slope * mean_t
    return slope, intercept


def preprocess_formula(formula: str) -> Tuple[str, Dict[str, str]]:
    """
    Replaces '{ROI Name}' with valid identifiers '_roi_ref_0', etc.
    Replaces '^' with '**'.
    Returns (processed_formula, ref_to_roi_map).
    """
    ref_map: Dict[str, str] = {}
    roi_to_ref: Dict[str, str] = {}
    pattern = re.compile(r"\{([^}]+)\}")

    def replacer(match: re.Match) -> str:
        roi_name = match.group(1).strip()
        if roi_name in roi_to_ref:
            return roi_to_ref[roi_name]
        var_name = f"_roi_ref_{len(ref_map)}"
        roi_to_ref[roi_name] = var_name
        ref_map[var_name] = roi_name
        return var_name

    f = pattern.sub(replacer, formula)
    f = f.replace("^", "**")
    return f, ref_map


class SafeFormulaEvaluator(ast.NodeVisitor):
    def __init__(
        self,
        context: Dict[str, Any],
        rate_func,
        slope_func,
        default_window: float = 10.0,
        var_to_roi: Optional[Dict[str, str]] = None,
    ):
        self.context = context
        self.rate_func = rate_func
        self.slope_func = slope_func
        self.default_window = default_window
        self.var_to_roi = var_to_roi or {}

    def visit_Expression(self, node: ast.Expression):
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, (int, float, str, bool)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    def visit_Num(self, node):
        return node.n

    def visit_Str(self, node):
        return node.s

    def visit_Name(self, node: ast.Name):
        if node.id in self.context:
            val = self.context[node.id]
            if val is None:
                return None
            return val
        raise NameError(f"Undefined variable '{node.id}'")

    def visit_UnaryOp(self, node: ast.UnaryOp):
        operand = self.visit(node.operand)
        if operand is None:
            return None
        if isinstance(node.op, ast.UAdd):
            return +operand
        elif isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

    def visit_BinOp(self, node: ast.BinOp):
        left = self.visit(node.left)
        right = self.visit(node.right)
        if left is None or right is None:
            return None

        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        elif isinstance(op, ast.Sub):
            return left - right
        elif isinstance(op, ast.Mult):
            return left * right
        elif isinstance(op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            return left / right
        elif isinstance(op, ast.FloorDiv):
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            return left // right
        elif isinstance(op, ast.Mod):
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            return left % right
        elif isinstance(op, ast.Pow):
            return left ** right
        raise ValueError(f"Unsupported binary operator: {type(op).__name__}")

    def visit_Call(self, node: ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")

        fn_name = node.func.id

        if fn_name in ("rate", "slope"):
            if len(node.args) < 1 or len(node.args) > 2:
                raise ValueError(f"{fn_name}() expects 1 or 2 arguments: (roi_target, [window_seconds])")

            first_arg = node.args[0]
            roi_name = None
            if isinstance(first_arg, ast.Name):
                var_id = first_arg.id
                roi_name = self.var_to_roi.get(var_id, var_id)
            elif isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                roi_name = first_arg.value
            elif hasattr(ast, "Str") and isinstance(first_arg, ast.Str):
                roi_name = first_arg.s
            else:
                raise ValueError(f"First argument to {fn_name}() must be a variable name or string")

            window = self.default_window
            if len(node.args) == 2:
                window_val = self.visit(node.args[1])
                if window_val is None or window_val <= 0:
                    raise ValueError(f"Invalid window_seconds: {window_val}")
                window = float(window_val)

            if fn_name == "rate":
                res = self.rate_func(roi_name, window)
            else:
                res = self.slope_func(roi_name, window)

            if res is None:
                return None
            return res

        math_map = {
            "sqrt": math.sqrt,
            "exp": math.exp,
            "log": math.log,
            "ln": math.log,
            "log10": math.log10,
            "abs": abs,
            "min": min,
            "max": max,
            "round": round,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
        }

        if fn_name in math_map:
            args = [self.visit(arg) for arg in node.args]
            if any(a is None for a in args):
                return None
            return math_map[fn_name](*args)

        raise ValueError(f"Disallowed or unknown function call: '{fn_name}'")

    def generic_visit(self, node: ast.AST):
        raise ValueError(f"Disallowed expression node: {type(node).__name__}")


class StepDetectorState:
    STEADY = "STEADY"
    TRANSIENT = "TRANSIENT"
    SETTLING = "SETTLING"


class ScaleStepDetector:
    def __init__(
        self,
        step_threshold_up: float = 0.06,
        slope_threshold_up: float = 0.02,
        excess_slope_down: float = -0.15,
        step_threshold_down: float = -0.15,
        shock_threshold: float = 0.035,
        refill_threshold: float = 5.0,
        window_dt: float = 1.5,
        settling_duration: float = 1.0,
        settling_samples: int = 2,
    ):
        self.step_threshold_up = step_threshold_up
        self.slope_threshold_up = slope_threshold_up
        self.excess_slope_down = excess_slope_down
        self.step_threshold_down = step_threshold_down
        self.shock_threshold = shock_threshold
        self.refill_threshold = refill_threshold
        self.window_dt = window_dt
        self.settling_duration = settling_duration
        self.settling_samples = settling_samples

        self.state: str = StepDetectorState.STEADY
        self.stage: int = 1
        self.total_offset: float = 0.0
        self.start_mass: Optional[float] = None
        self.corrected_mass: Optional[float] = None
        self.evaporated_mass: float = 0.0
        self.status_text: str = "Stationär"

        self.raw_history: deque[Tuple[float, float]] = deque()
        self.steady_history: deque[Tuple[float, float]] = deque()
        self.last_steady_slope: float = 0.0
        self._has_steady_baseline: bool = False

        # Transient tracking
        self.transient_start_time: Optional[float] = None
        self.transient_start_mass: Optional[float] = None
        self.transient_corr_start: Optional[float] = None
        self.transient_type: Optional[str] = None  # "UP", "DOWN", "REFILL"
        self.transient_target_stage: Optional[int] = None
        self.settling_start_time: Optional[float] = None
        self.settling_points: List[Tuple[float, float]] = []

        # Buffer for spliced points to update history
        self._spliced_history: List[Tuple[float, float]] = []

        # Idempotence cache
        self._last_ts: Optional[float] = None
        self._last_raw: Optional[float] = None

    @property
    def has_readings(self) -> bool:
        return self.start_mass is not None

    @property
    def is_transition(self) -> bool:
        return self.state in (StepDetectorState.TRANSIENT, StepDetectorState.SETTLING)

    def reset(self, start_mass: Optional[float] = None, hard: bool = False):
        self.state = StepDetectorState.STEADY
        self.stage = 1
        self.total_offset = 0.0
        self.transient_start_time = None
        self.transient_start_mass = None
        self.transient_corr_start = None
        self.transient_type = None
        self.transient_target_stage = None
        self.settling_start_time = None
        self.settling_points.clear()
        self._spliced_history.clear()
        self.steady_history.clear()
        self.raw_history.clear()
        self._has_steady_baseline = False
        self.last_steady_slope = 0.0
        self._last_ts = None
        self._last_raw = None
        self.status_text = "Stationär"
        if hard:
            self.start_mass = start_mass
            self.corrected_mass = start_mass
            self.evaporated_mass = 0.0
        else:
            if start_mass is not None:
                self.start_mass = start_mass
            if self.start_mass is not None:
                self.corrected_mass = self._last_raw if self._last_raw is not None else self.start_mass
                self.evaporated_mass = max(0.0, self.start_mass - self.corrected_mass)
            else:
                self.start_mass = None
                self.corrected_mass = None
                self.evaporated_mass = 0.0

    def get_and_clear_spliced_history(self) -> List[Tuple[float, float]]:
        spliced = list(self._spliced_history)
        self._spliced_history.clear()
        return spliced

    def process_reading(self, raw_mass: float, timestamp: float) -> Tuple[float, float, int, str]:
        # Idempotent guard
        if self._last_ts == timestamp and self._last_raw == raw_mass:
            return (
                self.corrected_mass if self.corrected_mass is not None else raw_mass,
                self.evaporated_mass,
                self.stage,
                self.status_text,
            )

        self._last_ts = timestamp
        self._last_raw = raw_mass

        # Initialize on first reading
        if self.start_mass is None:
            self.start_mass = raw_mass
            self.corrected_mass = raw_mass
            self.evaporated_mass = 0.0
            self.total_offset = 0.0
            self.stage = 1
            self.state = StepDetectorState.STEADY
            self.status_text = "Stationär"
            self.raw_history.append((timestamp, raw_mass))
            self.steady_history.append((timestamp, raw_mass))
            return self.corrected_mass, self.evaporated_mass, self.stage, self.status_text

        # Record reading in raw history
        prev_reading = self.raw_history[-1] if self.raw_history else (timestamp, raw_mass)
        self.raw_history.append((timestamp, raw_mass))
        while self.raw_history and (timestamp - self.raw_history[0][0]) > 300.0:
            self.raw_history.popleft()

        # Find reference sample ~1.5s ago (prefer strictly prior samples)
        target_t = timestamp - self.window_dt
        prior_samples = [s for s in self.raw_history if s[0] < timestamp]
        if prior_samples:
            ref_sample = prior_samples[0]
            min_diff = abs(ref_sample[0] - target_t)
            for s in prior_samples:
                diff = abs(s[0] - target_t)
                if diff <= min_diff:
                    min_diff = diff
                    ref_sample = s
        else:
            ref_sample = self.raw_history[0]

        dt_ref = timestamp - ref_sample[0]
        if dt_ref >= 0.05:
            dm_ref = raw_mass - ref_sample[1]
            slope_ref = dm_ref / dt_ref
        else:
            dm_ref = 0.0
            slope_ref = 0.0

        # Change from immediate previous sample
        dt_prev = timestamp - prev_reading[0]
        dm_prev = raw_mass - prev_reading[1]
        slope_prev = (dm_prev / dt_prev) if dt_prev > 0.01 else 0.0

        baseline_slope = self.last_steady_slope if self._has_steady_baseline else 0.0

        if self.state == StepDetectorState.STEADY:
            is_step = False
            # 1. Refill detection: Delta m > 5.0 g
            if dm_ref > self.refill_threshold or dm_prev > self.refill_threshold:
                is_step = True
                self._enter_transient(
                    ref_sample[0], ref_sample[1], "REFILL", target_stage=self.stage, current_t=timestamp
                )
            # 2. Upward step: Delta m_1.5s > +0.06 g AND Delta m / Delta t > +0.02 g/s
            elif (
                (dm_ref > self.step_threshold_up and slope_ref > self.slope_threshold_up)
                or (dm_prev > self.step_threshold_up and slope_prev > self.slope_threshold_up)
            ):
                is_step = True
                self._enter_transient(
                    ref_sample[0], ref_sample[1], "UP", target_stage=self.stage + 1, current_t=timestamp
                )
            # 3. Downward throttling: excess slope over steady evaporation < -0.15 g/s AND Delta m < -0.15 g
            elif (
                ((slope_ref - baseline_slope) < self.excess_slope_down and dm_ref < self.step_threshold_down)
                or ((slope_prev - baseline_slope) < self.excess_slope_down and dm_prev < self.step_threshold_down)
            ):
                is_step = True
                self._enter_transient(
                    ref_sample[0], ref_sample[1], "DOWN", target_stage=self.stage + 1, current_t=timestamp
                )

            if not is_step:
                # Update steady history and baseline ONLY when strictly in steady state
                self.steady_history.append((timestamp, raw_mass))
                while self.steady_history and (timestamp - self.steady_history[0][0]) > 15.0:
                    self.steady_history.popleft()
                if len(self.steady_history) >= 3:
                    res = calculate_linear_regression(list(self.steady_history))
                    if res is not None:
                        self.last_steady_slope = min(0.0, res[0])
                        self._has_steady_baseline = True

                self.corrected_mass = raw_mass - self.total_offset
                self.evaporated_mass = max(0.0, self.start_mass - self.corrected_mass)
                self.status_text = "Stationär"

        if self.state == StepDetectorState.TRANSIENT:
            # Hold last steady rate during transient
            dt_t = timestamp - self.transient_start_time
            self.corrected_mass = self.transient_corr_start + self.last_steady_slope * dt_t
            self.evaporated_mass = max(0.0, self.start_mass - self.corrected_mass)
            self.status_text = "Stabilisierung..."

            # Check for flattening / settling
            flattened = False
            if self.transient_type == "UP":
                flattened = (slope_prev <= self.slope_threshold_up) or (dt_t >= 0.5 and abs(dm_prev) < 0.03)
            elif self.transient_type == "DOWN":
                flattened = ((slope_prev - self.last_steady_slope) >= self.excess_slope_down / 2.0) or (dt_t >= 0.5 and abs(dm_prev) < 0.03)
            elif self.transient_type == "REFILL":
                flattened = (slope_prev <= 0.1) or (dt_t >= 0.5 and abs(dm_prev) < 0.05)

            if flattened or (dt_t >= 5.0):
                self.state = StepDetectorState.SETTLING
                self.settling_start_time = timestamp
                self.settling_points = [(timestamp, raw_mass)]

        elif self.state == StepDetectorState.SETTLING:
            # Continue holding last rate while observing plateau
            dt_t = timestamp - self.transient_start_time
            self.corrected_mass = self.transient_corr_start + self.last_steady_slope * dt_t
            self.evaporated_mass = max(0.0, self.start_mass - self.corrected_mass)
            self.status_text = "Stabilisierung..."

            self.settling_points.append((timestamp, raw_mass))
            settling_dt = timestamp - self.settling_start_time

            # If sudden large movement occurs during settling, re-enter transient
            if abs(slope_prev - self.last_steady_slope) > 0.15 and abs(dm_prev) > 0.06:
                self.state = StepDetectorState.TRANSIENT
            # Check if settling complete
            elif (settling_dt >= self.settling_duration and len(self.settling_points) >= self.settling_samples) or (settling_dt >= 3.0):
                self._finish_settling(raw_mass, timestamp)

        return self.corrected_mass, self.evaporated_mass, self.stage, self.status_text

    def _enter_transient(self, ref_t: float, ref_m: float, t_type: str, target_stage: int, current_t: float):
        start_t = ref_t
        start_m = ref_m
        # Find exact point where deviation began between ref and current
        for s in self.raw_history:
            if s[0] >= ref_t and s[0] < current_t:
                m_model = ref_m + self.last_steady_slope * (s[0] - ref_t)
                delta = s[1] - m_model
                if t_type in ("UP", "REFILL") and delta > 0.02:
                    break
                elif t_type == "DOWN" and delta < -0.02:
                    break
                start_t = s[0]
                start_m = s[1]

        self.state = StepDetectorState.TRANSIENT
        self.transient_start_time = start_t
        self.transient_start_mass = start_m
        self.transient_corr_start = start_m - self.total_offset
        self.transient_type = t_type
        self.transient_target_stage = target_stage
        self.settling_start_time = None
        self.settling_points.clear()
        self.status_text = "Stabilisierung..."

    def _finish_settling(self, current_raw: float, current_t: float):
        if self.settling_points:
            t_post = self.settling_points[-1][0]
            t_mid = (self.settling_points[0][0] + self.settling_points[-1][0]) / 2.0
            mean_m = sum(p[1] for p in self.settling_points) / len(self.settling_points)
            m_post = mean_m + self.last_steady_slope * (t_post - t_mid)
        else:
            m_post = current_raw
            t_post = current_t

        dt_total = t_post - self.transient_start_time
        m_expected = self.transient_start_mass + self.last_steady_slope * dt_total
        delta_m_step = m_post - m_expected

        if self.transient_type == "REFILL" or delta_m_step > self.refill_threshold:
            # Refill: absorb offset, keep stage unchanged
            self.total_offset += delta_m_step
            self.corrected_mass = current_raw - self.total_offset
            self.evaporated_mass = max(0.0, self.start_mass - self.corrected_mass)
            self._splice_history(self.transient_start_time, t_post, self.transient_corr_start, self.corrected_mass)
            self.state = StepDetectorState.STEADY
            self.status_text = "Stationär"

        elif abs(delta_m_step) < self.shock_threshold:
            # Mechanical shock / bump: reject
            self.corrected_mass = current_raw - self.total_offset
            self.evaporated_mass = max(0.0, self.start_mass - self.corrected_mass)
            self._splice_history(self.transient_start_time, t_post, self.transient_corr_start, self.corrected_mass)
            self.state = StepDetectorState.STEADY
            self.status_text = "Stationär"

        else:
            # Real step change (up or down)
            self.total_offset += delta_m_step
            self.stage = self.transient_target_stage if self.transient_target_stage is not None else (self.stage + 1)
            self.corrected_mass = current_raw - self.total_offset
            self.evaporated_mass = max(0.0, self.start_mass - self.corrected_mass)
            self._splice_history(self.transient_start_time, t_post, self.transient_corr_start, self.corrected_mass)
            self.state = StepDetectorState.STEADY
            self.status_text = "Stationär"

        self.transient_start_time = None
        self.transient_start_mass = None
        self.transient_corr_start = None
        self.transient_type = None
        self.transient_target_stage = None
        self.settling_start_time = None
        self.settling_points.clear()

        # Re-initialize steady baseline on new plateau
        self.steady_history.clear()
        self.steady_history.append((current_t, current_raw))

    def _splice_history(self, t0: float, t1: float, m0: float, m1: float):
        self._spliced_history = [(t0, m0), (t1, m1)]


class CalculationEngine:
    def __init__(self, max_history_seconds: float = 300.0):
        self.max_history_seconds = max_history_seconds
        # Maps roi_name -> deque of (timestamp, value)
        self.history: Dict[str, deque[Tuple[float, float]]] = {}
        self.channels: Dict[str, CalculationChannel] = {}
        # Stores the latest calculated results
        self.latest_results: Dict[str, CalculationResult] = {}
        self.scale_roi_name: Optional[str] = None
        self.step_detector = ScaleStepDetector()

    def is_scale_roi(self, roi_name: str) -> bool:
        if not roi_name:
            return False
        name_lower = roi_name.lower()
        if name_lower in ("waage_korrigiert", "voc_verdampft", "stufe", "stufen_status"):
            return False
        if self.scale_roi_name is not None:
            return name_lower == self.scale_roi_name.lower()
        keywords = ("waage", "masse", "scale", "weight")
        return any(k in name_lower for k in keywords)

    def set_scale_roi(self, roi_name: Optional[str]):
        self.scale_roi_name = roi_name

    def reset_scale_detector(self, hard: bool = False):
        """Resets the scale step detector and clears calculated virtual channel histories."""
        self.step_detector.reset(hard=hard)
        self.history.pop("Waage_korrigiert", None)
        self.history.pop("VOC_verdampft", None)
        self.history.pop("Stufe", None)

    def add_channel(self, channel: CalculationChannel):
        self.channels[channel.id] = channel

    def remove_channel(self, channel_id: str):
        self.channels.pop(channel_id, None)
        self.latest_results.pop(channel_id, None)

    def get_channel(self, channel_id: str) -> Optional[CalculationChannel]:
        return self.channels.get(channel_id)

    def clear_channels(self):
        self.channels.clear()
        self.latest_results.clear()

    def add_reading(self, roi_name: str, value: Optional[float], timestamp: Optional[float] = None):
        """Records a timestamped numeric reading for an ROI into the history buffer."""
        if value is None:
            return
        try:
            val_float = float(value)
            if math.isnan(val_float) or math.isinf(val_float):
                return
        except (ValueError, TypeError):
            return

        ts = timestamp if timestamp is not None else time.time()
        if roi_name not in self.history:
            self.history[roi_name] = deque()

        q = self.history[roi_name]
        q.append((ts, val_float))

        # Prune old entries beyond max_history_seconds
        min_ts = ts - self.max_history_seconds
        while q and q[0][0] < min_ts:
            q.popleft()

        # If scale ROI, process with ScaleStepDetector and publish virtual channels
        if self.is_scale_roi(roi_name):
            if self.scale_roi_name is None:
                self.scale_roi_name = roi_name
            self._process_scale_reading(val_float, ts)

    def _process_scale_reading(self, raw_mass: float, ts: float):
        corr_m, evap_m, stage, status = self.step_detector.process_reading(raw_mass, ts)

        # Update virtual channel history directly
        for v_name, v_val in [
            ("Waage_korrigiert", corr_m),
            ("VOC_verdampft", evap_m),
            ("Stufe", float(stage)),
        ]:
            if v_name not in self.history:
                self.history[v_name] = deque()
            vq = self.history[v_name]
            vq.append((ts, v_val))
            min_ts = ts - self.max_history_seconds
            while vq and vq[0][0] < min_ts:
                vq.popleft()

        # Apply retroactive spliced points to Waage_korrigiert and VOC_verdampft history
        spliced = self.step_detector.get_and_clear_spliced_history()
        if spliced and len(spliced) >= 2:
            t0, m0 = spliced[0]
            t1, m1 = spliced[-1]
            dt = t1 - t0

            if "Waage_korrigiert" in self.history:
                q_corr = self.history["Waage_korrigiert"]
                new_q = deque()
                for t_item, m_item in q_corr:
                    if t0 <= t_item <= t1:
                        spliced_val = m0 + (m1 - m0) * ((t_item - t0) / dt) if dt > 1e-6 else m0
                        new_q.append((t_item, spliced_val))
                    else:
                        new_q.append((t_item, m_item))
                self.history["Waage_korrigiert"] = new_q

            if "VOC_verdampft" in self.history and self.step_detector.start_mass is not None:
                sm = self.step_detector.start_mass
                q_evap = self.history["VOC_verdampft"]
                new_q_evap = deque()
                for t_item, v_item in q_evap:
                    if t0 <= t_item <= t1:
                        spliced_val = m0 + (m1 - m0) * ((t_item - t0) / dt) if dt > 1e-6 else m0
                        new_q_evap.append((t_item, max(0.0, sm - spliced_val)))
                    else:
                        new_q_evap.append((t_item, v_item))
                self.history["VOC_verdampft"] = new_q_evap

    def update_readings(self, readings: List[Dict[str, Any]], timestamp: Optional[float] = None):
        """Bulk update history from readings list."""
        ts = timestamp if timestamp is not None else time.time()
        for r in readings:
            roi_name = r.get("roi_name")
            val = r.get("parsed_value")
            if roi_name and val is not None and r.get("is_valid", True):
                self.add_reading(roi_name, val, ts)

    def calculate_slope(
        self, roi_name: str, window_seconds: float = 10.0, current_time: Optional[float] = None
    ) -> Optional[float]:
        """Calculates linear regression slope (dy/dt) for an ROI over window_seconds."""
        if roi_name not in self.history:
            return None
        q = self.history[roi_name]
        if len(q) < 2:
            return None

        t_end = current_time if current_time is not None else q[-1][0]
        t_start = t_end - window_seconds

        window_points = [p for p in q if t_start <= p[0] <= t_end]
        res = calculate_linear_regression(window_points)
        if res is None:
            return None
        return res[0]

    def calculate_rate(
        self, roi_name: str, window_seconds: float = 10.0, current_time: Optional[float] = None
    ) -> Optional[float]:
        """
        Calculates loss-in-weight rate: rate = -slope = -dm/dt.
        A positive rate indicates consumption / mass flowing out.
        """
        slope = self.calculate_slope(roi_name, window_seconds, current_time)
        if slope is None:
            return None
        return -slope

    def evaluate_channel(
        self,
        channel: CalculationChannel,
        current_values: Dict[str, Optional[float]],
        current_time: Optional[float] = None,
    ) -> CalculationResult:
        """Evaluates a single CalculationChannel against current readings and constants."""
        if not channel.formula or not channel.formula.strip():
            return CalculationResult(
                channel_id=channel.id,
                name=channel.name,
                value=None,
                unit=channel.unit,
                formula=channel.formula,
                is_valid=False,
                error_message="Formel ist leer",
                formatted_value="-",
            )

        processed_formula, ref_map = preprocess_formula(channel.formula)

        # Build execution context and variable-to-roi mapping
        context: Dict[str, Any] = {}
        var_to_roi: Dict[str, str] = dict(channel.variables)

        # 1. Populate constants
        for c_name, c_val in channel.constants.items():
            context[c_name] = c_val

        # 2. Populate variable mappings from current_values
        for v_name, target_name in channel.variables.items():
            if target_name in current_values:
                context[v_name] = current_values[target_name]

        # 3. Populate raw ROI and channel names directly into context
        for name, val in current_values.items():
            if name.isidentifier() and name not in context:
                context[name] = val

        # 4. Populate preprocessed '{ROI Name}' references
        for ref_var, roi_name in ref_map.items():
            var_to_roi[ref_var] = roi_name
            context[ref_var] = current_values.get(roi_name)

        def rate_fn(target_name: str, win_s: float):
            return self.calculate_rate(target_name, win_s, current_time)

        def slope_fn(target_name: str, win_s: float):
            return self.calculate_slope(target_name, win_s, current_time)

        evaluator = SafeFormulaEvaluator(
            context=context,
            rate_func=rate_fn,
            slope_func=slope_fn,
            default_window=channel.window_seconds,
            var_to_roi=var_to_roi,
        )

        try:
            tree = ast.parse(processed_formula, mode="eval")
            res_val = evaluator.visit(tree)

            if res_val is None:
                return CalculationResult(
                    channel_id=channel.id,
                    name=channel.name,
                    value=None,
                    unit=channel.unit,
                    formula=channel.formula,
                    is_valid=False,
                    error_message="Warte auf Messwerte...",
                    formatted_value="-",
                )

            if isinstance(res_val, str):
                return CalculationResult(
                    channel_id=channel.id,
                    name=channel.name,
                    value=None,
                    unit=channel.unit,
                    formula=channel.formula,
                    is_valid=True,
                    error_message=None,
                    formatted_value=res_val,
                )

            res_float = float(res_val)
            if math.isnan(res_float) or math.isinf(res_float):
                return CalculationResult(
                    channel_id=channel.id,
                    name=channel.name,
                    value=None,
                    unit=channel.unit,
                    formula=channel.formula,
                    is_valid=False,
                    error_message="Ungültiger Zahlenwert (NaN/Inf)",
                    formatted_value="-",
                )

            formatted = f"{res_float:.{channel.decimal_places}f}"

            return CalculationResult(
                channel_id=channel.id,
                name=channel.name,
                value=res_float,
                unit=channel.unit,
                formula=channel.formula,
                is_valid=True,
                error_message=None,
                formatted_value=formatted,
            )

        except ZeroDivisionError:
            return CalculationResult(
                channel_id=channel.id,
                name=channel.name,
                value=None,
                unit=channel.unit,
                formula=channel.formula,
                is_valid=False,
                error_message="Division durch Null",
                formatted_value="-",
            )
        except NameError as e:
            return CalculationResult(
                channel_id=channel.id,
                name=channel.name,
                value=None,
                unit=channel.unit,
                formula=channel.formula,
                is_valid=False,
                error_message=str(e),
                formatted_value="-",
            )
        except Exception as e:
            return CalculationResult(
                channel_id=channel.id,
                name=channel.name,
                value=None,
                unit=channel.unit,
                formula=channel.formula,
                is_valid=False,
                error_message=str(e),
                formatted_value="-",
            )

    def calculate_all(
        self,
        current_values: Optional[Dict[str, Any]] = None,
        current_time: Optional[float] = None,
    ) -> List[CalculationResult]:
        """
        Evaluates all channels in order. Supports channels depending on prior calculated channels.
        Iterates until convergence up to the number of channels to resolve any dependency chain depth.
        """
        vals: Dict[str, Any] = dict(current_values) if current_values else {}

        # If scale ROI is present in vals and needs processing, feed it
        for k, v in vals.items():
            if self.is_scale_roi(k) and v is not None:
                try:
                    val_f = float(v)
                    if (
                        self.step_detector._last_ts != current_time
                        or self.step_detector._last_raw != val_f
                    ):
                        self.add_reading(k, val_f, current_time)
                except (ValueError, TypeError):
                    pass
                break

        # Publish virtual channels to current values
        if self.step_detector.has_readings:
            vals["Waage_korrigiert"] = self.step_detector.corrected_mass
            vals["VOC_verdampft"] = self.step_detector.evaporated_mass
            vals["Stufe"] = float(self.step_detector.stage)
            vals["Stufen_Status"] = self.step_detector.status_text
            if current_values is not None:
                current_values["Waage_korrigiert"] = self.step_detector.corrected_mass
                current_values["VOC_verdampft"] = self.step_detector.evaporated_mass
                current_values["Stufe"] = float(self.step_detector.stage)
                current_values["Stufen_Status"] = self.step_detector.status_text

        results: List[CalculationResult] = []

        max_passes = max(1, len(self.channels))
        for _ in range(max_passes):
            newly_resolved = False
            for ch in self.channels.values():
                res = self.evaluate_channel(ch, vals, current_time)
                self.latest_results[ch.id] = res
                if res.is_valid and res.value is not None:
                    if vals.get(ch.name) != res.value or vals.get(ch.id) != res.value:
                        vals[ch.name] = res.value
                        vals[ch.id] = res.value
                        newly_resolved = True
            if not newly_resolved:
                break

        results = [self.latest_results[ch_id] for ch_id in self.channels.keys() if ch_id in self.latest_results]
        return results
