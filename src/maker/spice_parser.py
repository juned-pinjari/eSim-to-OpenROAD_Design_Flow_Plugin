# =====================================================================
#          FILE: spice_parser.py
#
#   DESCRIPTION: Tokenizer and parser for eSim XSPICE digital netlists.
#                Reads .cir.out and .sub files, producing structured
#                data (ParsedNetlist) for downstream processing.
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-09-30
# =====================================================================

"""SPICE/XSPICE netlist parser for eSim digital circuits.

This module handles:
- ``a``-lines  (XSPICE code model instances)
- ``.model``   (model definitions with parameter extraction)
- ``.subckt`` / ``.ends``  (subcircuit blocks)
- ``.include`` (file includes, resolved relative to the netlist)
- ``x``-lines  (subcircuit instantiations)
- ``v``-lines  (voltage sources — needed for clock/tie-off detection)
- ``r``-lines  (resistors — skipped but recorded for port naming)
- ``.control`` … ``.endc``  (simulation blocks — skipped)
- Comments (``*``) — eSim metadata comments are parsed for port hints
"""

import os
import re
from dataclasses import dataclass, field


# ── Data Structures ────────────────────────────────────────────────

@dataclass
class ModelDef:
    """Parsed ``.model`` line."""
    name: str
    model_type: str
    params: dict = field(default_factory=dict)


@dataclass
class Instance:
    """Parsed ``a``-line (XSPICE code model instance).

    For combinational gates the a-line uses bracket notation:
        a1 [in1 in2] out model  →  inputs=[in1,in2], outputs=[out]

    For sequential elements all ports are positional:
        a3 j k clk set reset q qbar model  →  all_ports=[j,k,...,qbar]

    ``port_groups`` stores the raw parsed groups (each group is a list).
    ``model_name`` is the reference to the ``.model`` definition.
    """
    name: str
    port_groups: list = field(default_factory=list)
    model_name: str = ""


@dataclass
class SubcktInstance:
    """Parsed ``x``-line (subcircuit instantiation)."""
    name: str
    nets: list = field(default_factory=list)
    subckt_name: str = ""


@dataclass
class VoltageSource:
    """Parsed ``v``-line."""
    name: str
    net_plus: str
    net_minus: str
    dc_value: float = None
    is_pulse: bool = False
    pulse_params: list = field(default_factory=list)


@dataclass
class SubCircuit:
    """Parsed ``.subckt`` … ``.ends`` block (from .sub files)."""
    name: str
    ports: list = field(default_factory=list)
    instances: list = field(default_factory=list)
    models: dict = field(default_factory=dict)
    includes: list = field(default_factory=list)
    subckt_instances: list = field(default_factory=list)


@dataclass
class ParsedNetlist:
    """Top-level parse result for a .cir.out file."""
    title: str = ""
    includes: list = field(default_factory=list)
    instances: list = field(default_factory=list)       # a-lines
    models: dict = field(default_factory=dict)           # name → ModelDef
    voltage_sources: list = field(default_factory=list)  # v-lines
    subckt_instances: list = field(default_factory=list) # x-lines
    subcircuits: dict = field(default_factory=dict)       # name → SubCircuit
    comments: list = field(default_factory=list)          # eSim metadata


# ── Tokenizer Helpers ──────────────────────────────────────────────

def _tokenize_a_line(tokens):
    """Parse the port section of an a-line into groups.

    An a-line looks like:
        a1 [in1 in2] out u2        → groups = [[in1,in2], [out]]
        a5 net1 net2 u6            → groups = [[net1], [net2]]
        a3 n1 n2 n3 n4 n5 n6 n7 u9 → groups = [[n1],[n2],...,[n7]]
        a8 [a b] [c d] u1          → groups = [[a,b], [c,d]]

    Returns (instance_name, port_groups, model_name).
    """
    inst_name = tokens[0]
    model_name = tokens[-1]
    port_tokens = tokens[1:-1]

    groups = []
    current_vector = None

    for tok in port_tokens:
        if tok == "[":
            current_vector = []
        elif tok == "]":
            if current_vector is not None:
                groups.append(current_vector)
                current_vector = None
        elif tok.startswith("[") and tok.endswith("]"):
            # Single token like [net]
            inner = tok[1:-1]
            if inner:
                groups.append([inner])
        elif tok.startswith("["):
            current_vector = [tok[1:]]
        elif tok.endswith("]"):
            if current_vector is not None:
                val = tok[:-1]
                if val:
                    current_vector.append(val)
                groups.append(current_vector)
                current_vector = None
            else:
                val = tok[:-1]
                if val:
                    groups.append([val])
        else:
            if current_vector is not None:
                current_vector.append(tok)
            else:
                groups.append([tok])

    # Disambiguate unconnected terminals '?' so each gets a distinct net
    nc_count = 0
    resolved_groups = []
    for grp in groups:
        resolved_grp = []
        for net in grp:
            if net == "?":
                nc_count += 1
                resolved_grp.append(f"__nc_{inst_name}_{nc_count}")
            else:
                resolved_grp.append(net)
        resolved_groups.append(resolved_grp)

    return inst_name, resolved_groups, model_name


def _parse_model_params(param_str):
    """Extract key=value pairs from a .model parameter string.

    Example: ``"fall_delay=1.0e-9 input_load=1.0e-12 rise_delay=1.0e-9"``
    Returns: ``{"fall_delay": "1.0e-9", "input_load": "1.0e-12", ...}``
    """
    params = {}
    for match in re.finditer(r'(\w+)\s*=\s*([^\s)]+)', param_str):
        params[match.group(1)] = match.group(2)
    return params


def _parse_voltage_source(tokens, full_line):
    """Parse a v-line into a VoltageSource.

    Formats observed:
        v1 in1 gnd  dc 5
        v2 clk gnd pulse(0 5 1m 1m 1m 20 40)
        v4  net-_u8-pad1_ gnd 0
        v5  net-_u10-pad1_ gnd 0
    """
    name = tokens[0]
    net_plus = tokens[1]
    net_minus = tokens[2]

    vs = VoltageSource(name=name, net_plus=net_plus, net_minus=net_minus)

    rest = " ".join(tokens[3:])

    # Check for pulse source
    pulse_match = re.search(r'pulse\s*\(([^)]+)\)', rest, re.IGNORECASE)
    if pulse_match:
        vs.is_pulse = True
        vs.pulse_params = pulse_match.group(1).split()
        return vs

    # Check for dc value
    dc_match = re.search(r'dc\s+([^\s]+)', rest, re.IGNORECASE)
    if dc_match:
        try:
            vs.dc_value = float(dc_match.group(1))
        except ValueError:
            pass
        return vs

    # Bare number after gnd (e.g., "v5 net gnd 0")
    remaining = tokens[3:]
    if remaining:
        try:
            vs.dc_value = float(remaining[0])
        except ValueError:
            pass

    return vs


# ── Main Parser ────────────────────────────────────────────────────

def parse_lines(lines):
    """Parse a list of SPICE lines into structured components.

    Returns ``(instances, models, voltage_sources, subckt_instances,
               includes, comments, subcircuits, title)``.
    """
    instances = []
    models = {}
    voltage_sources = []
    subckt_instances = []
    includes = []
    comments = []
    subcircuits = {}
    title = ""

    in_control = False
    in_subckt = False
    subckt_lines = []
    subckt_name = ""
    subckt_ports = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines
        if not line:
            i += 1
            continue

        # Title line (first non-empty line starting with *)
        if i == 0 and line.startswith("*") and not title:
            title = line[1:].strip()
            i += 1
            continue

        # Control block — skip entirely
        if line.lower().startswith(".control"):
            in_control = True
            i += 1
            continue
        if line.lower().startswith(".endc"):
            in_control = False
            i += 1
            continue
        if in_control:
            i += 1
            continue

        # .end — stop parsing
        if line.lower() == ".end":
            break

        # Inside a .subckt block — collect lines for recursive parse
        if in_subckt:
            if line.lower().startswith(".ends"):
                sub = _parse_subckt_block(
                    subckt_name, subckt_ports, subckt_lines
                )
                subcircuits[subckt_name] = sub
                in_subckt = False
                subckt_lines = []
                i += 1
                continue
            else:
                subckt_lines.append(line)
                i += 1
                continue

        # .subckt — start collecting
        if line.lower().startswith(".subckt"):
            tokens = line.split()
            subckt_name = tokens[1]
            # Filter out '?' (eSim placeholder for unconnected ports)
            subckt_ports = [t for t in tokens[2:] if t != '?']
            in_subckt = True
            i += 1
            continue

        # .include
        if line.lower().startswith(".include"):
            tokens = line.split()
            if len(tokens) >= 2:
                includes.append(tokens[1])
            i += 1
            continue

        # .model
        if line.lower().startswith(".model"):
            model = _parse_model_line(line)
            if model:
                models[model.name] = model
            i += 1
            continue

        # a-line (XSPICE instance)
        if line[0].lower() == "a" and len(line) > 1 and line[1:2].isdigit():
            tokens = line.split()
            inst_name, port_groups, model_name = _tokenize_a_line(tokens)
            inst = Instance(
                name=inst_name,
                port_groups=port_groups,
                model_name=model_name,
            )
            instances.append(inst)
            i += 1
            continue

        # x-line (subcircuit instance)
        if line[0].lower() == "x" and len(line) > 1 and line[1:2].isdigit():
            tokens = line.split()
            # Filter out '?' (eSim placeholder for unconnected ports)
            nets = [t for t in tokens[1:-1] if t != '?']
            si = SubcktInstance(
                name=tokens[0],
                nets=nets,
                subckt_name=tokens[-1],
            )
            subckt_instances.append(si)
            i += 1
            continue

        # v-line (voltage source)
        if line[0].lower() == "v" and len(line) > 1 and line[1:2].isdigit():
            tokens = line.split()
            vs = _parse_voltage_source(tokens, line)
            voltage_sources.append(vs)
            i += 1
            continue

        # Comment lines — capture eSim metadata comments
        if line.startswith("*"):
            comments.append(line)
            i += 1
            continue

        # Skip: r-lines, .tran, .ac, and other directives
        i += 1

    return (instances, models, voltage_sources, subckt_instances,
            includes, comments, subcircuits, title)


def _parse_model_line(line):
    """Parse a ``.model`` line.

    Format: ``.model <name> <type>(<params>)``
    or:     ``.model <name> <type>(key=val key=val ...)``
    """
    # Match: .model name type(params)
    match = re.match(
        r'\.model\s+(\S+)\s+(\w+)\s*\(([^)]*)\)',
        line, re.IGNORECASE
    )
    if match:
        name = match.group(1)
        model_type = match.group(2)
        params = _parse_model_params(match.group(3))
        return ModelDef(name=name, model_type=model_type, params=params)

    # Fallback: .model name type (no params)
    match = re.match(
        r'\.model\s+(\S+)\s+(\w+)',
        line, re.IGNORECASE
    )
    if match:
        return ModelDef(name=match.group(1), model_type=match.group(2))

    return None


def _parse_subckt_block(name, ports, lines):
    """Parse lines inside a .subckt block into a SubCircuit."""
    (instances, models, voltage_sources, subckt_instances,
     includes, comments, nested_subckts, _) = parse_lines(lines)

    return SubCircuit(
        name=name,
        ports=ports,
        instances=instances,
        models=models,
        includes=includes,
        subckt_instances=subckt_instances,
    )


# ── Public API ─────────────────────────────────────────────────────

def _find_include_file(inc_file, search_paths):
    """Locate an include file across a sequence of search paths.

    Checks:
      1. Direct path or normalized path relative to each search path
      2. Filename directly under each search path
      3. Filename under a subfolder named after the subcircuit base
         (e.g., ``library/SubcircuitLibrary/4_and/4_and.sub``)
    """
    clean_inc = inc_file.replace("\\", "/")
    inc_name = os.path.basename(clean_inc)
    inc_base = os.path.splitext(inc_name)[0]

    for sp in search_paths:
        if not sp or not os.path.isdir(sp):
            continue
        # 1. Direct path
        cand = os.path.join(sp, clean_inc)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
        # 2. Filename directly in search path
        cand = os.path.join(sp, inc_name)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
        # 3. Subfolder matching base name
        cand = os.path.join(sp, inc_base, inc_name)
        if os.path.isfile(cand):
            return os.path.abspath(cand)

    return None


def parse_file(filepath, search_paths=None):
    """Parse a ``.cir.out`` or ``.sub`` file into a ``ParsedNetlist``.

    Resolves ``.include`` directives relative to the file's directory
    and configured library search paths, and recursively parses referenced
    ``.sub`` files.
    """
    filepath = os.path.abspath(filepath)
    base_dir = os.path.dirname(filepath)

    if search_paths is None:
        search_paths = []
    else:
        search_paths = list(search_paths)

    # Ensure base_dir is first in search_paths
    if base_dir not in search_paths:
        search_paths.insert(0, base_dir)

    # Add standard eSim library paths from repository root
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    subckt_lib = os.path.join(repo_root, "library", "SubcircuitLibrary")
    if os.path.isdir(subckt_lib) and subckt_lib not in search_paths:
        search_paths.append(subckt_lib)

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    (instances, models, voltage_sources, subckt_instances,
     includes, comments, subcircuits, title) = parse_lines(lines)

    # Resolve top-level includes: parse .sub files
    for inc_file in includes:
        inc_path = _find_include_file(inc_file, search_paths)
        if inc_path:
            inc_dir = os.path.dirname(inc_path)
            child_paths = [inc_dir] + [p for p in search_paths if p != inc_dir]
            sub_netlist = parse_file(inc_path, search_paths=child_paths)
            # Merge subcircuits from included files
            subcircuits.update(sub_netlist.subcircuits)
        else:
            print(f"Warning: Could not resolve include file '{inc_file}' in search paths: {search_paths}")

    # Also resolve includes inside subcircuit definitions.
    # Example: full_adder.sub has ".include half_adder.sub" inside
    # the .subckt block, or 74151.sub has ".include 4_and.sub".
    _resolve_subckt_includes(subcircuits, search_paths)

    return ParsedNetlist(
        title=title,
        includes=includes,
        instances=instances,
        models=models,
        voltage_sources=voltage_sources,
        subckt_instances=subckt_instances,
        subcircuits=subcircuits,
        comments=comments,
    )


def parse_string(text, search_paths=None):
    """Parse SPICE netlist text into a ParsedNetlist."""
    lines = text.splitlines(keepends=True)
    (instances, models, voltage_sources, subckt_instances,
     includes, comments, subcircuits, title) = parse_lines(lines)

    if search_paths is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        search_paths = [os.path.join(repo_root, "library", "SubcircuitLibrary")]

    _resolve_subckt_includes(subcircuits, search_paths)

    return ParsedNetlist(
        title=title,
        includes=includes,
        instances=instances,
        models=models,
        voltage_sources=voltage_sources,
        subckt_instances=subckt_instances,
        subcircuits=subcircuits,
        comments=comments,
    )



def _resolve_subckt_includes(subcircuits, search_paths):
    """Resolve .include directives inside subcircuit definitions.

    Uses a worklist queue to transitively resolve nested subcircuit includes
    at any arbitrary depth (e.g. 74151 -> 4_and -> 3_and).
    """
    processed = set()
    while True:
        current_names = [n for n in subcircuits.keys() if n not in processed]
        if not current_names:
            break

        for name in current_names:
            processed.add(name)
            subckt = subcircuits[name]
            for inc_file in subckt.includes:
                inc_path = _find_include_file(inc_file, search_paths)
                if inc_path:
                    inc_dir = os.path.dirname(inc_path)
                    child_paths = [inc_dir] + [p for p in search_paths if p != inc_dir]
                    sub_netlist = parse_file(inc_path, search_paths=child_paths)
                    # Merge any newly discovered subcircuits
                    for sc_name, sc_def in sub_netlist.subcircuits.items():
                        if sc_name not in subcircuits:
                            subcircuits[sc_name] = sc_def
                else:
                    print(f"Warning: Subcircuit '{name}' could not resolve include '{inc_file}'")

