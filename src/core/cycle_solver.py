"""Real-fluid closed cycles, in SI units and per unit working-fluid mass.

Steady state; negligible kinetic/potential energy; adiabatic turbomachinery;
external heating/cooling by two isothermal reservoirs. No combustion, chemical
exergy, leakage, shaft losses, or generator losses. Rankine is a simple cycle;
Regeneration denotes a Brayton recuperator, not a feedwater heater.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from CoolProp.CoolProp import PropsSI
from scipy.optimize import brentq

Array = NDArray[np.float64]


class CycleError(ValueError):
    """Invalid cycle specification or unsupported thermodynamic state."""


@dataclass(frozen=True)
class State:
    """Mass-specific state: p [Pa], T [K], h [J/kg], s [J/(kg K)].

    quality is vapor mass fraction in [0, 1], or None outside two-phase states.
    """

    p: float
    T: float
    h: float
    s: float
    quality: float | None


@dataclass(frozen=True)
class Environment:
    """Dead state and infinite reservoirs: temperatures [K], pressure [Pa]."""

    temperature: float = 298.15
    pressure: float = 101325.0
    hot_temperature: float = 1600.0
    cold_temperature: float = 298.15


@dataclass(frozen=True)
class BraytonConfig:
    """Closed recuperated Brayton inputs, in SI.

    Pressure losses are fractions of each component's inlet pressure. The
    recuperator has equal mass flow on both sides; effectiveness = q/q_max.
    q_max is limited by the counterflow temperature pinch, including pressure
    losses and variable real-fluid heat capacity (sampled numerical limit).
    """

    fluid: str = "Air"
    inlet_pressure: float = 101325.0
    inlet_temperature: float = 300.0
    pressure_ratio: float = 6.0
    turbine_inlet_temperature: float = 1200.0
    compressor_efficiency: float = 0.85
    turbine_efficiency: float = 0.90
    effectiveness: float = 0.75
    cold_regenerator_drop: float = 0.02
    hot_regenerator_drop: float = 0.02
    heater_drop: float = 0.0
    cooler_drop: float = 0.0
    mass_flow: float = 1.0
    environment: Environment = field(default_factory=Environment)


@dataclass(frozen=True)
class RankineConfig:
    """Simple Rankine inputs [Pa, K, kg/s]; condenser exits saturated liquid.

    Boiler and condenser losses are inlet-pressure fractions. Turbine inlet
    may be saturated vapor (temperature=None), superheated, or supercritical.
    The condenser pressure is its outlet pressure; pump outlet compensates
    boiler loss. No recuperator is included in this simple Rankine model.
    """

    fluid: str = "Water"
    condenser_pressure: float = 10000.0
    boiler_pressure: float = 8.0e6
    turbine_inlet_temperature: float | None = 773.15
    pump_efficiency: float = 0.80
    turbine_efficiency: float = 0.85
    boiler_drop: float = 0.0
    condenser_drop: float = 0.0
    mass_flow: float = 1.0
    environment: Environment = field(default_factory=Environment)


@dataclass(frozen=True)
class Curve:
    """Plot-ready arrays p [Pa], T [K], h [J/kg], s [J/(kg K)]."""

    p: Array
    T: Array
    h: Array
    s: Array


@dataclass(frozen=True)
class CycleResult:
    """Cycle outputs, with positive work output and heat input conventions.

    Specific energies/exergies and residuals are J/kg; rates are W. Component
    destruction includes external reservoirs in heater/cooler control volumes.
    physical_exergy = (h-h0)-T0*(s-s0); chemical exergy is excluded.
    """

    states: dict[str, State]
    paths: dict[str, Curve]
    saturation: dict[str, Curve]
    specific_work: float
    heat_input: float
    heat_rejected: float
    thermal_efficiency: float
    exergy_efficiency: float
    physical_exergy: dict[str, float]
    exergy_destruction: dict[str, float]
    energy_residual: float
    exergy_residual: float
    power: float
    destruction_rates: dict[str, float]
    regeneration_heat: float
    warnings: tuple[str, ...]


def _positive(name: str, value: float) -> None:
    """Require a finite positive SI magnitude.

    Args:
        name: Parameter identifier.
        value: Magnitude in its declared SI unit.

    Raises:
        CycleError: If value is nonfinite or nonpositive.
    """
    if not np.isfinite(value) or value <= 0.0:
        raise CycleError(f"{name} must be finite and positive; got {value}.")


def _fraction(name: str, value: float, efficiency: bool = False) -> None:
    """Validate dimensionless loss [0,1) or efficiency (0,1].

    Args:
        name: Parameter identifier.
        value: Dimensionless fraction.
        efficiency: Whether to use the efficiency interval.

    Raises:
        CycleError: If the interval constraint is violated.
    """
    valid = 0.0 < value <= 1.0 if efficiency else 0.0 <= value < 1.0
    if not np.isfinite(value) or not valid:
        raise CycleError(f"Invalid {name}: {value}.")


def _prop(
    output: str, a: str, x: float, b: str, y: float, fluid: str
) -> float:
    """Evaluate a CoolProp mass-specific SI property with contextual errors.

    Args:
        output: CoolProp property key.
        a: First input key.
        x: First input in SI.
        b: Second input key.
        y: Second input in SI.
        fluid: CoolProp fluid identifier.

    Returns:
        Finite property value in SI.

    Raises:
        CycleError: For unavailable states or nonfinite results.
    """
    try:
        value = float(PropsSI(output, a, x, b, y, fluid))
    except ValueError as exc:
        raise CycleError(
            f"{fluid}: {output}({a}={x}, {b}={y}): {exc}"
        ) from exc
    if not np.isfinite(value):
        raise CycleError(f"Nonfinite {output} for {fluid}.")
    return value


def _state(fluid: str, p: float, key: str, value: float) -> State:
    """Flash a state using pressure and T, H, S, or Q.

    Args:
        fluid: CoolProp fluid identifier.
        p: Pressure [Pa].
        key: Independent property key.
        value: Independent property in SI (Q is dimensionless).

    Returns:
        Mass-specific equilibrium state, including two-phase quality.
    """
    if key == "T":
        try:
            minimum = max(
                float(PropsSI("Ttriple", fluid)), float(PropsSI("Tmin", fluid))
            )
        except ValueError as exc:
            raise CycleError(f"Unsupported fluid {fluid!r}: {exc}") from exc
        if not np.isfinite(value) or value < minimum:
            raise CycleError(
                f"{fluid}: temperature {value} K below the supported "
                f"fluid/triple-point limit {minimum} K (or nonfinite)."
            )
    q = _prop("Q", "P", p, key, value, fluid)
    return State(
        p,
        _prop("T", "P", p, key, value, fluid),
        _prop("H", "P", p, key, value, fluid),
        _prop("S", "P", p, key, value, fluid),
        q if 0.0 <= q <= 1.0 else None,
    )


def _machine(
    fluid: str, inlet: State, p: float, eta: float, compression: bool
) -> State:
    """Solve an adiabatic compressor/pump or turbine using real-fluid h(p,s).

    Args:
        fluid: CoolProp fluid identifier.
        inlet: Inlet state in SI.
        p: Outlet pressure [Pa].
        eta: Isentropic efficiency (0,1].
        compression: True for compression, False for expansion.

    Returns:
        Outlet using h2=h1+(h2s-h1)/eta for compression, or
        h2=h1-eta*(h1-h2s) for expansion, all enthalpies in J/kg.

    Raises:
        CycleError: If the pressure direction is inconsistent.
    """
    _fraction("isentropic efficiency", eta, True)
    if p == inlet.p:
        return inlet
    if (compression and p < inlet.p) or (not compression and p > inlet.p):
        raise CycleError("Machine has invalid pressure direction.")
    hs = _prop("H", "P", p, "S", inlet.s, fluid)
    h = inlet.h + (hs - inlet.h) * (1.0 / eta if compression else eta)
    return _state(fluid, p, "H", h)


def _curve(states: list[State]) -> Curve:
    """Collect SI state coordinates without changing the path.

    Args:
        states: Ordered equilibrium states.

    Returns:
        NumPy arrays for T-s and h-s plotting.
    """
    return Curve(
        *(
            np.array([getattr(s, key) for s in states], dtype=float)
            for key in ("p", "T", "h", "s")
        )
    )


def _path(
    fluid: str, a: State, b: State, n: int, machine: bool = False
) -> Curve:
    """Sample a specified plotting path and explicitly insert phase boundaries.

    Args:
        fluid: CoolProp fluid identifier.
        a: Inlet state in SI.
        b: Outlet state in SI.
        n: Minimum number of points (at least 20).
        machine: Use logarithmic pressure for turbomachinery.

    Returns:
        Equilibrium flashes of linear h versus fractional progress; pressure
        is linear for heat exchangers, logarithmic for machines. These are
        visualization assumptions, not resolved internal machine trajectories.
        Saturation crossings are located with SciPy roots when available.
    """

    def pressure(x: float) -> float:
        """Return interpolated pressure [Pa] at dimensionless progress x."""
        return float(
            a.p * (b.p / a.p) ** x if machine else a.p + x * (b.p - a.p)
        )

    def boundary(x: float, q: float) -> float:
        """Return enthalpy distance [J/kg] from saturation quality q."""
        return (
            a.h + x * (b.h - a.h) - _prop("H", "P", pressure(x), "Q", q, fluid)
        )

    xs = list(np.linspace(0.0, 1.0, n))
    for q in (0.0, 1.0):
        for left, right in zip(xs[: n - 1], xs[1:n]):
            try:
                if boundary(left, q) * boundary(right, q) < 0.0:
                    xs.append(brentq(lambda x: boundary(x, q), left, right))
            except CycleError:
                continue  # Supercritical or unsupported saturation pressure.
    middle = [
        _state(fluid, pressure(float(x)), "H", a.h + x * (b.h - a.h))
        for x in sorted(set(xs))[1:-1]
    ]
    return _curve([a, *middle, b])


def saturation_envelope(fluid: str, points: int = 400) -> dict[str, Curve]:
    """Generate liquid/vapor saturation curves below the critical point.

    Args:
        fluid: Pure or pseudo-pure CoolProp fluid identifier.
        points: Number of temperatures per branch (at least 20).

    Returns:
        Liquid and vapor SI curves over the backend's valid saturation range.
        Raises rather than silently dropping failed points; mixtures requiring
        a phase envelope are outside this solver's scope.

    Raises:
        CycleError: If the saturation range or flashes are unsupported.
    """
    if points < 20:
        raise CycleError("At least 20 saturation samples are required.")
    try:
        low = (
            max(
                float(PropsSI("Ttriple", fluid)), float(PropsSI("Tmin", fluid))
            )
            + 0.01
        )
        high = float(PropsSI("Tcrit", fluid)) - 0.01
    except ValueError as exc:
        raise CycleError(f"No saturation range for {fluid}.") from exc
    if low >= high:
        raise CycleError(f"Empty saturation range for {fluid}.")
    result = {}
    for name, q in (("liquid", 0.0), ("vapor", 1.0)):
        states = []
        for t in np.linspace(low, high, points):
            p = _prop("P", "T", float(t), "Q", q, fluid)
            states.append(
                State(
                    p,
                    float(t),
                    _prop("H", "T", float(t), "Q", q, fluid),
                    _prop("S", "T", float(t), "Q", q, fluid),
                    q,
                )
            )
        result[name] = _curve(states)
    return result


def _regenerator(
    fluid: str,
    cold: State,
    hot: State,
    pc: float,
    ph: float,
    effectiveness: float,
) -> tuple[State, State, float]:
    """Solve equal-flow counterflow recuperation with pressure losses.

    Args:
        fluid: CoolProp identifier.
        cold: Cold inlet state.
        hot: Hot inlet state.
        pc: Cold outlet pressure [Pa].
        ph: Hot outlet pressure [Pa].
        effectiveness: q/q_max in [0,1].

    Returns:
        Cold outlet, hot outlet, and q [J/kg]. q_max is bounded by inlet
        temperatures and checked at 201 counterflow stations. Pinch accuracy
        is numerical, not an analytic guarantee between stations.

    Raises:
        CycleError: If pressure losses cause an infeasible temperature cross.
    """
    _fraction("effectiveness", effectiveness, True) if effectiveness else None
    if hot.T <= cold.T or effectiveness == 0.0:
        return (
            _state(fluid, pc, "H", cold.h),
            _state(fluid, ph, "H", hot.h),
            0.0,
        )
    upper = min(
        _prop("H", "P", pc, "T", hot.T, fluid) - cold.h,
        hot.h - _prop("H", "P", ph, "T", cold.T, fluid),
    )

    def pinch(q: float) -> float:
        """Return minimum sampled counterflow approach temperature [K]."""
        differences = []
        for x in np.linspace(0.0, 1.0, 201):
            tc = _prop(
                "T",
                "P",
                cold.p + x * (pc - cold.p),
                "H",
                cold.h + x * q,
                fluid,
            )
            th = _prop(
                "T",
                "P",
                ph + x * (hot.p - ph),
                "H",
                hot.h - (1.0 - x) * q,
                fluid,
            )
            differences.append(th - tc)
        return min(differences)

    if upper <= 0.0 or pinch(0.0) < -1e-7:
        raise CycleError("Recuperator pressure losses yield infeasible pinch.")
    maximum = upper if pinch(upper) >= 0.0 else brentq(pinch, 0.0, upper)
    q = effectiveness * maximum
    return (
        _state(fluid, pc, "H", cold.h + q),
        _state(fluid, ph, "H", hot.h - q),
        q,
    )


def _finish(
    fluid: str,
    states: dict[str, State],
    edges: dict[str, tuple[str, str, bool]],
    env: Environment,
    mass_flow: float,
    n: int,
    q_regen: float,
    envelope: bool,
) -> CycleResult:
    """Assemble first/second-law accounts and dense plotting data.

    Args:
        fluid: CoolProp identifier.
        states: Named SI states.
        edges: Component inlet/outlet names and machine flag.
        env: Dead state and reservoir temperatures [K], pressure [Pa].
        mass_flow: Working-fluid flow [kg/s].
        n: Path point count.
        q_regen: Recovered heat [J/kg].
        envelope: Whether saturation data is requested.

    Returns:
        Complete cycle. q_in-q_out-w_net is the energy residual;
        (1-T0/TH)*q_in-(1-T0/TC)*q_out-w_net-sum(ED) is the
        exergy residual [J/kg]. ED=T0*S_gen for each control volume.

    Raises:
        CycleError: For non-power cycles, reservoir temperature crosses, or
            negative entropy generation beyond numerical roundoff.
    """
    for name, value in vars(env).items():
        _positive(name, value)
    _positive("mass_flow", mass_flow)
    if env.hot_temperature <= env.temperature:
        raise CycleError("Hot reservoir must exceed dead-state temperature.")
    paths = {
        name: _path(fluid, states[a], states[b], n, machine)
        for name, (a, b, machine) in edges.items()
    }
    ref = _state(fluid, env.pressure, "T", env.temperature)
    exergy = {
        k: v.h - ref.h - env.temperature * (v.s - ref.s)
        for k, v in states.items()
    }
    destruction = {}
    qin = qout = work = 0.0
    for name, (a, b, machine) in edges.items():
        inlet, outlet = states[a], states[b]
        ds = outlet.s - inlet.s
        dh = outlet.h - inlet.h
        if machine:
            work -= dh
            destruction[name] = env.temperature * ds
        elif name == "heater":
            qin = dh
            if np.max(paths[name].T) > env.hot_temperature + 1e-6:
                raise CycleError("Hot reservoir is colder than heater fluid.")
            destruction[name] = env.temperature * (
                ds - dh / env.hot_temperature
            )
        elif name == "cooler":
            qout = -dh
            if np.min(paths[name].T) < env.cold_temperature - 1e-6:
                raise CycleError("Cold reservoir is hotter than cooler fluid.")
            destruction[name] = env.temperature * (
                ds - dh / env.cold_temperature
            )
    if "regenerator_cold" in edges:
        destruction["regenerator"] = env.temperature * sum(
            states[b].s - states[a].s
            for name, (a, b, _) in edges.items()
            if name.startswith("regenerator")
        )
    scale = max(abs(qin), abs(qout), abs(work), 1.0)
    if min(destruction.values()) < -1e-7 * scale:
        raise CycleError("Negative component entropy generation.")
    if min(qin, qout) <= 0.0 or work < 0.0:
        raise CycleError("Specification is not a heat-engine power cycle.")
    fuel = (1.0 - env.temperature / env.hot_temperature) * qin
    reject = (1.0 - env.temperature / env.cold_temperature) * qout
    warnings = [
        "Machine paths interpolate h versus log(p); internal paths "
        "are not determined by endpoint isentropic efficiencies."
    ]
    if q_regen == 0.0 and "regenerator_cold" in edges:
        warnings.append("Recuperation is bypassed; specified losses remain.")
    if work == 0.0:
        warnings.append("Degenerate zero-work cycle; efficiencies are zero.")
    sat = saturation_envelope(fluid) if envelope else {}
    for key, state in states.items():
        if state.quality is not None and 0.0 < state.quality < 1.0:
            warnings.append(
                f"State {key} is wet: quality={state.quality:.5f}."
            )
    return CycleResult(
        states,
        paths,
        sat,
        work,
        qin,
        qout,
        work / qin,
        work / fuel,
        exergy,
        destruction,
        qin - qout - work,
        fuel - reject - work - sum(destruction.values()),
        mass_flow * work,
        {k: mass_flow * v for k, v in destruction.items()},
        q_regen,
        tuple(warnings),
    )


def solve_brayton(
    config: BraytonConfig, points: int = 200, include_saturation: bool = True
) -> CycleResult:
    """Solve a closed real-fluid recuperated Brayton cycle.

    Args:
        config: SI cycle specifications. No ideal-gas approximation is used.
        points: Minimum samples per component, at least 20.
        include_saturation: Generate liquid/vapor saturation curves.

    Returns:
        States 1 compressor inlet, 2 compressor outlet, 3 recuperator cold
        outlet, 4 turbine inlet, 5 turbine outlet, 6 recuperator hot outlet.
        eta_th=(w_t-w_c)/q_in; eta_II=w_net/[(1-T0/TH)*q_in].

    Raises:
        CycleError: For infeasible inputs or CoolProp domain failures.
    """
    c = config
    if points < 20:
        raise CycleError("At least 20 path samples are required.")
    for name in (
        "inlet_pressure",
        "inlet_temperature",
        "pressure_ratio",
        "turbine_inlet_temperature",
    ):
        _positive(name, getattr(c, name))
    for name in (
        "cold_regenerator_drop",
        "hot_regenerator_drop",
        "heater_drop",
        "cooler_drop",
    ):
        _fraction(name, getattr(c, name))
    if c.pressure_ratio < 1.0 or not 0.0 <= c.effectiveness <= 1.0:
        raise CycleError(
            "Require pressure_ratio>=1 and effectiveness in [0,1]."
        )
    s1 = _state(c.fluid, c.inlet_pressure, "T", c.inlet_temperature)
    if s1.quality is not None:
        raise CycleError("Brayton compressor inlet must be single phase.")
    s2 = _machine(
        c.fluid, s1, s1.p * c.pressure_ratio, c.compressor_efficiency, True
    )
    p3 = s2.p * (1.0 - c.cold_regenerator_drop)
    s4 = _state(
        c.fluid, p3 * (1.0 - c.heater_drop), "T", c.turbine_inlet_temperature
    )
    p6 = s1.p / (1.0 - c.cooler_drop)
    s5 = _machine(
        c.fluid,
        s4,
        p6 / (1.0 - c.hot_regenerator_drop),
        c.turbine_efficiency,
        False,
    )
    s3, s6, recovered = _regenerator(c.fluid, s2, s5, p3, p6, c.effectiveness)
    states = dict(
        zip(("1", "2", "3", "4", "5", "6"), (s1, s2, s3, s4, s5, s6))
    )
    edges = {
        "compressor": ("1", "2", True),
        "regenerator_cold": ("2", "3", False),
        "heater": ("3", "4", False),
        "turbine": ("4", "5", True),
        "regenerator_hot": ("5", "6", False),
        "cooler": ("6", "1", False),
    }
    return _finish(
        c.fluid,
        states,
        edges,
        c.environment,
        c.mass_flow,
        points,
        recovered,
        include_saturation,
    )


def solve_rankine(
    config: RankineConfig, points: int = 200, include_saturation: bool = True
) -> CycleResult:
    """Solve a simple real-fluid Rankine cycle, including wet expansion.

    Args:
        config: SI cycle specification with a subcritical condenser.
        points: Minimum samples per component, at least 20.
        include_saturation: Generate saturation envelope for plotting.

    Returns:
        States 1 saturated-liquid pump inlet, 2 pump outlet, 3 turbine inlet,
        4 turbine outlet. Pump work uses exact h(p,s), not v*delta_p.
        eta_th=(w_t-w_p)/q_in; eta_II=w_net/[(1-T0/TH)*q_in].

    Raises:
        CycleError: For invalid phase selection or infeasible states.
    """
    c = config
    if points < 20:
        raise CycleError("At least 20 path samples are required.")
    _positive("condenser_pressure", c.condenser_pressure)
    _positive("boiler_pressure", c.boiler_pressure)
    _fraction("boiler_drop", c.boiler_drop)
    _fraction("condenser_drop", c.condenser_drop)
    if c.boiler_pressure <= c.condenser_pressure:
        raise CycleError("Boiler pressure must exceed condenser pressure.")
    s1 = _state(c.fluid, c.condenser_pressure, "Q", 0.0)
    s2 = _machine(
        c.fluid,
        s1,
        c.boiler_pressure / (1.0 - c.boiler_drop),
        c.pump_efficiency,
        True,
    )
    if c.turbine_inlet_temperature is None:
        s3 = _state(c.fluid, c.boiler_pressure, "Q", 1.0)
    else:
        _positive("turbine_inlet_temperature", c.turbine_inlet_temperature)
        s3 = _state(
            c.fluid, c.boiler_pressure, "T", c.turbine_inlet_temperature
        )
        phase = _prop("Phase", "P", s3.p, "H", s3.h, c.fluid)
        if phase not in (1.0, 2.0, 5.0):
            raise CycleError(
                "Rankine turbine inlet must be vapor/supercritical."
            )
    s4 = _machine(
        c.fluid,
        s3,
        s1.p / (1.0 - c.condenser_drop),
        c.turbine_efficiency,
        False,
    )
    states = dict(zip(("1", "2", "3", "4"), (s1, s2, s3, s4)))
    edges = {
        "pump": ("1", "2", True),
        "heater": ("2", "3", False),
        "turbine": ("3", "4", True),
        "cooler": ("4", "1", False),
    }
    return _finish(
        c.fluid,
        states,
        edges,
        c.environment,
        c.mass_flow,
        points,
        0.0,
        include_saturation,
    )
