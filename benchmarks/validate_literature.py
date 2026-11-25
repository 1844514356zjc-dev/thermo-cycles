"""Reproducible textbook-formula / IAPWS comparisons, in SI units.

These are explicitly constructed classical cases, not claimed transcriptions
of numbered Moran/Shapiro or Cengel examples. Air comparisons measure model
variation (constant cp versus HEOS), NOT a numerical-error certification.
The independent Rankine reference uses Python iapws, not CoolProp flashes.
Run after `pip install -e '.[benchmark]'`, or directly from the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.cycle_solver import (  # noqa: E402
    BraytonConfig,
    CycleResult,
    RankineConfig,
    solve_brayton,
    solve_rankine,
)

BRAYTON_SOURCE = (
    "https://qrg.northwestern.edu/thermo/design-library/airstd/brayton.html"
)
WATER_SOURCE = "https://iapws.org/technical-guidance/release/IF97-Rev"


@dataclass(frozen=True)
class Comparison:
    """Comparison row; values use unit, tolerance is relative percent."""

    case: str
    metric: str
    unit: str
    reference: float
    computed: float
    tolerance_percent: float
    category: str

    @property
    def relative_error_percent(self) -> float:
        """Return 100*abs(computed-reference)/abs(reference) [%].

        Returns:
            Relative error [%]; zero references are rejected because relative
            error is undefined there (use an absolute-error test instead).

        Raises:
            ValueError: If the reference is zero or either value nonfinite.
        """
        if (
            self.reference == 0.0
            or not np.isfinite([self.reference, self.computed]).all()
        ):
            raise ValueError(
                "Relative error requires finite, nonzero reference."
            )
        return (
            100.0 * abs(self.computed - self.reference) / abs(self.reference)
        )

    @property
    def passed(self) -> bool:
        """Return whether error [%] is below the declared acceptance limit.

        Returns:
            True for an accepted comparison.
        """
        return self.relative_error_percent <= self.tolerance_percent


def ideal_air_reference(config: BraytonConfig) -> dict[str, float]:
    """Evaluate independent constant-cp air-standard Brayton formulas.

    Args:
        config: SI inputs, requiring zero pressure losses. cp=1004.5 J/(kg K),
            gamma=1.4; endpoint machine efficiencies remain configurable.

    Returns:
        T2,T5 [K], recovered heat, q_in,w_net [J/kg], and efficiency [1].
        tau=rp**((gamma-1)/gamma); T2=T1+(T1*tau-T1)/eta_c;
        T5=T4-eta_t*(T4-T4/tau); q_reg=epsilon*cp*max(T5-T2,0).

    Raises:
        ValueError: If losses are nonzero or heat input is nonpositive.
    """
    if any(
        (
            config.cold_regenerator_drop,
            config.hot_regenerator_drop,
            config.heater_drop,
            config.cooler_drop,
        )
    ):
        raise ValueError(
            "Constant-cp reference requires zero pressure losses."
        )
    cp = 1004.5
    tau = config.pressure_ratio ** (0.4 / 1.4)
    t1, t4 = config.inlet_temperature, config.turbine_inlet_temperature
    t2 = t1 + (t1 * tau - t1) / config.compressor_efficiency
    t5 = t4 - config.turbine_efficiency * (t4 - t4 / tau)
    recovered = config.effectiveness * cp * max(t5 - t2, 0.0)
    qin = cp * (t4 - t2) - recovered
    work = cp * ((t4 - t5) - (t2 - t1))
    if qin <= 0.0:
        raise ValueError("Reference requires positive heat input [J/kg].")
    return {
        "T2": t2,
        "T5": t5,
        "regeneration_heat": recovered,
        "heat_input": qin,
        "specific_work": work,
        "thermal_efficiency": work / qin,
    }


def rankine_reference(config: RankineConfig) -> dict[str, float]:
    """Build a separate IF97 Rankine account using the iapws implementation.

    Args:
        config: Water cycle inputs in SI; reference accepts zero losses only.

    Returns:
        h1..h4, heat_input, specific_work [J/kg], efficiency [1], x4 [1].
        The adapter converts iapws MPa/kJ units to SI. Pump and turbine use
        h(p,s), including equilibrium wet-steam expansion.

    Raises:
        ValueError: For unsupported fluid or pressure losses.
        ImportError: If optional benchmark dependency iapws is absent.
    """
    from iapws import IAPWS97

    if config.fluid != "Water" or config.boiler_drop or config.condenser_drop:
        raise ValueError("Reference requires Water and zero pressure losses.")
    pl, ph = config.condenser_pressure / 1e6, config.boiler_pressure / 1e6
    a = IAPWS97(P=pl, x=0)
    bs = IAPWS97(P=ph, s=a.s)
    h2 = a.h + (bs.h - a.h) / config.pump_efficiency
    c = (
        IAPWS97(P=ph, x=1)
        if config.turbine_inlet_temperature is None
        else IAPWS97(P=ph, T=config.turbine_inlet_temperature)
    )
    ds = IAPWS97(P=pl, s=c.s)
    h4 = c.h - config.turbine_efficiency * (c.h - ds.h)
    d = IAPWS97(P=pl, h=h4)
    work = (c.h - h4 - h2 + a.h) * 1000.0
    qin = (c.h - h2) * 1000.0
    return {
        "h1": a.h * 1000.0,
        "h2": h2 * 1000.0,
        "h3": c.h * 1000.0,
        "h4": h4 * 1000.0,
        "heat_input": qin,
        "specific_work": work,
        "thermal_efficiency": work / qin,
        "x4": d.x,
    }


def build_comparisons() -> list[Comparison]:
    """Run three constructed classical engineering benchmark cases.

    Returns:
        Rows with explicit model categories and predeclared tolerances [%].
        Air limits are broad model-consistency bounds; IF97 versus HEOS
        permits 0.15% for formulation/backward-equation differences.
    """
    rows = []
    for label, effectiveness in (
        ("A: simple Brayton", 0.0),
        ("B: recuperated Brayton", 0.75),
    ):
        cfg = BraytonConfig(
            inlet_pressure=100000.0,
            inlet_temperature=300.0,
            pressure_ratio=4.0,
            turbine_inlet_temperature=900.0,
            compressor_efficiency=1.0,
            turbine_efficiency=1.0,
            cold_regenerator_drop=0.0,
            hot_regenerator_drop=0.0,
            effectiveness=effectiveness,
        )
        ref = ideal_air_reference(cfg)
        result = solve_brayton(cfg, points=40, include_saturation=False)
        for metric, unit in (
            ("specific_work", "J/kg"),
            ("heat_input", "J/kg"),
            ("thermal_efficiency", "1"),
        ):
            rows.append(
                Comparison(
                    label,
                    metric,
                    unit,
                    ref[metric],
                    getattr(result, metric),
                    10.0,
                    "constant-cp vs HEOS model difference",
                )
            )
    cfg_r = RankineConfig(pump_efficiency=1.0, turbine_efficiency=1.0)
    ref_r = rankine_reference(cfg_r)
    result_r = solve_rankine(cfg_r, points=40, include_saturation=False)
    for metric, unit in (
        ("specific_work", "J/kg"),
        ("heat_input", "J/kg"),
        ("thermal_efficiency", "1"),
        ("x4", "1"),
        *((f"h{i}", "J/kg") for i in range(1, 5)),
    ):
        value = _rankine_metric(result_r, metric)
        rows.append(
            Comparison(
                "C: ideal Rankine",
                metric,
                unit,
                ref_r[metric],
                value,
                0.15,
                "independent IF97 vs HEOS",
            )
        )
    return rows


def _rankine_metric(result: CycleResult, metric: str) -> float:
    """Extract a Rankine scalar without recomputing thermodynamics.

    Args:
        result: Solver result in SI.
        metric: Output field, h1..h4, or x4.

    Returns:
        Scalar in the metric's declared SI unit.

    Raises:
        ValueError: If x4 is requested outside the two-phase region.
    """
    if metric.startswith("h") and metric[1:].isdigit():
        return result.states[metric[1:]].h
    if metric == "x4":
        quality = result.states["4"].quality
        if quality is None:
            raise ValueError("Expected wet-steam outlet for reference case.")
        return quality
    return float(getattr(result, metric))


def main() -> int:
    """Print versioned Markdown comparisons and return failure status.

    Returns:
        Zero if all declared limits pass, one if any comparison fails.
        Missing optional dependencies or invalid states raise visibly.
    """
    print(f"CoolProp {version('CoolProp')}; iapws {version('iapws')}")
    print("Constructed cases, not numbered textbook examples or NIST data.")
    print("A/B: Air; p1=100000 Pa, T1=300 K, rp=4, T4=900 K;")
    print("eta_c=eta_t=1, zero losses; effectiveness=0 / 0.75.")
    print("C: Water; p_cond=10000 Pa, p_boiler=8e6 Pa, T3=773.15 K;")
    print("eta_p=eta_t=1, zero losses, saturated-liquid pump inlet.")
    print(f"Air-standard source: {BRAYTON_SOURCE}")
    print(f"Water formulation: {WATER_SOURCE}")
    print("Air errors measure model differences, not solver accuracy.\n")
    print(
        "| Case | Metric | Unit | Reference | Solver | Relative error % | "
        "Limit % | Result |"
    )
    print("|---|---|---|---:|---:|---:|---:|---|")
    rows = build_comparisons()
    for row in rows:
        print(
            f"| {row.case} | {row.metric} | {row.unit} | "
            f"{row.reference:.9g} | {row.computed:.9g} | "
            f"{row.relative_error_percent:.6g} | "
            f"{row.tolerance_percent:g} | "
            f"{'PASS' if row.passed else 'FAIL'} |"
        )
    return 0 if all(row.passed for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
