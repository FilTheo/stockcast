# Decisions happen before demand

*A deep dive from our [Philosophy](../../get-started/philosophy.md).*

**Decision.** Within every period, Stockcast receives due deliveries, then lets
the policy decide, then serves demand. An order placed in period $t$ arrives at
the start of period $t + L$.

## Why

**Lead time gets one exact meaning.** "Lead time" hides a choice: does an order
placed today with $L = 1$ serve tomorrow's customers, or the day after's? It
depends on where the decision sits inside the period. Fixing the sequence
fixes the answer: with $L = 1$, tomorrow. With $L = 0$, today, which makes
same-day replenishment (a bakery, a kiosk, a warehouse with morning
deliveries) a first-class case rather than an edge case.

**The protection window follows directly.** With receive → decide → demand,
a periodic decision covers exactly $H = L + R$ periods of demand, the
classic protection interval of periodic review. The derivation is a few lines
([Timing](../concepts/timing.md#the-protection-horizon)), and every policy,
target check, and notebook uses the same window.

**Decisions use only past information.** The policy decides before today's
demand is known, like a real buyer placing a morning order. The engine hides
current demand from the policy, and forecast origins are checked against the
decision dates. A backtest therefore cannot accidentally peek at the answer.

**It matches the literature.** Receive outstanding orders, review and order,
then observe demand is a standard event order in periodic-review models (for
example Zhu, 2022, Section 3). Zero lead time is a well-studied case too
(Dubois, Allaert and Witlox, 2013).

## What it means for you

- Decide `lead_time` as "periods until the goods can serve customers". `0` is
  valid.
- Expect $H = L + R$ for order-up-to targets and $H = L + 1$ for reorder
  points checked every period.
- A forecast used at period $t$ should end at the previous period, which the
  engine checks for you.
- Other simulators may place the decision elsewhere in the period. When you
  compare numbers across tools, compare the event order first.

## A choice among valid ones

Ordering after demand, or at the end of a period, are also valid models;
Stockpyl and SimOpt use such conventions. Stockcast chose one clear sequence
and applies it everywhere, which is what makes experiments comparable. Earlier
pre-release versions of Stockcast decided after demand; the
[upgrade guide](../../how-to/timing-migration.md) explains the change.

**References:** see [Timing](../concepts/timing.md#references).
