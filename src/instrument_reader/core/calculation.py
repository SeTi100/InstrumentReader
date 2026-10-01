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


class CalculationEngine:
    def __init__(self, max_history_seconds: float = 300.0):
        self.max_history_seconds = max_history_seconds
        # Maps roi_name -> deque of (timestamp, value)
        self.history: Dict[str, deque[Tuple[float, float]]] = {}
        self.channels: Dict[str, CalculationChannel] = {}
        # Stores the latest calculated results
        self.latest_results: Dict[str, CalculationResult] = {}

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
        current_values: Optional[Dict[str, Optional[float]]] = None,
        current_time: Optional[float] = None,
    ) -> List[CalculationResult]:
        """
        Evaluates all channels in order. Supports channels depending on prior calculated channels.
        Iterates until convergence up to the number of channels to resolve any dependency chain depth.
        """
        vals: Dict[str, Optional[float]] = dict(current_values) if current_values else {}
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
