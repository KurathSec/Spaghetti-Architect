"""Figures for the paper, drawn only from committed analysis artifacts. Zero API.

Registered 2026-11-07. No new statistic is computed here: every plotted value is
read straight from a committed JSON file, so a figure can never disagree with the
artifact behind it.

  fig_levels_code.tex      the running example at four levels, additions marked
  fig_code_lines.pdf       code lines per family and level, and which levels change the
                           code (rung_census.json)
  fig_comment_removal.pdf  score and 95% interval, with and without comments
                           (annotation_ablation.json)
  fig_replication.pdf      drops in the first run and the replication
                           (annotation_ablation.json, rep1_results.json)
  fig_base_rate.pdf        failures without comments and rescue rate
                           (did_robustness.json)
  fig_comment_kinds.pdf    refactoring score under five renderings (rep1_results.json)
  fig_static_metrics.pdf   correlations with the messiness level (anchor.json,
                           anchor_v21.json)
  fig_messiness.pdf        mean score per scored level, with and without comments
                           (unannotated_slopes.json)
  fig_slopes.pdf           slope per level, first run and replication
                           (unannotated_slopes.json, rep1_results.json)
  fig_size.pdf             score over list length (g3_analysis.json, rep1_results.json)
  fig_power.pdf            power of the dev against Tier A comparison
                           (freshness_power.json)

Design: every figure uses one language. A score is a dot with a pale interval band;
slate means with comments, rose means without; grids of values are heatmaps in one
slate ramp, where a printed number marks only what the figure argues.

Usage: python3 bench/paper_figures.py OUT_DIR    (needs matplotlib)
"""

from __future__ import annotations

import json
import os
import sys

import difflib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
OUT = os.path.join(_HERE, "out")

MODELS = ["Llama-3.1-8B", "Mistral-24B", "Llama-3.3-70B", "DeepSeek-V4-Flash"]
WITH_COLOR, WITHOUT_COLOR = "#39414F", "#CF4A5F"
FALSE_COLOR, PARTIAL_COLOR = "#C98A1B", "#7D8A9C"
SLATE = LinearSegmentedColormap.from_list("slate", ["#F6F7F9", "#AEB6C2", "#39414F"])
INK, MUTED, FAINT = "#1C1C1C", "#5A5A5A", "#E4E4E4"
TEXT_WIDTH = 6.5

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Liberation Serif", "STIXGeneral"],  # Times metrics; embeds cleanly
    "mathtext.fontset": "stix",
    "font.size": 9,
    "axes.titlesize": 9.5,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.titlecolor": INK,
    "axes.labelsize": 9,
    "axes.labelcolor": MUTED,
    "axes.edgecolor": "#8A8A8A",
    "axes.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.axisbelow": True,
    "axes.grid": False,
    "grid.color": FAINT,
    "grid.linewidth": 0.6,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "xtick.major.size": 2.5,
    "ytick.major.size": 2.5,
    "legend.fontsize": 8.5,
    "legend.frameon": False,
    "lines.solid_capstyle": "round",
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
    "pdf.fonttype": 42,
    "svg.fonttype": "none",
})


PRE_SAVE = None  # optional QA hook: called as PRE_SAVE(fig, name) before each export


def _load(name: str):
    return json.load(open(os.path.join(OUT, name)))


def _save(fig, out_dir: str, name: str) -> None:
    fig.canvas.draw()
    if PRE_SAVE is not None:
        PRE_SAVE(fig, name)
    for ext in ("pdf", "svg"):
        fig.savefig(os.path.join(out_dir, f"{name}.{ext}"))
    fig.savefig(os.path.join(out_dir, name + ".png"), dpi=300)
    plt.close(fig)


def fig_comment_removal(out_dir: str) -> None:
    data = _load("annotation_ablation.json")
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 2.1), sharey=True,
                             gridspec_kw={"wspace": 0.2})
    panels = (("refactor", "Refactoring", "semantic-equivalence rate"),
              ("comprehend", "Output prediction", "exact match"))
    for ax, (task, title, xlabel) in zip(axes, panels):
        per = data[task]["per_model"]
        for i, model in enumerate(MODELS):
            m = per[model]
            for offset, mean, ci, color, face in (
                    (0.15, m["annotated_k1"], m["annotated_k1_ci95"], WITH_COLOR, WITH_COLOR),
                    (-0.15, m["unannotated_k1"], m["unannotated_k1_ci95"], WITHOUT_COLOR, "white")):
                ax.plot(ci, [i + offset] * 2, color=color, lw=3.2, alpha=0.28,
                        solid_capstyle="butt")
                ax.plot([mean], [i + offset], marker="o", ms=4.6, mfc=face, mec=color,
                        mew=1.1, ls="none", zorder=3)
            ax.text(1.02, i, f"${m['delta']:+.3f}$", va="center", ha="left", fontsize=8,
                    color=MUTED, transform=ax.get_yaxis_transform())
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_xlim(0.2, 1.0)
        ax.set_ylim(-0.55, len(MODELS) - 0.45)
        ax.set_yticks(range(len(MODELS)), MODELS)
        ax.set_xticks([0.2, 0.4, 0.6, 0.8, 1.0])
        ax.xaxis.grid(True)
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0, labelcolor=INK)
    for ax in axes:
        ax.text(1.02, 1.0, r"$\Delta$", transform=ax.transAxes, fontsize=8.5, color=MUTED,
                va="bottom", ha="left")
    handles = [
        plt.Line2D([], [], color=WITH_COLOR, marker="o", ms=4.6, mfc=WITH_COLOR, ls="none",
                   label="with comments"),
        plt.Line2D([], [], color=WITHOUT_COLOR, marker="o", ms=4.6, mfc="white", mew=1.1,
                   ls="none", label="without comments"),
        plt.Line2D([], [], color="#9A9A9A", lw=3.2, alpha=0.45, solid_capstyle="butt",
                   label="95% interval"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, handletextpad=0.4,
               columnspacing=1.6, bbox_to_anchor=(0.54, -0.2))
    _save(fig, out_dir, "fig_comment_removal")


def _dot(ax, y, mean, ci, color, face=None, marker="o", ms=4.6, band=True):
    """A score: a pale interval band with a crisp dot on top."""
    if band and ci is not None:
        ax.plot(ci, [y, y], color=color, lw=3.2, alpha=0.28, solid_capstyle="butt")
    ax.plot([mean], [y], marker=marker, ms=ms, mfc=color if face is None else face, mec=color,
            mew=1.1, ls="none", zorder=3)


def _rows(ax, labels, xlim, xticks):
    ax.set_xlim(*xlim)
    ax.set_ylim(-0.55, len(labels) - 0.45)
    ax.set_yticks(range(len(labels)), labels)
    ax.set_xticks(xticks)
    ax.xaxis.grid(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, labelcolor=INK)


def _legend(fig, handles, y):
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), handletextpad=0.4,
               columnspacing=1.6, bbox_to_anchor=(0.54, y))


def _h(color, face=None, marker="o", label=""):
    return plt.Line2D([], [], color=color, marker=marker, ms=4.6, ls="none", mew=1.1,
                      mfc=color if face is None else face, label=label)


BAND = plt.Line2D([], [], color="#9A9A9A", lw=3.2, alpha=0.45, solid_capstyle="butt",
                  label="95% interval")


def _heat(ax, grid, rows, cols, show=None, fmt=None, fontsize=7.8):
    """A grid of values in one slate ramp. Exact zeros stay white, so a zero never reads as
    a small number. Only the cells chosen by show(i, j, v) print their value."""
    masked = [[float("nan") if v == 0 else v for v in row] for row in grid]
    im = ax.imshow(masked, cmap=SLATE, vmin=0, vmax=1, aspect="auto")
    if show is not None:
        for i, row in enumerate(grid):
            for j, v in enumerate(row):
                if show(i, j, v):
                    ax.text(j, i, fmt(v), ha="center", va="center", fontsize=fontsize,
                            color="white" if v > 0.58 else INK)
    ax.set_xticks(range(len(cols)), cols)
    ax.set_yticks(range(len(rows)), rows)
    ax.tick_params(length=0)
    ax.tick_params(axis="y", labelcolor=INK)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_xticks([x - 0.5 for x in range(1, len(cols))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(rows))], minor=True)
    ax.grid(which="minor", color="white", lw=1.4)
    ax.tick_params(which="minor", length=0)
    return im


def _colourbar(fig, im, axes, label):
    bar = fig.colorbar(im, ax=axes, fraction=0.018, pad=0.012, aspect=28)
    bar.set_label(label, color=MUTED, fontsize=8.2)
    bar.outline.set_visible(False)
    bar.ax.tick_params(length=0, labelsize=7.8)
    bar.set_ticks([0, 0.5, 1])


def _two_decimals(v):
    return "1" if v == 1 else f"{v:.2f}".lstrip("0")


def fig_levels_code(out_dir: str) -> None:
    """The running example, comment-free, at four levels; new lines are marked."""
    from src.engine import Engine
    db = os.path.join(_ROOT, "config", "anti_patterns_db.json")
    ir = {"version": "1.0", "module_name": "demo",
          "inputs": {"readings": [137, 131, 156, 111]},
          "operations": [{"operation": "AGGREGATE", "mode": "sum",
                          "collection_name": "readings", "result_var": "total"}]}
    levels = ["clean", "minimal", "heavy", "max"]
    renders = {}
    for level in levels:
        src = Engine(db, level, annotate=False).generate(ir)["sources"]["python"]
        renders[level] = [ln for ln in src.splitlines() if ln.strip()][1:]  # drop the input line
    style = "style=levelcode"  # defined in the paper preamble
    blocks = []
    previous = None
    for level in levels:
        lines = renders[level]
        new = set()
        if previous is not None:
            matcher = difflib.SequenceMatcher(a=previous, b=lines, autojunk=False)
            for tag, _, _, j1, j2 in matcher.get_opcodes():
                if tag in ("insert", "replace"):
                    new.update(range(j1, j2))
        body = []
        for i, line in enumerate(lines):
            indent = len(line) - len(line.lstrip())
            body.append(" " * indent + f"@@{line.strip()}@@" if i in new else line)
        blocks.append((level, "\n".join(body)))
        previous = lines
    tex = ["% Generated by bench/paper_figures.py; do not edit."]
    for n, (level, body) in enumerate(blocks):
        tex.append(r"\begin{minipage}[t]{0.49\linewidth}\vspace{0pt}")
        tex.append(r"{\footnotesize\textsf{" + level + r"}}\par\vspace{2pt}")
        tex.append(r"\begin{lstlisting}[" + style + "]")
        tex.append(body)
        tex.append(r"\end{lstlisting}")
        tex.append(r"\end{minipage}" + (r"\hfill" if n % 2 == 0 else r"\par\vspace{4pt}"))
    with open(os.path.join(out_dir, "fig_levels_code.tex"), "w") as fh:
        fh.write("\n".join(tex) + "\n")


def fig_code_lines(out_dir: str) -> None:
    census = _load("rung_census.json")
    lines = census["code_lines"]
    groupings = census["distinct_renders"]["groupings_per_family"]
    families = ["config_resolver", "status_router", "fsm_transition", "allowlist",
                "agg_stats", "threshold_select", "discovery_pipeline"]
    levels = ["clean", "minimal", "light", "standard", "heavy", "max"]
    fig, ax = plt.subplots(figsize=(TEXT_WIDTH, 2.0))
    for i, family in enumerate(families):
        same = [set(group.split("=")) for group in
                next(k for k in groupings[family] if k.startswith("python:"))
                .split(": ", 1)[1].split("|")]
        for j, level in enumerate(levels):
            changed = j == 0 or not any({level, levels[j - 1]} <= group for group in same)
            ax.add_patch(plt.Rectangle((j - 0.48, i - 0.44), 0.96, 0.88, lw=0,
                                       color="#D5DAE1" if changed else "#F6F7F9"))
            ax.text(j, i, f"{lines[family][level]:.0f}", ha="center", va="center", fontsize=8.4,
                    color=INK if changed else "#9A9A9A")
    ax.set_xlim(-0.5, len(levels) - 0.5)
    ax.set_ylim(len(families) - 0.5, -0.5)
    ax.set_xticks(range(len(levels)), levels)
    ax.set_yticks(range(len(families)), families)
    ax.xaxis.tick_top()
    ax.tick_params(length=0, labelcolor=INK)
    for side in ax.spines.values():
        side.set_visible(False)
    _save(fig, out_dir, "fig_code_lines")


def fig_replication(out_dir: str) -> None:
    first = _load("annotation_ablation.json")
    rep = _load("rep1_results.json")["conditions"]
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 2.0), sharey=True,
                             gridspec_kw={"wspace": 0.08})
    for ax, (task, title) in zip(axes, (("refactor", "Refactoring"),
                                        ("comprehend", "Output prediction"))):
        for i, model in enumerate(MODELS):
            a = first[task]["per_model"][model]
            b = rep[task]["unannotated"][model]["delta_vs_annotated"]
            _dot(ax, i + 0.15, a["delta"], a["delta_ci95"], WITHOUT_COLOR, face="white")
            _dot(ax, i - 0.15, b["point"], b["ci95"], WITHOUT_COLOR, marker="D", ms=4)
        _rows(ax, MODELS, (-0.26, 0.02), [-0.25, -0.2, -0.15, -0.1, -0.05, 0])
        ax.axvline(0, color="#8A8A8A", lw=0.8)
        ax.set_title(title)
        ax.set_xlabel(r"change on removing the comments ($\Delta$)")
    _legend(fig, [_h(WITHOUT_COLOR, "white", label="first run"),
                  _h(WITHOUT_COLOR, marker="D", label="replication"), BAND], -0.2)
    _save(fig, out_dir, "fig_replication")


def fig_base_rate(out_dir: str) -> None:
    data = _load("did_robustness.json")["rescue_spoil"]["refactor"]
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 1.55), sharey=True,
                             gridspec_kw={"wspace": 0.08})
    panels = ((axes[0], "n_unannotated_fail", "Failures without comments",
               "items failed, of 1500", lambda v: f"{v:d}", 750),
              (axes[1], "rescue_rate", "Rescue rate",
               "share of those failures that pass with comments", lambda v: f"{v:.3f}", 1.0))
    for ax, key, title, xlabel, fmt, xmax in panels:
        for i, model in enumerate(MODELS):
            v = data[model][key]
            ax.barh(i, v, height=0.56, color=WITH_COLOR)
            ax.text(v + xmax * 0.012, i, fmt(v), va="center", ha="left", fontsize=8.2, color=INK)
        ax.set_yticks(range(len(MODELS)), MODELS)
        ax.set_xlim(0, xmax)
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0, labelcolor=INK)
    _save(fig, out_dir, "fig_base_rate")


def fig_comment_kinds(out_dir: str) -> None:
    data = _load("rep1_results.json")["conditions"]["refactor"]
    kinds = [("lying", "false intent comments", FALSE_COLOR, None, "D"),
             ("unannotated", "no comments", WITHOUT_COLOR, "white", "o"),
             ("markers_only", "header and markers only", PARTIAL_COLOR, None, "s"),
             ("comments_only", "intent comments only", PARTIAL_COLOR, None, "^"),
             ("annotated", "all comments", WITH_COLOR, None, "o")]
    fig, axes = plt.subplots(1, 4, figsize=(TEXT_WIDTH, 1.7), sharey=True,
                             gridspec_kw={"wspace": 0.14})
    for ax, model in zip(axes, MODELS):
        for i, (key, _, color, face, marker) in enumerate(kinds):
            s = data[key][model]["score"]
            _dot(ax, i, s["mean"], s["ci95"], color, face=face, marker=marker, ms=4.4)
        _rows(ax, [k[1] for k in kinds], (0.4, 1.03), [0.5, 0.75, 1.0])
        ax.set_title(model, fontsize=8.8)
    fig.supxlabel("semantic-equivalence rate, with $95\\%$ interval", fontsize=9, color=MUTED, y=-0.14)
    _save(fig, out_dir, "fig_comment_kinds")


def fig_static_metrics(out_dir: str) -> None:
    now = _load("anchor.json")["anchors"]
    opt = _load("anchor_v21.json")["anchors"]

    def rows(a):
        cross = a["lizard"]["cross_language"]
        return [
            ("tokens, five non-clean levels", cross["per_language_tokens"]["python"]),
            ("cyclomatic, five non-clean levels", cross["per_language"]["python"]),
            ("readability proxy", a["bw_readability"]["aggregate_density_proxy"]),
            ("maintainability index", a["radon"]["metrics"]["mi"]),
            ("cognitive complexity", a["cognitive"]["metrics"]["cognitive"]),
            ("cyclomatic complexity", a["radon"]["metrics"]["cc"]),
        ]

    fig, ax = plt.subplots(figsize=(TEXT_WIDTH * 0.72, 1.85))
    labels = [r[0] for r in rows(now)]
    for i, ((_, m), (_, o)) in enumerate(zip(rows(now), rows(opt))):
        _dot(ax, i, m["incidental_knob_spearman_mean"], m["incidental_knob_spearman_ci95"], WITH_COLOR)
        ax.plot([o["incidental_knob_spearman_mean"]], [i], marker="D", ms=4, mfc="white",
                mec=PARTIAL_COLOR, mew=1.0, ls="none", zorder=2)
        ax.text(1.02, i, f"${m['incidental_knob_spearman_mean']:+.2f}$", va="center", ha="left",
                fontsize=8, color=MUTED, transform=ax.get_yaxis_transform())
    _rows(ax, labels, (-1.0, 1.0), [-1, -0.5, 0, 0.5, 1])
    ax.axvline(0, color="#8A8A8A", lw=0.8)
    ax.set_xlabel(r"Spearman $\rho$ with messiness level")
    ax.text(1.02, 1.0, r"$\rho$", transform=ax.transAxes, fontsize=8.5, color=MUTED,
            va="bottom", ha="left")
    _legend(fig, [_h(WITH_COLOR, label="default specification"), BAND,
                  _h(PARTIAL_COLOR, "white", "D", label="opt-in specification")], -0.22)
    _save(fig, out_dir, "fig_static_metrics")


def fig_messiness(out_dir: str) -> None:
    data = _load("unannotated_slopes.json")["per_model"]
    levels = ["minimal", "standard", "max"]
    fig, axes = plt.subplots(2, 4, figsize=(TEXT_WIDTH, 2.9), sharex=True, sharey=True,
                             gridspec_kw={"wspace": 0.08, "hspace": 0.12})
    tasks = (("refactor", "Refactoring\nequivalence rate"),
             ("comprehend", "Output prediction\nexact match"))
    xs = list(range(len(levels)))
    for r, (task, label) in enumerate(tasks):
        for c, model in enumerate(MODELS):
            ax = axes[r][c]
            w = [data[task][model]["annotated_k1"]["by_knob"][k]["mean"] for k in levels]
            wo = [data[task][model]["unannotated_k1"]["by_knob"][k]["mean"] for k in levels]
            ax.fill_between(xs, w, wo, color=WITHOUT_COLOR, alpha=0.10, lw=0)
            ax.plot(xs, w, color=WITH_COLOR, lw=1.3, marker="o", ms=4.2, mew=1.0)
            ax.plot(xs, wo, color=WITHOUT_COLOR, lw=1.3, marker="o", ms=4.2, mfc="white", mew=1.0)
            ax.set_ylim(0.2, 1.03)
            ax.set_xlim(-0.12, 2.12)
            ax.set_xticks(xs)
            tick_labels = ax.set_xticklabels(levels, fontsize=7.6)
            if r == 1:
                tick_labels[0].set_ha("left")
                tick_labels[-1].set_ha("right")
            ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
            ax.yaxis.grid(True)
            ax.spines["left"].set_visible(False)
            ax.tick_params(axis="both", length=0)
            if r == 0:
                ax.spines["bottom"].set_visible(False)
                ax.set_title(model, fontsize=8.8)
            if c == 0:
                ax.set_ylabel(label)
    _legend(fig, [_h(WITH_COLOR, label="with comments"),
                  _h(WITHOUT_COLOR, "white", label="without comments")], -0.06)
    _save(fig, out_dir, "fig_messiness")


def fig_slopes(out_dir: str) -> None:
    first = _load("unannotated_slopes.json")["per_model"]
    rep = _load("rep1_results.json")["slopes"]
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 2.25), sharey=True,
                             gridspec_kw={"wspace": 0.08})
    for ax, (task, title) in zip(axes, (("refactor", "Refactoring"),
                                        ("comprehend", "Output prediction"))):
        for i, model in enumerate(MODELS):
            a = first[task][model]["annotated_k1"]
            u = first[task][model]["unannotated_k1"]
            r = rep[task]["unannotated"][model]
            _dot(ax, i + 0.22, a["slope"], a["ci95"], WITH_COLOR)
            _dot(ax, i, u["slope"], u["ci95"], WITHOUT_COLOR, face="white")
            _dot(ax, i - 0.22, r["slope"], r["ci95"], WITHOUT_COLOR, marker="D", ms=4)
        _rows(ax, MODELS, (-0.145, 0.04), [-0.12, -0.08, -0.04, 0, 0.04])
        ax.axvline(0, color="#8A8A8A", lw=0.8)
        ax.set_title(title)
        ax.set_xlabel("change in score per messiness level")
    _legend(fig, [_h(WITH_COLOR, label="with comments"),
                  _h(WITHOUT_COLOR, "white", label="without, first run"),
                  _h(WITHOUT_COLOR, marker="D", label="without, replication"), BAND], -0.17)
    _save(fig, out_dir, "fig_slopes")


def fig_size(out_dir: str) -> None:
    data = _load("g3_analysis.json")["structure_vs_computation"]
    cot = _load("rep1_results.json")["cot"]["per_model"]
    widths = ["8", "12", "16", "24", "32", "48", "64", "96", "128", "160"]
    order = list(reversed(MODELS))
    panels = (
        ("Output prediction, direct answer: exact match",
         [[data[m]["comprehend_EM_by_W"][w] for w in widths] for m in order]),
        ("Output prediction, written reasoning permitted: exact match",
         [[cot[m]["em_by_width"][w]["cot"] for w in widths] for m in order]),
        ("Refactoring, same programs: semantic-equivalence rate",
         [[data[m]["refactor_semok_by_W"][w] for w in widths] for m in order]),
    )
    fig, axes = plt.subplots(3, 1, figsize=(TEXT_WIDTH, 3.5), sharex=True,
                             gridspec_kw={"hspace": 0.34})
    im = None
    for ax, (title, grid) in zip(axes, panels):
        im = _heat(ax, grid, order, widths)
        ax.set_title(title, fontsize=9)
    axes[-1].set_xlabel("list length $W$")
    _colourbar(fig, im, list(axes), "score (white: exactly zero)")
    _save(fig, out_dir, "fig_size")


def fig_power(out_dir: str) -> None:
    data = _load("freshness_power.json")["power"]
    rows = ["pooled"] + list(reversed(MODELS))
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 1.6), sharey=True,
                             gridspec_kw={"wspace": 0.05})
    im = None
    for ax, (task, title) in zip(axes, (("refactor", "Refactoring"),
                                        ("comprehend", "Output prediction"))):
        keys = sorted(data[task]["pooled"]["cells"], key=float)
        grid = [[data[task][m]["cells"][k]["power"] for k in keys] for m in rows]
        last = len(keys) - 1
        im = _heat(ax, grid, rows, [f"{float(k):.2f}".lstrip("0") if float(k) else "0" for k in keys],
                   show=lambda i, j, v: v >= 0.8 or j == last, fmt=_two_decimals)
        for i, row in enumerate(grid):
            for j, v in enumerate(row):
                if v >= 0.8:
                    ax.add_patch(plt.Rectangle((j - 0.47, i - 0.46), 0.94, 0.92, fill=False,
                                               ec=WITHOUT_COLOR, lw=1.2))
        ax.set_title(title)
        ax.set_xlabel("injected score increase")
    _colourbar(fig, im, list(axes), "power")
    _save(fig, out_dir, "fig_power")


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    fig_levels_code(out_dir)
    fig_code_lines(out_dir)
    fig_comment_removal(out_dir)
    fig_replication(out_dir)
    fig_base_rate(out_dir)
    fig_comment_kinds(out_dir)
    fig_static_metrics(out_dir)
    fig_messiness(out_dir)
    fig_slopes(out_dir)
    fig_size(out_dir)
    fig_power(out_dir)
    print(f"wrote 11 figures to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
