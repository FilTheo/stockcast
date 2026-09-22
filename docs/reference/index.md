# API reference

The namespaces below are frozen public imports for the Stockcast 0.1 release line.
The top-level `stockcast` namespace is the normal entry point. The specialist
submodules are also supported public imports, not implementation details.

| Namespace | Use it for |
|---|---|
| `stockcast` | The normal workflow: state, policies, engine, and supported extensions. |
| `stockcast.core` | Specialist engine, constraint, callback, and shelf-life objects. |
| `stockcast.policies` | Built-in policies and periodic-target provider types. |
| `stockcast.evaluation` | Event validation, evaluator, and operational metrics. |
| `stockcast.utils` | Demand generation and manual-loop primitives. |
| `stockcast.visualization` | Matplotlib plots and dashboards. |

Generated signatures and parameter documentation below are sourced from the
installed source tree. The surrounding concept and guide pages explain when a
public object belongs in a workflow.
