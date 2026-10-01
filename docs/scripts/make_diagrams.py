"""Draw the pipeline diagrams of the documentation.

Run from the repository root:

    python docs/scripts/make_diagrams.py

Every diagram is written to ``docs/assets/diagrams`` as a plain SVG. The docs
pages include them inline (``--8<-- "how-it-works.svg"``), so the colours
follow the light and dark theme through ``docs/stylesheets/extra.css``; the
README shows the same files as images, on their white card.

Colour carries the meaning, the same in every diagram:

- grey: your data (forecast, demand, stock);
- orange: your choices (policy, rules, options);
- blue: what Stockcast does;
- green: what you get.
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "assets" / "diagrams"

STYLE = """
.sc-bg{fill:#ffffff}
.sc-label-bg{fill:#ffffff}
.sc-box rect{stroke-width:1.5}
.sc-data rect{fill:#f1f0ec;stroke:#898781}
.sc-choice rect{fill:#fdeee6;stroke:#eb6834}
.sc-engine rect{fill:#e7effb;stroke:#2a78d6}
.sc-result rect{fill:#e4f5ee;stroke:#1baf7a}
.sc-optional rect{stroke-dasharray:5 4}
.sc-title{font-size:15px;font-weight:600;fill:#0b0b0b}
.sc-sub{font-size:12.5px;fill:#52514e}
.sc-head{font-size:11px;font-weight:700;letter-spacing:.08em}
.sc-head.sc-data{fill:#52514e}
.sc-head.sc-choice{fill:#c4531f}
.sc-head.sc-engine{fill:#1f5fae}
.sc-head.sc-result{fill:#12855c}
.sc-arrow{fill:none;stroke:#52514e;stroke-width:1.6}
.sc-arrowhead{fill:#52514e}
.sc-lane{fill:none;stroke:#2a78d6;stroke-width:1;stroke-dasharray:3 4;opacity:.6}
.sc-note{font-size:12px;font-style:italic;fill:#52514e}
"""

FONT = "Roboto, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"


class Diagram:
    def __init__(self, name, width, height, label):
        self.name, self.width, self.height, self.label = name, width, height, label
        self.parts = []

    def head(self, x, y, text, role, anchor="start"):
        self.parts.append(
            f'<text class="sc-head sc-{role}" x="{x}" y="{y}" '
            f'text-anchor="{anchor}">{escape(text.upper())}</text>')

    def box(self, x, y, w, h, role, title, sub=(), optional=False):
        """A rounded box with a bold title and up to a few lines of detail."""
        title = [title] if isinstance(title, str) else list(title)
        sub = [sub] if isinstance(sub, str) else list(sub)
        lines = [("sc-title", t, 19) for t in title] + [("sc-sub", s, 17) for s in sub]
        gap = 3 if sub else 0
        block = sum(step for _, _, step in lines) + gap
        cy = y + (h - block) / 2 + 13
        classes = f"sc-box sc-{role}" + (" sc-optional" if optional else "")
        texts = []
        for i, (cls, text, step) in enumerate(lines):
            if cls == "sc-sub" and i == len(title):
                cy += gap
            texts.append(f'<text class="{cls}" x="{x + w / 2}" y="{cy:.1f}" '
                         f'text-anchor="middle">{escape(text)}</text>')
            cy += step
        self.parts.append(
            f'<g class="{classes}"><rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'rx="10"/>{"".join(texts)}</g>')

    def arrow(self, *points):
        """A polyline from the first point to an arrowhead at the last one."""
        *body, (tx, ty) = points
        px, py = body[-1]
        # Stop the line at the arrowhead's base, then draw the head as a triangle.
        if px == tx:
            sign = 1 if ty > py else -1
            base = (tx, ty - 8 * sign)
            head = [(tx, ty), (tx - 5, ty - 8 * sign), (tx + 5, ty - 8 * sign)]
        else:
            sign = 1 if tx > px else -1
            base = (tx - 8 * sign, ty)
            head = [(tx, ty), (tx - 8 * sign, ty - 5), (tx - 8 * sign, ty + 5)]
        path = " ".join(f"{x},{y}" for x, y in [*body, base])
        self.parts.append(f'<polyline class="sc-arrow" points="{path}"/>')
        self.parts.append('<polygon class="sc-arrowhead" points="'
                          + " ".join(f"{x},{y}" for x, y in head) + '"/>')

    def raw(self, svg):
        self.parts.append(svg)

    def write(self):
        # No blank lines: the docs inline the file, and a blank line would end
        # the HTML block.
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" class="sc-diagram-svg" '
            f'viewBox="0 0 {self.width} {self.height}" width="{self.width}" '
            f'height="{self.height}" role="img" aria-label="{escape(self.label)}" '
            f'font-family="{FONT}">\n'
            f'<title>{escape(self.label)}</title>\n'
            f'<style>{" ".join(STYLE.split())}</style>\n'
            f'<rect class="sc-bg" width="{self.width}" height="{self.height}" rx="12"/>\n'
            + "\n".join(self.parts) + "\n</svg>\n")
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{self.name}.svg").write_text(svg)
        print("wrote", OUT / f"{self.name}.svg")


def how_it_works():
    d = Diagram("how-it-works", 920, 290,
                "How Stockcast works: your forecast feeds your policy; with your "
                "optional rules, demand and starting stock, Stockcast plays out "
                "each day and reports service, stock and cost.")
    cols = {"data": (20, 170), "choice": (240, 210), "engine": (510, 160), "result": (720, 180)}
    rows = [44, 124, 204]
    h = 64
    for role, text in [("data", "Your data"), ("choice", "Your choices"),
                       ("engine", "Stockcast"), ("result", "You get")]:
        d.head(cols[role][0] + 2, 28, text, role)

    (x1, w1), (x2, w2), (x3, w3), (x4, w4) = cols.values()
    d.box(x1, rows[0], w1, h, "data", "Forecast", "from any library")
    d.box(x1, rows[2], w1, h, "data", "Demand", "and starting stock")
    d.box(x2, rows[0], w2, h, "choice", "Policy", "when and how much to order")
    d.box(x2, rows[1], w2, h, "choice", "Rules (optional)",
          "suppliers · packs · shelf life", optional=True)
    d.box(x3, rows[0], w3, rows[2] + h - rows[0], "engine", ["Plays out", "each day"],
          ["", "1  deliveries arrive", "2  orders go out", "3  demand is met"])
    d.box(x4, rows[1], w4, h, "result", "Service, stock, cost", "and the event table")

    mid = [r + h / 2 for r in rows]
    d.arrow((x1 + w1, mid[0]), (x2, mid[0]))
    d.arrow((x2 + w2, mid[0]), (x3, mid[0]))
    d.arrow((x2 + w2, mid[1]), (x3, mid[1]))
    d.arrow((x1 + w1, mid[2]), (x3, mid[2]))
    d.arrow((x3 + w3, mid[1]), (x4, mid[1]))
    d.write()


def compare():
    d = Diagram("compare", 860, 270,
                "Comparing candidates: two candidates run on the same demand and "
                "starting stock; the results differ only because of the candidate.")
    (x1, w1), (x2, w2), (x3, w3) = (20, 220), (300, 260), (620, 230)
    rows, h = [44, 144], 64
    d.head(x1 + 2, 28, "Your candidates", "choice")
    d.head(x2 + 2, 28, "Stockcast", "engine")
    d.head(x3 + 2, 28, "You compare", "result")

    d.box(x1, rows[0], w1, h, "choice", "Candidate A", "a forecast, policy or rule")
    d.box(x1, rows[1], w1, h, "choice", "Candidate B", "another forecast, policy or rule")
    d.box(x1, rows[1] + h + 14, w1, 34, "choice", [], "+ as many as you like", optional=True)
    d.box(x2, rows[0], w2, rows[1] + h - rows[0], "engine",
          ["Same demand,", "same starting stock"], "each candidate played out day by day")
    # Illustrative numbers with a trade-off: more service costs more stock.
    d.box(x3, rows[0], w3, h, "result", "A: 98% served", "avg. 14 packs on the shelf")
    d.box(x3, rows[1], w3, h, "result", "B: 93% served", "avg. 8 packs on the shelf")

    for r in rows:
        y = r + h / 2
        d.arrow((x1 + w1, y), (x2, y))
        d.arrow((x2 + w2, y), (x3, y))
    d.write()


def production_loop():
    d = Diagram("production-loop", 938, 180,
                "The daily production loop: today's sales and deliveries update "
                "the stock; a fresh forecast and your policy give the orders to "
                "send; tomorrow it starts again.")
    w, h, y, step = 158, 70, 44, 190
    steps = [
        ("data", "Your data", "Today's sales", "and deliveries"),
        ("engine", "Stockcast", "Update the stock", "on hand, on order"),
        ("data", "Your data", "Fresh forecast", "from any library"),
        ("choice", "Your choice", "Your policy", "and supplier rules"),
        ("result", "You get", "Orders to send", "to your suppliers"),
    ]
    xs = [20 + i * step for i in range(len(steps))]
    for x, (role, head, title, sub) in zip(xs, steps):
        d.head(x + w / 2, 28, head, role, anchor="middle")
        d.box(x, y, w, h, role, title, sub)
    for a, b in zip(xs, xs[1:]):
        d.arrow((a + w, y + h / 2), (b, y + h / 2))
    low = y + h + 40
    d.arrow((xs[-1] + w / 2, y + h), (xs[-1] + w / 2, low),
            (xs[0] + w / 2, low), (xs[0] + w / 2, y + h))
    mid = (xs[0] + xs[-1] + w) / 2
    d.raw(f'<rect class="sc-label-bg" x="{mid - 48}" y="{low - 10}" width="96" height="20"/>'
          f'<text class="sc-note" x="{mid}" y="{low + 4}" text-anchor="middle">'
          'next day</text>')
    d.write()


if __name__ == "__main__":
    how_it_works()
    compare()
    production_loop()
