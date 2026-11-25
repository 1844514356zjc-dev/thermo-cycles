# Contributing

Contributions that improve physical traceability, numerical verification, and
teaching clarity are welcome. Use this directory as the repository root so
GitHub can discover `.github/workflows/ci.yml`.

## Development environment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -c requirements/validation-constraints.txt -e '.[dev,ui,benchmark]'
python -m flake8 src tests benchmarks examples
python -m ruff check src tests benchmarks examples
python -m pytest -q
python benchmarks/validate_literature.py
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell.

## Physical and numerical changes

- Keep physical inputs and outputs in SI. Mark scaled plotting units explicitly.
- Give functions complete type hints and Google-style docstrings; state the
  equations, sign conventions, units, and assumptions behind new calculations.
- Include a minimal reproducible configuration for bug reports. Report Python,
  CoolProp, NumPy, and SciPy versions and the complete `CycleError` diagnostic.
- Separate algebraic errors, numerical convergence errors, equation-of-state
  differences, and experimental uncertainty when proposing validation results.
- Test limiting cases and relevant conservation laws. Use independent reference
  implementations or traceable source values for claims about accuracy.
- Do not adjust acceptance tolerances simply to make a failing test pass.
- Update the README, benchmark provenance, and model limitations when scope or
  reference conditions change.

## Dependency updates

Flexible dependency ranges in `pyproject.toml` support library consumers.
The recommended developer/CI install constrains reviewed direct dependencies
using `requirements/validation-constraints.txt`; Python 3.10 uses SciPy 1.14.1
and NumPy 2.2.6, while newer Python versions use the reviewed reference pair.
The container additionally uses a Python 3.10 reference lock with exact runtime
versions and SHA-256 hashes, generated using `uv 0.13.8`:

```bash
python -m pip install 'uv==0.13.8'
uv pip compile pyproject.toml --extra ui \
  --constraint requirements/docker.in --python-version 3.10 \
  --universal --generate-hashes --output-file requirements/docker.txt
```

Review `requirements/docker.in` before changing direct pins. For deliberate
transitive updates, add `--upgrade` to the compilation command. Re-run all tests,
the reference comparison, and `docker compose up --build --wait` after updates.
The base image tracks the Python 3.10 Debian Bookworm maintenance tag; the image
is not claimed to be bit-for-bit reproducible across base-image updates.

Pull requests should explain the observed problem, the resulting behavior, the
physical assumptions, and the validation performed. No external account,
credentials, or proprietary property database is required for the test suite.
