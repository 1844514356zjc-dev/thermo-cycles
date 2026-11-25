"""Physics regression tests in SI units."""

from dataclasses import replace
from typing import Callable

import numpy as np
import pytest
from CoolProp.CoolProp import PropsSI

from core.cycle_solver import (
    BraytonConfig,
    CycleError,
    CycleResult,
    Environment,
    RankineConfig,
    solve_brayton,
    solve_rankine,
)


@pytest.mark.parametrize(
    "solver,config",
    [
        (solve_brayton, BraytonConfig()),
        (solve_rankine, RankineConfig()),
    ],
)
def test_conservation(
    solver: Callable[..., CycleResult],
    config: BraytonConfig | RankineConfig,
) -> None:
    """Check energy/exergy closure [J/kg] and nonnegative destruction."""
    r = solver(config, points=30)
    assert abs(r.energy_residual) < 1e-6
    assert abs(r.exergy_residual) < 1e-6
    assert min(r.exergy_destruction.values()) >= -1e-6
    assert 0.0 < r.thermal_efficiency < r.exergy_efficiency < 1.0
    assert set(r.saturation) == {"liquid", "vapor"}
    for path in r.paths.values():
        assert len(path.T) >= 30
        assert np.isfinite(path.s).all()


def test_efficiency_definitions() -> None:
    """Recover specified dimensionless machine efficiencies independently."""
    c = BraytonConfig()
    r = solve_brayton(c, points=20, include_saturation=False)
    a, b, d, e = (r.states[k] for k in ("1", "2", "4", "5"))
    hc = PropsSI("H", "P", b.p, "S", a.s, c.fluid)
    ht = PropsSI("H", "P", e.p, "S", d.s, c.fluid)
    assert (hc - a.h) / (b.h - a.h) == pytest.approx(c.compressor_efficiency)
    assert (d.h - e.h) / (d.h - ht) == pytest.approx(c.turbine_efficiency)
    assert r.states["3"].h - b.h == pytest.approx(e.h - r.states["6"].h)
    assert r.states["3"].p == pytest.approx(
        b.p * (1.0 - c.cold_regenerator_drop)
    )


def test_wet_expansion_and_boundary_samples() -> None:
    """Resolve wet steam and the boiler liquid/vapor boundaries [J/kg]."""
    r = solve_rankine(RankineConfig(), points=20, include_saturation=False)
    assert 0.0 < r.states["4"].quality < 1.0
    for q in (0.0, 1.0):
        h = PropsSI("H", "P", 8e6, "Q", q, "Water")
        assert np.min(abs(r.paths["heater"].h - h)) < 1e-4


@pytest.mark.parametrize(
    "fluid,p,t,ratio,hot",
    [
        ("CO2", 8e6, 310.0, 2.0, 800.0),
        ("R134a", 5e5, 300.0, 2.0, 450.0),
        ("Water", 1e5, 400.0, 2.0, 700.0),
    ],
)
def test_real_fluid_brayton(
    fluid: str, p: float, t: float, ratio: float, hot: float
) -> None:
    """Exercise real-fluid cycle operating points in Pa and K."""
    c = BraytonConfig(
        fluid=fluid,
        inlet_pressure=p,
        inlet_temperature=t,
        pressure_ratio=ratio,
        turbine_inlet_temperature=hot,
    )
    r = solve_brayton(c, points=20, include_saturation=False)
    assert r.specific_work > 0.0


def test_ideal_machines_and_zero_regeneration() -> None:
    """Adiabatic isentropic machines destroy zero exergy [J/kg]."""
    c = BraytonConfig(
        compressor_efficiency=1.0, turbine_efficiency=1.0, effectiveness=0.0
    )
    r = solve_brayton(c, points=20, include_saturation=False)
    assert abs(r.exergy_destruction["compressor"]) < 1e-5
    assert abs(r.exergy_destruction["turbine"]) < 1e-5
    assert r.regeneration_heat == 0.0


def test_invalid_inputs() -> None:
    """Reject efficiencies and reservoir heat flow opposing temperature."""
    with pytest.raises(CycleError):
        solve_rankine(RankineConfig(pump_efficiency=0.0))
    with pytest.raises(CycleError):
        solve_brayton(BraytonConfig(cold_regenerator_drop=1.0))
    with pytest.raises(CycleError):
        solve_rankine(
            replace(
                RankineConfig(), environment=Environment(hot_temperature=500.0)
            ),
            points=20,
            include_saturation=False,
        )
