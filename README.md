# rig_exp

Wrist rig experiment software with two independent applications: TMSi EMG acquisition and serial experiment control. Both applications can use the same Python 3.10 environment and run as separate processes; `serial_manager` currently does not read or process EMG.

## Architecture

```text
rig_exp/
├─ requirements.txt           Shared Python dependencies for both applications
├─ tmsi-python-interface-main/ Bundled TMSi Python SDK, documentation, and examples
├─ tmsi_manager/              SAGA connections, EMG recording, LSL, and EMG tools
│  ├─ gui/                   TMSi interface and single-channel live monitoring
│  └─ workers/               Connections, streaming, and recording for real and mock SAGAs
└─ serial_manager/            Motor, load cell, experiment flow, and synchronization
   ├─ gui/                   Operator interface, subject display, and session log template
   ├─ hardware/              Motor, Nano17, trigger, and Vicon communication
   ├─ motions/               Experiment modes and an approximately 100 Hz control loop
   ├─ recording/             Raw CSV, free CSV, and trigger timing records
   ├─ high_freq_onboard/      Motor firmware
   └─ trigger_timed/          Runtime trigger firmware
```

- `tmsi_manager` controls up to two SAGAs through the local TMSi SDK, records Poly5 independently, and can enable LSL streaming and live monitoring.
- `serial_manager` reads motor and load cell data over serial connections, executes experiment modes, updates the subject display, records data, and sends Vicon control commands. Experiments are synchronized through external trigger pulses.
- Each application manages its own devices and files. Hardware synchronization links the experiment recordings; the serial application's main GUI does not invoke EMG acquisition.

## Available GUIs

Run all commands below from the `rig_exp` root directory. Launch each GUI separately.

| Subdirectory | Launch command | Purpose |
| --- | --- | --- |
| `tmsi_manager` | `python .\tmsi_manager\run.py` | Main TMSi GUI: SAGA connections, LSL streaming, recording, and single-channel live monitoring |
| `tmsi_manager` | `python .\tmsi_manager\find_channels.py` | EMG channel selection GUI: analyzes SAGA1/SAGA2 LSL data and displays channel RMS, signal-to-noise ratios, and heatmaps; LSL streams must be enabled first |
| `tmsi_manager` | `python .\tmsi_manager\review_poly5.py` | Offline Poly5 viewer: select an existing file to view channel information and trigger signal plots |
| `serial_manager` | `python .\serial_manager\run.py` | Main experiment GUI; automatically opens the subject display window |
| `serial_manager` | `python .\serial_manager\run_UDP_GUI.py` | Standalone motor/load cell UDP GUI: motor position, Active/Passive, Zero Wrist, and six-channel LC forwarding |

The serial application's main GUI retains seven tabs: Rest, MVC_LC, RAH_LC, Test Velocity, Direction reversal, Passive Movement, and Voluntary Movement. During the configured duration, MVC_LC displays `Use your max force` without an elapsed-time display. At the end, it calculates the mean of all valid LC samples from **0 to the configured duration**.

Run the UDP GUI separately. Its output order is `[tx, ty, tz, fx, fy, fz]`, with torque in Nmm and force in N. It uses the same motor/LC serial ports as the main serial GUI; avoid connecting both GUIs to the same ports simultaneously.

## Environment and typical workflow

Use an existing Python 3.10 environment (such as `exo`) and install the shared dependencies from the root directory:

```powershell
conda activate exo
cd C:\Users\25168\Projects\NML\rig_experiment\rig_exp
python -m pip install -r requirements.txt
```

Tkinter is provided by the Python/Conda Tk installation. The TMSi Python SDK is bundled in `tmsi-python-interface-main/` at the project root. `tmsi_manager/config.py` loads it relative to its own location, so moving the entire project does not require changing the SDK path. Device drivers and native device library DLLs for real SAGAs must still be installed on the system according to the manufacturer's instructions; copying the Python SDK does not install these drivers. The bundled SDK retains its original license, documentation, examples, and interface resources, without the original Python caches. Refer to the dependency instructions inside the SDK for additional requirements of other manufacturer examples and GUIs.

Typical workflow: first launch the TMSi GUI, connect the SAGAs, start Stream, and begin recording. Then launch the main serial GUI in another terminal, connect the required serial ports, and run the experiment. Active initialization of the SAGA external trigger currently occurs in the **Start Stream** path; perform this step first when synchronization pulses are needed.

Both main GUIs accept `--mock` at the end of their launch commands. TMSi mock mode does not produce real Poly5 files or LSL streams. Serial mock mode simulates the motor/LC, while Vicon behavior remains controlled by configuration. Serial ports, Vicon addresses, and other parameters are configured in the respective `config.py` files in the two directories.

## Supporting scripts and outputs

- `tmsi_manager/mock_lsl_sagas.py`: generates mock SAGA1/SAGA2 LSL streams from the command line for testing the channel selection tool.
- `serial_manager/test_vicon.py`: command-line Vicon UDP test; use `--help` to view its arguments.
- `serial_manager/udp_sender.py`: UDP encoding/sending module with a command-line entry point; use `--help` to view its arguments.

Running the applications creates `tmsi_manager/recordings/`, `serial_manager/recordings/`, and `serial_manager/GUI_logs/` as needed. Automatic combined experiment CSV recording is disabled by default; raw recordings, trigger timing records, and the independent Start CSV/Stop CSV controls remain available. `serial_manager/gui/run_sheet_template.xlsx` is the session log template and must be retained.
