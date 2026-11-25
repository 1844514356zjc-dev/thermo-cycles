"""Print two reproducible SI cycle summaries after editable installation."""

from core.cycle_solver import (
    BraytonConfig,
    RankineConfig,
    solve_brayton,
    solve_rankine,
)


def main() -> None:
    """Solve default cycles and print efficiencies and power [W]."""
    for result in (
        solve_brayton(BraytonConfig()),
        solve_rankine(RankineConfig()),
    ):
        print(f"Thermal efficiency: {result.thermal_efficiency:.6f}")
        print(f"Exergy efficiency: {result.exergy_efficiency:.6f}")
        print(f"Net power [W]: {result.power:.3f}")
        print(f"Destruction [J/kg]: {result.exergy_destruction}")
        print(f"First-law residual [J/kg]: {result.energy_residual:.3e}")
        print(f"Exergy residual [J/kg]: {result.exergy_residual:.3e}")


if __name__ == "__main__":
    main()
