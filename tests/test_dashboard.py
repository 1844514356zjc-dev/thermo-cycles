"""Dashboard tests for physical accounting, exports, and reactive controls."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from streamlit.testing.v1 import AppTest  # noqa: E402

from core.cycle_solver import BraytonConfig, Environment  # noqa: E402
from ui.dashboard import (  # noqa: E402
    csv_report,
    flow_diagram,
    json_report,
    optimal_point,
    parameter_sweep,
    report,
    solve,
    state_diagram,
    state_labels,
)

APP = Path(__file__).resolve().parents[1] / "src/ui/app.py"


@pytest.mark.parametrize("exergy", [False, True])
@pytest.mark.parametrize("cold", [280.0, 298.15])
def test_sankey_conserves_rates(exergy: bool, cold: float) -> None:
    """Verify rates at the central Sankey node, including subambient cold.

    Args:
        exergy: Whether the chart represents exergy rather than energy.
        cold: External cold-reservoir temperature [K].
    """
    config = BraytonConfig(environment=Environment(cold_temperature=cold))
    result = solve(config, points=20, envelope=False)
    trace = flow_diagram(config, result, exergy).data[0]
    incoming = sum(
        v for t, v in zip(trace.link.target, trace.link.value) if t == 1
    )
    outgoing = sum(
        v for s, v in zip(trace.link.source, trace.link.value) if s == 1
    )
    assert min(trace.link.value) >= 0.0
    assert incoming == pytest.approx(outgoing, abs=1e-6)
    if exergy and cold < config.environment.temperature:
        assert (3, 1) in zip(trace.link.source, trace.link.target)


def test_full_report_roundtrip_and_numbering() -> None:
    """Ensure reports retain dense SI data and simple-cycle state mapping."""
    config = BraytonConfig(
        effectiveness=0.0, cold_regenerator_drop=0.0, hot_regenerator_drop=0.0
    )
    result = solve(config, points=20, envelope=True)
    assert state_labels(config, result) == {
        "1": "1",
        "2": "2",
        "3": "4",
        "4": "5",
    }
    payload = report(config, result, "Brayton · 简单循环")
    parsed = json.loads(json_report(payload))
    assert parsed["result"]["states"]["4"]["T"] == 1200.0
    assert len(parsed["result"]["saturation"]["liquid"]["T"]) == 400
    rows = dict(list(csv.reader(io.StringIO(csv_report(payload))))[1:])
    assert (
        float(rows["result.paths.heater.T[0]"]) == result.paths["heater"].T[0]
    )
    assert rows["units.power"] == "W"
    figure = state_diagram(config, result, "T", [], True)
    assert list(figure.data[-1].text) == ["1", "2", "3", "4"]
    assert figure.data[-1].customdata.shape == (4, 5)


def test_sweep_failure_is_not_zero_or_interpolated() -> None:
    """Retain failed trials and choose the optimum only among valid states."""
    config = BraytonConfig()
    rows = parameter_sweep(config, "pressure_ratio", np.array([1.0, 4.0, 6.0]))
    assert rows[0].error is not None  # Default pressure losses prevent rp=1.
    assert rows[0].thermal_efficiency is None
    best = optimal_point(rows, "thermal_efficiency")
    assert best is not None and best.error is None
    expected = max(r.thermal_efficiency for r in rows if r.error is None)
    assert best.thermal_efficiency == expected
    assert optimal_point(rows[:1], "specific_work") is None
    with pytest.raises(ValueError, match="Unsupported"):
        parameter_sweep(config, "fluid", np.array([1.0, 2.0]))


def test_streamlit_controls_topology_and_physical_errors() -> None:
    """Exercise paired controls, simple/Rankine switching, and input errors."""
    app = AppTest.from_file(str(APP), default_timeout=40).run()
    assert not app.exception and not app.error
    assert len(app.metric) == 4
    app.number_input(key="Brayton_Airpressure_ratio_number").set_value(
        5.123
    ).run()
    assert app.slider(key="Brayton_Airpressure_ratio_slider").value == 5.123
    app.slider(key="Brayton_Airpressure_ratio_slider").set_value(4.5).run()
    assert (
        app.number_input(key="Brayton_Airpressure_ratio_number").value == 4.5
    )
    app.sidebar.radio[0].set_value("简单循环").run()
    assert not app.exception and not app.error
    app.sidebar.selectbox[0].set_value("Rankine").run()
    app.sidebar.selectbox[1].set_value("Water").run()
    assert not app.exception and not app.error
    assert app.metric[1].value == "33.425"
    app.number_input(
        key="Rankine_Waterturbine_inlet_temperature_number"
    ).set_value(250.0).run()
    assert app.error and not app.exception
    assert "triple-point" in app.error[0].value
    assert len(app.metric) == 0  # Never show a stale successful result.


def test_streamlit_scan_submission_and_invalidation() -> None:
    """Run a short scan and require rerun after the base design changes."""
    # AppTest does not yet emulate interactive st.tabs state persistence.
    # Exercise the same production panel in a minimal reactive host instead.
    app = AppTest.from_string(
        "\n".join(
            [
                "import streamlit as st",
                "from ui.app import sweep_panel",
                "from core.cycle_solver import BraytonConfig",
                'flow = st.number_input("mass_flow", value=1.0, key="flow")',
                "sweep_panel(BraytonConfig(mass_flow=flow), "
                '"Brayton · 回热循环")',
            ]
        ),
        default_timeout=40,
    ).run()
    count = next(
        widget for widget in app.number_input if widget.label == "采样点数"
    )
    count.set_value(5)
    next(
        button for button in app.button if button.label == "运行参数扫描"
    ).click().run()
    assert not app.exception
    assert any("最佳可行采样点" in message.value for message in app.success)
    app.number_input(key="flow").set_value(2.0).run()
    assert not app.exception
    assert any("请重新运行扫描" in message.value for message in app.info)
    assert not app.success
