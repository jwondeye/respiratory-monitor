import subprocess, sys

try:
    import wfdb
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "wfdb"])
    import wfdb

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt, find_peaks

# ---------------------------------------------------------------------------
# Shared pipeline
# ---------------------------------------------------------------------------

def bandpass(signal, lowcut, highcut, fs, order=2):
    nyq = fs / 2
    b, a = butter(order, [lowcut / nyq, highcut / nyq], btype="band")
    return filtfilt(b, a, signal)


def estimate_rr(rec, rec_n):
    """Return (rr_estimated, rr_reference) or raise on any failure."""
    # --- locate PLETH ---
    sig_names = [s.strip(", \t").lower() for s in rec.sig_name]
    pleth_idx = sig_names.index("pleth")
    pleth = rec.p_signal[:, pleth_idx]
    fs = rec.fs

    # --- preprocess ---
    trim = int(fs * 10)
    if len(pleth) <= 2 * trim:
        raise ValueError("Record too short to trim 10 s from each end")
    pleth_trim = pleth[trim:-trim]
    mu, sigma = pleth_trim.mean(), pleth_trim.std()
    if sigma == 0:
        raise ValueError("Zero-variance PLETH after trimming")
    pleth_clean = np.clip(pleth_trim, mu - 3 * sigma, mu + 3 * sigma)

    # --- filter ---
    filtered = bandpass(pleth_clean, 0.1, 0.5, fs)
    if not np.isfinite(filtered).all():
        raise ValueError("Non-finite values after bandpass filter")

    # --- peaks ---
    min_dist = int(fs * 1.0)
    peaks, _ = find_peaks(filtered, distance=min_dist,
                          prominence=0.05 * np.ptp(filtered))
    duration_s = len(pleth_trim) / fs
    rr_est = len(peaks) / (duration_s / 60.0)

    # --- reference ---
    sig_names_n = [s.strip(", \t").lower() for s in rec_n.sig_name]
    resp_idx = sig_names_n.index("resp")
    resp_vals = rec_n.p_signal[:, resp_idx]
    resp_valid = resp_vals[np.isfinite(resp_vals) & (resp_vals > 0)]
    if len(resp_valid) == 0:
        raise ValueError("No valid RESP values in numerics record")
    rr_ref = float(np.mean(resp_valid))

    return rr_est, rr_ref


# ---------------------------------------------------------------------------
# Loop over all 53 records
# ---------------------------------------------------------------------------

results = []
skipped = []

for i in range(1, 54):
    pid = f"bidmc{i:02d}"
    pid_n = f"{pid}n"
    try:
        rec   = wfdb.rdrecord(pid,   pn_dir=None)
        rec_n = wfdb.rdrecord(pid_n, pn_dir=None)
        rr_est, rr_ref = estimate_rr(rec, rec_n)
        results.append({"patient": pid, "rr_estimated": rr_est, "rr_reference": rr_ref})
        print(f"  {pid}  est={rr_est:.2f}  ref={rr_ref:.2f}  err={abs(rr_est-rr_ref):.2f}")
    except Exception as e:
        skipped.append(pid)
        print(f"  {pid}  SKIPPED — {e}")

print(f"\nProcessed {len(results)} records, skipped {len(skipped)}: {skipped or 'none'}")

if not results:
    sys.exit("No records processed successfully.")

df = pd.DataFrame(results)
df["abs_error"] = (df["rr_estimated"] - df["rr_reference"]).abs()

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

mae    = df["abs_error"].mean()
med_ae = df["abs_error"].median()
rmse   = float(np.sqrt((df["abs_error"] ** 2).mean()))
pct2   = (df["abs_error"] <= 2).mean() * 100
pct4   = (df["abs_error"] <= 4).mean() * 100

print("\n=== Evaluation Metrics ===")
print(f"  N                       : {len(df)}")
print(f"  Mean Absolute Error     : {mae:.2f} breaths/min")
print(f"  Median Absolute Error   : {med_ae:.2f} breaths/min")
print(f"  RMSE                    : {rmse:.2f} breaths/min")
print(f"  Within 2 breaths/min    : {pct2:.1f}%")
print(f"  Within 4 breaths/min    : {pct4:.1f}%")

# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

fig, axes = plt.subplots(1, 2, figsize=(13, 6))

# -- Scatter: estimated vs reference --
ax = axes[0]
ax.scatter(df["rr_reference"], df["rr_estimated"], alpha=0.75, edgecolors="k",
           linewidths=0.4, color="steelblue", zorder=3)
lims = [min(df["rr_reference"].min(), df["rr_estimated"].min()) - 1,
        max(df["rr_reference"].max(), df["rr_estimated"].max()) + 1]
ax.plot(lims, lims, "r--", lw=1.2, label="Perfect agreement")
ax.set_xlim(lims); ax.set_ylim(lims)
ax.set_xlabel("Reference RR (breaths/min)")
ax.set_ylabel("Estimated RR (breaths/min)")
ax.set_title("Estimated vs Reference RR")
ax.legend()
ax.set_aspect("equal", "box")

# Annotate each point with patient number
for _, row in df.iterrows():
    label = row["patient"].replace("bidmc", "")
    ax.annotate(label, (row["rr_reference"], row["rr_estimated"]),
                fontsize=6, textcoords="offset points", xytext=(3, 2))

# -- Bland-Altman --
ax = axes[1]
means = (df["rr_estimated"] + df["rr_reference"]) / 2
diffs = df["rr_estimated"] - df["rr_reference"]
bias  = diffs.mean()
sd    = diffs.std()

ax.scatter(means, diffs, alpha=0.75, edgecolors="k", linewidths=0.4,
           color="darkorange", zorder=3)
ax.axhline(bias,            color="navy",   lw=1.5, linestyle="-",
           label=f"Bias = {bias:.2f}")
ax.axhline(bias + 1.96 * sd, color="firebrick", lw=1.2, linestyle="--",
           label=f"+1.96 SD = {bias + 1.96*sd:.2f}")
ax.axhline(bias - 1.96 * sd, color="firebrick", lw=1.2, linestyle="--",
           label=f"−1.96 SD = {bias - 1.96*sd:.2f}")
ax.axhline(0, color="gray", lw=0.8, linestyle=":")
ax.set_xlabel("Mean of Estimated & Reference RR (breaths/min)")
ax.set_ylabel("Estimated − Reference RR (breaths/min)")
ax.set_title("Bland-Altman Plot")
ax.legend(fontsize=8)

plt.suptitle(
    f"BIDMC PPG Respiratory Rate Evaluation  |  N={len(df)}  "
    f"MAE={mae:.2f}  RMSE={rmse:.2f}  Within±2={pct2:.0f}%",
    fontsize=10, y=1.01
)
plt.tight_layout()
plt.savefig("bidmc_evaluation.png", dpi=150, bbox_inches="tight")
print("\nFigure saved to bidmc_evaluation.png")

# ---------------------------------------------------------------------------
# Save results CSV
# ---------------------------------------------------------------------------

df_out = df[["patient", "rr_estimated", "rr_reference", "abs_error"]].copy()
df_out = df_out.round(2).sort_values("patient").reset_index(drop=True)
df_out.to_csv("bidmc_results.csv", index=False)
print("Results saved to bidmc_results.csv")

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

print("\n=== Per-Patient Results ===")
print(f"{'Patient':<12} {'Est RR':>8} {'Ref RR':>8} {'Abs Err':>9}")
print("-" * 42)
for _, row in df_out.iterrows():
    print(f"{row['patient']:<12} {row['rr_estimated']:>8.2f} "
          f"{row['rr_reference']:>8.2f} {row['abs_error']:>9.2f}")
print("-" * 42)
print(f"{'Mean':<12} {df_out['rr_estimated'].mean():>8.2f} "
      f"{df_out['rr_reference'].mean():>8.2f} {mae:>9.2f}")

plt.show()
