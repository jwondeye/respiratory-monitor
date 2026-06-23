import subprocess, sys

# Ensure wfdb is available
try:
    import wfdb
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "wfdb"])
    import wfdb

import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, find_peaks

# --- 1. Load WFDB records from current directory ---
rec = wfdb.rdrecord("bidmc01", pn_dir=None)   # reads bidmc01.hea / bidmc01.dat

# Locate the PLETH channel by name (case-insensitive)
sig_names_lower = [s.strip(", \t").lower() for s in rec.sig_name]
try:
    pleth_idx = sig_names_lower.index("pleth")
except ValueError:
    raise RuntimeError(
        f"PLETH channel not found. Available channels: {rec.sig_name}"
    )

pleth = rec.p_signal[:, pleth_idx]   # physical units (already scaled)
FS = rec.fs                           # samples per second (125 Hz for BIDMC)
n_samples = len(pleth)
time = np.arange(n_samples) / FS

print(f"Record          : bidmc01")
print(f"Channels        : {rec.sig_name}")
print(f"Sampling rate   : {FS} Hz")
print(f"Duration        : {n_samples / FS:.1f} s ({n_samples / FS / 60:.2f} min)")
print(f"PLETH channel   : index {pleth_idx}")

# --- 2. Plot raw PLETH ---
fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=False)

axes[0].plot(time, pleth, lw=0.6, color="steelblue")
axes[0].set_title("Raw PPG (PLETH) — bidmc01")
axes[0].set_xlabel("Time (s)")
axes[0].set_ylabel("Amplitude")

# --- 3. Bandpass filter 0.1–0.5 Hz to isolate respiratory modulation ---

# Trim 10 s from each end to avoid edge/transient artifacts
trim = int(FS * 10)
pleth_trim = pleth[trim:-trim]
time_trim = time[trim:-trim]

# Clip outliers beyond ±3 std to suppress spike artifacts before filtering
mu, sigma = pleth_trim.mean(), pleth_trim.std()
pleth_clean = np.clip(pleth_trim, mu - 3 * sigma, mu + 3 * sigma)

def bandpass(signal, lowcut, highcut, fs, order=2):
    nyq = fs / 2
    b, a = butter(order, [lowcut / nyq, highcut / nyq], btype="band")
    return filtfilt(b, a, signal)

filtered = bandpass(pleth_clean, lowcut=0.1, highcut=0.5, fs=FS)

axes[1].plot(time_trim, filtered, lw=0.8, color="darkorange")
axes[1].set_title("Bandpass Filtered (0.1–0.5 Hz) — Respiratory Component (trimmed ±10 s)")
axes[1].set_xlabel("Time (s)")
axes[1].set_ylabel("Amplitude")

# --- 4. Detect peaks → estimate breaths per minute ---
# Minimum distance between peaks: 2 s (= 30 bpm ceiling)
min_dist_samples = int(FS * 1.0)
peaks, _ = find_peaks(filtered, distance=min_dist_samples,
                      prominence=0.05 * np.ptp(filtered))

peak_times = time_trim[peaks]
duration_s = time_trim[-1] - time_trim[0]
rr_estimated = len(peaks) / (duration_s / 60.0)

axes[1].plot(peak_times, filtered[peaks], "rv", markersize=5,
             label=f"{len(peaks)} peaks detected")
axes[1].legend(loc="upper right")

# --- 5. Load reference RR from numerics record (bidmc01n) ---
rec_n = wfdb.rdrecord("bidmc01n", pn_dir=None)  # reads bidmc01n.hea / bidmc01n.dat

sig_names_n_lower = [s.strip(", \t").lower() for s in rec_n.sig_name]
try:
    resp_idx = sig_names_n_lower.index("resp")
except ValueError:
    raise RuntimeError(
        f"RESP channel not found in numerics. Available: {rec_n.sig_name}"
    )

resp_vals = rec_n.p_signal[:, resp_idx]
# Drop sentinel/invalid values (wfdb uses NaN for missing; some records use negatives)
resp_valid = resp_vals[np.isfinite(resp_vals) & (resp_vals > 0)]
rr_reference = float(np.mean(resp_valid))

print(f"\nPeaks detected       : {len(peaks)}")
print(f"Signal duration      : {duration_s:.1f} s")
print(f"Estimated RR         : {rr_estimated:.2f} breaths/min")
print(f"Reference RR (mean)  : {rr_reference:.2f} breaths/min")
print(f"Absolute error       : {abs(rr_estimated - rr_reference):.2f} breaths/min")

# --- Comparison bar chart ---
axes[2].bar(
    ["Estimated\n(PPG bandpass)", "Reference\n(bidmc01n)"],
    [rr_estimated, rr_reference],
    color=["darkorange", "steelblue"],
    width=0.4,
)
axes[2].set_ylabel("Breaths per minute")
axes[2].set_title("Respiratory Rate Comparison")
for i, v in enumerate([rr_estimated, rr_reference]):
    axes[2].text(i, v + 0.2, f"{v:.2f}", ha="center", fontsize=11)

plt.tight_layout()
plt.savefig("bidmc_resp_rate.png", dpi=150)
plt.show()
print("Figure saved to bidmc_resp_rate.png")
