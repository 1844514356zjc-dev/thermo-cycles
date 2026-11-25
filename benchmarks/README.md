# 工程验证

从项目根目录运行：

```bash
python -m pip install -e '.[dev,benchmark]'
pytest -q
python benchmarks/validate_literature.py
```

脚本也可从其他工作目录直接以绝对路径运行。标准输出为 Markdown 表格，失败返回退出码 1，依赖缺失和物性错误不静默忽略。可重定向到报告文件。

## 三组输入及来源

| 算例 | 输入（SI） | 参考 |
|---|---|---|
| A 简单理想布雷顿 | Air，p1=100000 Pa，T1=300 K，压比4，T4=900 K，机器效率1，无回热/压损 | 恒定 cp 空气标准解析公式 |
| B 回热理想布雷顿 | 同 A，回热度0.75 | 等流量恒定 cp 逆流能量平衡 |
| C 理想朗肯 | Water，冷凝压力10000 Pa，锅炉压力8e6 Pa，涡轮入口773.15 K，饱和液泵入口，机器效率1，无压损 | 独立 Python iapws 包实现的 IAPWS-IF97 |

这些是按经典模型自行构造的可复现算例，不冒充某本教材的编号例题，也不是 NIST 发布的循环测试数据。

参考资料：

- [Northwestern University: Improving an Air-Standard Power Cycle](https://qrg.northwestern.edu/thermo/design-library/airstd/brayton.html)：理想机器、等压换热、回热循环的教学框架。
- [IAPWS R7-97(2012)](https://iapws.org/technical-guidance/release/IF97-Rev)：水与水蒸气工业物性标准。
- [iapws 实现文档](https://iapws.readthedocs.io/en/latest/iapws.iapws97.html)：独立参考实现；MPa、kJ 单位在适配层转换成 SI。

## 如何解读误差

`Relative Error % = 100*abs(solver-reference)/abs(reference)`。参考值为零时拒绝相对误差计算，应使用绝对误差；pytest 对零功退化工况采用绝对误差。

A/B 的参考为 cp=1004.5 J/(kg K)、gamma=1.4，而生产求解器使用真实 Air。这里的偏差是模型一致性检查；10% 仅为示例工程筛查阈值，不是数值求解精度或普遍物性误差上界。确切的代数数值精度由 pytest 中临时替换的恒定 cp 理想气体物性适配器验证，生产实现仍保持 CoolProp。

C 不复用求解器的状态函数，也不调用 CoolProp 计算参考状态；以独立 iapws 实现构建循环。HEOS 的水物性基于不同于 IF97 的状态方程，因此允许0.15%的筛查阈值。这是跨物性公式验证，并非同一个方程实现的机器精度比较。

实际运行版本 CoolProp 7.2.0、iapws 1.5.5 的结果见 [报告](validation_report.md)。脚本会输出当前环境的版本，以实际重跑结果为准。

## 测试范围和限制

`tests/test_thermodynamics.py` 验证压比1及邻近值、理想机器、零回热、理想气体解析极限、朗肯能量与第二定律、非法温度/工质/效率/压损、质量流量缩放、路径分辨率端点与饱和包络。

能量/㶲闭合误差阈值为绝对值1e-6 J/kg，不能单独用来证明物性的准确性。独立 IF97 对照补充了外部参考，但仍需文献中完整记录边界条件的实验数据、近临界夹点收敛研究，以及跨 Python/CoolProp 版本测试矩阵，才能进一步说明工程适用范围。
