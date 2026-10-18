# rig_exp

腕部实验程序，分为 TMSi 肌电采集和串口实验控制两个独立应用。两个应用可使用同一个 Python 3.10 环境，在不同进程中分别启动；`serial_manager` 当前不读取或处理 EMG。

## 逻辑架构

```text
rig_exp/
├─ requirements.txt       两个应用共用的 Python 依赖
├─ tmsi-python-interface-main/ 随附的 TMSi Python SDK、文档及示例
├─ tmsi_manager/          SAGA 连接、肌电录制、LSL 及肌电辅助工具
│  ├─ gui/               TMSi 操作界面与单通道实时监视
│  └─ workers/           真实及模拟 SAGA 的连接、推流、录制
└─ serial_manager/        电机、力传感器、实验流程及同步控制
   ├─ gui/               操作者界面、受试者显示和会话日志模板
   ├─ hardware/          电机、Nano17、trigger、Vicon 通信
   ├─ motions/           实验模式与约 100 Hz 控制循环
   ├─ recording/         原始 CSV、自由 CSV 和 trigger 时序记录
   ├─ high_freq_onboard/ 电机固件
   └─ trigger_timed/     运行时 trigger 固件
```

- `tmsi_manager` 经本地 TMSi SDK 控制最多两台 SAGA，独立录制 Poly5，并可开启 LSL 推流与实时监视。
- `serial_manager` 通过串口读取电机和力传感器、执行实验模式、更新受试者显示，并记录数据和发送 Vicon 控制命令。实验同步依靠外部 trigger 脉冲。
- 两个应用分别管理设备和文件，通过硬件同步关联实验，不由串口主 GUI 调用肌电采集功能。

## 可以运行的 GUI

以下命令均从 `rig_exp` 根目录执行，每个 GUI 单独启动。

| 子目录 | 启动命令 | 界面用途 |
| --- | --- | --- |
| `tmsi_manager` | `python .\tmsi_manager\run.py` | TMSi 主 GUI：SAGA 连接、LSL 推流、录制及单通道实时监视 |
| `tmsi_manager` | `python .\tmsi_manager\find_channels.py` | 肌电通道筛选 GUI：分析 SAGA1/SAGA2 的 LSL 数据，显示通道 RMS、信噪比及热图；需先开启 LSL 流 |
| `tmsi_manager` | `python .\tmsi_manager\review_poly5.py` | 离线 Poly5 查看窗口：选择已有文件，查看通道信息及触发信号图 |
| `serial_manager` | `python .\serial_manager\run.py` | 主实验 GUI，并自动打开受试者显示窗口 |
| `serial_manager` | `python .\serial_manager\run_UDP_GUI.py` | 独立电机／力传感器 UDP GUI：电机位置、Active/Passive、Zero Wrist 与六通道 LC 转发 |

串口主 GUI 保留七个页面：Rest、MVC_LC、RAH_LC、Test Velocity、Direction reversal、Passive Movement、Voluntary Movement。MVC_LC 在设置时长内提示 `Use your max force`，不显示运行计时，结束后计算 **0～设置时长**内全部有效 LC 样本的平均值。

UDP GUI 需单独运行，输出顺序为 `[tx, ty, tz, fx, fy, fz]`，力矩单位 Nmm、力单位 N。它与串口主 GUI 共用电机／LC 串口，使用时避免同时连接相同端口。

## 环境与常用顺序

使用现有 Python 3.10 环境（例如 `exo`），在根目录安装共用依赖：

```powershell
conda activate exo
cd C:\Users\25168\Projects\NML\rig_experiment\rig_exp
python -m pip install -r requirements.txt
```

Tkinter 由 Python／Conda 的 Tk 提供。TMSi Python SDK 已放在根目录的 `tmsi-python-interface-main/` 中，由 `tmsi_manager/config.py` 根据自身位置加载，移动整个项目时无需修改 SDK 路径。真实 SAGA 的设备驱动及原生设备库 DLL 仍需按厂家方式安装到系统；复制 Python SDK 不会安装这些驱动。随附 SDK 保留原许可证、文档、示例和界面资源，不包含原有 Python 缓存；厂家其他示例／GUI 的额外依赖见 SDK 内的依赖说明。

常用顺序：先启动 TMSi GUI，连接 SAGA、开启 Stream、开始录制；再在另一个终端启动串口主 GUI，连接所需串口并运行实验。当前 SAGA 外部 trigger 的主动初始化位于 **Start Stream** 路径，需要同步脉冲时先执行此步。

两个主 GUI 均支持在启动命令末尾追加 `--mock`。TMSi mock 模式不产生真实 Poly5 或 LSL 流；串口 mock 模式模拟电机／LC，Vicon 行为仍由配置控制。串口、Vicon 地址等参数分别在两个目录的 `config.py` 中设置。

## 辅助脚本与输出

- `tmsi_manager/mock_lsl_sagas.py`：命令行生成 SAGA1/SAGA2 模拟 LSL 流，可用于通道筛选工具测试。
- `serial_manager/test_vicon.py`：命令行 Vicon UDP 测试；使用 `--help` 查看参数。
- `serial_manager/udp_sender.py`：UDP 编码／发送模块，也提供命令行入口；使用 `--help` 查看参数。

运行后按需生成 `tmsi_manager/recordings/`、`serial_manager/recordings/` 和 `serial_manager/GUI_logs/`。自动合并实验 CSV 默认关闭，原始及 trigger 时序记录、独立 Start CSV/Stop CSV 仍保留。`serial_manager/gui/run_sheet_template.xlsx` 是会话日志模板，需要保留。
