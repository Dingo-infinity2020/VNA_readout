# D 盘自主采集验收：2026-11-10

结论：**E5071C 内部 VBA 运行、内部 D 盘保存、关闭 Mac 控制 TCP 会话后的 3 次自主扫频已通过。** 当前不要求校准。

物理拔网线、拔 U 盘、断电和长时间连续运行没有在本次试验中验证；不据此声称这些情形均已通过。

## 已完成

1. 只读核对型号、固件 A.11.02、当前配置、宏状态和 RF 状态。
2. 在 D 盘新建 `D:\KID_DATA` 和独立部署子目录，备份当时的空白宏项目。没有修改 C/E/F，也没有删除已有文件。
3. 上传 `KIDStorageProbe.bas` 并逐字节读回校验。探针在仪器内部运行成功，写出测试文本。
4. Windows FSO 返回 C/D/E/F 均为固定盘；G 为可移动盘但 `IsReady=False`。这比只读目录更明确，但仍不能证明 G 的物理介质一定就是本次插入的那只 U 盘。没有格式化或向 G 写入。
5. 上传 `Module1.bas`，逐字节读回校验，导入 VBA。`ValidateOnly` 在仪器上返回：

```text
VALIDATION_PASS 1601,1000000000,4000000000,1000,-30,UNCALIBRATED
```

6. 仅启动一次 `Module1.Acceptance3`，随即关闭 TCP 控制连接。之后 18 秒没有查询或触发；期间由内部宏独立完成测量与保存。
7. 重新连接：宏 STOP、RF OFF、仪器错误队列为空。D 盘生成一个 RUN 目录，包含 3 个完整 CSV、manifest、run_info 和 status 日志。
8. 数据读回本机后导入为 VNA_NATIVE、storage_origin=internal_d，3 组均为 1601 点，校验通过，生成离线报告。实验数据未上传公共仓库。

## 仪器内部日志摘要

以下是仪器本地时钟，**不是已同步的 UTC**：

```text
15:48:31 TRIGGER_REQUEST 1
15:48:33 COMMITTED sweep_000001.csv
15:48:36 TRIGGER_REQUEST 2
15:48:38 COMMITTED sweep_000002.csv
15:48:41 TRIGGER_REQUEST 3
15:48:43 COMMITTED sweep_000003.csv
15:48:43 RF_OFF_CONFIRMED
15:48:43 FINISHED 3
```

控制会话关闭的 Mac UTC 时间记录为 07:36:12，显示两端时钟有明显偏差（按启动对照约 12 分 18 秒，非精密校时）。本次未修改仪器时钟。用于降温曲线关联时，需先核对时区和时钟，或单独记录偏差；导入器不猜测、不改写原始时标。

## 可直接加载的原生项目

仓库：[`native/KIDRecorder_v021.vba`](../native/KIDRecorder_v021.vba)

- 这是在该仪器上保存并读回的 VBA 项目文件，可直接 Load Project；不再需要第一次手动导入 `.bas`。
- 文件大小：56,320 字节。
- SHA256：`bcc4c285b551ac2e30c82cfd85d60c412c3e1e8b752f5250fe2b8496a605a2bd`。
- 普通操作入口：`Module1.Main`；已选回这个入口，当前没有在运行。
- `Acceptance3` 是限定的无源滤波器验收入口，固定 3 次、5 秒间隔；不能把它当作 KID 的正式测量配置。
- `ValidateOnly` 和 `KIDStorageProbe.Main` 不触发测量，用于配置与存储检查。

仪器已保存：`D:\KID_DATA\KIDRecorder_v021_ready.vba`。启动时从 Macro Setup → Load Project 选择它，再运行 Module1.Main。输入已有目录 `D:\KID_DATA`、间隔和次数，检查实际参数后确认开始。

## 存储估算

本次 1601 点复数 CSV 每扫约 83.5–83.6 kB。按这个实测大小：每 60 秒一次约 0.12 GB/天，每 30 秒一次约 0.24 GB/天，另加少量日志。D 盘当前约 10.2 GB 空闲；空间只是估算，不代表长时间可靠性已经验证。建议每天新建实验段、定期导出并保留至少 1 GB 余量。

## 仍待完成

- 物理拔网线后继续采集的独立试验。
- 普通 Main 输入界面、普通停止、强制 Macro Break 等现场操作验收。
- 可移动介质识别与写入验证。
- 低温链路功率预算和真实 KID 测量。

本次证明了 VNA 可以在没有 Mac 控制会话的情况下自行定时触发并保存到 D 盘；未扩大到断电恢复、自动重试或科学测量精度保证。
