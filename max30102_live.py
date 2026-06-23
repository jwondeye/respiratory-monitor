"""
MAX30102 live respiratory rate + heart rate monitor.
Wiring: MAX30102 SDA/SCL → Pi GPIO 2/3, I2C bus 1, address 0x57.
Run with: python max30102_live.py
"""

import time
import collections
import numpy as np
from scipy.signal import butter, filtfilt, find_peaks
from smbus2 import SMBus, i2c_msg

# ---------------------------------------------------------------------------
# Hardware constants
# ---------------------------------------------------------------------------

I2C_BUS  = 1
I2C_ADDR = 0x57
FS       = 100          # samples per second (set in SpO2 config below)

# Registers (from MAX30102 datasheet)
REG_INTR_STATUS_1 = 0x00
REG_INTR_ENABLE_1 = 0x02
REG_FIFO_WR_PTR   = 0x04
REG_OVF_COUNTER   = 0x05
REG_FIFO_RD_PTR   = 0x06
REG_FIFO_DATA     = 0x07
REG_FIFO_CONFIG   = 0x08
REG_MODE_CONFIG   = 0x09
REG_SPO2_CONFIG   = 0x0A
REG_LED1_PA       = 0x0C   # RED amplitude
REG_LED2_PA       = 0x0D   # IR  amplitude

# ---------------------------------------------------------------------------
# Sensor setup
# ---------------------------------------------------------------------------

def setup(bus: SMBus) -> None:
    # Soft-reset (bit 6 of MODE_CONFIG); device clears it when done
    bus.write_byte_data(I2C_ADDR, REG_MODE_CONFIG, 0x40)
    time.sleep(0.1)

    # Clear FIFO pointers and overflow counter
    bus.write_byte_data(I2C_ADDR, REG_FIFO_WR_PTR,  0x00)
    bus.write_byte_data(I2C_ADDR, REG_OVF_COUNTER,  0x00)
    bus.write_byte_data(I2C_ADDR, REG_FIFO_RD_PTR,  0x00)

    # FIFO config: SMP_AVE=000 (no averaging), ROLLOVER=1, A_FULL=0000
    # 0b 000 1 0000 = 0x10
    bus.write_byte_data(I2C_ADDR, REG_FIFO_CONFIG, 0x10)

    # SpO2 config:
    #   SPO2_ADC_RGE[6:5] = 01  → 4096 nA full-scale
    #   SPO2_SR[4:2]       = 001 → 100 samples/s
    #   LED_PW[1:0]        = 11  → 411 µs pulse / 18-bit ADC
    # 0b 0 01 001 11 = 0x27
    bus.write_byte_data(I2C_ADDR, REG_SPO2_CONFIG, 0x27)

    # LED drive current: 0x24 = 7.2 mA (0.2 mA per LSB)
    bus.write_byte_data(I2C_ADDR, REG_LED1_PA, 0x24)   # RED
    bus.write_byte_data(I2C_ADDR, REG_LED2_PA, 0x24)   # IR

    # Mode: SpO2 (0x03) → enables RED + IR, interleaved in FIFO
    bus.write_byte_data(I2C_ADDR, REG_MODE_CONFIG, 0x03)

    # Clear any pending interrupt flags before entering the loop
    bus.read_byte_data(I2C_ADDR, REG_INTR_STATUS_1)

    time.sleep(0.05)

# ---------------------------------------------------------------------------
# FIFO read
# ---------------------------------------------------------------------------

def read_fifo(bus: SMBus) -> list:
    """
    Return a list of new IR sample values (18-bit integers).

    SpO2 mode FIFO layout per sample (6 bytes total):
      byte 0: RED[17:16] in bits[1:0]
      byte 1: RED[15:8]
      byte 2: RED[7:0]
      byte 3: IR[17:16]  in bits[1:0]
      byte 4: IR[15:8]
      byte 5: IR[7:0]
    The read pointer auto-increments as bytes are consumed.
    """
    wr  = bus.read_byte_data(I2C_ADDR, REG_FIFO_WR_PTR)
    rd  = bus.read_byte_data(I2C_ADDR, REG_FIFO_RD_PTR)
    num = (wr - rd) & 0x1F   # FIFO depth is 32 slots (5-bit pointer)

    if num == 0:
        return []

    num_bytes = num * 6

    # i2c_rdwr supports transfers > 32 bytes unlike read_i2c_block_data
    set_reg = i2c_msg.write(I2C_ADDR, [REG_FIFO_DATA])
    rx      = i2c_msg.read(I2C_ADDR, num_bytes)
    bus.i2c_rdwr(set_reg, rx)
    raw = list(rx)

    ir_values = []
    for i in range(num):
        base = i * 6
        ir = (raw[base + 3] & 0x03) << 16 | raw[base + 4] << 8 | raw[base + 5]
        ir_values.append(ir)

    return ir_values

# ---------------------------------------------------------------------------
# Signal processing
# ---------------------------------------------------------------------------

def bandpass(signal: np.ndarray, lo: float, hi: float) -> np.ndarray:
    nyq = FS / 2
    b, a = butter(2, [lo / nyq, hi / nyq], btype="band")
    return filtfilt(b, a, signal)


def estimate_rate(signal: np.ndarray, lo: float, hi: float,
                  min_dist_s: float) -> float | None:
    """Bandpass-filter then count peaks; return rate in beats/breaths per min."""
    try:
        filtered = bandpass(signal, lo, hi)
    except Exception:
        return None

    if not np.isfinite(filtered).all():
        return None

    ptp = np.ptp(filtered)
    if ptp == 0:
        return None

    peaks, _ = find_peaks(filtered,
                          distance=int(FS * min_dist_s),
                          prominence=0.05 * ptp)
    duration_min = len(signal) / FS / 60.0
    return len(peaks) / duration_min


def preprocess(buf: collections.deque) -> np.ndarray:
    """Clip ±3σ outliers from the raw IR buffer."""
    sig = np.array(buf, dtype=float)
    mu, sigma = sig.mean(), sig.std()
    if sigma > 0:
        sig = np.clip(sig, mu - 3 * sigma, mu + 3 * sigma)
    return sig

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

BUFFER_SECONDS  = 15
BUFFER_SIZE     = BUFFER_SECONDS * FS   # 3 000 samples
UPDATE_INTERVAL = 5                      # seconds between printed estimates
POLL_INTERVAL   = 0.010                  # 10 ms poll → drains FIFO at ~100 Hz

def main() -> None:
    ir_buf      = collections.deque(maxlen=BUFFER_SIZE)
    last_print  = time.monotonic()
    next_poll   = time.monotonic()

    with SMBus(I2C_BUS) as bus:
        setup(bus)
        print(f"MAX30102 ready  |  I2C 0x{I2C_ADDR:02X}  |  {FS} Hz")
        print(f"Buffering {BUFFER_SECONDS} s of data before first estimate …\n")

        while True:
            now = time.monotonic()

            # --- drain FIFO ---
            try:
                for sample in read_fifo(bus):
                    ir_buf.append(sample)
            except OSError as e:
                print(f"[{_ts()}] I2C error: {e}")

            # --- update every UPDATE_INTERVAL once buffer is full ---
            if (len(ir_buf) == BUFFER_SIZE and
                    now - last_print >= UPDATE_INTERVAL):

                sig = preprocess(ir_buf)

                rr = estimate_rate(sig, lo=0.1, hi=0.5, min_dist_s=1.0)
                hr = estimate_rate(sig, lo=0.8, hi=3.0, min_dist_s=0.3)

                rr_str = f"{rr:5.1f} bpm" if rr is not None else "  --- "
                hr_str = f"{hr:5.1f} bpm" if hr is not None else "  --- "

                print(f"[{_ts()}]  Resp: {rr_str}    Heart: {hr_str}")
                last_print = now

            # --- pace the loop to ~100 Hz ---
            next_poll += POLL_INTERVAL
            sleep_for = next_poll - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)


def _ts() -> str:
    return time.strftime("%H:%M:%S")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
