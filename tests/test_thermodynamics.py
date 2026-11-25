"""Engineering verification: SI balances, analytic limits, invalid domains."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from CoolProp.CoolProp import PropsSI

from core import cycle_solver as solver
from core.cycle_solver import (
    BraytonConfig,
    CycleError,
    RankineConfig,
    solve_brayton,
    solve_rankine,
)


@pytest.mark.parametrize("ratio", [1.0, 1.0001, 2.0, 6.0])
@pytest.mark.parametrize("effectiveness", [0.0, 0.75])
def test_brayton_energy_boundary(ratio: float, effectiveness: float) -> None:
    """Check q_in-q_out-w_net < 1e-6 J/kg at ideal-machine boundaries.

    Args:
        ratio: Compressor pressure ratio [1].
        effectiveness: Regenerator effectiveness [1].
    """
    c = BraytonConfig(
        pressure_ratio=ratio,
        effectiveness=effectiveness,
        compressor_efficiency=1.0,
        turbine_efficiency=1.0,
        cold_regenerator_drop=0.0,
        hot_regenerator_drop=0.0,
    )
    r = solve_brayton(c, points=20, include_saturation=False)
    s = r.states
    wc = s["2"].h - s["1"].h
    wt = s["4"].h - s["5"].h
    qin = s["4"].h - s["3"].h
    qout = s["6"].h - s["1"].h
    assert abs(qin - qout - (wt - wc)) < 1e-6
    assert abs(r.energy_residual) < 1e-6
    assert abs(r.exergy_residual) < 1e-6
    assert abs(s["2"].s - s["1"].s) < 1e-7
    assert abs(s["5"].s - s["4"].s) < 1e-7
    if ratio == 1.0:
        assert r.specific_work == 0.0
        assert r.thermal_efficiency == 0.0
        assert r.exergy_efficiency == 0.0
    if effectiveness == 0.0:
        assert r.regeneration_heat == 0.0
        assert s["3"].h == pytest.approx(s["2"].h, abs=1e-7)
        assert s["6"].h == pytest.approx(s["5"].h, abs=1e-7)


@pytest.mark.parametrize("efficiency", [0.7, 1.0])
def test_rankine_energy_and_second_law(efficiency: float) -> None:
    """Check independent enthalpy balances and ED=T0*Delta_s [J/kg].

    Args:
        efficiency: Pump/turbine isentropic efficiency [1].
    """
    c = RankineConfig(
        pump_efficiency=efficiency, turbine_efficiency=efficiency
    )
    r = solve_rankine(c, points=20, include_saturation=False)
    a, b, d, e = (r.states[k] for k in ("1", "2", "3", "4"))
    assert abs((d.h - b.h) - (e.h - a.h) - ((d.h - e.h) - (b.h - a.h))) < 1e-6
    assert abs(r.exergy_residual) < 1e-6
    for name, inlet, outlet in (("pump", a, b), ("turbine", d, e)):
        assert r.exergy_destruction[name] == pytest.approx(
            c.environment.temperature * (outlet.s - inlet.s), abs=1e-6
        )
        if efficiency == 1.0:
            assert abs(r.exergy_destruction[name]) < 1e-5
    assert min(r.exergy_destruction.values()) >= -1e-6


@pytest.mark.parametrize(
    "change,match",
    [
        ({"pressure_ratio": 0.9}, "pressure_ratio"),
        ({"inlet_temperature": 40.0}, "triple-point"),
        ({"fluid": "DefinitelyNotAFluid"}, "Unsupported fluid"),
        ({"compressor_efficiency": 0.0}, "efficiency"),
        ({"turbine_efficiency": 1.01}, "efficiency"),
        ({"effectiveness": -0.1}, "effectiveness"),
        ({"heater_drop": 1.0}, "heater_drop"),
        ({"inlet_temperature": float("nan")}, "finite"),
        ({"pressure_ratio": float("inf")}, "finite"),
    ],
)
def test_invalid_brayton_inputs(change: dict[str, object], match: str) -> None:
    """Require contextual CycleError rather than backend exceptions.

    Args:
        change: Invalid config overrides, with SI magnitudes.
        match: Required diagnostic fragment.
    """
    with pytest.raises(CycleError, match=match):
        solve_brayton(
            replace(BraytonConfig(), **change),
            points=20,
            include_saturation=False,
        )


def test_water_below_triple_point() -> None:
    """Reject Water turbine temperature 250 K under fluid-only policy."""
    with pytest.raises(CycleError, match="triple-point"):
        solve_rankine(
            RankineConfig(turbine_inlet_temperature=250.0),
            points=20,
            include_saturation=False,
        )


def test_unit_ratio_with_losses_is_infeasible() -> None:
    """Unit pressure ratio cannot sustain the default exchanger losses."""
    with pytest.raises(CycleError, match="pressure direction"):
        solve_brayton(
            BraytonConfig(pressure_ratio=1.0),
            points=20,
            include_saturation=False,
        )


@pytest.mark.parametrize("effectiveness", [0.0, 0.75, 1.0])
def test_constant_cp_analytic_limit(
    monkeypatch: pytest.MonkeyPatch,
    effectiveness: float,
) -> None:
    """Verify topology against an independent exact ideal-gas oracle.

    Args:
        monkeypatch: pytest scoped property adapter replacement.
        effectiveness: Heat recovery fraction [1].

    This test isolates algebra from CoolProp model differences. It does not
    claim that real Air has constant cp. h=cp*T and s=cp*ln(T)-R*ln(p).
    """
    cp, gas_constant = 1004.5, 287.0

    def ideal_property(
        output: str, a: str, x: float, b: str, y: float, fluid: str
    ) -> float:
        """Flash an exact constant-cp ideal gas in SI for analytic testing.

        Args:
            output: Requested property key.
            a: First key (P).
            x: Pressure [Pa].
            b: Second key (T, H, S, or Q).
            y: Second magnitude in SI.
            fluid: Ignored fluid identifier.

        Returns:
            Requested ideal-gas mass-specific SI property.

        Raises:
            CycleError: If saturation is requested (no ideal-gas dome).
        """
        if b == "Q":
            raise CycleError("No ideal-gas saturation boundary.")
        if b == "T":
            t = y
        elif b == "H":
            t = y / cp
        else:
            t = float(np.exp((y + gas_constant * np.log(x)) / cp))
        return float(
            {
                "T": t,
                "H": cp * t,
                "S": cp * np.log(t) - gas_constant * np.log(x),
                "Q": -1.0,
            }[output]
        )

    monkeypatch.setattr(solver, "_prop", ideal_property)
    c = BraytonConfig(
        pressure_ratio=4.0,
        turbine_inlet_temperature=900.0,
        effectiveness=effectiveness,
        compressor_efficiency=1.0,
        turbine_efficiency=1.0,
        cold_regenerator_drop=0.0,
        hot_regenerator_drop=0.0,
    )
    r = solve_brayton(c, points=20, include_saturation=False)
    tau = c.pressure_ratio ** (gas_constant / cp)
    t2, t5 = c.inlet_temperature * tau, 900.0 / tau
    expected_work = cp * (900.0 - t5 - t2 + c.inlet_temperature)
    expected_qin = cp * (900.0 - t2 - effectiveness * (t5 - t2))
    assert r.specific_work == pytest.approx(expected_work, rel=1e-11)
    assert r.thermal_efficiency == pytest.approx(
        expected_work / expected_qin, rel=1e-11
    )
    assert abs(r.energy_residual) < 1e-6


def test_flow_scaling_and_path_resolution() -> None:
    """Mass flow changes rates only; plot sampling preserves SI endpoints."""
    c = RankineConfig()
    a = solve_rankine(c, points=20, include_saturation=False)
    b = solve_rankine(
        replace(c, mass_flow=3.0), points=80, include_saturation=False
    )
    assert b.power == pytest.approx(3.0 * a.power)
    assert b.thermal_efficiency == a.thermal_efficiency
    for name in a.paths:
        assert b.paths[name].h[[0, -1]] == pytest.approx(
            a.paths[name].h[[0, -1]], abs=1e-7
        )
    for name in a.destruction_rates:
        assert b.destruction_rates[name] == pytest.approx(
            3.0 * a.destruction_rates[name]
        )


@pytest.mark.parametrize("quality", [0.0, 1.0])
def test_saturation_envelope_matches_eos(quality: float) -> None:
    """Verify saturation envelope p,T,h,s against Water EOS flashes.

    Args:
        quality: Saturated-liquid/vapor mass fraction [1].
    """
    curves = solver.saturation_envelope("Water", points=30)
    curve = curves["liquid" if quality == 0.0 else "vapor"]
    for index in (0, 15, 29):
        for key, values in (("H", curve.h), ("S", curve.s)):
            expected = PropsSI(key, "T", curve.T[index], "Q", quality, "Water")
            assert values[index] == pytest.approx(expected, rel=1e-10)
