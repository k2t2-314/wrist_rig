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

## MVC from EMG (tab `MVC_EMG` in the serial GUI)

Estimates the maximal wrist force from EMG when the load cell cannot take a maximal effort. Package
`serial_manager/mvc_emg/` (numpy, scipy, matplotlib from `requirements.txt`; imported only when the analysis runs).

1. **Set-up.** TMSi GUI: connect both SAGAs, press Start Stream on each (the trigger input is only enabled by
   Start Stream; a file recorded without it has a flat `TRIGGERS` channel and cannot be aligned), set the file
   tags (flexor SAGA = `FLX`). Serial GUI, tab `MVC_EMG`: the flexor tag and the TMSi recordings folder are
   remembered in `serial_manager/mvc_emg_settings.json`.
2. **Maximal effort, EMG only.** Efforts (3), rest 5 s, MAX 5 s, recovery 60 s. Type the prefix shown in the
   tab (e.g. `01_MVC_EMG_R_flex`, Copy button) as the TMSi filename prefix, Start Recording there, then Start
   here. The participant screen shows REST / MAX EFFORT! / RELAX with a countdown; trigger codes 6 (rest),
   7 (MAX), 8 (recovery), 15 (stop). Rig files go to `serial_manager/recordings/mvc_emg/`. The load cell is
   recorded but not used.
3. **Square-wave calibration.** Levels in N (default `1, 5, 20, 30`), hold 5 s, rest 10 s, ramp 0.5 s.
   "Send to RAH tab" writes the profile into RAH_LC, sets its MVC value to 100 N (so % = N) and the channel to
   Fx and generates the diagram; check the direction, type the RAH stem as the TMSi prefix and run it in RAH_LC.
4. **Estimate.** Browse for the calibration Poly5 (the RAH trial's flexor file) and the MVC Poly5 (the
   EMG-only efforts); the matching rig log is filled in from the file name or, failing that, from the closest
   trial in time (browse to override it). Choose the force quantity (Fx by default) and the channels (`all` =
   every channel that passes the quality rules, a number = only the best N by signal-to-noise ratio, or a fixed
   list), press Analyse. The analysis aligns EMG and force with the TTL pulses,
   takes the middle 3 s of every hold and the rest before it, band-passes 20-500 Hz, computes RMS in 0.5 s
   epochs with noise correction, excludes silent/noisy/clipped/unresponsive channels, takes the median over the
   kept channels per epoch, measures the peak EMG of each effort on the same channels in the same way,
   fits a line, a power law and a quadratic, inverts them at the effort EMG and reports the median with the
   spread between the models (above 25 % the result is reported as "above the top level" instead of a number).
   Report, figure and epoch table are written next to the rig files
   (`recordings/mvc_emg/<RAH stem>__mvc_from_emg/`); "Use as RAH MVC value" copies the estimate into RAH_LC.

Command line (from `serial_manager`): `python -m mvc_emg --rah-log <RAH log> --mvc-log <MVC_EMG log> --tmsi-dir <folder> --tag FLX --out <folder>`;
`python -m mvc_emg.selftest [file.poly5]` runs the fit on synthetic data and the Poly5 reader.
