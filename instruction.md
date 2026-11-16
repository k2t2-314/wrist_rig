# Windows Setup and Running Instructions

These instructions use Python `venv`. Both applications share one environment and run in separate terminals.

## 1. Install Python and VS Code

Install **Python 3.10, 64-bit**. The current project dependencies are configured for Python 3.10.

An official Windows installer is available on the [Python 3.10.11 download page](https://www.python.org/downloads/release/python-31011/). Select **Windows installer (64-bit)**.

During installation:

- Enable **Add python.exe to PATH**.
- Keep **pip** and **Tcl/Tk and IDLE** selected.
- Select **Disable path length limit** if offered.

Install VS Code and the **Python extension by Microsoft**.

## 2. Open the Project

Extract the downloaded project and open its root folder in VS Code.

The root folder should contain:

```text
wrist_rig-main/
├─ requirements.txt
├─ serial_manager/
├─ tmsi_manager/
└─ tmsi-python-interface-main/
```

The commands below assume this location:

```text
D:\Projects\wrist_rig-main
```

Replace this path with your actual project location.

## 3. Create the Virtual Environment

Open a **PowerShell terminal** in VS Code and run:

```powershell
cd "D:\Projects\wrist_rig-main"

py -3.10 --version
py -3.10 -m venv .venv

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Run these setup commands once on each new computer. Internet access is required to download dependencies.

All commands below call the virtual environment's Python directly, so environment activation is optional.

In VS Code:

1. Press `Ctrl+Shift+P`.
2. Run **Python: Select Interpreter**.
3. Select the project's `.venv\Scripts\python.exe`.

## 4. Install Hardware Drivers

To connect real SAGA devices, install **TMSi SAGA Device Driver 2.0.0.0** according to the [TMSi installation instructions](https://artinis-medical-systems-b-v.github.io/TMSi-Documentation-Python-Interface/getting_started/installation.html).

The Python SDK is already bundled with the project. The SAGA driver must be installed separately.

The current SDK expects this native library:

```text
C:\Program Files\TMSi\Saga\TMSiSagaDeviceLib.dll
```

Check whether the file exists:

```powershell
Test-Path "C:\Program Files\TMSi\Saga\TMSiSagaDeviceLib.dll"
```

For the serial devices, check their COM ports in Windows Device Manager. Update the port settings in `serial_manager/config.py` if needed.

If using Vicon, also check its destination IP address and other Vicon settings in that file.

## 5. Start the TMSi GUI

In the first terminal:

```powershell
cd "D:\Projects\wrist_rig-main"
.\.venv\Scripts\python.exe .\tmsi_manager\run.py
```

Connect the SAGAs, click **Start Stream**, and then **Start Recording**.

Start Stream must be performed before recording when external trigger synchronization is required.

## 6. Start the Serial Experiment GUI

Open a second terminal:

```powershell
cd "D:\Projects\wrist_rig-main"
.\.venv\Scripts\python.exe .\serial_manager\run.py
```

This opens the operator GUI and the subject display.

Connect the required serial devices, configure the experiment, and start it after TMSi recording has begun.

## 7. Run the Standalone UDP GUI

From the project root:

```powershell
.\.venv\Scripts\python.exe .\serial_manager\run_UDP_GUI.py
```

The UDP GUI uses the same motor and load-cell ports as the main serial GUI. Disconnect those ports in one application before connecting them in the other.

## 8. Open the GUIs in Mock Mode

Use these commands in separate terminals to open the main GUIs without physical devices.

TMSi GUI:

```powershell
cd "D:\Projects\wrist_rig-main"
.\.venv\Scripts\python.exe .\tmsi_manager\run.py --mock
```

Serial GUI:

```powershell
cd "D:\Projects\wrist_rig-main"
.\.venv\Scripts\python.exe .\serial_manager\run.py --mock
```

TMSi mock mode does not produce real EMG recordings. Vicon behavior in serial mock mode remains controlled by `serial_manager/config.py`.

## 9. Subsequent Runs

After the initial setup, run the appropriate GUI commands directly.

You do not need to recreate `.venv` or reinstall dependencies each time.
