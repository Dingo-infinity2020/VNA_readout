# VNA_readout

一个简单的 **Agilent / Keysight E5071C 离线 S21 测量工具**，用于 KID 原型的降温监测、暗态扫频，以及无源滤波器验证。

**Python 3.10 及以上，无第三方依赖。** 电脑通过网线连接 VNA；采集不需要互联网、AI 或云服务器。GitHub 只存程序和说明，实验数据默认留在本机。

## 当前状态

v0.2.2：已增加中文菜单、离线 HTML 报告、文件校验、U 盘导入，以及已经完成 D 盘自主采集短测的 VNA 内部宏。原有功能包括只读检查、单次扫频、定时重复采集、CSV/JSON 保存及 SVG 曲线预览。兼容固件完成回复 `1` 和 `+1`。

- 离线协议/保存测试通过；演示数据明确标记为 SIMULATED。
- 2026-11-10：真实 E5071C 的身份与当前设置只读查询通过。
- 2026-11-10：DC–1575 MHz 无源 LPF 的真实单次采集通过（1–4 GHz、1601 点、-30 dBm、IFBW 1 kHz），保存 CSV/JSON/SVG，正常结束后 RF OFF 读回确认。
- 同日：每 5 秒一次、共 3 次的短 monitor 通过，3 组文件校验一致，结束后 RF OFF 确认。这是短时间功能验证，不是长时间稳定性证明。详见[实机验证记录](docs/实机验证记录.md)。
- **校准后的采集和 Ctrl+C/断网时的实际行为仍待实测。** 此处无源未校准结果不等于 KID 或校准精度验收通过。
- 暂不包含自动找峰、Q 拟合、温控器连接或噪声 PSD；先把采集和保存做好。

## 推荐使用方式：VNA 自主采集

**当前先用未校准模式，不要求先做校准。** 为满足 Mac 断联后仍能工作，主路径改为：

> VNA 内部 VBA 宏 → U 盘或 VNA 用户数据盘 → 实验后由 Mac 导入并分析。

- [U 盘/内部数据盘独立采集说明](docs/U盘独立采集.md)
- [可直接加载的原生项目 KIDRecorder_v022.vba](native/KIDRecorder_v022.vba) 与 [源代码 Module1.bas](native/Module1.bas)
- [D 盘自主采集验收记录](docs/D盘自主采集验收.md)
- 仪器内部宏已运行并在控制 TCP 连接关闭后完成 3 次 D 盘自主扫频；结束 RF OFF、数据校验通过。物理拔网线、可移动介质和长时间运行尚未验证。
- 只有把采集循环运行在 VNA 内部，Mac 断联才不影响后续调度。下面的 Python 网络采集始终依赖电脑运行。

### 默认保存目录

VNA 宏默认使用 `D:\KID_DATA`。目录框已预填该路径，直接回车即可；清空或只输入空格后确认也使用默认目录。Cancel 退出。默认目录不存在会自动创建，新的每次实验仍进入独立 RUN 子目录；自定义目录必须已经存在。

2026-11-11 已在仪器上加载新版并完成不触发测量的目录自检，保持原来的 RF/连续扫频状态。仪器项目文件为 `D:\KID_DATA\KIDRecorder_v022_ready.vba`。

## 中文菜单与离线分析

Mac 可双击 `启动.command`，Windows 可运行 `start.bat`，也可以：

```bash
python3 vna.py
```

菜单包含连接检查、单次采集、降温/暗测试、离线演示、报告及 U 盘导入。只有明确选择采集并输入 START 才开启 RF；演示、报告和导入不连接仪器。本地 IP 记在被 Git 忽略的 connection.local.json 中。

```bash
python3 vna.py demo --kind kid
python3 vna.py report data/某次实验目录
python3 vna.py verify data/某次实验目录
python3 vna.py import-usb /Volumes/MY_USB/KID_DATA/RUN_日期_时间 --sample sampleA
```

每次正常完成后生成 report.html，可离线查看幅度、包裹相位、复平面叠图及逐次摘要 summary.csv。报告检查提交记录、数据形状及 SHA256，排除未提交文件；原始 CSV 不改动。摘要中的最小值频率只是采样点最小值，不是自动谐振拟合。

降温与暗测试的未校准配置模板在 configs/cooldown.example.json、configs/dark.example.json，需填实际频段和核算过的源功率；模板中空功率不能直接执行。

## Mac 网络采集：三步开始

安装 Python 后，下载本仓库 ZIP 并解压，或：

```bash
git clone https://github.com/Dingo-infinity2020/VNA_readout.git
cd VNA_readout
python3 vna.py demo
```

`demo` 不连接仪器；打开终端打印目录里的 SVG，可看到一条模拟滤波器曲线。Windows 使用 `py -3` 代替 `python3`。

确认 VNA 的当前 IP，先只读检查（把示例地址换成实际地址）：

```bash
python3 vna.py check --host 192.168.50.2
```

**接无源滤波器，断开 KID/低温链路，先核对功率额定值**。默认滤波器配置为 1–4 GHz、1601 点、IFBW 1 kHz、-30 dBm，关闭校正并明确标记未校准。确认这些设置适合器件后：

```bash
python3 vna.py capture --host 192.168.50.2 --profile configs/filter.json --allow-rf
```

一次采集完成后程序尝试关闭 RF 并读回确认；此前频率、轨迹、功率等设置不会自动恢复。不要同时用其他程序操作仪器。

## 重复测量

下面计划每 60 秒一次，共 60 次，约一小时；每次都保存完整曲线。

```bash
python3 vna.py monitor --host 192.168.50.2 --profile configs/filter.json --interval 60 --count 60 --allow-rf
```

- 扫频较慢时自动延长周期至整轮耗时的至少 1.2 倍，不堆积补扫。
- Ctrl+C 停止，保留已保存文件，尝试关闭 RF。
- 断连/超时即停止，不自动重新触发；出现 `RF OFF NOT CONFIRMED` 时请在前面板确认输出状态。
- 电脑必须接电并避免系统睡眠。Mac 可用 `caffeinate -i python3 vna.py monitor ...` 包装完整命令。

## 数据在哪里

每次运行创建 `data/日期_时间_随机编号/`，不覆盖旧实验：

| 文件 | 内容 |
|---|---|
| `run.json` | 仪器身份、开始前设置、配置、样品与说明 |
| `sweep_000001.csv` | 频率 Hz、S21 实部/虚部、幅度 dB、相位度 |
| `sweep_000001.json` | 起止时间、实际设置、校准标记、数据 SHA256 |
| `sweep_000001.svg` | 可用浏览器打开的幅度预览 |
| `events.jsonl` | 每次触发请求、提交、停止原因及 RF 关闭确认 |

正常结束后还有 report.html 和 summary.csv。

CSV 是主数据，JSON 是配套记录；以 `events.jsonl` 的 `committed` 为完整扫频标志。只有 CSV、没有匹配 JSON/提交记录的文件不能当作完整数据。`.tmp` 是未完成文件，不用于分析。预览失败不会覆盖已经提交的数据。

默认不丢弃任何完整扫频；数据、真实 IP、仪器序列号和本地配置不应提交到公共仓库。`data/` 和 `*.local.json` 已忽略。每小时或实验结束后手动复制新增数据到第二介质，保留整套 CSV、JSON 和日志。

**完整中文操作说明：[docs/操作说明.md](docs/操作说明.md)**，包含接线、手动校准、降温/暗测试节奏、故障处理。

## 开发与验证

```bash
python3 -m unittest discover -s tests -v
```

GitHub Actions 运行离线测试，不连接真实仪器。

## 依据

- [E5071C LAN 控制与 TCP 5025](https://helpfiles.keysight.com/csg/e5071c/programming/remote_control/overview/lan_remote_control_system.htm)
- [触发完成等待](https://helpfiles.keysight.com/csg/e5071c/programming/remote_control/making_measurement/waiting_for_the_end_of_measurement.htm)
- [复数数据与实际频率轴](https://helpfiles.keysight.com/csg/e5071c/programming/remote_control/reading-writing_measurement_data/internal_data_processing.htm)
- [Hold 状态的源端口控制](https://helpfiles.keysight.com/csg/e5071c/setting_control_functions/initial_source_port_control_function.htm)

软件查询的是 VNA 的复数 S 参数，不是原始 ADC 数据；校正开启时数据含相应处理。常规扫频不能代替高速脉冲或经时序验证的连续噪声读出。
