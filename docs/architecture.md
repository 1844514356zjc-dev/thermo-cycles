# 项目架构

```text
thermo-cycles/
├── pyproject.toml             # 依赖、打包与质量检查
├── README.md
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   └── cycle_solver.py    # 无 UI 依赖的稳态物理内核
│   └── ui/
│       └── __init__.py        # 后续 Streamlit/PySide 展示适配层
├── tests/
│   └── test_cycle_solver.py   # 守恒、效率、两相区、工质、非法边界
├── docs/
│   ├── architecture.md
│   └── physics.md
├── benchmarks/
│   └── README.md
└── examples/
    └── solve_cycles.py
```

数据流：配置 dataclass → 输入验证 → CoolProp 状态闪蒸 → 部件求解 → 能量/㶲核算 → 结构化结果 → UI/导出。
核心模块独立于图形界面，UI 不应重复计算物理量。Curve 内的数组可直接用于 T-s、h-s 绘图。

扩展阶段可将物性适配层、机器/换热器部件和循环拓扑拆成独立模块，保留当前入口以兼容调用者。
现已补充 MIT LICENSE、CONTRIBUTING.md、GitHub Actions CI、Docker 运行依赖锁定与 Python 3.10/3.12/3.14 测试矩阵。物理验证包含独立 IF97 参考对照；实验对标仍待完成。发布检查记录见 release-verification.md。
当前包名 core 按任务指定目录组织；正式发布到 PyPI 时建议迁移为独有的顶层包名，避免命名冲突。
