"""Draw the documentation figures from real Stockcast runs.

Run from the repository root:

    python docs/scripts/make_figures.py

Every figure is written to ``docs/assets/figures``. The numbers in each
figure come from the same code shown on the documentation pages, so the
pictures and the text stay in step.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyArrowPatch, Rectangle  # noqa: E402

from stockcast.core import InventoryStateDataFrame, SimulationEngine  # noqa: E402
from stockcast.policies import OrderUpToPolicy, ReorderPointPolicy  # noqa: E402
from stockcast.utils import DemandGenerator  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "assets" / "figures"

# Palette: validated categorical slots, recessive chrome.
BLUE, ORANGE, AQUA, YELLOW, VIOLET, RED = (
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#4a3aa7", "#e34948",
)
SURFACE, INK, INK2, MUTED, GRID, BASE = (
    "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7",
)

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.edgecolor": BASE,
    "axes.labelcolor": INK2,
    "axes.titlecolor": INK,
    "axes.titlesize": 11,
    "axes.titleweight": "semibold",
    "axes.titlelocation": "left",
    "axes.labelsize": 9,
    "axes.grid": True,
    "axes.grid.axis": "y",
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelcolor": INK2,
    "ytick.labelcolor": INK2,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.frameon": False,
    "legend.fontsize": 8,
    "legend.labelcolor": INK2,
    "lines.linewidth": 2,
    "lines.solid_capstyle": "round",
    "font.size": 9,
    "svg.fonttype": "path",
})


# ---------------------------------------------------------------------------
# The tea-shop scenario used throughout the Walkthrough.
# ---------------------------------------------------------------------------

SKU = "tea_250g"
OPENING = pd.Timestamp("2026-01-05")
LEAD_TIME, REVIEW_PERIOD = 2, 4
HORIZON = LEAD_TIME + REVIEW_PERIOD


def tea_demand(n_periods=56):
    demand = DemandGenerator(
        [SKU],
        first_date=OPENING + pd.Timedelta(days=1),
        freq="D",
        random_seed=3,
        negative_demand_handling="clip_zero",
    ).seasonal(n_periods=n_periods, base=6.0, amplitude=2.0, season_length=7, std=2.0)
    demand["y"] = demand["y"].round()
    return demand


def tea_paths():
    rng = np.random.default_rng(42)
    return rng.poisson(6.0, size=(10_000, HORIZON))


def tea_inventory():
    return InventoryStateDataFrame(
        [SKU], max_lead_time=LEAD_TIME, allow_backorders=False
    ).initialize_from_observed(
        pd.DataFrame({"unique_id": [SKU], "on_hand": [30.0]}),
        on_hand_column="on_hand",
        opening_date=OPENING,
    )


def tea_policy(probability):
    target_value = float(np.quantile(tea_paths().sum(axis=1), probability))
    target = pd.DataFrame({
        "unique_id": [SKU],
        "target": [target_value],
        "target_end_date": [OPENING + pd.Timedelta(days=HORIZON)],
    })
    return OrderUpToPolicy(
        lead_time=LEAD_TIME,
        review_period=REVIEW_PERIOD,
        freq="D",
        service_level=probability,
        allow_backorders=False,
        date_column="target_end_date",
    ).fit(
        target,
        target_column="target",
        forecast_origin=OPENING,
    )


def tea_run(policy, demand, **windows):
    return SimulationEngine().run(
        policy=policy,
        demand_source=demand,
        inventory=tea_inventory(),
        random_seed=3,
        **windows,
    )


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    print("wrote", OUT / name)


def date_axis(ax, dates, every=7):
    ticks = dates[::every]
    ax.set_xticks(ticks)
    ax.set_xticklabels([d.strftime("%b %d") for d in ticks])


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def fig_tea_demand():
    demand = tea_demand()
    fig, ax = plt.subplots(figsize=(8, 2.6))
    ax.bar(demand["date"], demand["y"], width=0.7, color=BLUE)
    ax.set(ylabel="Packs sold")
    ax.set_title("Daily demand for tea_250g, 56 days")
    date_axis(ax, demand["date"])
    save(fig, "tea-demand.svg")


def quickstart_runs():
    """The Quickstart: one daily policy fitted on a mean/std and on a quantile forecast."""
    today = pd.Timestamp("2026-02-01")
    days = pd.date_range("2026-01-05", periods=84, freq="D")
    sales = pd.DataFrame({
        "unique_id": SKU,
        "date": days,
        "y": np.random.default_rng(3).poisson(6, len(days)),
    })
    past, future = sales[sales["date"] <= today], sales[sales["date"] > today]
    last_4_weeks = past["y"].tail(28)
    forecast = pd.DataFrame({
        "unique_id": [SKU],
        "date": [today + pd.Timedelta(days=1)],
        "fh": [1],
        "mean": [last_4_weeks.mean()],
        "std": [last_4_weeks.std()],
        "q95": [last_4_weeks.quantile(0.95)],
    })
    shelf = InventoryStateDataFrame.from_observed(
        pd.DataFrame({"unique_id": [SKU], "date": [today], "on_hand": [30]}))
    policy = OrderUpToPolicy(lead_time=0, review_period=1, freq="D",
                             service_level=0.95, allow_backorders=False)
    runs = {}
    for label, fit_args in (
        ("mean + std", {"mean_column": "mean", "std_column": "std"}),
        ("quantile", {"target_column": "q95"}),
    ):
        policy.fit(forecast, **fit_args)
        level = float(policy.get_target_levels()["target_level"].iloc[0])
        result = SimulationEngine().run(policy=policy, demand_source=future, inventory=shelf)
        runs[label] = (level, result.to_event_frame())
    return past, runs


def fig_quickstart_run():
    past, runs = quickstart_runs()
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(8.6, 4.8), sharex=True,
        gridspec_kw={"height_ratios": [1, 1.3], "hspace": 0.35},
    )
    events = runs["mean + std"][1]
    ax1.bar(past["date"], past["y"], width=0.7, color=BASE, label="Past sales")
    ax1.bar(events["date"], events["demand"], width=0.7, color=BLUE, label="Simulated weeks")
    ax1.axvline(past["date"].iloc[-1] + pd.Timedelta(hours=12), color=INK2, linewidth=1)
    ax1.set_title("Daily sales: four weeks to forecast from, eight to simulate")
    ax1.set_ylabel("Packs")
    ax1.set_ylim(0, past["y"].max() * 1.45 + 2)
    ax1.legend(loc="upper left", ncol=2)

    for (label, (level, frame)), color in zip(runs.items(), (BLUE, ORANGE)):
        ax2.plot(frame["date"], frame["ending_on_hand"], color=color,
                 label=f"{label} (S = {level:.2f})")
    ax2.set_title("Stock on the shelf at the end of each day")
    ax2.set_ylabel("Packs")
    ax2.set_ylim(0, 30)
    ax2.legend(loc="upper right", ncol=1)
    date_axis(ax2, pd.concat([past["date"], events["date"]]).reset_index(drop=True))
    save(fig, "quickstart-run.svg")


def fig_cumulative_vs_summed():
    paths = tea_paths()
    totals = paths.sum(axis=1)
    q_total = np.quantile(totals, 0.95)
    q_summed = np.quantile(paths, 0.95, axis=0).sum()
    fig, ax = plt.subplots(figsize=(8, 3))
    bins = np.arange(totals.min() - 0.5, max(totals.max(), q_summed) + 1.5)
    ax.hist(totals, bins=bins, color=BLUE, alpha=0.85, rwidth=0.85)
    top = ax.get_ylim()[1]
    ax.axvline(q_total, color=INK, linewidth=2)
    ax.axvline(q_summed, color=ORANGE, linewidth=2)
    ax.text(q_total + 0.6, top * 0.92, f"95% quantile of the total = {q_total:.0f}",
            color=INK, fontsize=8.5)
    ax.text(q_summed - 0.6, top * 0.5, f"sum of six daily\n95% quantiles = {q_summed:.0f}",
            color=INK2, fontsize=8.5, ha="right")
    ax.set(xlabel="Total demand over the 6-day window (packs)", ylabel="Sample paths")
    ax.set_title("10,000 forecast paths, summed over the protection window")
    save(fig, "cumulative-vs-summed.svg")


def fig_timing_window():
    L, R = LEAD_TIME, REVIEW_PERIOD
    n = L + R + L + 1
    fig, ax = plt.subplots(figsize=(8.6, 2.9))
    ax.set_xlim(-0.6, n + 0.2)
    ax.set_ylim(-1.35, 2.35)
    ax.axis("off")
    ax.grid(False)
    for k in range(n):
        covered = k < L + R
        ax.add_patch(Rectangle((k + 0.05, 0), 0.9, 0.62,
                               facecolor="#cde2fb" if covered else "#f0efec",
                               edgecolor="none"))
        # Three thin phase bands inside each period: receive, decide, demand.
        for j, color in enumerate((AQUA, ORANGE, BLUE)):
            ax.add_patch(Rectangle((k + 0.1 + j * 0.28, 0.06), 0.24, 0.1,
                                   facecolor=color, edgecolor="none"))
        label = "t" if k == 0 else f"t+{k}"
        ax.text(k + 0.5, -0.28, label, ha="center", va="center", color=INK2, fontsize=9)
    # Covered window bracket.
    ax.plot([0.05, L + R - 0.05], [0.95, 0.95], color=BLUE, linewidth=2)
    for x in (0.05, L + R - 0.05):
        ax.plot([x, x], [0.8, 0.95], color=BLUE, linewidth=2)
    ax.text((L + R) / 2, 1.1, f"window protected by today's order: H = L + R = {L + R} periods",
            ha="center", color=INK, fontsize=9)

    def arrow(start, end, y, text, color):
        ax.add_patch(FancyArrowPatch((start + 0.38, y), (end + 0.2, y),
                                     arrowstyle="-|>", mutation_scale=12,
                                     color=color, linewidth=1.6,
                                     connectionstyle="arc3,rad=-0.25"))
        ax.text((start + end) / 2 + 0.3, y + 0.62, text, ha="center", color=INK2, fontsize=8.5)

    arrow(0, L, 1.35, f"order at t arrives at t+L (L = {L})", ORANGE)
    arrow(R, R + L, 1.35, "next order arrives at t+R+L", MUTED)
    ax.text(R + 0.5, -0.62, f"next decision (R = {R})", ha="center", color=INK2, fontsize=8.5)
    handles = [Rectangle((0, 0), 1, 1, color=c) for c in (AQUA, ORANGE, BLUE)]
    ax.legend(handles, ["1. receive due stock", "2. decide and order", "3. meet demand"],
              loc="lower center", bbox_to_anchor=(0.5, -0.12), ncol=3)
    save(fig, "timing-window.svg")


def fig_timing_conventions():
    """One physical order, described in three conventions."""
    rows = [
        ("Decide before demand (Stockcast)", "decides at the start of t+1,  lead_time = 2", 1.5),
        ("Order after demand, L counts the ordering period", "decides at the end of t,  L = 3", 0.95),
        ("Order after demand, arrival at the start of t + l + 1", "decides at the end of t,  l = 2", 0.95),
    ]
    n, arrival = 5, 3
    fig, axes = plt.subplots(len(rows), 1, figsize=(8.6, 4.9), sharex=True)
    for ax, (title, note, decision_x) in zip(axes, rows):
        ax.set_xlim(-0.2, n + 0.1)
        ax.set_ylim(-0.45, 1.25)
        ax.axis("off")
        ax.grid(False)
        for k in range(n):
            ax.add_patch(Rectangle((k + 0.05, 0), 0.9, 0.5, facecolor="#f0efec", edgecolor="none"))
            for j, color in enumerate((AQUA, ORANGE, BLUE)):
                ax.add_patch(Rectangle((k + 0.1 + j * 0.28, 0.06), 0.24, 0.1,
                                       facecolor=color, edgecolor="none"))
            ax.text(k + 0.5, -0.22, "t" if k == 0 else f"t+{k}", ha="center",
                    va="center", color=INK2, fontsize=8.5)
        ax.text(-0.15, 1.12, title, ha="left", va="center", color=INK, fontsize=9.5,
                fontweight="semibold")
        ax.text(-0.15, 0.86, note, ha="left", va="center", color=INK2, fontsize=8.5)
        ax.plot([decision_x], [0.33], marker="D", markersize=8, color=ORANGE,
                markeredgecolor=SURFACE, markeredgewidth=1.5)
        ax.add_patch(FancyArrowPatch((decision_x + 0.08, 0.36), (arrival + 0.22, 0.36),
                                     arrowstyle="-|>", mutation_scale=11, color=INK2,
                                     linewidth=1.4, connectionstyle="arc3,rad=-0.12"))
        ax.plot([arrival + 0.22], [0.33], marker="o", markersize=8, color=AQUA,
                markeredgecolor=SURFACE, markeredgewidth=1.5)
    axes[-1].text(arrival + 0.22, -0.5, "goods on the shelf before demand at t+3",
                  ha="center", va="center", color=INK2, fontsize=8.5)
    handles = [Rectangle((0, 0), 1, 1, color=c) for c in (AQUA, ORANGE, BLUE)] + [
        plt.Line2D([], [], linestyle="none", marker="D", markersize=7, color=ORANGE),
        plt.Line2D([], [], linestyle="none", marker="o", markersize=7, color=AQUA),
    ]
    fig.legend(handles, ["receive due stock", "decide and order", "meet demand",
                         "the decision", "the order arrives"],
               loc="lower center", bbox_to_anchor=(0.5, -0.02), ncol=5)
    fig.subplots_adjust(hspace=0.05, bottom=0.1)
    save(fig, "timing-conventions.svg")


def fig_tea_run():
    demand = tea_demand()
    policy = tea_policy(0.95)
    target = float(policy.get_target_levels()["target_level"].iloc[0])
    events = tea_run(policy, demand).to_event_frame()
    dates = events["date"]
    orders = events[events["order_quantity"] > 0]
    receipts = events[events["received_units"] > 0]

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(8.6, 6.4), sharex=True,
        gridspec_kw={"height_ratios": [1, 1.6, 1.3], "hspace": 0.35},
    )
    ax1.bar(dates, events["demand"], width=0.7, color=BLUE)
    ax1.set_title("Demand")
    ax1.set_ylabel("Packs")

    ax2.plot(dates, events["ending_on_hand"], color=BLUE, label="On hand (end of day)")
    ax2.scatter(receipts["date"], receipts["ending_on_hand"], s=48, color=AQUA,
                edgecolor=SURFACE, linewidth=2, zorder=5, label="Delivery received")
    ax2.set_title("Stock on the shelf")
    ax2.set_ylabel("Packs")
    ax2.legend(loc="upper right", ncol=2)

    ax3.plot(dates, events["inventory_position_end"], color=VIOLET,
             label="Inventory position (end of day)")
    ax3.axhline(target, color=INK2, linewidth=1.2)
    ax3.text(dates.iloc[-1], target + 1.2, f"S = {target:.0f}", ha="right", color=INK2, fontsize=8.5)
    ax3.scatter(orders["date"], orders["inventory_position_end"], s=48, color=ORANGE,
                edgecolor=SURFACE, linewidth=2, zorder=5, label="Order placed")
    ax3.set_title("Inventory position = on hand + on order − backorders")
    ax3.set_ylabel("Packs")
    ax3.set_ylim(0, target + 8)
    ax3.legend(loc="lower right", ncol=2)
    date_axis(ax3, dates)
    save(fig, "tea-run.svg")


def fig_tea_compare():
    demand = tea_demand()
    rows = []
    for probability in (0.5, 0.8, 0.95):
        summary = tea_run(tea_policy(probability), demand).summary()
        rows.append((probability, summary["stockout_periods"],
                     float(summary["mean_ending_on_hand_per_sku_period"])))
    labels = [f"{p:.2f}" for p, _, _ in rows]
    fig, (left, right) = plt.subplots(1, 2, figsize=(8.6, 2.8))
    left.bar(labels, [r[1] for r in rows], width=0.45, color=BLUE)
    left.set_title("Days with a stockout")
    left.set_xlabel("Target probability")
    left.set_ylim(0, max(r[1] for r in rows) + 1.5)
    for i, r in enumerate(rows):
        left.text(i, r[1] + 0.15, f"{r[1]}", ha="center", color=INK, fontsize=8.5)
    right.bar(labels, [r[2] for r in rows], width=0.45, color=ORANGE)
    right.set_title("Average stock on hand (packs)")
    right.set_xlabel("Target probability")
    right.set_ylim(0, max(r[2] for r in rows) + 3)
    for i, r in enumerate(rows):
        right.text(i, r[2] + 0.3, f"{r[2]:.1f}", ha="center", color=INK, fontsize=8.5)
    fig.subplots_adjust(wspace=0.3)
    save(fig, "tea-compare.svg")


def fig_reorder_point():
    demand = tea_demand()
    horizon = LEAD_TIME + 1
    rng = np.random.default_rng(42)
    s = float(np.quantile(rng.poisson(6.0, size=(10_000, horizon)).sum(axis=1), 0.95))
    targets = pd.DataFrame({
        "unique_id": [SKU],
        "s": [s],
        "s_end_date": [OPENING + pd.Timedelta(days=horizon)],
    })
    policy = ReorderPointPolicy(
        lead_time=LEAD_TIME, review_period=1, freq="D", policy_type="sQ",
        service_level=0.95, order_quantity=30.0,
        allow_backorders=False,
        date_column="s_end_date",
    ).fit(
        targets, forecast_origin=OPENING,
        reorder_point_column="s",
    )
    events = tea_run(policy, demand).to_event_frame()
    dates = events["date"]
    position_before = events["decision_inventory_position"]
    orders = events[events["order_quantity"] > 0]
    fig, ax = plt.subplots(figsize=(8.6, 3))
    ax.plot(dates, position_before, color=VIOLET,
            label="Inventory position when the rule is checked")
    ax.axhline(s, color=INK2, linewidth=1.2)
    ax.text(dates.iloc[-1], s + 1, f"s = {s:.0f}", ha="right", color=INK2, fontsize=8.5)
    ax.scatter(orders["date"], orders["decision_inventory_position"], s=48, color=ORANGE,
               edgecolor=SURFACE, linewidth=2, zorder=5, label="At or below s: order Q = 30")
    ax.set_ylabel("Packs")
    ax.set_title("(s, Q) with daily review: order a fixed Q whenever the position falls to s")
    ax.legend(loc="upper right", ncol=2)
    date_axis(ax, dates)
    save(fig, "reorder-point.svg")


def fig_newsvendor():
    from statistics import NormalDist

    price, cost, salvage = 10.0, 4.0, 2.0
    alpha = (price - cost) / (price - salvage)
    dist = NormalDist(40, 40 ** 0.5)
    x = np.linspace(20, 60, 400)
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.plot(x, [dist.cdf(v) for v in x], color=BLUE)
    q = dist.inv_cdf(alpha)
    ax.plot([x[0], q], [alpha, alpha], color=INK2, linewidth=1.2)
    ax.plot([q, q], [0, alpha], color=INK2, linewidth=1.2)
    ax.scatter([q], [alpha], s=52, color=ORANGE, edgecolor=SURFACE, linewidth=2, zorder=5)
    ax.text(q + 1.2, alpha - 0.12,
            f"critical fractile α = (p − c)/(p − v) = {alpha:.2f}\norder q* = F⁻¹(α) ≈ {q:.0f}",
            color=INK, fontsize=8.5)
    ax.set(xlabel="Demand on the selling day (sandwiches)", ylabel="F(q) = P(D ≤ q)",
           ylim=(0, 1.02))
    ax.set_title("Newsvendor: buy up to the demand quantile at the critical fractile")
    save(fig, "newsvendor.svg")


def fig_run_windows():
    demand = tea_demand(28)
    colors = {"warmup": "#f0efec", "scoring": "#cde2fb", "settlement": "#fbe0d3"}
    result = tea_run(tea_policy(0.95), demand, warmup_periods=4, settlement_periods=8,
                     order_during_settlement=False)
    events = result.to_event_frame()
    fig, ax = plt.subplots(figsize=(8.6, 2.6))
    for window, rows in events.groupby("run_window", sort=False):
        ax.axvspan(rows["date"].min() - pd.Timedelta(hours=12),
                   rows["date"].max() + pd.Timedelta(hours=12),
                   color=colors[window], zorder=0)
        ax.text(rows["date"].min() + (rows["date"].max() - rows["date"].min()) / 2,
                16.2, window, ha="center", color=INK2, fontsize=9)
    ax.bar(events["date"], events["demand"], width=0.7, color=BLUE, zorder=2)
    ax.set_ylim(0, 17.5)
    ax.set_ylabel("Packs")
    ax.set_title("Warm-up, scoring and settlement windows (4 + 16 + 8 = 28 periods)")
    date_axis(ax, events["date"])
    save(fig, "run-windows.svg")


def fig_dashboard():
    from stockcast.visualization import plot_simulation_dashboard

    result = tea_run(tea_policy(0.8), tea_demand())
    axes = plot_simulation_dashboard(result, sku=SKU, figsize=(10, 8))
    save(axes[0].figure, "dashboard.svg")


if __name__ == "__main__":
    fig_quickstart_run()
    fig_tea_demand()
    fig_cumulative_vs_summed()
    fig_timing_window()
    fig_timing_conventions()
    fig_tea_run()
    fig_tea_compare()
    fig_reorder_point()
    fig_newsvendor()
    fig_run_windows()
    fig_dashboard()
