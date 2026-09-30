# =====================================================================
#          FILE: verilog_emitter.py
#
#   DESCRIPTION: Generates synthesizable structural Verilog from a
#                DigitalCircuit extracted by circuit_graph.py.
#
#        AUTHOR: Juned Pinjari
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-09-30
# =====================================================================

"""Verilog code emitter for eSim digital circuits.

Produces synthesizable structural Verilog using Verilog gate
primitives (``and``, ``or``, ``xor``, ``not``, etc.).
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

    # ── Module header ─────────────────────────────────────────────
    lines.append(f"module {circuit.module_name} (")

    port_decls = []
    for port in circuit.ports:
        port_decls.append(f"    {port.direction} {port.name}")

    lines.append(",\n".join(port_decls))
    lines.append(");")
    lines.append("")

    # ── Wire declarations ─────────────────────────────────────────
    if circuit.wires:
        for wire in circuit.wires:
            lines.append(f"wire {wire};")
        lines.append("")

    # ── Gate instantiations ───────────────────────────────────────
    for gate in circuit.gates:
        line = _emit_gate(gate)
        if line:
            lines.append(line)

    lines.append("")
    lines.append("endmodule")
    lines.append("")

    return "\n".join(lines)


def _emit_gate(gate):
    """Emit a single Verilog gate primitive instantiation.

    Format: ``<type> <inst> (<output>, <input1>, <input2>, ...);``

    Verilog gate primitives put the output first.
    """
    gate_type = gate.gate_type

    # For combinational Verilog primitives
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

    # Sequential elements (Phase 2 — placeholder for now)
    return f"// TODO: sequential {gate_type} instance {gate.inst_name}"
