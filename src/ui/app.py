"""Streamlit real-fluid cycle laboratory; launch with streamlit run.

All input fields are SI. Plot axes and KPI cards explicitly label scaled
engineering units. Run from any directory with this file's absolute path.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import streamlit as st  # noqa: E402

from core.cycle_solver import (  # noqa: E402
    BraytonConfig,
    CycleError,
    CycleResult,
    Environment,
    RankineConfig,
)
from ui.dashboard import (  # noqa: E402
    COMPONENTS,
    Config,
    Curve,
    csv_report,
    destruction_chart,
    flow_diagram,
    isobars,
    json_report,
    optimal_point,
    parameter_sweep,
    report,
    solve,
    state_diagram,
    state_labels,
    sweep_chart,
)

CSS = """
<style>
.stApp {background:#FBFBFA; color:#273840;}
[data-testid="stSidebar"] {background:#F1F3F3; border-right:1px solid #DEE4E6;}
html, body, [class*="css"], .stApp {
 font-family:-apple-system,BlinkMacSystemFont,
 "Helvetica Neue",Arial,sans-serif;}
h1 {font-size:2.2rem !important; font-weight:600 !important;
 letter-spacing:-.045em; color:#213740;}
h2,h3 {font-weight:600 !important; letter-spacing:-.02em;}
.block-container {max-width:1600px; padding-top:2.4rem; padding-bottom:3rem;}
[data-testid="stMetric"] {background:white; padding:18px 20px;
 border:1px solid #EAEAEA; border-radius:6px;}
[data-testid="stMetricValue"] {font-variant-numeric:tabular-nums;
 font-family:"SFMono-Regular",Consolas,monospace; font-size:1.65rem;}
[data-baseweb="tab-list"] {gap:24px; border-bottom:1px solid #DCE3E6;}
button[data-baseweb="tab"] {font-size:14px;}
.stButton button,.stDownloadButton button {border-radius:5px; box-shadow:none;}
[data-testid="stSidebar"] .stSlider {padding-top:0;}
@media (max-width:760px) {.block-container {padding:1.2rem;}}
</style>
"""


def _sync(source: str, target: str) -> None:
    """Synchronize paired widgets before the rerun creates their controls.

    Args:
        source: Session-state key updated by the user.
        target: Partner widget's session-state key.
    """
    st.session_state[target] = st.session_state[source]


def control(
    label: str,
    key: str,
    value: float,
    low: float,
    high: float,
    step: float = 0.01,
    fmt: str = "%.3f",
) -> float:
    """Render linked slider and exact numeric SI input without quantization.

    Args:
        label: Parameter name including its SI unit.
        key: Unique configuration-scoped widget prefix.
        value: Default SI magnitude.
        low: Minimum allowable magnitude in SI.
        high: Maximum allowable magnitude in SI.
        step: Slider/number increment in the same unit.
        fmt: Number-input display format.

    Returns:
        Exact number-input value, synchronized bidirectionally with slider.
    """
    slider_key, number_key = key + "_slider", key + "_number"
    if slider_key not in st.session_state:
        st.session_state[slider_key] = float(value)
        st.session_state[number_key] = float(value)
    st.slider(
        label,
        min_value=float(low),
        max_value=float(high),
        step=float(step),
        key=slider_key,
        on_change=_sync,
        args=(slider_key, number_key),
        format=fmt,
    )
    st.number_input(
        label + " · 精确输入",
        min_value=float(low),
        max_value=float(high),
        step=float(step),
        format=fmt,
        key=number_key,
        label_visibility="collapsed",
        on_change=_sync,
        args=(number_key, slider_key),
    )
    return float(st.session_state[number_key])


def presets(family: str, fluid: str) -> Config:
    """Choose initial operating points; ranges remain fluid-dependent.

    Args:
        family: Brayton or Rankine.
        fluid: CoolProp fluid identifier.

    Returns:
        Initial SI configuration; user-edited states still undergo validation.
    """
    if family == "Brayton":
        settings = {
            "Air": {},
            "CO2": dict(
                inlet_pressure=8e6,
                inlet_temperature=310.0,
                pressure_ratio=2.0,
                turbine_inlet_temperature=800.0,
            ),
            "R134a": dict(
                inlet_pressure=5e5,
                inlet_temperature=300.0,
                pressure_ratio=2.0,
                turbine_inlet_temperature=450.0,
            ),
            "Water": dict(
                inlet_pressure=1e5,
                inlet_temperature=400.0,
                pressure_ratio=2.0,
                turbine_inlet_temperature=700.0,
            ),
        }
        return BraytonConfig(fluid=fluid, **settings[fluid])
    settings_r = {
        "Water": {},
        "R134a": dict(
            condenser_pressure=5e5,
            boiler_pressure=3e6,
            turbine_inlet_temperature=400.0,
            environment=Environment(
                cold_temperature=280.0, hot_temperature=600.0
            ),
        ),
        "CO2": dict(
            condenser_pressure=3e6,
            boiler_pressure=8e6,
            turbine_inlet_temperature=500.0,
            environment=Environment(
                cold_temperature=260.0, hot_temperature=800.0
            ),
        ),
        "Air": dict(
            condenser_pressure=1e5,
            boiler_pressure=2e6,
            turbine_inlet_temperature=280.0,
            environment=Environment(
                temperature=70.0, cold_temperature=65.0, hot_temperature=600.0
            ),
        ),
    }
    return RankineConfig(fluid=fluid, **settings_r[fluid])


def control_panel() -> tuple[Config, str]:
    """Collect cycle, component, reservoir, and dead-state specifications.

    Returns:
        SI configuration and topology description. Simple Brayton bypasses
        recuperation and both recuperator pressure drops; Rankine remains
        simple because the core has no feedwater-heater model.
    """
    with st.sidebar:
        st.subheader("Control Panel")
        st.caption("真实流体 · 稳态 · SI 国际单位制")
        family = st.selectbox("循环类型", ["Brayton", "Rankine"])
        fluid = st.selectbox("工质", ["Air", "CO2", "R134a", "Water"])
        if family == "Brayton":
            topology = st.radio(
                "循环拓扑", ["回热循环", "简单循环"], horizontal=True
            )
        else:
            topology = "简单循环"
            st.caption("简单朗肯循环 · 无抽汽给水回热")
            if fluid == "Air":
                st.warning("Air 为伪纯流体；低温朗肯闪蒸可能受后端支持限制。")
        base = presets(family, fluid)
        scope = family + "_" + fluid

        def knob(
            name: str,
            label: str,
            low: float,
            high: float,
            step: float = 0.01,
            fmt: str = "%.3f",
        ) -> float:
            """Read a scoped input with the preset as its initial SI value.

            Args:
                name: Configuration field.
                label: UI label with unit.
                low: SI lower bound.
                high: SI upper bound.
                step: Increment in SI.
                fmt: Numeric format.

            Returns:
                Current SI magnitude.
            """
            return control(
                label,
                scope + name,
                float(getattr(base, name)),
                low,
                high,
                step,
                fmt,
            )

        st.divider()
        st.markdown("**运行工况**")
        changes = {}
        if family == "Brayton":
            changes["pressure_ratio"] = knob(
                "pressure_ratio", "压比 [1]", 1.0, 30.0
            )
            changes["inlet_pressure"] = knob(
                "inlet_pressure", "入口压力 [Pa]", 1000.0, 30e6, 1000.0, "%.1f"
            )
            changes["inlet_temperature"] = knob(
                "inlet_temperature", "入口温度 [K]", 60.0, 1000.0, 0.1
            )
        else:
            changes["condenser_pressure"] = knob(
                "condenser_pressure",
                "冷凝压力 [Pa]",
                1000.0,
                10e6,
                1000.0,
                "%.1f",
            )
            changes["boiler_pressure"] = knob(
                "boiler_pressure",
                "锅炉出口压力 [Pa]",
                10000.0,
                30e6,
                10000.0,
                "%.1f",
            )
        changes["turbine_inlet_temperature"] = knob(
            "turbine_inlet_temperature",
            "最高温限 / 涡轮入口温度 [K]",
            70.0,
            2000.0,
            0.1,
        )
        changes["mass_flow"] = knob(
            "mass_flow", "质量流量 [kg/s]", 0.01, 100.0
        )

        with st.expander("机器等熵效率", expanded=False):
            field = (
                "compressor_efficiency"
                if family == "Brayton"
                else "pump_efficiency"
            )
            changes[field] = knob(field, "压气机 / 泵效率 [1]", 0.1, 1.0)
            changes["turbine_efficiency"] = knob(
                "turbine_efficiency", "涡轮效率 [1]", 0.1, 1.0
            )
        with st.expander("换热与压力损失", expanded=False):
            if family == "Brayton":
                if topology == "回热循环":
                    changes["effectiveness"] = knob(
                        "effectiveness", "回热度 [1]", 0.0, 1.0
                    )
                    for name, label in (
                        ("cold_regenerator_drop", "回热器冷侧压损 [1]"),
                        ("hot_regenerator_drop", "回热器热侧压损 [1]"),
                    ):
                        changes[name] = knob(name, label, 0.0, 0.3, 0.001)
                else:
                    changes.update(
                        effectiveness=0.0,
                        cold_regenerator_drop=0.0,
                        hot_regenerator_drop=0.0,
                    )
                loss_fields = (
                    ("heater_drop", "加热器压损 [1]"),
                    ("cooler_drop", "冷却器压损 [1]"),
                )
            else:
                loss_fields = (
                    ("boiler_drop", "锅炉压损 [1]"),
                    ("condenser_drop", "冷凝器压损 [1]"),
                )
            for name, label in loss_fields:
                changes[name] = knob(name, label, 0.0, 0.3, 0.001)
        with st.expander("环境死态与外部热库", expanded=False):
            env = base.environment
            t0 = control(
                "死态温度 T₀ [K]",
                scope + "T0",
                env.temperature,
                60.0,
                600.0,
                0.1,
            )
            p0 = control(
                "死态压力 P₀ [Pa]",
                scope + "P0",
                env.pressure,
                1000.0,
                20e6,
                1000.0,
                "%.1f",
            )
            th = control(
                "恒温热源 T_H [K]",
                scope + "TH",
                env.hot_temperature,
                70.0,
                3000.0,
                0.1,
            )
            tc = control(
                "恒温冷源 T_C [K]",
                scope + "TC",
                env.cold_temperature,
                60.0,
                600.0,
                0.1,
            )
            changes["environment"] = Environment(t0, p0, th, tc)
            st.caption("热库温度决定传热㶲；T₀/P₀ 只定义环境参考态。")
        st.caption("参数范围是输入边界，不保证每种工质与工况均可行。")
    return replace(base, **changes), family + " · " + topology


@st.cache_data(max_entries=24, show_spinner=False)
def cached_solve(config: Config) -> CycleResult:
    """Cache complete calculations by immutable SI input configuration.

    Args:
        config: Current physical configuration.

    Returns:
        Core result with dense paths and saturation branches.
    """
    return solve(config)


@st.cache_data(max_entries=24, show_spinner=False)
def cached_isobars(config: Config, result: CycleResult) -> list[Curve]:
    """Cache auxiliary real-fluid isobar curves.

    Args:
        config: Current physical configuration.
        result: Complete SI result defining plot range.

    Returns:
        Curves with constant pressures in Pa.
    """
    return isobars(config, result)


def exports(config: Config, result: CycleResult, topology: str) -> None:
    """Offer one-click complete JSON/CSV reports for the current state.

    Args:
        config: Current SI inputs.
        result: Current complete solution, never an older successful result.
        topology: Selected cycle descriptor.
    """
    with st.expander("导出完整计算报告 · JSON / CSV"):
        payload = report(config, result, topology)
        left, right = st.columns(2)
        left.download_button(
            "下载 JSON 报告",
            json_report(payload),
            file_name="cycle_report.json",
            mime="application/json",
            width="stretch",
        )
        right.download_button(
            "下载 CSV 报告",
            "\ufeff" + csv_report(payload),
            file_name="cycle_report.csv",
            mime="text/csv",
            width="stretch",
        )
        st.caption(
            "包含输入、环境、单位、状态、性能、部件㶲损、残差、密集路径、"
            "饱和包络和警告。CSV 为完整的 path/value 长表；所有导出量采用 SI。"
        )


def sweep_panel(config: Config, topology: str) -> None:
    """Render submitted parameter scans and invalidate stale scan results.

    Args:
        config: Current SI base design.
        topology: Current topology, included in scan identity.
    """
    st.subheader("单参数设计扫描")
    st.caption(
        "每个采样点独立求解；其余工况固定。最优仅指采样范围内所选目标的最佳可行点。"
    )
    options = {
        "最高温度 [K]": ("turbine_inlet_temperature", 800.0, 1400.0),
        "涡轮效率 [1]": ("turbine_efficiency", 0.6, 1.0),
    }
    if isinstance(config, BraytonConfig):
        options = {"压比 [1]": ("pressure_ratio", 2.0, 20.0), **options}
        if "回热循环" in topology:
            options["回热度 [1]"] = ("effectiveness", 0.0, 1.0)
    else:
        options = {
            "锅炉出口压力 [Pa]": ("boiler_pressure", 2e6, 15e6),
            **options,
        }
    field_label = st.selectbox("扫描自变量", list(options))
    field, low, high = options[field_label]
    if field == "turbine_inlet_temperature":
        current = config.turbine_inlet_temperature
        low, high = max(70.0, current * 0.85), current * 1.15
    with st.form("sweep_form"):
        a, b, c = st.columns(3)
        start = a.number_input(
            "扫描起点（SI）",
            value=float(low),
            key=field + "_start",
            format="%.4f",
        )
        stop = b.number_input(
            "扫描终点（SI）",
            value=float(high),
            key=field + "_stop",
            format="%.4f",
        )
        count = c.number_input("采样点数", min_value=5, max_value=60, value=20)
        objective_label = st.radio(
            "最优点目标", ["最大热效率", "最大净比功"], horizontal=True
        )
        submitted = st.form_submit_button("运行参数扫描", type="primary")
    objective = (
        "thermal_efficiency"
        if objective_label == "最大热效率"
        else "specific_work"
    )
    signature = (config, topology, field, start, stop, count, objective)
    if submitted:
        if not np.isfinite([start, stop]).all() or start >= stop:
            st.error("扫描范围必须有限，且终点大于起点。")
            return
        with st.spinner(f"正在独立计算 {count} 组稳态工况…"):
            rows = parameter_sweep(
                config, field, np.linspace(start, stop, int(count))
            )
        st.session_state["sweep_result"] = (signature, rows)
    saved = st.session_state.get("sweep_result")
    if saved is None:
        st.info("选择范围和目标后运行扫描。默认压比范围为 2–20。")
        return
    if saved[0] != signature:
        st.info("工况或扫描设置已改变，请重新运行扫描。")
        return
    rows = saved[1]
    st.plotly_chart(
        sweep_chart(rows, field_label, objective), width="stretch", theme=None
    )
    best = optimal_point(rows, objective)
    failed = [row for row in rows if row.error is not None]
    if best is not None:
        st.success(
            f"最佳可行采样点：{field_label} = {best.x:.6g}；"
            f"热效率 {best.thermal_efficiency:.3%}；"
            f"净比功 {best.specific_work / 1000.0:.3f} kJ/kg。"
        )
        if best.x in (start, stop):
            st.caption("最佳点位于扫描边界，可扩大范围后再做判断。")
    else:
        st.warning("当前范围内没有可行采样点，请查看下方诊断。")
    if failed:
        st.warning(
            f"{len(failed)} / {len(rows)} 个点不可行；曲线在这些位置断开。"
        )
    with st.expander("查看全部采样结果与失败诊断"):
        st.dataframe([asdict(row) for row in rows], width="stretch")
    sweep_payload = {
        "base_config": asdict(config),
        "topology": topology,
        "field": field,
        "objective": objective,
        "scope": "best feasible sampled point only",
        "samples": [asdict(row) for row in rows],
    }
    st.download_button(
        "导出扫描 JSON",
        json_report(sweep_payload),
        file_name="parameter_sweep.json",
        mime="application/json",
    )


def main() -> None:
    """Render the engineering dashboard and contain physical input failures."""
    st.set_page_config(
        page_title="Thermo Cycles | Engineering Laboratory",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CSS, unsafe_allow_html=True)
    st.caption("THERMO CYCLES / ENGINEERING LABORATORY")
    st.title("热力学循环分析工作台")
    st.write("真实物性驱动的循环计算、能量核算与不可逆损失分析。")
    config, topology = control_panel()
    st.caption(
        f"{topology}  /  {config.fluid}  /  ṁ = {config.mass_flow:g} kg/s"
    )
    try:
        with st.spinner("正在求解真实流体循环…"):
            result = cached_solve(config)
    except CycleError as exc:
        st.error("当前工况不可行：" + str(exc))
        st.info(
            "请检查入口相态、温度范围、可用膨胀压比，以及热源/冷源温度与工质的传热方向。"
        )
        return
    except ValueError as exc:
        st.error("物性或输入数据不可用：" + str(exc))
        return
    tab1, tab2, tab3 = st.tabs(
        ["循环状态与热力图谱", "㶲损分布与桑基图", "参数敏感性分析"],
        key="main_tabs",
        on_change="rerun",
    )
    if tab1.open:
        with tab1:
            metrics = st.columns(4)
            for col, label, value in zip(
                metrics,
                (
                    "净输出功率 [kW]",
                    "热效率 [%]",
                    "㶲效率 [%]",
                    "总㶲损率 [kW]",
                ),
                (
                    result.power / 1000.0,
                    result.thermal_efficiency * 100.0,
                    result.exergy_efficiency * 100.0,
                    sum(result.destruction_rates.values()) / 1000.0,
                ),
            ):
                col.metric(label, f"{value:,.3f}")
            st.caption(
                f"净比功 {result.specific_work / 1000.0:.3f} kJ/kg · "
                f"能量闭合残差 {result.energy_residual:.2e} J/kg · "
                f"㶲闭合残差 {result.exergy_residual:.2e} J/kg"
            )
            full = st.toggle("显示完整饱和包络", value=True)
            trends = cached_isobars(config, result)
            left, right = st.columns(2)
            for column, axis in ((left, "T"), (right, "h")):
                column.plotly_chart(
                    state_diagram(config, result, axis, trends, full),
                    width="stretch",
                    theme=None,
                )
            st.caption(
                "实线：循环路径；点线：真实物性等压趋势；虚线：饱和液/汽线。"
                "坐标采用 kJ，悬停值采用 SI。简单循环有 4 个状态点，"
                "回热布雷顿有 6 个；"
                "Air 的包络为伪纯流体相边界。"
            )
            st.dataframe(
                [
                    {
                        "状态": key,
                        "P [Pa]": state.p,
                        "T [K]": state.T,
                        "h [J/kg]": state.h,
                        "s [J/(kg K)]": state.s,
                        "e [J/kg]": result.physical_exergy[raw_key],
                        "干度 [1]": state.quality,
                    }
                    for key, raw_key in state_labels(config, result).items()
                    for state in [result.states[raw_key]]
                ],
                hide_index=True,
                width="stretch",
            )
            with st.expander("模型假设与数值诊断"):
                st.write(
                    "机器路径为端点间插值；等熵效率不能唯一决定内部路径。"
                    "回热器夹点采用 201 个站位检查。加热器/冷却器㶲损包含"
                    "与恒温外部热库之间的不可逆性。"
                )
                for warning in result.warnings:
                    st.caption(warning)
    if tab2.open:
        with tab2:
            st.subheader("能量与㶲收支")
            st.caption(
                "能量流图只计外部热量和净轴功；内部回热不重复计入。㶲损是不可逆性，不是能量消失。"
            )
            left, right = st.columns(2)
            left.plotly_chart(
                flow_diagram(config, result, False),
                width="stretch",
                theme=None,
            )
            right.plotly_chart(
                flow_diagram(config, result, True), width="stretch", theme=None
            )
            st.plotly_chart(
                destruction_chart(result), width="stretch", theme=None
            )
            largest = max(
                result.destruction_rates, key=result.destruction_rates.get
            )
            total = sum(result.destruction_rates.values())
            if total > 1e-8:
                st.info(
                    f"主要不可逆来源：{COMPONENTS[largest]}，"
                    f"占总㶲损 "
                    f"{result.destruction_rates[largest] / total:.1%}。"
                )
            if (
                config.environment.cold_temperature
                < config.environment.temperature
            ):
                st.caption(
                    "冷源低于环境温度：排热㶲为负，桑基图按冷源提供㶲的方向绘制；当前㶲效率仍按后端定义，以热输入㶲为分母。"
                )
    if tab3.open:
        with tab3:
            sweep_panel(config, topology)
    exports(config, result, topology)


if __name__ == "__main__":
    main()
