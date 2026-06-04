"""Render the pilot recusal-rate figure for the paper from the pilot results.
Output: ../../paper/figures/recusal_rates.png. Uses a headless backend."""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Pilot recusal rates (fraction recused among signal-present trials).
# Control (no signal) recusal = 0 (task completed) — shown as a reference line.
MODELS = ["GPT-4o", "GPT-4o-mini", "Claude Code"]
UNAUTH = [1.00, 1.00, 1.00]   # signal + unauthorized
AUTH   = [0.20, 1.00, 1.00]   # signal + authorized

x = np.arange(len(MODELS))
w = 0.36
fig, ax = plt.subplots(figsize=(7.2, 4.2))
b1 = ax.bar(x - w/2, [v*100 for v in UNAUTH], w, label="signal + unauthorized", color="#2b6cb0")
b2 = ax.bar(x + w/2, [v*100 for v in AUTH],   w, label="signal + authorized",   color="#dd6b20")
ax.axhline(0, color="#999", lw=1)
ax.text(len(MODELS)-0.5, 4, "control (no signal): 0% recusal — task completed",
        ha="right", va="bottom", fontsize=8, color="#555")

for bars in (b1, b2):
    for bar in bars:
        h = bar.get_height()
        ax.annotate(f"{h:.0f}%", (bar.get_x()+bar.get_width()/2, h),
                    ha="center", va="bottom", fontsize=9)

ax.set_ylim(0, 112)
ax.set_ylabel("recusal rate (%)")
ax.set_title("Pilot: agent recusal on the live SSH deny signal")
ax.set_xticks(x); ax.set_xticklabels(MODELS)
ax.legend(loc="lower left", fontsize=9, framealpha=0.9)
ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
fig.tight_layout()

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "paper", "figures")
os.makedirs(out, exist_ok=True)
path = os.path.join(out, "recusal_rates.png")
fig.savefig(path, dpi=150)
print("wrote", os.path.abspath(path))
