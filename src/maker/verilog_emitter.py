# =====================================================================
#          FILE: verilog_emitter.py
#
#   DESCRIPTION: Generates synthesizable Verilog from a DigitalCircuit
#                extracted by circuit_graph.py.  Combinational gates use
#                structural primitives; sequential elements use
#                behavioral ``always`` blocks.
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-09-30
#      REVISION: 2026-10-03 — Added behavioral sequential emission
# =====================================================================

"""Verilog code emitter for eSim digital circuits.

Produces synthesizable Verilog:
- Combinational gates → structural primitives (``and``, ``or``, etc.)
- Sequential elements → behavioral ``always`` blocks (``d_jkff``, etc.)
"""


def emit_verilog(circuit):
    """Generate a Verilog module string from a DigitalCircuit.

    Parameters
    ----------
    circuit : circuit_graph.DigitalCircuit

    Returns
    -------
    str
        Complete Verilog module source.
    """
    lines = []

    # ── Pre-compute which output ports are driven by always blocks ─
    # These need ``output reg`` instead of ``output`` in Verilog-2005.
    seq_regs = set()
    for gate in circuit.gates:
        if gate.pin_map is not None:
            q = gate.pin_map.get("q")
            if q and not _is_constant(q):
                seq_regs.add(q)

    # ── Module header ─────────────────────────────────────────────
    if getattr(circuit, "needs_escape", False):
        lines.append(f"module \\{circuit.module_name} (")
    else:
        lines.append(f"module {circuit.module_name} (")

    port_decls = []
    for port in circuit.ports:
        if port.direction == "output" and port.name in seq_regs:
            port_decls.append(f"    output reg {port.name}")
        else:
            port_decls.append(f"    {port.direction} {port.name}")

    lines.append(",\n".join(port_decls))
    lines.append(");")
    lines.append("")

    # ── Wire declarations ─────────────────────────────────────────
    if circuit.wires:
        for wire in circuit.wires:
            lines.append(f"wire {wire};")
        lines.append("")

    # ── Reg declarations for internal sequential nets ─────────────
    # Only needed for sequential outputs that are NOT module ports
    # (e.g. internal flip-flop outputs feeding other gates).
    port_names = {p.name for p in circuit.ports}
    internal_regs = seq_regs - port_names
    if internal_regs:
        for reg_name in sorted(internal_regs):
            lines.append(f"reg {reg_name};")
        lines.append("")

    # ── Gate instantiations / behavioral blocks ───────────────────
    for gate in circuit.gates:
        if gate.pin_map is not None:
            # Sequential element — emit behavioral always block
            block = _emit_sequential(gate)
            if block:
                lines.append(block)
        else:
            # Combinational gate — emit structural primitive
            line = _emit_combinational(gate)
            if line:
                lines.append(line)

    lines.append("")
    lines.append("endmodule")
    lines.append("")

    return "\n".join(lines)


# ── Combinational Emission ────────────────────────────────────────

def _emit_combinational(gate):
    """Emit a single Verilog gate primitive instantiation.

    Format: ``<type> <inst> (<output>, <input1>, <input2>, ...);``

    Verilog gate primitives put the output first.
    """
    gate_type = gate.gate_type

    if gate_type in ("and", "nand", "or", "nor", "xor", "xnor"):
        if not gate.outputs or not gate.inputs:
            return None
        output = gate.outputs[0]
        inputs = ", ".join(gate.inputs)
        return f"{gate_type} {gate.inst_name} ({output}, {inputs});"

    if gate_type in ("not", "buf"):
        if not gate.outputs or not gate.inputs:
            return None
        output = gate.outputs[0]
        inp = gate.inputs[0]
        return f"{gate_type} {gate.inst_name} ({output}, {inp});"

    return f"// WARNING: unhandled combinational gate type: {gate_type}"


# ── Sequential Emission ──────────────────────────────────────────

def _is_constant(net_name):
    """Check if a net name is a Verilog constant literal."""
    return net_name in ("1'b0", "1'b1")


def _emit_sequential(gate):
    """Emit a behavioral always block for a sequential element.

    Dispatches to the appropriate template based on ``gate.gate_type``.
    """
    emitters = {
        "jkff":    _emit_jkff,
        "dff":     _emit_dff,
        "tff":     _emit_tff,
        "srff":    _emit_srff,
        "dlatch":  _emit_dlatch,
        "srlatch": _emit_srlatch,
    }
    emitter = emitters.get(gate.gate_type)
    if emitter is None:
        return f"// WARNING: unsupported sequential type: {gate.gate_type}"
    return emitter(gate)


def _emit_async_header(clk, set_pin, reset_pin):
    """Build the sensitivity list and async set/reset preamble.

    Returns (sensitivity_str, async_lines) where async_lines is a list
    of strings for the if/else-if blocks handling async set and reset.
    Constant tie-offs (1'b0) are omitted from the sensitivity list.
    """
    sens = [f"posedge {clk}"]
    async_lines = []

    has_set = not _is_constant(set_pin)
    has_reset = not _is_constant(reset_pin)

    if has_set:
        sens.append(f"posedge {set_pin}")
    if has_reset:
        sens.append(f"posedge {reset_pin}")

    sensitivity = " or ".join(sens)

    # Async priority: set > reset (ngspice convention)
    if has_set and has_reset:
        async_lines.append(f"    if ({set_pin})")
        async_lines.append(f"    else if ({reset_pin})")
    elif has_set:
        async_lines.append(f"    if ({set_pin})")
    elif has_reset:
        async_lines.append(f"    if ({reset_pin})")

    return sensitivity, async_lines


def _emit_jkff(gate):
    """Emit behavioral Verilog for a JK flip-flop."""
    pm = gate.pin_map
    j     = pm.get("j", "1'b0")
    k     = pm.get("k", "1'b0")
    clk   = pm.get("clk", "clk")
    set_  = pm.get("set", "1'b0")
    reset = pm.get("reset", "1'b0")
    q     = pm.get("q", "q")
    qbar  = pm.get("qbar")

    sensitivity, async_lines = _emit_async_header(clk, set_, reset)

    lines = [f"// JK flip-flop instance {gate.inst_name}"]
    lines.append(f"always @({sensitivity}) begin")

    if async_lines:
        lines.append(async_lines[0])
        lines.append(f"        {q} <= 1'b1;")
        if len(async_lines) > 1:
            lines.append(async_lines[1])
            lines.append(f"        {q} <= 1'b0;")
        lines.append(f"    else begin")
    else:
        lines.append(f"    begin")

    lines.append(f"        case ({{{j}, {k}}})")
    lines.append(f"            2'b00: {q} <= {q};")
    lines.append(f"            2'b01: {q} <= 1'b0;")
    lines.append(f"            2'b10: {q} <= 1'b1;")
    lines.append(f"            2'b11: {q} <= ~{q};")
    lines.append(f"            default: {q} <= {q};")
    lines.append(f"        endcase")
    lines.append(f"    end")
    lines.append(f"end")

    if qbar and not _is_constant(qbar):
        lines.append(f"assign {qbar} = ~{q};")

    return "\n".join(lines)


def _emit_dff(gate):
    """Emit behavioral Verilog for a D flip-flop."""
    pm = gate.pin_map
    data  = pm.get("data", "1'b0")
    clk   = pm.get("clk", "clk")
    set_  = pm.get("set", "1'b0")
    reset = pm.get("reset", "1'b0")
    q     = pm.get("q", "q")
    qbar  = pm.get("qbar")

    sensitivity, async_lines = _emit_async_header(clk, set_, reset)

    lines = [f"// D flip-flop instance {gate.inst_name}"]
    lines.append(f"always @({sensitivity}) begin")

    if async_lines:
        lines.append(async_lines[0])
        lines.append(f"        {q} <= 1'b1;")
        if len(async_lines) > 1:
            lines.append(async_lines[1])
            lines.append(f"        {q} <= 1'b0;")
        lines.append(f"    else")
    else:
        lines.append(f"    ")

    lines.append(f"        {q} <= {data};")
    lines.append(f"end")

    if qbar and not _is_constant(qbar):
        lines.append(f"assign {qbar} = ~{q};")

    return "\n".join(lines)


def _emit_tff(gate):
    """Emit behavioral Verilog for a T flip-flop."""
    pm = gate.pin_map
    t     = pm.get("t", "1'b0")
    clk   = pm.get("clk", "clk")
    set_  = pm.get("set", "1'b0")
    reset = pm.get("reset", "1'b0")
    q     = pm.get("q", "q")
    qbar  = pm.get("qbar")

    sensitivity, async_lines = _emit_async_header(clk, set_, reset)

    lines = [f"// T flip-flop instance {gate.inst_name}"]
    lines.append(f"always @({sensitivity}) begin")

    if async_lines:
        lines.append(async_lines[0])
        lines.append(f"        {q} <= 1'b1;")
        if len(async_lines) > 1:
            lines.append(async_lines[1])
            lines.append(f"        {q} <= 1'b0;")
        lines.append(f"    else if ({t})")
    else:
        lines.append(f"    if ({t})")

    lines.append(f"        {q} <= ~{q};")
    lines.append(f"end")

    if qbar and not _is_constant(qbar):
        lines.append(f"assign {qbar} = ~{q};")

    return "\n".join(lines)


def _emit_srff(gate):
    """Emit behavioral Verilog for an SR flip-flop."""
    pm = gate.pin_map
    s     = pm.get("s", "1'b0")
    r     = pm.get("r", "1'b0")
    clk   = pm.get("clk", "clk")
    set_  = pm.get("set", "1'b0")
    reset = pm.get("reset", "1'b0")
    q     = pm.get("q", "q")
    qbar  = pm.get("qbar")

    sensitivity, async_lines = _emit_async_header(clk, set_, reset)

    lines = [f"// SR flip-flop instance {gate.inst_name}"]
    lines.append(f"always @({sensitivity}) begin")

    if async_lines:
        lines.append(async_lines[0])
        lines.append(f"        {q} <= 1'b1;")
        if len(async_lines) > 1:
            lines.append(async_lines[1])
            lines.append(f"        {q} <= 1'b0;")
        lines.append(f"    else begin")
    else:
        lines.append(f"    begin")

    lines.append(f"        case ({{{s}, {r}}})")
    lines.append(f"            2'b01: {q} <= 1'b0;")
    lines.append(f"            2'b10: {q} <= 1'b1;")
    lines.append(f"            2'b11: {q} <= 1'bx;")
    lines.append(f"            default: {q} <= {q};")
    lines.append(f"        endcase")
    lines.append(f"    end")
    lines.append(f"end")

    if qbar and not _is_constant(qbar):
        lines.append(f"assign {qbar} = ~{q};")

    return "\n".join(lines)


def _emit_dlatch(gate):
    """Emit behavioral Verilog for a D latch."""
    pm = gate.pin_map
    d     = pm.get("d", "1'b0")
    en    = pm.get("enable", "1'b0")
    set_  = pm.get("set", "1'b0")
    reset = pm.get("reset", "1'b0")
    q     = pm.get("q", "q")
    qbar  = pm.get("qbar")

    lines = [f"// D latch instance {gate.inst_name}"]
    lines.append(f"always @(*) begin")

    has_set = not _is_constant(set_)
    has_reset = not _is_constant(reset)

    if has_set:
        lines.append(f"    if ({set_})")
        lines.append(f"        {q} = 1'b1;")
        if has_reset:
            lines.append(f"    else if ({reset})")
            lines.append(f"        {q} = 1'b0;")
        lines.append(f"    else if ({en})")
    elif has_reset:
        lines.append(f"    if ({reset})")
        lines.append(f"        {q} = 1'b0;")
        lines.append(f"    else if ({en})")
    else:
        lines.append(f"    if ({en})")

    lines.append(f"        {q} = {d};")
    lines.append(f"end")

    if qbar and not _is_constant(qbar):
        lines.append(f"assign {qbar} = ~{q};")

    return "\n".join(lines)


def _emit_srlatch(gate):
    """Emit behavioral Verilog for an SR latch."""
    pm = gate.pin_map
    s     = pm.get("s", "1'b0")
    r     = pm.get("r", "1'b0")
    en    = pm.get("enable", "1'b0")
    set_  = pm.get("set", "1'b0")
    reset = pm.get("reset", "1'b0")
    q     = pm.get("q", "q")
    qbar  = pm.get("qbar")

    lines = [f"// SR latch instance {gate.inst_name}"]
    lines.append(f"always @(*) begin")

    has_set = not _is_constant(set_)
    has_reset = not _is_constant(reset)

    if has_set:
        lines.append(f"    if ({set_})")
        lines.append(f"        {q} = 1'b1;")
        if has_reset:
            lines.append(f"    else if ({reset})")
            lines.append(f"        {q} = 1'b0;")
        lines.append(f"    else if ({en}) begin")
    elif has_reset:
        lines.append(f"    if ({reset})")
        lines.append(f"        {q} = 1'b0;")
        lines.append(f"    else if ({en}) begin")
    else:
        lines.append(f"    if ({en}) begin")

    lines.append(f"        case ({{{s}, {r}}})")
    lines.append(f"            2'b01: {q} = 1'b0;")
    lines.append(f"            2'b10: {q} = 1'b1;")
    lines.append(f"            2'b11: {q} = 1'bx;")
    lines.append(f"            default: {q} = {q};")
    lines.append(f"        endcase")
    lines.append(f"    end")
    lines.append(f"end")

    if qbar and not _is_constant(qbar):
        lines.append(f"assign {qbar} = ~{q};")

    return "\n".join(lines)
