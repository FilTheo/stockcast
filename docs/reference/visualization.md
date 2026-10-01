# Plots

Ready-made Matplotlib views of runs and comparisons. Guide: [Plots](../user-guide/visualization.md).

Each picture below is drawn by the function above it on the tea shop from the
[Walkthrough](../learn/index.md): one run with an 80% target, and a
comparison of the 80% and 95% targets on the same demand.

::: stockcast.visualization.plot_inventory

<figure markdown="span">
  ![On-hand stock over eight weeks, with the two stockout days shaded](../assets/figures/plot-inventory.svg)
  <figcaption><code>plot_inventory(result, sku="tea_250g")</code>: on-hand stock, with stockout periods shaded</figcaption>
</figure>

::: stockcast.visualization.plot_demand_vs_orders

<figure markdown="span">
  ![Daily demand and the orders placed every four days](../assets/figures/plot-demand-vs-orders.svg)
  <figcaption><code>plot_demand_vs_orders(result, sku="tea_250g")</code>: daily demand and the order placed at each review</figcaption>
</figure>

::: stockcast.visualization.plot_simulation_dashboard

<figure markdown="span">
  ![Four-panel dashboard of one run](../assets/figures/dashboard.svg)
  <figcaption><code>plot_simulation_dashboard(result, sku="tea_250g")</code>: stock with the target level, demand and orders, shortage, and cumulative shortage</figcaption>
</figure>

::: stockcast.visualization.plot_comparison

<figure markdown="span">
  ![On-hand stock of two policies on the same demand](../assets/figures/plot-comparison.svg)
  <figcaption><code>plot_comparison(comparison, metric="on_hand", sku="tea_250g", ax=ax)</code>, with a title set on <code>ax</code></figcaption>
</figure>

::: stockcast.visualization.plot_summary_comparison

<figure markdown="span">
  ![Bars of fill rate and demand-period service level for two policies](../assets/figures/plot-summary-comparison.svg)
  <figcaption><code>plot_summary_comparison(comparison, metrics=["fill_rate", "demand_period_service_level"])</code>, with the legend moved outside</figcaption>
</figure>

::: stockcast.visualization.plot_comparison_dashboard

<figure markdown="span">
  ![Three-panel comparison of two policies](../assets/figures/plot-comparison-dashboard.svg)
  <figcaption><code>plot_comparison_dashboard(comparison, sku="tea_250g")</code>: stock, cumulative shortage, and fill rate over time</figcaption>
</figure>
