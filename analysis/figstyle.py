"""Manuscript-format matplotlib defaults — import before pyplot use.

One place controls every figure's look so the paper's figures are
uniform: serif text sized for a two-column layout, 300 dpi, no chartjunk.
"""
import matplotlib

matplotlib.use("Agg")  # headless: scripts always write files, never show
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams.update({
    "figure.figsize": (3.5, 2.6),      # single column width (inches)
    "figure.dpi": 120,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "font.family": "serif",
    "font.size": 8,
    "axes.titlesize": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.linewidth": 0.4,
    "grid.alpha": 0.35,
    "lines.linewidth": 1.2,
})


def save(fig, path):
    fig.savefig(path)
    print(f"figure -> {path}")
