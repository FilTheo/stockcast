# Utilities API

`DemandGenerator` creates declared synthetic demand paths with explicit
frequency and reproducibility settings. `process_demand` and
`update_inventory_with_orders` are the public primitives for a caller-owned
loop; use the engine when you need canonical run outputs and lifecycle
orchestration.

::: stockcast.utils
    options:
      show_root_heading: false
      members: true
