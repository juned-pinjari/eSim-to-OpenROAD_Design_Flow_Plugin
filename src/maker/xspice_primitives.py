# =====================================================================
#          FILE: xspice_primitives.py
#
#   DESCRIPTION: XSPICE digital primitive pin map lookup table.
#                Maps ngspice code model types to their port ordering
#                and corresponding Verilog primitives.
#
#        AUTHOR: Juned Pinjari
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-09-30
# =====================================================================

"""XSPICE digital primitive definitions.

Pin orderings are sourced from the ngspice manual (Chapter 12,
XSPICE Code Models). Each entry defines:

- ``pins``:    Ordered list of pin names matching the ``a``-line
               positional convention.  For combinational gates the
               special token ``"in..."`` means *N* inputs packed
               inside ``[...]`` brackets, followed by one output.
- ``verilog``: The Verilog gate primitive keyword, or a tag used
               by the emitter to select a behavioural template
               (e.g. ``"jkff"``).
- ``role``:    Present only for bridge models (``adc_bridge``,
               ``dac_bridge``).  These are not emitted as gates;
               they mark the analog/digital I/O boundary.
"""


# ── Combinational gates ────────────────────────────────────────────
# a-line format: a<N> [in1 in2 ...] out <model>
#   - inputs are inside brackets (vector)
#   - output is a single net after the bracket group
COMBINATIONAL_GATES = {
    "d_and":      {"pins": ["in...", "out"], "verilog": "and"},
    "d_nand":     {"pins": ["in...", "out"], "verilog": "nand"},
    "d_or":       {"pins": ["in...", "out"], "verilog": "or"},
    "d_nor":      {"pins": ["in...", "out"], "verilog": "nor"},
    "d_xor":      {"pins": ["in...", "out"], "verilog": "xor"},
    "d_not":      {"pins": ["in", "out"],    "verilog": "not"},
    "d_inverter": {"pins": ["in", "out"],    "verilog": "not"},
    "d_buffer":   {"pins": ["in", "out"],    "verilog": "buf"},
}


# ── Sequential elements ────────────────────────────────────────────
# a-line format: a<N> pin1 pin2 ... pinN <model>  (NO brackets)
#   - ports are strictly positional per ngspice manual
SEQUENTIAL_ELEMENTS = {
    "d_dff": {
        "pins": ["data", "clk", "set", "reset", "q", "qbar"],
        "verilog": "dff",
    },
    "d_jkff": {
        "pins": ["j", "k", "clk", "set", "reset", "q", "qbar"],
        "verilog": "jkff",
    },
    "d_srff": {
        "pins": ["s", "r", "clk", "set", "reset", "q", "qbar"],
        "verilog": "srff",
    },
    "d_tff": {
        "pins": ["t", "clk", "set", "reset", "q", "qbar"],
        "verilog": "tff",
    },
    "d_dlatch": {
        "pins": ["d", "enable", "set", "reset", "q", "qbar"],
        "verilog": "dlatch",
    },
    "d_srlatch": {
        "pins": ["s", "r", "enable", "set", "reset", "q", "qbar"],
        "verilog": "srlatch",
    },
}


# ── Bridge models (I/O boundary markers) ───────────────────────────
BRIDGE_MODELS = {
    "adc_bridge": {"role": "input_boundary"},
    "dac_bridge": {"role": "output_boundary"},
}


# ── Unified lookup ─────────────────────────────────────────────────
XSPICE_PRIMITIVES = {}
XSPICE_PRIMITIVES.update(COMBINATIONAL_GATES)
XSPICE_PRIMITIVES.update(SEQUENTIAL_ELEMENTS)
XSPICE_PRIMITIVES.update(BRIDGE_MODELS)


def is_digital_gate(model_type):
    """Return True if *model_type* is a synthesisable digital primitive."""
    return model_type in COMBINATIONAL_GATES or model_type in SEQUENTIAL_ELEMENTS


def is_combinational(model_type):
    """Return True if *model_type* is a combinational gate."""
    return model_type in COMBINATIONAL_GATES


def is_sequential(model_type):
    """Return True if *model_type* is a sequential element."""
    return model_type in SEQUENTIAL_ELEMENTS


def is_bridge(model_type):
    """Return True if *model_type* is an adc/dac bridge."""
    return model_type in BRIDGE_MODELS


def get_primitive(model_type):
    """Return the primitive dict for *model_type*, or None."""
    return XSPICE_PRIMITIVES.get(model_type)
