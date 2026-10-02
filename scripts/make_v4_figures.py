from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-heatplan"))
import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    before = pd.read_csv(ROOT / "outputs" / "v4" / "northern_resilience_suite.csv")
    after = pd.read_csv(ROOT / "outputs" / "v4_resilient" / "northern_resilience_suite.csv")
    labels = ["Nominal", "Cold wave", "Source N-1", "Pipe N-1"]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    ax.bar([i - 0.18 for i in x], before["unserved_rate"] * 100, width=0.36, label="previous plan", color="#d95f5f")
    ax.bar([i + 0.18 for i in x], after["unserved_rate"] * 100, width=0.36, label="V4 resilient plan", color="#2a9d8f")
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("Unserved heat rate (%)")
    ax.set_title("Out-of-sample resilience comparison")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    for i, value in enumerate(before["unserved_rate"] * 100):
        ax.text(i - 0.18, value + 1.0, f"{value:.1f}%", ha="center", fontsize=9)
    for i, value in enumerate(after["unserved_rate"] * 100):
        ax.text(i + 0.18, value + 1.0, f"{value:.1f}%", ha="center", fontsize=9)
    ax.set_ylim(0, max(65, before["unserved_rate"].max() * 100 + 8))
    fig.tight_layout()
    output = ROOT / "outputs" / "figures" / "v4_resilience_comparison.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=200)
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    main()
