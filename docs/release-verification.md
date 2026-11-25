# Release preparation verification

Recorded on **2026-11-15**, on macOS ARM64. This is a local verification record,
not a claim that GitHub-hosted jobs or Docker execution have already passed.

| Environment | Physical/UI reference stack | Checks observed |
|---|---|---|
| CPython 3.10.22, clean virtual environment | CoolProp 7.2.0, NumPy 2.2.6, SciPy 1.14.1, Streamlit 1.56.0, Plotly 5.18.0, iapws 1.5.5 | 44 pytest tests passed; Flake8 passed; reference script passed; dependency compatibility check passed |
| CPython 3.12.16, clean virtual environment | Hash-locked runtime dependencies from requirements/docker.txt, plus development/reference extras | 44 pytest tests passed; Flake8 passed; reference script passed; dependency compatibility check passed |
| CPython 3.14.0, existing development environment | CoolProp 7.2.0, NumPy 2.3.5, SciPy 1.16.3, Streamlit 1.56.0, Plotly 5.18.0 | 44 pytest tests passed; Flake8 and Ruff passed |

Additional checks:

- Compose and workflow YAML parsed successfully; expected trigger, service,
  port and matrix settings were checked.
- Project TOML parsed successfully.
- Every benchmark table row included in the README exactly matches the existing
  benchmark report; the independent Rankine results are explicitly separated
  from constant-cp air model differences.
- All 38 locked Linux x86-64 Python 3.12 runtime packages were resolved and
  unpacked from wheels, with target-platform markers and artifact hashes
  checked. This verifies availability and resolution; it does **not** execute
  Linux binaries on macOS or replace a container startup test.

## Pending execution checks

No Docker command/Engine is available in this local execution environment.
Consequently the Dockerfile and Compose service have not been built or started
here. The committed GitHub Actions container job builds the image, waits for
health, checks the HTTP endpoint, and executes both cycle solvers when the
workflow runs on a Docker-capable Ubuntu runner.

GitHub Actions has been configured, not dispatched or observed remotely.
Publishing this project directory as the repository root is required for GitHub
to discover `.github/workflows/ci.yml`.

## Re-running the checks

Install the reviewed local dependencies and run:

```bash
python -m pip install -c requirements/validation-constraints.txt -e '.[dev,ui,benchmark]'
python -m flake8 src tests benchmarks examples --count --statistics
python -m pytest -q
python benchmarks/validate_literature.py
```

On a Docker-capable machine:

```bash
docker compose config --quiet
docker compose up --build --detach --wait --wait-timeout 180
curl --fail http://127.0.0.1:8501/_stcore/health
docker compose down
```

The Compose port is fixed at local port 8501; stop an existing local Streamlit
instance before the container check. Docker on other CPU architectures still
requires a build/start verification on that architecture.
