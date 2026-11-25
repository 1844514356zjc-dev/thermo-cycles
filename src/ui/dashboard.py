"""Plotting, parameter studies, and portable reports for SI cycle results.

This module has no Streamlit dependency and never replaces the core solver's
physics. Plot axes scale h and s to kJ units; hover data and exports remain SI.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from importlib.metadata import version
import io
import json
from typing import Any, Literal

from CoolProp.CoolProp import PropsSI
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.cycle_solver import (
    BraytonConfig,
    Curve,
    CycleError,
    CycleResult,
    RankineConfig,
    solve_brayton,
    solve_rankine,
)

Config = BraytonConfig | RankineConfig
BLUE = "#216181"
ORANGE = "#A46532"
GRAY = "#75848B"
COMPONENTS = {
    "compressor": "压气机",
    "pump": "给水泵",
    "turbine": "涡轮",
    "heater": "加热器",
    "cooler": "冷却器",
    "regenerator": "回热器",
    "regenerator_cold": "回热器 · 冷侧",
    "regenerator_hot": "回热器 · 热侧",
}
HOVER = (
    "T = %{customdata[0]:.3f} K<br>P = %{customdata[1]:.3f} Pa"
    "<br>h = %{customdata[2]:.3f} J/kg<br>s = %{customdata[3]:.5f} J/(kg·K)"
    "<br>e = %{customdata[4]:.3f} J/kg<extra>%{fullData.name}</extra>"
)


@dataclass(frozen=True)
class SweepPoint:
    """One sweep sample: x in field SI units, work [J/kg], efficiency [1]."""

    x: float
    thermal_efficiency: float | None
    specific_work: float | None
    error: str | None


def solve(
    config: Config, points: int = 160, envelope: bool = True
) -> CycleResult:
    """Dispatch a typed configuration to the physical solver.

    Args:
        config: Cycle configuration in SI.
        points: Minimum interpolation points per component.
        envelope: Include saturation liquid/vapor data.

    Returns:
        Unmodified core result in SI.

    Raises:
        CycleError: For invalid physics or unavailable properties.
    """
    function = (
        solve_brayton if isinstance(config, BraytonConfig) else solve_rankine
    )
    return function(config, points=points, include_saturation=envelope)


def style_figure(
    figure: go.Figure, title: str, height: int = 460
) -> go.Figure:
    """Apply consistent light engineering-chart typography and axes.

    Args:
        figure: Figure to style.
        title: Human-readable chart title.
        height: Plot height [CSS px].

    Returns:
        Styled figure, with zoom and hover retained.
    """
    figure.update_layout(
        title=dict(text=title, font=dict(size=17)),
        template="plotly_white",
        font=dict(family="Helvetica Neue, Arial, sans-serif", color="#273840"),
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        height=height,
        margin=dict(l=30, r=30, t=65, b=50),
        legend=dict(orientation="h", y=-0.2, x=0),
        hoverlabel=dict(bgcolor="white"),
    )
    figure.update_xaxes(gridcolor="#EDF0F1", zeroline=False)
    figure.update_yaxes(gridcolor="#EDF0F1", zeroline=False)
    return figure


def _reference(config: Config) -> tuple[float, float]:
    """Obtain the mass-specific environmental reference h0 and s0 in SI.

    Args:
        config: Cycle including environmental T0 [K] and P0 [Pa].

    Returns:
        h0 [J/kg], s0 [J/(kg K)] consistent with the core reference.
    """
    env = config.environment
    return tuple(
        float(
            PropsSI(key, "P", env.pressure, "T", env.temperature, config.fluid)
        )
        for key in ("H", "S")
    )


def _hover(curve: Curve, config: Config) -> np.ndarray:
    """Compute SI hover columns including physical exergy e=h-h0-T0(s-s0).

    Args:
        curve: Plot curve in SI.
        config: Cycle and dead state in SI.

    Returns:
        N by 5 float array ordered T, p, h, s, e in SI.
    """
    h0, s0 = _reference(config)
    exergy = curve.h - h0 - config.environment.temperature * (curve.s - s0)
    return np.column_stack((curve.T, curve.p, curve.h, curve.s, exergy))


def isobars(config: Config, result: CycleResult) -> list[Curve]:
    """Flash constant-pressure trend lines over the cycle enthalpy range.

    Args:
        config: Real-fluid cycle specification.
        result: Solved state range in SI.

    Returns:
        Up to four constant-pressure curves, with NaN at invalid flashes to
        prevent lines crossing unsupported domains. h sampling resolves wet
        regions through P,H flashes instead of drawing interpolated T,s.
    """
    pressures = sorted(
        set(round(state.p, 3) for state in result.states.values())
    )
    selected = np.unique(
        np.linspace(0, len(pressures) - 1, min(4, len(pressures))).astype(int)
    )
    hs = np.array([state.h for state in result.states.values()])
    span = max(float(np.ptp(hs)), 1.0)
    samples = np.linspace(
        float(hs.min()) - 0.08 * span, float(hs.max()) + 0.08 * span, 120
    )
    curves = []
    for index in selected:
        p = pressures[index]
        temperatures, entropies = [], []
        for h in samples:
            try:
                t = float(PropsSI("T", "P", p, "H", float(h), config.fluid))
                s = float(PropsSI("S", "P", p, "H", float(h), config.fluid))
            except ValueError:
                t = s = float("nan")
            temperatures.append(t)
            entropies.append(s)
        curves.append(
            Curve(
                np.full(len(samples), p),
                np.array(temperatures),
                samples.copy(),
                np.array(entropies),
            )
        )
    return curves


def state_labels(config: Config, result: CycleResult) -> dict[str, str]:
    """Map display numbering to the core's immutable state identifiers.

    Args:
        config: Physical cycle configuration in SI.
        result: Core result retaining all original state names.

    Returns:
        Four state labels for simple cycles, six for recuperated Brayton.
        A bypass with residual recuperator losses retains all six states.
    """
    if (
        isinstance(config, BraytonConfig)
        and config.effectiveness == 0.0
        and config.cold_regenerator_drop == 0.0
        and config.hot_regenerator_drop == 0.0
    ):
        return {"1": "1", "2": "2", "3": "4", "4": "5"}
    return {key: key for key in result.states}


def state_diagram(
    config: Config,
    result: CycleResult,
    axis: Literal["T", "h"],
    trends: list[Curve],
    full_envelope: bool = True,
) -> go.Figure:
    """Build a T-s or h-s diagram with numbered states and SI hover data.

    Args:
        config: Cycle and dead-state inputs in SI.
        result: Core result; Brayton retains all six state identifiers.
        axis: Vertical property T [K] or h (displayed kJ/kg).
        trends: EOS isobars in SI.
        full_envelope: Show all saturation data; False zooms to cycle range.

    Returns:
        Interactive Plotly figure. Solid processes are sampled assumed paths;
        gray dotted lines are isobars, dashed lines saturation branches.
    """
    fig = go.Figure()
    for name, curve in result.saturation.items():
        fig.add_trace(
            go.Scatter(
                x=curve.s / 1000.0,
                y=curve.T if axis == "T" else curve.h / 1000.0,
                name="饱和液线" if name == "liquid" else "饱和汽线",
                line=dict(color="#9CA9AE", width=1.3, dash="dash"),
                customdata=_hover(curve, config),
                hovertemplate=HOVER,
            )
        )
    for curve in trends:
        fig.add_trace(
            go.Scatter(
                x=curve.s / 1000.0,
                y=curve.T if axis == "T" else curve.h / 1000.0,
                name=f"等压线 {curve.p[0] / 1e6:.3g} MPa",
                line=dict(color="#D3DCE0", width=1, dash="dot"),
                customdata=_hover(curve, config),
                hovertemplate=HOVER,
                connectgaps=False,
            )
        )
    labels = state_labels(config, result)
    for name, curve in result.paths.items():
        if len(labels) == 4 and name.startswith("regenerator"):
            continue
        color = ORANGE if name == "heater" else BLUE
        fig.add_trace(
            go.Scatter(
                x=curve.s / 1000.0,
                y=curve.T if axis == "T" else curve.h / 1000.0,
                name=COMPONENTS.get(name, name),
                line=dict(color=color, width=2.5),
                customdata=_hover(curve, config),
                hovertemplate=HOVER,
            )
        )
    states = [result.states[key] for key in labels.values()]
    points = Curve(
        *(
            np.array([getattr(s, key) for s in states])
            for key in ("p", "T", "h", "s")
        )
    )
    fig.add_trace(
        go.Scatter(
            x=points.s / 1000.0,
            y=points.T if axis == "T" else points.h / 1000.0,
            mode="markers+text",
            text=list(labels),
            textposition="top center",
            name="状态点",
            marker=dict(size=8, color=BLUE, line=dict(color="white", width=1)),
            customdata=_hover(points, config),
            hovertemplate=HOVER,
        )
    )
    if not full_envelope:
        for prop, values in (
            ("x", points.s / 1000.0),
            ("y", points.T if axis == "T" else points.h / 1000.0),
        ):
            margin = max(float(np.ptp(values)) * 0.18, 0.01)
            limits = [
                float(values.min()) - margin,
                float(values.max()) + margin,
            ]
            if prop == "x":
                fig.update_xaxes(range=limits)
            else:
                fig.update_yaxes(range=limits)
    fig.update_xaxes(title_text="s [kJ/(kg·K)]")
    fig.update_yaxes(title_text="T [K]" if axis == "T" else "h [kJ/kg]")
    return style_figure(
        fig, "温度–熵 · T–s" if axis == "T" else "焓–熵 · h–s", 530
    )


def flow_diagram(
    config: Config, result: CycleResult, exergy: bool
) -> go.Figure:
    """Build separate balanced energy or exergy rate Sankey diagrams.

    Args:
        config: Mass flow [kg/s], dead state and reservoirs [K].
        result: Core specific balances in J/kg.
        exergy: Use exergy destruction branches instead of energy flows.

    Returns:
        Sankey in W. A cold reservoir below T0 supplies positive exergy to the
        cycle (reversed branch), rather than an invalid negative Sankey link.
        Internal regeneration is not counted as external heat or destruction.
    """
    env = config.environment
    source = (
        (1.0 - env.temperature / env.hot_temperature) * result.heat_input
        if exergy
        else result.heat_input
    )
    reject = (
        (1.0 - env.temperature / env.cold_temperature) * result.heat_rejected
        if exergy
        else result.heat_rejected
    )
    names = [
        "热输入㶲" if exergy else "外部吸热",
        "循环",
        "净轴功",
        "冷源输入㶲"
        if exergy and reject < 0.0
        else ("排热㶲" if exergy else "外部排热"),
    ]
    sources, targets = [0, 1], [1, 2]
    values = [source, result.specific_work]
    sources.append(3 if reject < 0.0 else 1)
    targets.append(1 if reject < 0.0 else 3)
    values.append(abs(reject))
    colors = ["#A46532", BLUE, BLUE, GRAY]
    if exergy:
        for name, destroyed in result.exergy_destruction.items():
            if destroyed < -1e-5:
                raise CycleError("Cannot render negative exergy destruction.")
            names.append(COMPONENTS.get(name, name) + "㶲损")
            sources.append(1)
            targets.append(len(names) - 1)
            values.append(max(0.0, destroyed))
            colors.append("#A5AFB4")
    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            valueformat=".3f",
            valuesuffix=" W",
            node=dict(
                label=names,
                color=colors,
                pad=24,
                thickness=14,
                line=dict(color="white", width=1),
            ),
            link=dict(
                source=sources,
                target=targets,
                value=[v * config.mass_flow for v in values],
                color="rgba(70,105,122,0.20)",
                hovertemplate="%{source.label} → %{target.label}"
                "<br>%{value:.3f} W<extra></extra>",
            ),
        )
    )
    return style_figure(
        fig, "㶲流与不可逆损失 [W]" if exergy else "系统能量流 [W]"
    )


def destruction_chart(result: CycleResult) -> go.Figure:
    """Plot sorted component destruction rates and fractions.

    Args:
        result: Cycle with component destruction rates [W].

    Returns:
        Horizontal bar chart with percentages of total destruction [1].
    """
    items = sorted(result.destruction_rates.items(), key=lambda item: item[1])
    total = sum(max(0.0, value) for _, value in items)
    percentages = [
        100.0 * max(0.0, value) / total if total else 0.0 for _, value in items
    ]
    fig = go.Figure(
        go.Bar(
            x=[value for _, value in items],
            y=[COMPONENTS.get(key, key) for key, _ in items],
            orientation="h",
            marker_color=[GRAY] * (len(items) - 1) + [ORANGE],
            customdata=percentages,
            text=[f"{p:.1f}%" for p in percentages],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}<br>㶲损 = %{x:.3f} W"
            "<br>占比 = %{customdata:.2f}%<extra></extra>",
        )
    )
    fig.update_xaxes(title_text="㶲损率 [W]", rangemode="tozero")
    return style_figure(fig, "各部件㶲损占比", 370)


def parameter_sweep(
    config: Config, field: str, values: np.ndarray
) -> list[SweepPoint]:
    """Evaluate independent steady-state samples without plot/envelope cost.

    Args:
        config: Base cycle in SI.
        field: Supported dataclass input field (not an environmental field).
        values: One-dimensional finite trial values in field SI units.

    Returns:
        Every sample, preserving failed physics as explicit error records.
        No interpolation across failures and no claim of continuous optimum.

    Raises:
        ValueError: For unsupported fields or invalid scan size/range.
    """
    allowed = {"turbine_inlet_temperature", "turbine_efficiency"}
    allowed |= (
        {"pressure_ratio", "effectiveness"}
        if isinstance(config, BraytonConfig)
        else {"boiler_pressure"}
    )
    if field not in allowed or values.ndim != 1 or not 2 <= len(values) <= 100:
        raise ValueError("Unsupported sweep field or sample count (2–100).")
    if not np.isfinite(values).all():
        raise ValueError("Sweep values must be finite.")
    rows = []
    for value in values:
        try:
            result = solve(
                replace(config, **{field: float(value)}),
                points=20,
                envelope=False,
            )
            rows.append(
                SweepPoint(
                    float(value),
                    result.thermal_efficiency,
                    result.specific_work,
                    None,
                )
            )
        except CycleError as exc:
            rows.append(SweepPoint(float(value), None, None, str(exc)))
    return rows


def optimal_point(rows: list[SweepPoint], objective: str) -> SweepPoint | None:
    """Find the best feasible sampled design, excluding failed states.

    Args:
        rows: All successful and failed sweep samples.
        objective: thermal_efficiency [1] or specific_work [J/kg].

    Returns:
        Sample maximizing the selected objective, or None if all fail.

    Raises:
        ValueError: If the objective is unsupported.
    """
    if objective not in {"thermal_efficiency", "specific_work"}:
        raise ValueError("Unknown optimization objective.")
    feasible = [
        row
        for row in rows
        if row.error is None and getattr(row, objective) is not None
    ]
    return (
        max(feasible, key=lambda row: getattr(row, objective))
        if feasible
        else None
    )


def sweep_chart(
    rows: list[SweepPoint], x_label: str, objective: str
) -> go.Figure:
    """Plot efficiency and specific work with a marked discrete optimum.

    Args:
        rows: Scan outcomes with None values at infeasible samples.
        x_label: Independent variable name and SI unit.
        objective: Optimization metric identifier.

    Returns:
        Dual-y chart, efficiency [%] left and specific work [kJ/kg] right.
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(
            x=[r.x for r in rows],
            y=[
                100.0 * r.thermal_efficiency
                if r.thermal_efficiency is not None
                else None
                for r in rows
            ],
            name="热效率",
            connectgaps=False,
            mode="lines+markers",
            line=dict(color=BLUE),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=[r.x for r in rows],
            y=[
                r.specific_work / 1000.0
                if r.specific_work is not None
                else None
                for r in rows
            ],
            name="净比功",
            connectgaps=False,
            mode="lines+markers",
            line=dict(color=ORANGE),
        ),
        secondary_y=True,
    )
    best = optimal_point(rows, objective)
    if best is not None:
        y = (
            100.0 * best.thermal_efficiency
            if objective == "thermal_efficiency"
            else best.specific_work / 1000.0
        )
        fig.add_trace(
            go.Scatter(
                x=[best.x],
                y=[y],
                name="Optimal Design Point · 采样最优",
                mode="markers",
                marker=dict(symbol="diamond", size=13, color="#283E48"),
            ),
            secondary_y=objective == "specific_work",
        )
        fig.add_vline(x=best.x, line_dash="dot", line_color=GRAY)
    fig.update_xaxes(title_text=x_label)
    fig.update_yaxes(title_text="热效率 [%]", secondary_y=False)
    fig.update_yaxes(
        title_text="净比功 [kJ/kg]", secondary_y=True, showgrid=False
    )
    return style_figure(fig, "参数响应与最佳可行采样点", 480)


def report(
    config: Config, result: CycleResult, topology: str
) -> dict[str, Any]:
    """Construct a complete versioned SI report including arrays and warnings.

    Args:
        config: Input dataclass, with all environmental assumptions.
        result: Unmodified current computation.
        topology: Display topology descriptor.

    Returns:
        JSON-compatible dictionary with config, result, units and provenance.
    """
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "topology": topology,
        "display_state_mapping": state_labels(config, result),
        "versions": {
            key: version(key)
            for key in ("CoolProp", "numpy", "scipy", "plotly")
        },
        "units": {
            "p": "Pa",
            "T": "K",
            "h": "J/kg",
            "s": "J/(kg K)",
            "physical_exergy": "J/kg",
            "specific_work": "J/kg",
            "heat_input": "J/kg",
            "heat_rejected": "J/kg",
            "regeneration_heat": "J/kg",
            "exergy_destruction": "J/kg",
            "energy_residual": "J/kg",
            "exergy_residual": "J/kg",
            "power": "W",
            "destruction_rates": "W",
            "efficiencies": "1",
            "mass_flow": "kg/s",
        },
        "assumptions": [
            "Steady state; negligible kinetic/potential energy.",
            "Isothermal external reservoirs; physical exergy only.",
            "Machine trajectories are plotting interpolations.",
            "Recuperator pinch is checked at 201 stations.",
        ],
        "config": asdict(config),
        "result": asdict(result),
    }


def json_report(payload: dict[str, Any]) -> str:
    """Serialize full report without nonstandard NaN/Infinity JSON tokens.

    Args:
        payload: SI report with NumPy arrays.

    Returns:
        UTF-8-ready JSON string, nonfinite plot samples represented by null.
    """

    def clean(value: Any) -> Any:
        """Normalize arrays and nonfinite values recursively for JSON.

        Args:
            value: Report node, scalar, container, or NumPy array.

        Returns:
            Standards-compliant JSON-compatible node.
        """
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, np.ndarray)):
            return [clean(v) for v in value]
        if isinstance(value, (float, np.floating)):
            return float(value) if np.isfinite(value) else None
        return value

    return json.dumps(
        clean(payload), ensure_ascii=False, indent=2, allow_nan=False
    )


def csv_report(payload: dict[str, Any]) -> str:
    """Flatten the full report into a lossless, long-format CSV.

    Args:
        payload: Report including states, paths, envelopes and metadata.

    Returns:
        CSV text, columns path/value; indices identify dense-array samples.
        Unit metadata is included. CSV is intentionally not a state-only table.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["path", "value"])

    def visit(value: Any, path: str) -> None:
        """Write report leaves with unambiguous indexed hierarchical paths.

        Args:
            value: Current report node in SI.
            path: Dot/index path identifying the current node.
        """
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else key)
        elif isinstance(value, (list, tuple, np.ndarray)):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
        else:
            writer.writerow([path, value])

    visit(json.loads(json_report(payload)), "")
    return output.getvalue()
