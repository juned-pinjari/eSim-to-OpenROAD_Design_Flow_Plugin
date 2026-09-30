# =====================================================================
#          FILE: circuit_graph.py
#
#   DESCRIPTION: Builds a connectivity graph from a parsed XSPICE
#                netlist and extracts the digital core: module ports,
#                internal wires, gate instances, and clock info.
#
#        AUTHOR: Juned Pinjari
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

from . import xspice_primitives as xp


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


@dataclass
class DigitalCircuit:
    """Fully extracted digital circuit, ready for Verilog emission."""
    module_name: str
    ports: list             # list of PortInfo
    wires: list             # list of legalized internal wire names
    gates: list             # list of GateInstance
    clock_net: str = None   # legalized clock port name (if detected)
    clock_period: float = None  # in seconds (from pulse source)


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

def _remap_net(net, port_map):
    """Substitute a net name using a port map.

    If *net* is a formal port of a subcircuit, replace it with the
    actual net from the parent.  Otherwise return it unchanged.
    """
    return port_map.get(net, net)


def _remap_port_groups(groups, port_map):
    """Apply port mapping to every net in a list of port groups."""
    return [
        [_remap_net(n, port_map) for n in group]
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
    from .spice_parser import Instance, ModelDef

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
        remapped_groups = _remap_port_groups(inst.port_groups, port_map)
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
            nested_port_map[formal] = _remap_net(net, port_map)

        _flatten_subckt(
            nested_subckt, nested_port_map,
            f"{prefix}_{nested_si.name}",
            out_instances, out_models, all_subcircuits,
        )


# ── Core Extraction ───────────────────────────────────────────────

def extract_circuit(parsed_netlist, module_name):
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

    # ── Step 1: Identify module ports via bridge tracing ───────────

    input_ports = []   # PortInfo list
    output_ports = []  # PortInfo list
    input_digital_nets = set()
    output_digital_nets = set()

    # ADC bridge: analog inputs → digital outputs
    # a-line format: a<N> [analog_in1 analog_in2 ...] [dig_out1 dig_out2 ...] model
    for bridge in adc_bridges:
        groups = bridge.port_groups
        if len(groups) >= 2:
            analog_nets = groups[0]   # first bracket group
            digital_nets = groups[1]  # second bracket group

            for analog, digital in zip(analog_nets, digital_nets):
                if analog.lower() == "gnd":
                    continue
                input_ports.append(PortInfo(
                    name=legalize_name(analog),
                    direction="input",
                    spice_analog_net=analog,
                    spice_digital_net=digital,
                ))
                input_digital_nets.add(digital)

    # DAC bridge: digital inputs → analog outputs
    # a-line format: a<N> [dig_in1 dig_in2 ...] [analog_out1 analog_out2 ...] model
    for bridge in dac_bridges:
        groups = bridge.port_groups
        if len(groups) >= 2:
            digital_nets = groups[0]  # first bracket group
            analog_nets = groups[1]   # second bracket group

            for digital, analog in zip(digital_nets, analog_nets):
                if analog.lower() == "gnd":
                    continue
                output_ports.append(PortInfo(
                    name=legalize_name(analog),
                    direction="output",
                    spice_analog_net=analog,
                    spice_digital_net=digital,
                ))
                output_digital_nets.add(digital)

    # Build net-name mapping: digital_net → port_name
    # For nets that cross a bridge, use the analog-side name (human-readable)
    net_to_port = {}
    for p in input_ports:
        net_to_port[p.spice_digital_net] = p.name
    for p in output_ports:
        net_to_port[p.spice_digital_net] = p.name

    # ── Step 2: Build gate instances ──────────────────────────────

    all_nets = set()
    gate_instances = []

    for inst, model in digital_gates:
        mtype = model.model_type
        prim = xp.get_primitive(mtype)
        if prim is None:
            continue

        gate_type = prim["verilog"]

        # Resolve inputs and outputs from port_groups
        if xp.is_combinational(mtype):
            gate_inputs, gate_outputs = _resolve_combinational_ports(
                inst, prim
            )
        else:
            gate_inputs, gate_outputs = _resolve_sequential_ports(
                inst, prim
            )

        # Map net names: if a net crosses a bridge, use the port name
        mapped_inputs = []
        for net in gate_inputs:
            mapped = net_to_port.get(net, legalize_name(net))
            mapped_inputs.append(mapped)
            all_nets.add(net)

        mapped_outputs = []
        for net in gate_outputs:
            mapped = net_to_port.get(net, legalize_name(net))
            mapped_outputs.append(mapped)
            all_nets.add(net)

        gate_instances.append(GateInstance(
            inst_name=inst.model_name,
            gate_type=gate_type,
            model_type=mtype,
            inputs=mapped_inputs,
            outputs=mapped_outputs,
        ))

    # ── Step 3: Classify internal wires ───────────────────────────

    port_names = set()
    for p in input_ports + output_ports:
        port_names.add(p.name)

    internal_wires = set()
    for net in all_nets:
        legalized = net_to_port.get(net, legalize_name(net))
        if legalized not in port_names:
            internal_wires.add(legalized)

    # ── Step 4: Clock detection (from pulse sources) ──────────────

    clock_net = None
    clock_period = None
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

    return DigitalCircuit(
        module_name=module_name,
        ports=input_ports + output_ports,
        wires=sorted(internal_wires),
        gates=gate_instances,
        clock_net=clock_net,
        clock_period=clock_period,
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
