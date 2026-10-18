# =====================================================================
#          FILE: circuit_graph.py
#
#   DESCRIPTION: Builds a connectivity graph from a parsed XSPICE
#                netlist and extracts the digital core: module ports,
#                internal wires, gate instances, and clock info.
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-09-30
# =====================================================================

"""Digital circuit extraction from parsed XSPICE netlists.

The key idea: eSim digital netlists are *simulation testbenches* with
analog voltage sources, load resistors, and ADC/DAC bridges wrapping
the actual digital core.  This module identifies the digital boundary
by tracing ``adc_bridge`` and ``dac_bridge`` instances, then classifies
every net as an input port, output port, or internal wire.
"""

import re
from dataclasses import dataclass, field

try:
    from . import xspice_primitives as xp
except ImportError:
    import xspice_primitives as xp


# ── Data Structures ────────────────────────────────────────────────

@dataclass
class PortInfo:
    """A top-level module port."""
    name: str                # legalized Verilog name
    direction: str           # "input" or "output"
    spice_analog_net: str    # original analog-side net (for traceability)
    spice_digital_net: str   # digital-side net inside the core


@dataclass
class GateInstance:
    """A digital gate ready for Verilog emission."""
    inst_name: str
    gate_type: str          # Verilog primitive: "and", "xor", "not", etc.
    model_type: str         # XSPICE model: "d_and", "d_xor", etc.
    inputs: list            # list of net names (legalized)
    outputs: list           # list of net names (legalized)
    pin_map: dict = None    # pin_name → legalized net (sequential only)


@dataclass
class GeneratedClockInfo:
    """Information about a derived/generated clock in sequential circuits."""
    name: str               # SDC clock identifier (e.g. "gen_clk_q0")
    target_net: str         # The net carrying the generated clock
    source_clock: str       # Name of source clock (master clock port or parent)
    master_port: str        # Top-level master clock port
    divide_by: int = 2      # Frequency division factor relative to master clock
    driver_gate: str = None # Driving gate instance name
    driver_pin: str = "q"   # "q" or "qbar"


@dataclass
class DigitalCircuit:
    """Fully extracted digital circuit, ready for Verilog emission."""
    module_name: str
    ports: list             # list of PortInfo
    wires: list             # list of legalized internal wire names
    gates: list             # list of GateInstance
    constant_nets: dict = field(default_factory=dict)  # net → "1'b0"/"1'b1"
    clock_net: str = None   # legalized clock port name (if detected)
    clock_period: float = None  # in seconds (from pulse source)
    needs_escape: bool = False  # whether the module name requires a Verilog escape sequence
    generated_clocks: list = field(default_factory=list)  # list of GeneratedClockInfo


# ── Identifier Legalization ────────────────────────────────────────

_LEGALIZED_CACHE = {}


def legalize_name(spice_name):
    """Convert a SPICE net name to a valid Verilog identifier.

    Rules:
    - Replace hyphens with underscores
    - Prefix with ``n_`` if the name starts with a digit
    - Replace any remaining non-alphanumeric/underscore characters
    """
    if spice_name in _LEGALIZED_CACHE:
        return _LEGALIZED_CACHE[spice_name]

    name = spice_name.replace("-", "_")
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if name and name[0].isdigit():
        name = "n_" + name
    if not name:
        name = "unnamed"

    _LEGALIZED_CACHE[spice_name] = name
    return name


# ── Subcircuit Flattening ──────────────────────────────────────────

def _remap_net(net, port_map, prefix):
    """Substitute a net name using a port map, or prefix it if internal.

    If *net* is a formal port of a subcircuit, replace it with the
    actual net from the parent.  Otherwise, prefix it to avoid aliasing.
    """
    if net in port_map:
        return port_map[net]
    if net.lower() in ("gnd", "0", "vcc"):
        return net
    return f"{prefix}_{net}"


def _remap_port_groups(groups, port_map, prefix):
    """Apply port mapping or prefixing to every net in port groups."""
    return [
        [_remap_net(n, port_map, prefix) for n in group]
        for group in groups
    ]


def _flatten_subckt(subckt, port_map, prefix, out_instances, out_models,
                    all_subcircuits):
    """Inline a subcircuit's instances into the parent netlist.

    Parameters
    ----------
    subckt : spice_parser.SubCircuit
    port_map : dict
        Maps subcircuit formal port names to actual parent nets.
    prefix : str
        Instance prefix for unique naming (e.g. "x1").
    out_instances : list
        Accumulator for flattened Instance objects.
    out_models : dict
        Accumulator for model definitions.
    all_subcircuits : dict
        All known subcircuit definitions (for recursive flattening).
    """
    try:
        from .spice_parser import Instance, ModelDef
    except ImportError:
        from spice_parser import Instance, ModelDef

    # Merge models from subcircuit with prefixed names to avoid
    # namespace collisions (e.g. subcircuit's "u2" vs top-level "u2")
    model_name_map = {}  # old_name → prefixed_name
    for old_name, model_def in subckt.models.items():
        new_name = f"{prefix}_{old_name}"
        model_name_map[old_name] = new_name
        out_models[new_name] = ModelDef(
            name=new_name,
            model_type=model_def.model_type,
            params=model_def.params,
        )

    # Inline a-instances with net substitution and model name remapping
    for inst in subckt.instances:
        remapped_groups = _remap_port_groups(inst.port_groups, port_map, prefix)
        remapped_model = model_name_map.get(inst.model_name, inst.model_name)
        new_inst = Instance(
            name=f"{prefix}_{inst.name}",
            port_groups=remapped_groups,
            model_name=remapped_model,
        )
        out_instances.append(new_inst)

    # Recursively flatten nested x-instances inside this subcircuit
    for nested_si in subckt.subckt_instances:
        nested_subckt = all_subcircuits.get(nested_si.subckt_name)
        if nested_subckt is None:
            continue

        # Build nested port map: nested formal → parent actual
        nested_port_map = {}
        for formal, net in zip(nested_subckt.ports, nested_si.nets):
            # The net from the nested x-line may itself be a formal
            # port of the current subcircuit — resolve through port_map
            nested_port_map[formal] = _remap_net(net, port_map, prefix)

        _flatten_subckt(
            nested_subckt, nested_port_map,
            f"{prefix}_{nested_si.name}",
            out_instances, out_models, all_subcircuits,
        )


# ── Core Extraction ───────────────────────────────────────────────

def extract_circuit(parsed_netlist, module_name, needs_escape=False):
    """Extract the digital core from a *flat* (non-hierarchical) netlist.

    Parameters
    ----------
    parsed_netlist : spice_parser.ParsedNetlist
        Output of ``spice_parser.parse_file()``.
    module_name : str
        Desired Verilog module name.

    Returns
    -------
    DigitalCircuit
        Ready for the Verilog emitter.

    Raises
    ------
    RuntimeError
        If the netlist has no digital gates (analog-only circuit).
    """
    models = dict(parsed_netlist.models)

    # ── Step 0: Flatten subcircuit instances ───────────────────────
    # If the top level has x-instances, inline their gates with
    # net-name substitution based on port binding.
    all_instances = list(parsed_netlist.instances)

    for si in parsed_netlist.subckt_instances:
        subckt = parsed_netlist.subcircuits.get(si.subckt_name)
        if subckt is None:
            continue

        # Build port mapping: subckt_port → actual_net
        port_map = {}
        for formal, actual in zip(subckt.ports, si.nets):
            port_map[formal] = actual

        # Recursively flatten nested subcircuits inside this subckt
        _flatten_subckt(
            subckt, port_map, si.name, all_instances, models,
            parsed_netlist.subcircuits
        )

    # Classify instances by role
    adc_bridges = []   # input boundary
    dac_bridges = []   # output boundary
    digital_gates = [] # actual gates

    for inst in all_instances:
        model = models.get(inst.model_name)
        if model is None:
            continue

        mtype = model.model_type

        if xp.is_bridge(mtype):
            if mtype == "adc_bridge":
                adc_bridges.append(inst)
            else:
                dac_bridges.append(inst)
        elif xp.is_digital_gate(mtype):
            digital_gates.append((inst, model))

    if not digital_gates:
        raise RuntimeError(
            "No digital gates found in netlist. "
            "This appears to be an analog-only or mixed-signal circuit."
        )

    # ── Step 1: Detect constant tie-off nets ─────────────────────────
    # Voltage sources with DC 0V or 5V feeding through adc_bridges
    # are tie-offs (SET/RESET tied low/high), not real input ports.
    #
    # IMPORTANT: Only auto-generated eSim internal nets (matching the
    # pattern ``net-_u*-pad*_``) are considered tie-offs.  Human-readable
    # net names like ``j``, ``k``, ``in1`` are real user-defined inputs
    # even if they happen to be driven by DC sources in the testbench.
    #
    # Build: analog_net → constant value ("1'b0" or "1'b1")
    _INTERNAL_NET_RE = re.compile(r'^net-_u\d+-pad\d+_$', re.IGNORECASE)

    constant_analog_nets = {}
    for vs in parsed_netlist.voltage_sources:
        if vs.is_pulse:
            continue  # pulse sources are clocks, not tie-offs
        if vs.dc_value is not None and _INTERNAL_NET_RE.match(vs.net_plus):
            if vs.dc_value == 0.0:
                constant_analog_nets[vs.net_plus] = "1'b0"
            else:
                constant_analog_nets[vs.net_plus] = "1'b1"

    # Trace constant analog nets through adc_bridges to find the
    # corresponding digital-side constant nets.
    constant_digital_nets = {}  # digital_net → "1'b0" / "1'b1"
    for bridge in adc_bridges:
        groups = bridge.port_groups
        if len(groups) >= 2:
            analog_nets = groups[0]
            digital_nets = groups[1]
            for analog, digital in zip(analog_nets, digital_nets):
                if analog in constant_analog_nets:
                    constant_digital_nets[digital] = \
                        constant_analog_nets[analog]

    # ── Step 2: Identify module ports ───────────────────────────────
    # For full simulation netlists with an analog testbench, identify module
    # I/O ports via the top-level ADC and DAC bridges found directly in
    # parsed_netlist.instances.
    top_level_bridges = [
        inst for inst in parsed_netlist.instances
        if models.get(inst.model_name) and xp.is_bridge(models[inst.model_name].model_type)
    ]

    input_ports = []   # PortInfo list
    output_ports = []  # PortInfo list
    input_digital_nets = set()
    output_digital_nets = set()

    if top_level_bridges:
        seen_in_names = set()
        seen_out_names = set()

        # DAC bridges first to identify true output ports
        for bridge in top_level_bridges:
            if models[bridge.model_name].model_type == "dac_bridge":
                groups = bridge.port_groups
                if len(groups) >= 2:
                    digital_nets = groups[0]
                    analog_nets = groups[1]
                    for digital, analog in zip(digital_nets, analog_nets):
                        if analog.lower() == "gnd":
                            continue
                        legal = legalize_name(analog)
                        if legal in seen_out_names:
                            continue
                        seen_out_names.add(legal)
                        output_ports.append(PortInfo(
                            name=legal,
                            direction="output",
                            spice_analog_net=analog,
                            spice_digital_net=digital,
                        ))
                        output_digital_nets.add(digital)

        # ADC bridges for input ports
        for bridge in top_level_bridges:
            if models[bridge.model_name].model_type == "adc_bridge":
                groups = bridge.port_groups
                if len(groups) >= 2:
                    analog_nets = groups[0]
                    digital_nets = groups[1]
                    for analog, digital in zip(analog_nets, digital_nets):
                        if analog.lower() == "gnd":
                            continue
                        if analog in constant_analog_nets:
                            continue
                        legal = legalize_name(analog)
                        if legal in seen_in_names or legal in seen_out_names:
                            continue
                        seen_in_names.add(legal)
                        input_ports.append(PortInfo(
                            name=legal,
                            direction="input",
                            spice_analog_net=analog,
                            spice_digital_net=digital,
                        ))
                        input_digital_nets.add(digital)

    else:
        # ── Step 2b: Port identification for standalone subcircuits ──
        # When no top-level analog testbench simulation bridges exist, extract ports
        # directly from the subcircuit's formal port interface.
        all_gate_inputs = set()
        all_gate_outputs = set()
        for inst, model in digital_gates:
            mtype = model.model_type
            prim = xp.get_primitive(mtype)
            if prim is None:
                continue
            if xp.is_combinational(mtype):
                gin, gout = _resolve_combinational_ports(inst, prim)
            else:
                gin, gout = _resolve_sequential_ports(inst, prim)
            all_gate_inputs.update(gin)
            all_gate_outputs.update(gout)

        # Also collect outputs driven by any inlined DAC bridges inside the subcircuit
        dac_bridge_outputs = set()
        for bridge in dac_bridges:
            if len(bridge.port_groups) >= 2:
                dac_bridge_outputs.update(bridge.port_groups[1])

        all_outputs = all_gate_outputs | dac_bridge_outputs

        # Candidate external nets: from top-level subckt instances, or all gate nets
        candidate_nets = []
        seen_cand = set()
        for si in parsed_netlist.subckt_instances:
            for net in si.nets:
                if net not in seen_cand and net.lower() not in ("gnd", "0", "vcc") and not net.startswith("__nc_"):
                    candidate_nets.append(net)
                    seen_cand.add(net)

        if not candidate_nets:
            for net in sorted(all_gate_inputs):
                if net not in all_outputs and net.lower() not in ("gnd", "0", "vcc") and not net.startswith("__nc_"):
                    if net not in seen_cand:
                        candidate_nets.append(net)
                        seen_cand.add(net)
            for net in sorted(all_outputs):
                if net not in all_gate_inputs and net.lower() not in ("gnd", "0", "vcc") and not net.startswith("__nc_"):
                    if net not in seen_cand:
                        candidate_nets.append(net)
                        seen_cand.add(net)

        for net in candidate_nets:
            legal_name = legalize_name(net)
            if net in all_outputs:
                output_ports.append(PortInfo(
                    name=legal_name,
                    direction="output",
                    spice_analog_net=net,
                    spice_digital_net=net,
                ))
                output_digital_nets.add(net)
            else:
                input_ports.append(PortInfo(
                    name=legal_name,
                    direction="input",
                    spice_analog_net=net,
                    spice_digital_net=net,
                ))
                input_digital_nets.add(net)

    # Build net-name mapping: digital_net → port_name
    # For nets that cross a bridge, use the analog-side name (human-readable)
    net_to_port = {}
    for p in input_ports:
        net_to_port[p.spice_digital_net] = p.name
    for p in output_ports:
        net_to_port[p.spice_digital_net] = p.name

    # ── Step 3: Build gate instances ──────────────────────────────

    all_nets = set()
    gate_instances = []
    has_sequential = False

    for inst, model in digital_gates:
        mtype = model.model_type
        prim = xp.get_primitive(mtype)
        if prim is None:
            continue

        gate_type = prim["verilog"]
        pin_map = None

        # Resolve inputs and outputs from port_groups
        if xp.is_combinational(mtype):
            gate_inputs, gate_outputs = _resolve_combinational_ports(
                inst, prim
            )
        else:
            has_sequential = True
            gate_inputs, gate_outputs = _resolve_sequential_ports(
                inst, prim
            )
            # Build pin_map for the emitter (pin_name → net)
            pin_map = _build_sequential_pin_map(inst, prim)

        # Map net names: if a net crosses a bridge, use the port name;
        # if it's a constant tie-off, use the constant literal.
        def _map_net(net):
            if net in constant_digital_nets:
                return constant_digital_nets[net]
            return net_to_port.get(net, legalize_name(net))

        mapped_inputs = []
        for net in gate_inputs:
            mapped_inputs.append(_map_net(net))
            all_nets.add(net)

        mapped_outputs = []
        for net in gate_outputs:
            mapped_outputs.append(_map_net(net))
            all_nets.add(net)

        # Remap pin_map values too
        if pin_map:
            pin_map = {k: _map_net(v) for k, v in pin_map.items()}

        gate_instances.append(GateInstance(
            inst_name=inst.model_name,
            gate_type=gate_type,
            model_type=mtype,
            inputs=mapped_inputs,
            outputs=mapped_outputs,
            pin_map=pin_map,
        ))

    # ── Step 4: Classify internal wires ───────────────────────────

    port_names = set()
    for p in input_ports + output_ports:
        port_names.add(p.name)

    # Constant literals are not wires
    constant_literals = set(constant_digital_nets.values())

    internal_wires = set()
    for net in all_nets:
        legalized = _map_net(net) if net in constant_digital_nets \
            else net_to_port.get(net, legalize_name(net))
        if legalized not in port_names and legalized not in constant_literals:
            internal_wires.add(legalized)

    # ── Step 5: Clock detection (from pulse sources) ──────────────
    # Only detect clock if the circuit has sequential elements.

    clock_net = None
    clock_period = None
    if has_sequential:
        for vs in parsed_netlist.voltage_sources:
            if vs.is_pulse:
                # Trace this voltage source's net through adc_bridge
                for bridge in adc_bridges:
                    groups = bridge.port_groups
                    if len(groups) >= 2:
                        analog_nets = groups[0]
                        digital_nets = groups[1]
                        if vs.net_plus in analog_nets:
                            idx = analog_nets.index(vs.net_plus)
                            if idx < len(digital_nets):
                                clock_net = legalize_name(vs.net_plus)
                                # pulse params: v1 v2 td tr tf pw period
                                if len(vs.pulse_params) >= 7:
                                    try:
                                        clock_period = float(
                                            vs.pulse_params[6]
                                        )
                                    except ValueError:
                                        pass
                                break

    # ── Step 5b: Infer root clock if not found from pulse source ─
    input_port_names = {p.name for p in input_ports}
    # Trace single-input combinational gates (inverters/buffers)
    net_alias = {}
    for g in gate_instances:
        if g.model_type in xp.COMBINATIONAL_GATES and len(g.inputs) == 1 and len(g.outputs) == 1:
            net_alias[g.outputs[0]] = g.inputs[0]

    def _resolve_to_root(n):
        visited = set()
        curr = n
        while curr not in input_port_names and curr in net_alias and curr not in visited:
            visited.add(curr)
            curr = net_alias[curr]
        return curr

    if has_sequential and not clock_net:
        clk_candidates = []
        for g in gate_instances:
            if g.pin_map and "clk" in g.pin_map:
                raw_clk = g.pin_map["clk"]
                root_cand = _resolve_to_root(raw_clk)
                if root_cand in input_port_names and root_cand not in clk_candidates:
                    clk_candidates.append(root_cand)

        if clk_candidates:
            def _clk_score(n):
                nl = n.lower()
                if "clk" in nl or "clock" in nl:
                    return 0
                if nl in ("in", "cp", "pin_1", "pin1"):
                    return 1
                return 2

            clk_candidates.sort(key=_clk_score)
            clock_net = clk_candidates[0]

    # ── Step 6: Multi-clock & Ripple Counter (Generated Clock) Detection ──
    generated_clocks = []
    if has_sequential:
        seq_gates = [g for g in gate_instances if g.model_type in xp.SEQUENTIAL_ELEMENTS and g.pin_map]

        # Map each sequential output net to (gate, pin_name)
        gate_output_map = {}
        for g in seq_gates:
            for pin in ("q", "qbar"):
                out_net = g.pin_map.get(pin)
                if out_net and out_net not in ("1'b0", "1'b1") and not out_net.startswith("__nc_"):
                    gate_output_map[out_net] = (g, pin)

        # Propagate sequential output mapping through single-input comb gates
        changed = True
        while changed:
            changed = False
            for g in gate_instances:
                if g.model_type in xp.COMBINATIONAL_GATES and len(g.inputs) == 1 and len(g.outputs) == 1:
                    inp, outp = g.inputs[0], g.outputs[0]
                    if inp in gate_output_map and outp not in gate_output_map:
                        gate_output_map[outp] = gate_output_map[inp]
                        changed = True

        # Track clock division factors and master ports per clock domain
        clock_divides = {}  # net -> int (divide factor relative to master)
        clock_master = {}   # net -> master_port_name

        # Seed root clock(s)
        effective_master = clock_net or (input_ports[0].name if input_ports else "clk")
        if clock_net:
            clock_divides[clock_net] = 1
            clock_master[clock_net] = clock_net

        for g in seq_gates:
            raw_clk = g.pin_map.get("clk")
            if raw_clk:
                root_p = _resolve_to_root(raw_clk)
                if root_p in input_port_names:
                    clock_divides[root_p] = 1
                    clock_master[root_p] = root_p
                    clock_divides[raw_clk] = 1
                    clock_master[raw_clk] = root_p
                elif raw_clk not in gate_output_map and root_p not in gate_output_map:
                    # Clock is not driven by any sequential element: it is a primary root clock
                    clock_divides[raw_clk] = 1
                    clock_master[raw_clk] = effective_master
                    clock_divides[root_p] = 1
                    clock_master[root_p] = effective_master

        # Iteratively propagate divide factors through sequential stages
        changed = True
        while changed:
            changed = False
            for g in seq_gates:
                clk_net_val = g.pin_map.get("clk")
                if not clk_net_val:
                    continue
                resolved_clk = _resolve_to_root(clk_net_val)
                div = clock_divides.get(clk_net_val) or clock_divides.get(resolved_clk)
                mport = clock_master.get(clk_net_val) or clock_master.get(resolved_clk) or effective_master
                if div is not None:
                    new_div = div * 2
                    for pin in ("q", "qbar"):
                        out_net = g.pin_map.get(pin)
                        if out_net and out_net not in ("1'b0", "1'b1") and not out_net.startswith("__nc_"):
                            if out_net not in clock_divides or clock_divides[out_net] != new_div:
                                clock_divides[out_net] = new_div
                                clock_master[out_net] = mport
                                changed = True
                                # Also propagate to outputs of buffers/inverters driving from out_net
                                for g_c in gate_instances:
                                    if g_c.model_type in xp.COMBINATIONAL_GATES and len(g_c.inputs) == 1 and len(g_c.outputs) == 1:
                                        if g_c.inputs[0] == out_net:
                                            clock_divides[g_c.outputs[0]] = new_div
                                            clock_master[g_c.outputs[0]] = mport

        # Extract GeneratedClockInfo for all unique generated clock nets
        seen_clock_nets = set()
        for g in seq_gates:
            clk_net_val = g.pin_map.get("clk")
            if not clk_net_val or clk_net_val in seen_clock_nets:
                continue
            resolved_clk = _resolve_to_root(clk_net_val)
            driver_info = gate_output_map.get(clk_net_val) or gate_output_map.get(resolved_clk)
            if driver_info:
                drv_gate, drv_pin = driver_info
                div = clock_divides.get(clk_net_val) or clock_divides.get(resolved_clk, 2)
                mport = clock_master.get(clk_net_val) or clock_master.get(resolved_clk) or effective_master
                gen_name = f"gen_clk_{legalize_name(clk_net_val)}"
                seen_clock_nets.add(clk_net_val)
                generated_clocks.append(GeneratedClockInfo(
                    name=gen_name,
                    target_net=clk_net_val,
                    source_clock=mport,
                    master_port=mport,
                    divide_by=div,
                    driver_gate=drv_gate.inst_name,
                    driver_pin=drv_pin,
                ))

    legalized_mod_name = legalize_name(module_name)
    return DigitalCircuit(
        module_name=legalized_mod_name,
        ports=input_ports + output_ports,
        wires=sorted(internal_wires),
        gates=gate_instances,
        constant_nets=constant_digital_nets,
        clock_net=clock_net,
        clock_period=clock_period,
        needs_escape=needs_escape,
        generated_clocks=generated_clocks,
    )


# ── Port Resolution Helpers ───────────────────────────────────────

def _resolve_combinational_ports(inst, prim):
    """Resolve inputs/outputs for a combinational gate.

    Combinational gates in eSim netlists use bracket notation:
        a1 [in1 in2] out model    → 2 groups: [[in1,in2], [out]]
        a5 in out model           → 2 groups: [[in], [out]]  (inverter)
    """
    groups = inst.port_groups
    pins = prim["pins"]

    if "in..." in pins:
        # Variable-input gate: first group(s) = inputs, last = output
        if len(groups) >= 2:
            # Groups may be: [[in1,in2], [out]] or [[in1,in2], [out1]]
            inputs = []
            for g in groups[:-1]:
                inputs.extend(g)
            outputs = groups[-1]
        elif len(groups) == 1:
            # All in one group — last element is output
            inputs = groups[0][:-1]
            outputs = [groups[0][-1]]
        else:
            inputs = []
            outputs = []
    elif "enable" in pins:
        # Fixed 3-pin gate: in, enable, out (e.g. d_tristate)
        flat = []
        for g in groups:
            flat.extend(g)
        if len(flat) >= 3:
            inputs = [flat[0], flat[1]]
            outputs = [flat[2]]
        else:
            inputs = flat[:-1]
            outputs = [flat[-1]] if flat else []
    else:
        # Fixed pins: "in", "out" (inverter/buffer)
        flat = []
        for g in groups:
            flat.extend(g)
        if len(flat) >= 2:
            inputs = [flat[0]]
            outputs = [flat[1]]
        else:
            inputs = flat
            outputs = []

    return inputs, outputs


def _resolve_sequential_ports(inst, prim):
    """Resolve inputs/outputs for a sequential element.

    Sequential elements use positional (non-bracketed) ports:
        a3 j k clk set reset q qbar model

    Pin mapping is determined by the primitive's pin list.
    """
    pins = prim["pins"]
    flat = []
    for g in inst.port_groups:
        flat.extend(g)

    # Map by position
    pin_map = {}
    for i, pin_name in enumerate(pins):
        if i < len(flat):
            pin_map[pin_name] = flat[i]

    # Split into inputs and outputs based on pin semantics
    output_pins = {"q", "qbar"}
    inputs = []
    outputs = []

    for pin_name in pins:
        net = pin_map.get(pin_name)
        if net is None:
            continue
        if pin_name in output_pins:
            outputs.append(net)
        else:
            inputs.append(net)

    return inputs, outputs


def _build_sequential_pin_map(inst, prim):
    """Build a pin_name → raw_net mapping for a sequential element.

    This is used by the emitter to know which net corresponds to
    ``clk``, ``set``, ``reset``, ``j``, ``k``, ``q``, ``qbar``, etc.
    Net names returned here are the raw SPICE names (before legalization);
    the caller is responsible for mapping/legalizing them.
    """
    pins = prim["pins"]
    flat = []
    for g in inst.port_groups:
        flat.extend(g)

    pin_map = {}
    for i, pin_name in enumerate(pins):
        if i < len(flat):
            pin_map[pin_name] = flat[i]

    return pin_map
