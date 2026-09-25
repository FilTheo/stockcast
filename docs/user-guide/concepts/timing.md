# Timing: receive, decide, demand

Inventory results depend on *when* things happen inside a period. Does a
delivery arrive before or after today's customers? Does the policy see
today's sales before it orders? Stockcast answers these questions once, writes
the answer down, and uses it everywhere. This page is that answer.

## The sequence of events

Every period runs the same steps, in this order:

| Step | What happens | Who acts |
|---|---|---|
| **1. Open** | Before-demand flows of inventory processes, such as expired lots leaving the shelf | `InventoryProcess.before_demand` (e.g. `ShelfLife`) |
| **2. Receive** | Deliveries due this period arrive; old backorders are served first | engine (supplier `DeliveryOutcome`s decide what arrives) |
| **3. Decide** | On a scheduled period, the policy proposes an order from the pre-demand state; callbacks, constraints, and the supply model shape it | `DecisionSchedule`, policy, `on_after_prediction`, `OrderingConstraints`, `SupplyModel` |
| **4. Place** | $L > 0$: the order enters the pipeline. $L = 0$: it is received now | engine |
| **5. Meet demand** | Serve customers from the shelf; the rest is lost or backordered | engine |
| **6. Close** | After-demand process flows, then stock adjustments from callbacks | `InventoryProcess.after_demand`, `on_after_demand` |
| **7. Record** | One ledger row per SKU, with every balance identity checked | engine |

In one sentence: **receive, decide, then meet demand.** This is the classic
periodic-review convention in the inventory literature: receive outstanding
orders, review and order, then observe demand (see for example Zhu, 2022,
Section 3).

### What the policy sees

At step 3 the policy sees the state *after* today's deliveries and *before*
today's demand. Current-period flow fields are reset and the policy receives a
copy of the state, so today's demand is never visible when the decision is
made. The inventory position it sees is recorded in the ledger as
`decision_inventory_position`.

### When an order arrives

**An order placed in period $t$ is received at the start of period $t + L$,
before that period's demand.**

| $L$ | Placed in | Received | Can serve demand of |
|---:|---|---|---|
| 0 | period $t$ | period $t$, immediately after the decision | period $t$ |
| 1 | period $t$ | start of period $t + 1$ | period $t + 1$ onwards |
| 2 | period $t$ | start of period $t + 2$ | period $t + 2$ onwards |

Zero lead time is a legitimate and well-studied case: goods ordered in the
morning are on the shelf before the shop opens (Dubois, Allaert and Witlox,
2013). A zero-lead-time order is recorded both as an order and as a receipt,
and never enters the pipeline.

## The protection horizon

Now the central result. Take a periodic policy that reviews every $R$ periods
with lead time $L$, and a decision at period $t$.

![Timing of one decision](../../assets/figures/timing-window.svg)

- The order placed now arrives at $t + L$.
- The next decision is at $t + R$. Its order arrives at $t + R + L$.
- Nothing ordered later can arrive earlier than that.

So the inventory position right after today's order,

$$
\mathit{IP}_t^{+} = \mathit{OH}_t + P_t + q_t - B_t ,
$$

is all the stock that will be available for demand in periods
$t, t+1, \dots, t+R+L-1$. That window has

$$
\boxed{H = L + R}
$$

periods. With backorders, the last period of the window ends without a
shortage exactly when total demand over the window is at most
$\mathit{IP}_t^{+}$:

$$
\Pr(\text{no shortage at } t + H - 1)
= \Pr\!\left(\sum_{h=0}^{H-1} D_{t+h} \le \mathit{IP}_t^{+}\right).
$$

An order-up-to policy sets $\mathit{IP}_t^{+} = S$. Choosing $S$ as the
$\alpha$-quantile of total demand over the window,

$$
S = Q_\alpha\!\left(\sum_{h=0}^{H-1} D_{t+h}\right),
$$

makes that probability $\alpha$. This is the familiar "lead time plus review
period" protection interval of periodic review (Graves; Silver, Pyke and
Thomas). The event sequence above fixes its exact endpoints. With lost sales
the same target is the standard, widely used approximation.

| $L$ | $R$ | $H$ | Periods covered by a decision at $t$ |
|---:|---:|---:|---|
| 0 | 1 | 1 | $t$ |
| 1 | 1 | 2 | $t,\ t+1$ |
| 2 | 4 | 6 | $t, \dots, t+5$ (the tea shop) |
| 7 | 7 | 14 | two weeks: one of lead time, one until the next order |

!!! note "Coverage is about the position, not the shelf"

    The window starts at $t$, before today's order arrives. Demand in
    periods $t, \dots, t+L-1$ is served from stock already on the shelf or
    already on its way. A higher target today cannot help those first $L$
    periods. This is why the opening stock matters so much at the start of a
    run.

### Irregular schedules

The same argument works for any deterministic schedule. If the next decision
after $t$ is at $u$, the order placed at $t$ must cover the periods until the
order placed at $u$ arrives:

$$
H_t = (u - t) + L .
$$

With `ExplicitSchedule([0, 3, 10])` and $L = 2$, the decision at 0 covers
$3 + 2 = 5$ periods and the decision at 3 covers $7 + 2 = 9$. Different
decisions need targets for different windows; you supply one fitted policy per
decision with `policy_schedule`. When there is no next decision, you state the
final window explicitly. See [Decision schedules](../decision-schedules.md).

### Reorder points

A reorder-point policy asks, at each opportunity, "should I order now?" If it
does not order at $t$, the earliest help arrives from an order at $u$, at
$u + L$. So its reorder point must cover the same window,
$H_t = (u - t) + L$. With review every period ($u = t + 1$):

$$
H = L + 1 .
$$

That extra period, compared with continuous-review textbook formulas that use
$L$ alone, is the price of reviewing once per period before demand. Notebook
[05b](../../notebooks/05b_reorder_points_and_review_frequency.ipynb) shows it
numerically. See [Reorder point](../policies/reorder-point.md).

## Information: what a forecast may know

A decision in period $t$ happens before period $t$'s demand. The latest demand
it can know is that of period $t - 1$. So the **forecast origin** for a
decision in period $t$ is

$$
\text{origin}_t = \text{opening date} + t\,\Delta ,
$$

that is, the date of period $t - 1$, or the opening date for $t = 0$. The
forecast's first step is period $t$, and a target for horizon $H$ ends at
$\text{origin}_t + H\Delta$. Stockcast checks these dates when you fit a
policy, and again before the run starts for every scheduled decision, so each
target lines up with
the decision it is used for.

## Two period counters

The ledger carries two period columns, for two different jobs:

| Column | Counts | First value | Used by |
|---|---|---|---|
| `demand_period` | Rows of your demand table | 0 | Decision schedules, `policy_schedule` keys, the demand table's `period` |
| `period` | Steps of the inventory state | opening period + 1 | The state, callbacks (`CallbackContext.period`), `current_period` in `predict` |

With an opening state at period 0, `period = demand_period + 1`. Callbacks can
also match on `date`, which is often the simplest choice.

## Other conventions you may meet

Simulators differ in where they place the decision inside a period. Stockpyl,
for example, observes demand before ordering, and SimOpt's $(s, S)$ model
orders at the end of a period and receives at the beginning of period
$n + l + 1$. Each convention is valid; the same number labelled "lead time"
simply means slightly different things in each. When you compare results
across tools, compare the event order first. Stockcast's order is the one on
this page, everywhere, and the manifest of every run records it as
`timing_convention`.

## How this is tested

The timing is pinned down by tests that compare the engine against an
independent, hand-written calendar of orders, arrivals, stock, and backorders
([`tests/unit/test_decision_timing.py`](https://github.com/FilTheo/stockcast/blob/main/tests/unit/test_decision_timing.py)).
They cover lead times $L = 0, 1, 2$ with review periods $R = 1, 2, 3$ in both
shortage modes, irregular and one-time schedules, reorder-point windows,
zero-lead-time receipts with shelf life and backorders, constraints before
receipt, and the rule that a decision never sees current demand. A separate
reference model
([`tests/unit/test_inventory_reference_model.py`](https://github.com/FilTheo/stockcast/blob/main/tests/unit/test_inventory_reference_model.py))
checks positive-lead-time runs and their evaluated costs.

## References

- Zhu, H. (2022). A simple heuristic policy for stochastic inventory systems
  with both minimum and maximum order quantity requirements. *Annals of
  Operations Research*, 309, 347–363.
  [doi:10.1007/s10479-021-04441-1](https://doi.org/10.1007/s10479-021-04441-1)
- Dubois, T., Allaert, G., and Witlox, F. (2013). Determining the fill rate
  for a periodic review inventory policy with capacitated replenishments,
  lost sales and zero lead time. *Operations Research Letters*, 41(6),
  726–729. [doi:10.1016/j.orl.2013.10.006](https://doi.org/10.1016/j.orl.2013.10.006)
- Graves, S. C. *Manufacturing system and supply chain design*, MIT
  OpenCourseWare 15.763J (2005), [summary slides](https://ocw.mit.edu/courses/15-763j-manufacturing-system-and-supply-chain-design-spring-2005/e1800430fdccaf618b22bd551fc00fac_summary.pdf).
- Silver, E. A., Pyke, D. F., and Thomas, D. J. (2017). *Inventory and
  Production Management in Supply Chains* (4th ed.). CRC Press.
- [Stockpyl simulation: sequence of events](https://stockpyl.readthedocs.io/en/latest/tutorial/tutorial_sim.html#sequence-of-events)
- [SimOpt: (s, S) inventory model](https://simopt.readthedocs.io/en/development/models/sscont.html)

**Go deeper:** [Decisions happen before demand](../design/decide-before-demand.md) ·
[Notebook 02b: decision schedules](../../notebooks/02b_decision_schedules.ipynb)
