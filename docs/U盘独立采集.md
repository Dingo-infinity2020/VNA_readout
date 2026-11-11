# 让 VNA 自己采集：U 盘或内部数据盘

目标：启动后由 **E5071C 内部 VBA 宏**触发和保存。拔掉电脑网线、关闭 Mac 不影响宏继续运行。这个能力来自仪器内部程序，而不是把 Mac 程序的保存路径换成 U 盘。

## 当前状态

代码：[`native/Module1.bas`](../native/Module1.bas)，适配 E5071C 内部 32 位 VBA，保存未校准的正向复数 S21。

**2026-11-10 更新：内部宏实际运行、D 盘写入及关闭控制 TCP 会话后的 3 次自主采集已通过。** [验收记录](D盘自主采集验收.md)；可直接加载的原生项目为 [`native/KIDRecorder_v022.vba`](../native/KIDRecorder_v022.vba)。

可移动介质仍未就绪：仪器内部存储探针显示 G 为可移动盘但未就绪，C/D/E/F 为固定盘。当前优先保存到 `D:\KID_DATA`；不向 G、系统或校准分区写入。物理拔网线、长时间运行和可移动介质写入仍待独立验证。

## 1. 准备保存目录

1. 插入 U 盘，在 VNA 的 **Save/Recall → Explorer** 查看实际盘符。必须能在 Explorer 中打开、建立普通测试文件夹。
2. 在确认的 U 盘里建立 `KID_DATA`，例如 `G:\KID_DATA`。**G 只是例子，不能据此认定现场盘符。** 不用格式化现有 U 盘来试错。
3. 如果 U 盘暂时不能识别，也可以在 VNA 已确认的内部用户数据盘建立目录，例如 `D:\KID_DATA`；先检查剩余空间，实验后手动复制。内部采集同样不依赖 Mac，不去动系统/恢复/校准目录。
4. 宏只往选择的既有目录下创建新的 `RUN_日期_时间` 子目录，不修改或删除原来的文件。运行前先保存仪器原有状态。

## 2. 加载宏（首次一次性操作）

**推荐：**仪器上已有 `D:\KID_DATA\KIDRecorder_v022_ready.vba`。直接从 Macro Setup → Load Project 加载，选择 Module1.Main；无需重新导入源文件。其他仪器可先复制仓库的 native/KIDRecorder_v022.vba 后加载。下面保留从源码导入的方法，供开发使用。


`.bas` 是可读 VBA 源文件，**不是可以在 Macro Setup → Load Project 直接运行的 `.vba` 项目**。需要在仪器编辑器中导入并保存一次。

1. 将仓库的 `native/Module1.bas` 传到仪器可访问位置；可以用一个已识别的 U 盘，或经后续确认的文件传输方式。不要覆盖已有宏。
2. 用 **Macro Setup** 打开 VBA 编辑器，创建一个新的独立项目，避免修改已有厂商宏。
3. 在编辑器 File → Import File 导入 `Module1.bas`。项目内模块名必须是 `Module1`，入口是 `Public Sub Main()`。如果新项目已有空白 Module1，可将源文件中 `Option Explicit` 起的内容粘贴进去；不要把 `Attribute VB_Name` 行当普通代码粘贴。
4. 用编辑器的编译功能检查。遇到不支持的 COM 成员或编译错误时停止，不忽略错误继续运行，将错误文本记录下来以适配实际固件。
5. 保存为新的 `KID_USB.vba`；放在 `D:\VBA` 后可从 Macro Setup → Load & Run 选择，或 Load Project 后选择 Module1 Main，再按 **Macro Run**。不覆盖任何已有项目。

## 3. 前面板设置（先用无源滤波器）

宏沿用人工设置，不执行 Preset、不改扫频范围或功率。当前原型只接收：

| 设置 | 要求 |
|---|---|
| 通道/轨迹 | Channel 1，恰好一条轨迹，S21 |
| 扫频 | Linear Freq，1–4 GHz 范围内 |
| 点数 | 2–1601，默认 1601 |
| IFBW | 10–10000 Hz；滤波器默认 1000 Hz，KID 精扫可从 100 Hz 起步 |
| 源功率 | -55 至 -20 dBm 范围内，滤波器试验可用 -30 dBm；KID 要另外核算功率 |
| 平均 | OFF |
| 校正 | OFF，文件明确标记 UNCALIBRATED，不要求校准 |
| 功率设置 | Port Couple ON、Slope OFF、Port 1 Power Correction OFF |

宏会核验这些条件。发现仪器错误或配置不满足时停止，不自动加功率或关闭提示继续运行。采集中不要人工切换通道/参数，也不要运行 Mac 采集程序。

低温链路有放大器/隔离器时，只做正向 S21，先确认衰减、增益、端口方向及芯片/接收端功率。未校准模式可以记录相对变化，但不等于绝对插损或内部 Q 已准确测出。

## 4. 启动和停止

1. 运行宏后，目录框默认是 `D:\KID_DATA`，直接回车即可。清空或只输入空格后确认也使用默认目录；Cancel 退出。默认目录缺失会自动创建，自定义目录仍需已经存在。然后输入间隔秒数和次数，第一次只做 3 次、间隔 5 秒。
2. 确认屏幕列出的实际频段、点数、IFBW 和源功率；点 Yes 后才由宏控制 RF 并开始采集。
3. 每扫等待测量完成，读取实际频率数组和 Re/Im 数组，写 `.tmp`，关闭文件后重命名为 CSV，再追加 manifest 提交记录。每一次完整扫频都保存，不依赖截图。
4. 降温默认每 60 秒一次，暗测试每 30 秒一次。扫频和保存较慢时留至少 20% 周期余量，不积压补扫。中间等待也在 VNA 内执行。
5. 普通停止：按住外接键盘 **Esc**，宏在本次扫频结束或等待阶段检查后退出；也可以在本次 RUN 目录里建立 `STOP.txt`，宏会在下次检查时停止。
6. 正常结束或普通错误时尝试关闭 RF，并记录 `RF_OFF_CONFIRMED`。**Macro Break/编辑器 Reset 是强制中断，可能绕过清理代码**；使用后必须人工确认 RF OFF。仪器断电、程序崩溃、拔 U 盘等也不属于可以保证数据完整的情况。

注意：屏幕测量曲线持续刷新不等于每条曲线都在存盘。只有宏循环和 manifest 记录能证明计划内的数据已保存。

## 5. 断开 Mac 的验收步骤

在无源滤波器上完成，先不接 KID：

1. 短试验：3 次、5 秒间隔，检查 3 个 CSV、manifest 行数和 RF 关闭状态。
2. 自主试验：设 6 次、10 秒间隔，见到第一个文件后断开 **Mac 到 VNA 的连接**，保持仪器和存储介质供电。
3. 在仪器上检查剩余 5 次是否继续生成，最后显示已保存 6 次并确认 RF OFF。
4. 正常退出并安全移除 U 盘，或把内部数据盘的整个 RUN 目录复制到 U 盘。
5. 在 Mac 上导入并校验全部曲线。全部通过后，才把该固件/介质/保存目录组合标记为“断网自主采集通过”。

代码里没有网络连接和自动上传；但真正“断网后正常工作”的结论仍以以上实测为准，不靠源码推断替代。

## 6. 数据带回 Mac

VNA 端目录包含：

```text
RUN_20261110_140000/
  run_info.txt
  manifest.csv
  status.log
  sweep_000001.csv
  sweep_000002.csv
  ...
```

CSV 仅存 `frequency_hz,s21_real,s21_imag`，幅度和相位可完全由这三列重建。manifest 记录每扫的实际频段、点数、IFBW、功率和起止时间；只接受完整 manifest 行引用的文件，孤立 CSV/`.tmp` 不参与自动导入。

接回 Mac 后，例如：

```bash
python3 vna.py import-usb /Volumes/MY_USB/KID_DATA/RUN_20261110_140000 --sample sampleA_dark
```

导入只读 U 盘，转换后保存在本机新的 `data/` 子目录，自动生成复数 CSV、JSON、校验值、摘要和离线 HTML 报告。VNA 原始文件不删除、不重命名。导入时计算的 SHA256 仅用于之后的完整性校验，不能证明介质上的数据在导入前未被改过。

**宏记录的是 VNA 本地时钟**，不是已经验证同步的 UTC。报告明确标记 `VNA_LOCAL_UNVERIFIED`；实验前核对仪器时间并记录时区，温度日志也保留原始时标。Mac 导入时不自动猜时区或替换为导入时间。

## 7. 能保证什么

- Mac 断联不影响运行，是内部宏架构的目标，需按断网试验验证。
- U 盘掉线/满盘：停止并尝试 RF OFF，不自动改存别的盘，不静默丢弃曲线。
- 每扫 Close 后再重命名，尽量缩小损坏范围；Windows 和 U 盘仍有缓存，**不保证突然断电时最后几次写入一定落到物理介质**。
- 不自动开机恢复、不自动重试失败的触发、不自动断点续扫。

## 官方文档

- [内部宏能力](https://helpfiles.keysight.com/csg/e5071c/programming/vba_programming/introduction_to_vba_programming/introduction_of_the_e5070b_e5071b_macro_function.htm)
- [运行 VBA 项目](https://helpfiles.keysight.com/csg/e5071c/programming/vba_programming/operation_basics/running_a_vba_program.htm)
- [测量完成等待](https://helpfiles.keysight.com/csg/e5071c/programming/command_reference/trigger/scpi_trigger_sequence_single.htm)
- [频率与复数数据读取](https://helpfiles.keysight.com/csg/e5071c/programming/vba_programming/controlling_e5071c/reading_writing_measurement_data.htm)
- [强制停止宏的行为](https://helpfiles.keysight.com/csg/e5071c/programming/vba_programming/operation_basics/stopping_a_vba_program.htm)

## v0.2.2 默认目录更新（2026-11-11）

新版已在仪器内部通过空白/空格、自定义路径、取消分支及目录创建的逻辑自检，没有调用 RF 或触发命令。更新前后均为 RF ON、Internal 触发、连续扫频 ON。这个自检覆盖目录逻辑和 VBA 运行兼容性，不等于人工点击每种输入框按钮的完整 UI 验收。

可加载项目 SHA256：`acd33db39917217b3dcf8113dd1adf6e46900366d080dbe2568f1f6163e29c41`；文件 48,128 字节。历史 v0.2.1 项目保留供回退，新版主入口仍为 Module1.Main。
