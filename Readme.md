## Project Structure

```
wrist_rig/
│
├── gui/
│   ├── app.py              # Main window (ExpGUI). Entry point.
│   ├── display.py          # Subject-facing display window (ball + force chart)
│   └── udp.py              # UDPManager — broadcast and listen
│
├── hardware/
│   ├── ring_buffer.py      # InterpRingBuffer — thread-safe timestamped ring
│   ├── serial_worker.py    # SerialWorker — exo UART read/write thread
│   ├── lc_worker.py        # LCWorker — Nano17 load cell read thread
│   └── mock_workers.py     # MockSerialWorker, MockLCWorker for --mock mode
│
├── motions/
│   ├── base.py             # ModeBase + Command dataclass
│   ├── motion_runner.py    # MotionRunner — 100 Hz control loop thread
│   ├── rest_mode.py
│   ├── calibration_mode.py
│   ├── rom_mode.py
│   ├── aan_mode.py
│   ├── passive_movement_mode.py
│   ├── active_movement_mode.py
│   ├── back_and_forth_mode.py
│   ├── rah_mode.py
│   └── __init__.py         # re-exports all modes + MODES dict
│
├── recording/
│   └── recorder.py         # BaseCsvRecorder — unified recorder for all experiments
│
├── firmware/
│   ├── onboard_withIMU.ino
│   └── config.h
│
├── data/                   # Experiment output (not committed to git)
│   ├── rest/
│   ├── calibration/
│   ├── rom/
│   ├── aan/
│   ├── passive_movement/
│   ├── active_movement/
│   ├── back_and_forth/
│   └── rah/
│
├── docs/
│   └── marker_reference.md
│
├── config.py               # All constants (baud rates, gear ratio, data root, etc.)
├── test_udp.py             # UDP loopback tester (run alongside GUI for local testing)
└── README.md
```

---

## UDP Marker Reference

All experiments share the same convention:
- **Start**: `0→1`
- **Last meaningful action**: sends `X→0` which also serves as stop signal
- Marker `0` always means "not in experiment"

Marker values per phase are defined in each mode file's `PHASE_TO_MARKER` dict.

| Experiment | Marker sequence |
|---|---|
| Rest | `0→1` start, `1→0` stop |
| Calibration | `0→1` start, `1→2` hold, `2→3` return, `3→0` final pos + stop |
| ROM | `0→1` start, `1→2` active end, `2→3` passive end, `3→4` return, `4→0` final pos + stop |
| AAN | `0→1` start, `1→2` active end (auto), `2→3` passive end (auto), `3→4` return, `4→0` returned + stop |
| Passive Movement | `0→1` go, `1→1` passed active_end (go, event), `1→2` hold, `2→3` return, `3→3` passed active_end (return, event), `3→0` done/stop |
| Active Movement | `0→1` start/guide, `1→2` hit, `2→3` return, `3→0` returned + stop |
| Back and Forth | `0→1` start, `1→0` stop |
| Ramp and Hold | `0→1` start, `1→0` stop |

---

## AAN Marker Reference (updated)

AAN now uses 6 markers per rep (0–5):

| Marker | Phase | Description |
|---|---|---|
| 0 | rest / idle | Between reps or not started |
| 1 | active / pre_passive | User moving to active end (or timeout pause) |
| 2 | passive | Servo driving to passive end |
| 3 | hold | Holding at passive end for hold_time_s |
| 4 | return | Torque off, damper on (if enabled), user return window |
| 5 | servo_back | Servo bringing hand back to 0° |

Sequence per rep: `0→1→2→3→4→5→0` (then next rep or done)

## Passive Movement sub-markers (CSV only, not UDP)

- `1.0` go, before active_end
- `1.5` go, past active_end  
- `2.0` hold
- `2.5` return, before active_end
- `3.0` return, past active_end
