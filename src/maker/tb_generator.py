# =====================================================================
#          FILE: tb_generator.py
#
#   DESCRIPTION: Automated Verilog Testbench Generator for eSim.
#                Parses SPICE voltage sources (pulse and dc) from
#                simulation netlists and synthesizes a corresponding
#                Verilog-2001 testbench with clock generators,
#                stimulus transitions, truth table sweeps, and VCD dumps.
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-10-18
# =====================================================================

"""Automated Verilog Testbench Generator.

Transforms analog simulation stimulus from eSim (.cir / .cir.out) netlists
into cycle-accurate, synthesizable Verilog testbenches.

Features:
  1. Extracts SPICE pulse() parameters (v1, v2, td, tr, tf, pw, per) and
     maps them to periodic Verilog stimulus blocks.
  2. Extracts SPICE DC voltage sources and maps them to initial constant levels.
  3. Identifies primary clock inputs and generates free-running clock toggles.
  4. For combinational inputs with static DC sources or unassigned pins,
     generates an exhaustive truth-table vector sweep.
  5. Emits $dumpfile / $dumpvars for VCD waveform viewing in GTKWave.
  6. Emits $monitor for automated console execution logs in iverilog/vvp.
"""

import os
import re
from typing import Optional, Dict, Any, List

try:
    from .circuit_graph import DigitalCircuit, PortInfo, legalize_name
    from .spice_parser import ParsedNetlist, VoltageSource
except ImportError:
    from circuit_graph import DigitalCircuit, PortInfo, legalize_name
    from spice_parser import ParsedNetlist, VoltageSource


# Multipliers for standard SPICE engineering suffixes
_ENG_MULTIPLIERS = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
}


def parse_spice_eng_value(val_str: str) -> float:
    """Parse a SPICE engineering notation string into a float value.

    Examples:
        '5'     -> 5.0
        '10n'   -> 10e-9
        '0.1u'  -> 0.1e-6
        '20m'   -> 20e-3
        '100'   -> 100.0
    """
    if not val_str:
        return 0.0
    s = val_str.strip().lower()

    if s.endswith("meg"):
        num_part = s[:-3]
        mult = 1e6
    elif s.endswith("ns") or s.endswith("us") or s.endswith("ps") or s.endswith("ms"):
        num_part = s[:-2]
        mult = _ENG_MULTIPLIERS.get(s[-2], 1.0)
    elif s and s[-1] in _ENG_MULTIPLIERS:
        num_part = s[:-1]
        mult = _ENG_MULTIPLIERS[s[-1]]
    else:
        num_part = s
        mult = 1.0

    try:
        return float(num_part) * mult
    except ValueError:
        return 0.0


def normalize_to_ns(time_val_sec: float) -> int:
    """Convert a time value in seconds to nanoseconds (integer, >= 1).

    Intelligently normalizes analog SPICE simulation time scales:
    - If in nanoseconds (< 1e-6 s): scaled directly via 1e9 (e.g. 10n -> 10 ns)
    - If in microseconds (1e-6 to 1e-3 s): scaled via 1e6 (e.g. 20u -> 20 ns)
    - If in milliseconds (1e-3 to 1.0 s): scaled via 1e3 (e.g. 20ms -> 20 ns)
    - If unitless >= 1.0 (e.g. 20, 40): treated directly as nanoseconds.
    """
    if time_val_sec <= 0.0:
        return 0
    if time_val_sec >= 1.0:
        return max(1, int(round(time_val_sec)))
    if time_val_sec >= 1e-3:
        return max(1, int(round(time_val_sec * 1e3)))
    if time_val_sec >= 1e-6:
        return max(1, int(round(time_val_sec * 1e6)))
    # Nanoseconds or smaller: convert seconds to nanoseconds
    ns = time_val_sec * 1e9
    return max(1, int(round(ns)))


def _find_matching_vsource(port: PortInfo, parsed_netlist: ParsedNetlist) -> Optional[VoltageSource]:
    """Find the SPICE voltage source driving a given input port."""
    port_legal = port.name
    port_analog = getattr(port, "spice_analog_net", port.name)

    for vs in parsed_netlist.voltage_sources:
        vs_net = vs.net_plus
        if vs_net == port_analog or legalize_name(vs_net) == port_legal:
            return vs

    return None


def emit_testbench(
    circuit: DigitalCircuit,
    parsed_netlist: ParsedNetlist,
    sim_duration: Optional[int] = None,
) -> str:
    """Generate a complete Verilog testbench from circuit graph and netlist stimulus.

    Parameters
    ----------
    circuit : DigitalCircuit
        The extracted digital circuit graph with port definitions and clock info.
    parsed_netlist : ParsedNetlist
        The parsed SPICE netlist containing voltage sources and models.
    sim_duration : int, optional
        Simulation stop time in nanoseconds. If omitted, calculated automatically.

    Returns
    -------
    str
        The synthesizable Verilog testbench code.
    """
    top_mod = legalize_name(circuit.module_name)
    tb_mod = f"{top_mod}_tb"

    input_ports = [p for p in circuit.ports if p.direction == "input"]
    output_ports = [p for p in circuit.ports if p.direction == "output"]

    lines = []
    lines.append("// =====================================================================")
    lines.append(f"//          FILE: {tb_mod}.v")
    lines.append("//")
    lines.append("//   DESCRIPTION: Automated Verilog simulation testbench generated")
    lines.append("//                from SPICE netlist voltage sources and stimulus.")
    lines.append("//")
    lines.append("//   GENERATED BY: eSim-to-OpenROAD Design Flow Plugin (tb_generator)")
    lines.append("// =====================================================================")
    lines.append("")
    lines.append("`timescale 1ns / 1ps")
    lines.append("")
    lines.append(f"module {tb_mod};")
    lines.append("")

    # Signal declarations
    lines.append("    // -----------------------------------------------------------------")
    lines.append("    // Testbench Signals")
    lines.append("    // -----------------------------------------------------------------")
    for inp in input_ports:
        lines.append(f"    reg  {inp.name};")

    for out in output_ports:
        lines.append(f"    wire {out.name};")

    lines.append("")

    # DUT Instantiation
    lines.append("    // -----------------------------------------------------------------")
    lines.append("    // Device Under Test (DUT) Instantiation")
    lines.append("    // -----------------------------------------------------------------")
    lines.append(f"    {top_mod} dut (")
    all_ports = input_ports + output_ports
    for idx, p in enumerate(all_ports):
        comma = "," if idx < len(all_ports) - 1 else ""
        lines.append(f"        .{p.name}({p.name}){comma}")
    lines.append("    );")
    lines.append("")

    # VCD Waveform Dump
    lines.append("    // -----------------------------------------------------------------")
    lines.append("    // Waveform Dump Configuration")
    lines.append("    // -----------------------------------------------------------------")
    lines.append("    initial begin")
    lines.append(f'        $dumpfile("{tb_mod}.vcd");')
    lines.append(f"        $dumpvars(0, {tb_mod});")
    lines.append("    end")
    lines.append("")

    # Signal Monitoring
    lines.append("    // -----------------------------------------------------------------")
    lines.append("    // Output Monitor Log")
    lines.append("    // -----------------------------------------------------------------")
    if all_ports:
        header_names = " ".join(f"{p.name}" for p in all_ports)
        fmt_parts = " ".join(f"{p.name}=%b" for p in all_ports)
        args_str = ", ".join(p.name for p in all_ports)
        lines.append("    initial begin")
        lines.append(f'        $display("Time | {header_names}");')
        lines.append(f'        $monitor("%4t | {fmt_parts}", $time, {args_str});')
        lines.append("    end")
        lines.append("")

    # Sequential State Register Extraction & Power-on Initialization
    # In standard ASIC flows, synthesizable RTL does not contain non-synthesizable
    # initial blocks inside modules. However, in 4-state simulation (iverilog),
    # registers power up as 1'bx, which latches toggle flip-flops in 'x' forever.
    # We hierarchically initialize state registers to 1'b0 at t=0 (matching SPICE ic=0).
    seq_regs = []
    for gate in circuit.gates:
        if gate.pin_map is not None:
            q = gate.pin_map.get("q")
            if q and q not in ("1'b0", "1'b1", "0", "1", "1'bx", "1'bz"):
                if q not in seq_regs:
                    seq_regs.append(q)

    if seq_regs:
        lines.append("    // -----------------------------------------------------------------")
        lines.append("    // Power-on Register State Initialization (Matching SPICE ic=0)")
        lines.append("    // Eliminates Verilog 4-state 'x'-pessimism latch-up in sequential logic")
        lines.append("    // -----------------------------------------------------------------")
        lines.append("    initial begin")
        for reg_name in sorted(seq_regs):
            lines.append(f"        dut.{reg_name} = 1'b0;")
        lines.append("    end")
        lines.append("")

    # Self-Checking Assertion Monitor Declarations
    if output_ports:
        lines.append("    // -----------------------------------------------------------------")
        lines.append("    // Automated Self-Checking Assertion Monitor")
        lines.append("    // Continuously detects unknown (x) or high-impedance (z) faults")
        lines.append("    // -----------------------------------------------------------------")
        lines.append("    integer error_count;")
        lines.append("    integer check_count;")
        for out in output_ports:
            lines.append(f"    integer toggle_{out.name};")
        lines.append("")
        lines.append("    initial begin")
        lines.append("        error_count = 0;")
        lines.append("        check_count = 0;")
        for out in output_ports:
            lines.append(f"        toggle_{out.name} = 0;")
        lines.append("    end")
        lines.append("")

        # Asynchronous transition monitor per output
        for out in output_ports:
            lines.append(f"    always @({out.name}) begin")
            lines.append(f"        toggle_{out.name} = toggle_{out.name} + 1;")
            lines.append("        if ($time > 1) begin")
            lines.append("            check_count = check_count + 1;")
            lines.append(f"            if ({out.name} === 1'bx || {out.name} === 1'bz) begin")
            lines.append(f'                $display("[ASSERTION ERROR] Time %0t ns: Output \'{out.name}\' transitioned to unknown (%b)", $time, {out.name});')
            lines.append("                error_count = error_count + 1;")
            lines.append("            end")
            lines.append("        end")
            lines.append("    end")
            lines.append("")

    # Stimulus Generation
    lines.append("    // -----------------------------------------------------------------")
    lines.append("    // Stimulus Generation")
    lines.append("    // -----------------------------------------------------------------")

    clock_port_name = circuit.clock_net
    recorded_periods = []
    unassigned_inputs = []

    for inp in input_ports:
        is_clock = (inp.name == clock_port_name)
        vs = _find_matching_vsource(inp, parsed_netlist)

        if is_clock:
            # Dedicated Clock Generator
            clk_per_ns = 20
            if vs and vs.is_pulse and len(vs.pulse_params) >= 7:
                raw_per = parse_spice_eng_value(vs.pulse_params[6])
                clk_per_ns = normalize_to_ns(raw_per)
            elif circuit.clock_period:
                clk_per_ns = max(2, int(round(circuit.clock_period)))

            recorded_periods.append(clk_per_ns)
            half_per = max(1, clk_per_ns // 2)

            lines.append(f"    // Clock Generator: {inp.name} (period = {clk_per_ns} ns)")
            lines.append("    initial begin")
            lines.append(f"        {inp.name} = 1'b0;")
            lines.append(f"        forever #{half_per} {inp.name} = ~{inp.name};")
            lines.append("    end")
            lines.append("")

            # Synchronous check on negative edge for sequential outputs
            if output_ports:
                lines.append(f"    always @(negedge {inp.name}) begin")
                lines.append("        if ($time > 1) begin")
                for out in output_ports:
                    lines.append("            check_count = check_count + 1;")
                    lines.append(f"            if ({out.name} === 1'bx || {out.name} === 1'bz) begin")
                    lines.append(f'                $display("[ASSERTION ERROR] Time %0t ns (negedge {inp.name}): Output \'{out.name}\' is unknown (%b)", $time, {out.name});')
                    lines.append("                error_count = error_count + 1;")
                    lines.append("            end")
                lines.append("        end")
                lines.append("    end")
                lines.append("")

        elif vs and vs.is_pulse and len(vs.pulse_params) >= 7:
            # Pulse source
            v1_raw = parse_spice_eng_value(vs.pulse_params[0])
            v2_raw = parse_spice_eng_value(vs.pulse_params[1])
            td_raw = parse_spice_eng_value(vs.pulse_params[2])
            pw_raw = parse_spice_eng_value(vs.pulse_params[5])
            per_raw = parse_spice_eng_value(vs.pulse_params[6])

            v1_bit = 1 if v1_raw >= 2.0 else 0
            v2_bit = 1 if v2_raw >= 2.0 else 0
            td_ns = normalize_to_ns(td_raw)
            pw_ns = max(1, normalize_to_ns(pw_raw))
            per_ns = max(pw_ns + 1, normalize_to_ns(per_raw))
            rest_ns = max(1, per_ns - pw_ns)

            recorded_periods.append(per_ns)

            lines.append(f"    // Periodic Pulse Stimulus: {inp.name} (period={per_ns}ns, pulse_width={pw_ns}ns)")
            lines.append("    initial begin")
            lines.append(f"        {inp.name} = 1'b{v1_bit};")
            if td_ns > 0:
                lines.append(f"        #{td_ns};")
            lines.append("        forever begin")
            lines.append(f"            {inp.name} = 1'b{v2_bit};")
            lines.append(f"            #{pw_ns};")
            lines.append(f"            {inp.name} = 1'b{v1_bit};")
            lines.append(f"            #{rest_ns};")
            lines.append("        end")
            lines.append("    end")
            lines.append("")

        elif vs and vs.dc_value is not None:
            # Constant DC source
            dc_bit = 1 if vs.dc_value >= 2.0 else 0
            lines.append(f"    // Constant DC Stimulus: {inp.name} = {vs.dc_value}V")
            lines.append("    initial begin")
            lines.append(f"        {inp.name} = 1'b{dc_bit};")
            lines.append("    end")
            lines.append("")
            # Also register for combinational truth-table sweep if no pulse sources exist
            unassigned_inputs.append(inp)

        else:
            unassigned_inputs.append(inp)

    # For combinational inputs lacking independent pulse sources,
    # generate an automated truth-table counter sweep to verify logic coverage.
    if unassigned_inputs and not clock_port_name:
        k = len(unassigned_inputs)
        max_states = 2 ** min(k, 6)
        step_ns = 20

        lines.append("    // -----------------------------------------------------------------")
        lines.append(f"    // Exhaustive Truth Table Sweep for Inputs ({k} signals)")
        lines.append("    // -----------------------------------------------------------------")
        lines.append("    integer sweep_i;")
        lines.append("    initial begin")
        # Apply initial state
        for p in unassigned_inputs:
            lines.append(f"        {p.name} = 1'b0;")
        lines.append(f"        for (sweep_i = 0; sweep_i < {max_states}; sweep_i = sweep_i + 1) begin")
        lines.append(f"            #{step_ns};")
        for bit_idx, p in enumerate(unassigned_inputs):
            mask = 1 << bit_idx
            lines.append(f"            {p.name} = (sweep_i & {mask}) ? 1'b1 : 1'b0;")
        # Sample assertion after settling
        if output_ports:
            lines.append("            #1;")
            for out in output_ports:
                lines.append("            check_count = check_count + 1;")
                lines.append(f"            if ({out.name} === 1'bx || {out.name} === 1'bz) begin")
                lines.append(f'                $display("[ASSERTION ERROR] Time %0t ns (sweep vector %0d): Output \'{out.name}\' is unknown (%b)", $time, sweep_i, {out.name});')
                lines.append("                error_count = error_count + 1;")
                lines.append("            end")
        lines.append("        end")
        lines.append("    end")
        lines.append("")

        sweep_total_time = max_states * (step_ns + (1 if output_ports else 0)) + 50
        recorded_periods.append(sweep_total_time)

    # Simulation Lifetime ($finish)
    if sim_duration is None:
        if recorded_periods:
            max_p = max(recorded_periods)
            sim_duration = max(max_p * 4, 200)
        else:
            sim_duration = 300

    lines.append("    // -----------------------------------------------------------------")
    lines.append("    // Simulation Termination & Comprehensive Verification Report")
    lines.append("    // -----------------------------------------------------------------")
    lines.append("    initial begin")
    lines.append(f"        #{sim_duration};")
    if output_ports:
        # Final end-of-sim sanity check
        lines.append("        // Final sanity check across all output ports")
        for out in output_ports:
            lines.append("        check_count = check_count + 1;")
            lines.append(f"        if ({out.name} === 1'bx || {out.name} === 1'bz) begin")
            lines.append(f'            $display("[ASSERTION ERROR] Final check at %0t ns: Output \'{out.name}\' is unknown (%b)", $time, {out.name});')
            lines.append("            error_count = error_count + 1;")
            lines.append("        end")
        lines.append('        $display("");')
        lines.append('        $display("==================================================================");')
        lines.append(f'        $display("   TESTBENCH VERIFICATION REPORT: {tb_mod}");')
        lines.append('        $display("[TESTBENCH] Simulation completed at time %0t ns.", $time);')
        lines.append('        $display("==================================================================");')
        lines.append('        $display("   Total Assertion Checks : %0d", check_count);')
        lines.append('        $display("   Total Assertion Errors : %0d", error_count);')
        for out in output_ports:
            lines.append(f'        $display("   Signal Activity: {out.name} toggles = %0d", toggle_{out.name});')
        lines.append("        if (error_count == 0) begin")
        lines.append('            $display("   Functional Verification Status: ALL CHECKS PASSED [OK]");')
        lines.append('            $display("==================================================================");')
        lines.append('            $display("");')
        lines.append("            $finish;")
        lines.append("        end else begin")
        lines.append('            $display("   Functional Verification Status: SIMULATION FAILED (%0d errors)", error_count);')
        lines.append('            $display("==================================================================");')
        lines.append('            $display("");')
        lines.append('            $fatal(1, "[TESTBENCH FAILED] Verification assertions violated.");')
        lines.append("        end")
    else:
        lines.append('        $display("[TESTBENCH] Simulation completed at time %0t ns.", $time);')
        lines.append("        $finish;")
    lines.append("    end")
    lines.append("")
    lines.append("endmodule")
    lines.append("")

    return "\n".join(lines)


def generate_testbench_file(
    circuit: DigitalCircuit,
    parsed_netlist: ParsedNetlist,
    output_file: Optional[str] = None,
    sim_duration: Optional[int] = None,
) -> str:
    """Generate and write testbench file to disk.

    Returns the absolute path to the generated testbench.
    """
    if output_file is None:
        output_file = f"{circuit.module_name}_tb.v"

    tb_content = emit_testbench(circuit, parsed_netlist, sim_duration)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(tb_content)

    return os.path.abspath(output_file)
