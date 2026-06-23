# Wrist-Based Respiratory Rate Monitor

Real-time respiratory rate and heart rate monitor built on a Raspberry Pi Zero 2W using a MAX30102 PPG sensor. The device extracts breathing rate and heart rate from a wrist-worn optical sensor using a signal processing pipeline running directly on the Pi, streaming live vitals to a React dashboard via WebSocket.

**Demo:** [YouTube](https://youtube.com/shorts/RK-HrU1LweM)

---

## How It Works

The MAX30102 sensor shines infrared light into the skin and measures how much bounces back. Blood volume in the capillaries changes with every heartbeat, creating a detectable waveform (PPG signal). Layered on top of the cardiac signal is a slower modulation caused by breathing — when you inhale, thoracic pressure shifts slightly change blood volume in the wrist capillaries.

A bandpass filter isolates each frequency band:
- **0.1–0.5 Hz** → respiratory rate (6–30 breaths/min)
- **0.8–3.0 Hz** → heart rate (48–180 BPM)

Peak detection on the filtered signals counts cycles per minute. After a 15-second initial buffer, the system streams live estimates every 2 seconds.

---

## Architecture

```
MAX30102 sensor (wrist)
        │  I2C @ 100 Hz
        ▼
Raspberry Pi Zero 2W
  ├── sensor thread: reads FIFO register → IR buffer (deque)
  ├── compute loop: bandpass filter + peak detection every 2s
  └── FastAPI WebSocket server → streams JSON vitals
        │  ws://192.168.1.232:8000/ws
        ▼
React Dashboard (laptop browser)
  ├── Live HR and RR display with clinical range annotations
  └── 60-second trend chart (Recharts)
```

---

## Validation

The signal processing algorithm was validated on the **BIDMC PPG and Respiration Dataset** (PhysioNet) — 53 ICU patient recordings with simultaneous PPG waveforms and clinical reference respiratory rates.

| Metric | Value |
|--------|-------|
| Mean Absolute Error | **2.14 breaths/min** |
| Median Absolute Error | 0.91 breaths/min |
| RMSE | 3.60 breaths/min |
| Within ±2 breaths/min | **71.7%** |
| Within ±4 breaths/min | 83.0% |

---

## Hardware

| Component | Purpose | Cost |
|-----------|---------|------|
| Raspberry Pi Zero 2W | Edge compute, WiFi, I2C host | ~$15 |
| MAX30102 breakout | PPG sensor (SpO2 + HR + RR) | ~$7 |
| Jumper wires | GPIO connections | ~$6 |
| MicroSD card (32GB) | Raspberry Pi OS | ~$9 |

**Total: ~$37**

Wiring (I2C):
- VIN → Pi pin 1 (3.3V)
- GND → Pi pin 6
- SDA → Pi pin 3 (GPIO 2)
- SCL → Pi pin 5 (GPIO 3)

---

## Setup

### Pi Setup

```bash
# Enable I2C
sudo raspi-config nonint do_i2c 0

# Install dependencies
pip install fastapi "uvicorn[standard]" smbus2 numpy scipy --break-system-packages

# Run the server
python3 max30102_server.py
```

### Dashboard Setup

```bash
cd vitals-dashboard
npm install
npm run dev
# Open http://localhost:5173
```

---

## Files

| File | Description |
|------|-------------|
| `max30102_live.py` | Standalone terminal monitor — prints live HR and RR |
| `max30102_server.py` | FastAPI WebSocket server — streams vitals to dashboard |
| `vitals-dashboard/` | React frontend with real-time trend charts |
| `bidmc_evaluation.py` | Algorithm validation across all 53 BIDMC patients |
| `bidmc_resp_rate.py` | Single-patient validation with Bland-Altman plots |

---

## Tech Stack

**Embedded:** Python · smbus2 · Raspberry Pi OS · I2C  
**Signal Processing:** scipy · NumPy · Butterworth bandpass filter · peak detection  
**Backend:** FastAPI · WebSockets · uvicorn  
**Frontend:** React · Recharts · Tailwind CSS  
**Validation Dataset:** BIDMC PPG and Respiration Dataset (PhysioNet)

---

## Notes

- The algorithm uses a sliding window buffer (configurable via `BUFFER_SECONDS` in `max30102_live.py`). Shorter windows respond faster but are noisier; 20–30 seconds gives clinically stable estimates.
- Motion artifacts significantly affect signal quality — keep the sensor still during measurement.
- This is a research prototype, not a medical device.

---

*Built by Jericho Wondeye — Computer Engineering, University of Maryland (Class of 2029)*
