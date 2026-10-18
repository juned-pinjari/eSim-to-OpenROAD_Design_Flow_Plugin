#!/usr/bin/env python3
# =====================================================================
#          FILE: test_74_series_sweep.py
#
#   DESCRIPTION: Comprehensive verification sweep across all digital
#                74-series and 54-series TTL IC subcircuit models in
#                eSim's SubcircuitLibrary.
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-10-15
# =====================================================================

"""Comprehensive 74-Series & 54-Series Digital Subcircuit Test Suite.

Verifies:
  1. Multi-level subcircuit dependency resolution via search paths.
  2. Hierarchical inlining & net scoping ({inst}_{net}).
  3. Verilog code emission (structural combinational + behavioral sequential).
  4. iverilog syntax check.
  5. Yosys technology mapping and gate count reporting.
"""

import os
import shutil
import sys
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "maker"))

from spice_parser import parse_file, ParsedNetlist, SubcktInstance
from circuit_graph import extract_circuit
from verilog_emitter import emit_verilog

# 74/54-series digital subcircuit catalog in library/SubcircuitLibrary (55 Verified Digital IC Models)
TTL_DIGITAL_CATALOG = [
    # Multiplexers / Data Selectors
    ("74151",          "SN74LS151/74151.sub",                   "8-to-1 Multiplexer"),
    ("74153",          "74153/74153.sub",                       "Dual 4-to-1 Multiplexer"),
    ("74157",          "74157/74157.sub",                       "Quad 2-to-1 Multiplexer"),
    ("74HC157",        "74HC157/74HC157.sub",                   "Quad 2-to-1 Multiplexer (HC)"),
    ("SN74351",        "SN74351/SN74351.sub",                   "Dual 8-to-1 Data Selector"),
    ("HD74HC149",      "HD74HC149/74149.sub",                   "8-to-1 Multiplexer (HC)"),
    ("HD74LS152",      "HD74LS152/HD74LS152.sub",               "8-to-1 Multiplexer (LS)"),
    ("SN74LVC257A",    "SN74LVC257A/SN74LVC257A.sub",           "Quad 2-to-1 Multiplexer (3-State)"),

    # Decoders & Demultiplexers
    ("SN74LS138",      "SN74LS138/SN74LS138.sub",               "3-to-8 Line Decoder"),
    ("HD74LS139",      "HD74LS139/74139.sub",                   "Dual 2-to-4 Line Decoder"),
    ("MC74HC238",      "MC74HC238/MC74HC238.sub",               "3-to-8 Line Decoder (Active High)"),
    ("SN54154",        "SN54154/SN54154.sub",                   "4-to-16 Line Decoder (SN)"),
    ("DM54154",        "DM54154/DM54154.sub",                   "4-to-16 Line Decoder (DM)"),
    ("SN74LS42",       "SN74LS42_IC/SN74LS42.sub",               "BCD-to-Decimal Decoder"),
    ("SN5442A",        "SN5442A_sub/SN5442A_IC.sub",             "BCD-to-Decimal Decoder (54A)"),
    ("DM7447A",        "DM7447A/7447.sub",                      "BCD-to-7-Segment Decoder (DM)"),
    ("SN74LS47",       "SN74LS47/SN74LS47.sub",                 "BCD-to-7-Segment Decoder (LS)"),
    ("sn54ls48",       "esim_ic_files/sn54ls48/74ls47.sub",     "BCD-to-7-Segment Decoder (54LS)"),
    ("sn7445",         "sn7445/sn7445.sub",                     "BCD-to-Decimal Decoder/Driver"),
    ("SN74LVC1G139",   "SN74LVC1G139/SN74LVC1G139.sub",         "2-to-4 Line Decoder (1G)"),

    # Encoders & Priority Encoders
    ("74HC147",        "74HC147N/74HC147.sub",                  "10-to-4 Priority Encoder (HC)"),
    ("SN54147",        "SN54147_sub/SN54147_IC.sub",             "10-to-4 Priority Encoder (54)"),
    ("SN74LS148",      "SN74LS148/74148.sub",                   "8-to-3 Priority Encoder (LS)"),
    ("SN74LS148_IC",   "SN74LS148_sub/SN74LS148_IC.sub",        "8-to-3 Priority Encoder (Sub)"),

    # Arithmetic & Comparators
    ("7485",           "7485/7485.sub",                         "4-Bit Magnitude Comparator"),
    ("SN5485",         "SN5485/SN5485.sub",                     "4-Bit Magnitude Comparator (54)"),
    ("SN74HC688",      "SN74HC688/74688.sub",                   "8-Bit Magnitude Comparator"),
    ("74HC688_sub",    "74HC688_sub/74HC688_sub.sub",           "8-Bit Magnitude Comparator (HC)"),
    ("SN7483A",        "SN7483A_sub/SN7483A_sub.sub",           "4-Bit Binary Full Adder"),
    ("74AC283",        "esim_ic_files/cdx4ac283/74AC283.sub",   "4-Bit Binary Adder (Fast Carry)"),
    ("SN54LS183",      "SN54LS183/SN54LS183.sub",               "Dual Carry-Save Full Adder"),
    ("SN74ALS280",     "SN74ALS280/SN74ALS280.sub",             "9-Bit Parity Generator/Checker (ALS)"),
    ("SN54180",        "SN54180/SN54180.sub",                   "9-Bit Parity Generator/Checker (54)"),
    ("CY74FCT480T",    "CY74FCT480T/74480.sub",                 "8-Bit Parity Checker (FCT)"),
    ("74ACT11286",     "74ACT11286/74ACT11286.sub",             "9-Bit Parity Generator/Checker (ACT)"),

    # Latches, Flip-Flops, Registers & Counters
    ("SN74LS76",       "SN74LS76/SN74LS76.sub",                 "Dual J-K Flip-Flop with Preset/Clr"),
    ("DM74LS279",      "DM74LS279/74LS279.sub",                 "Quad S-R Latch"),
    ("74HC175",        "74HC175/74HC175.sub",                   "Quad D-Type Flip-Flop with Clr"),
    ("74VHC373",       "74VHC373-D/74VHC373-D.sub",             "Octal D-Type Transparent Latch"),
    ("sn54als573",     "esim_ic_files/sn54als573/ls373.sub",   "Octal D-Type Transparent Latch (54)"),
    ("sn74ls375",      "esim_ic_files/sn74ls375/375.sub",       "Quad Bistable Latch"),
    ("74ls90",         "esim_ic_files/sn54ls90/74ls90.sub",     "Decade Counter"),
    ("74LS95B",        "74LS95B/74LS95B.sub",                   "4-Bit Shift Register"),
    ("74HC193",        "74HC193/74HC193.sub",                   "4-Bit Synchronous Up/Down Counter"),
    ("74HC4020",       "74HC4020/74HC4020.sub",                 "14-Stage Binary Ripple Counter"),
    ("74HC595",        "74HC595/74HC595.sub",                   "8-Bit Shift Register with Storage"),
    ("74F350",         "74F350/74F350.sub",                     "4-Bit Shifter with 3-State Out"),

    # Multi-Input Logic & Bus Gates
    ("74HC27",         "74HC27/74HC27.sub",                     "Triple 3-Input NOR Gate"),
    ("74HC86",         "74HC86/74HC86.sub",                     "Quad 2-Input XOR Gate"),
    ("74HC125",        "74HC125/74HC125.sub",                   "Quad Bus Buffer (3-State)"),
    ("74LVC1G57",      "74LVC1G57/74LVC1G57.sub",               "Configurable Multi-Function Gate"),
    ("74LVC1G97",      "74LVC1G97/74LVC1G97.sub",               "Configurable Multi-Function Gate"),
    ("SN74LVC1G29",    "SN74LVC1G29/7429.sub",                  "2-Input Configurable Gate"),
    ("54act11030",     "esim_ic_files/54act11030/74_1030.sub",  "8-Input NAND Gate (ACT)"),
    ("sn54als133",     "esim_ic_files/sn54als133/133.sub",      "13-Input NAND Gate (ALS)"),
]


def _run_cmd(cmd, cwd=REPO_ROOT, timeout=30):
    try:
        res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


def main():
    print("=" * 72)
    print("  74-SERIES & 54-SERIES TTL DIGITAL IC COMPREHENSIVE SWEEP")
    print("=" * 72)

    has_iverilog = shutil.which("iverilog") is not None
    has_yosys = shutil.which("yosys") is not None

    if not has_iverilog:
        print("NOTE: iverilog not found in PATH, skipping simulation checks.")
    if not has_yosys:
        print("NOTE: yosys not found in PATH, skipping synthesis checks.")

    lib_base = os.path.join(REPO_ROOT, "library", "SubcircuitLibrary")
    out_dir = os.path.join(REPO_ROOT, "scratch", "74_sweep")
    os.makedirs(out_dir, exist_ok=True)

    passed = 0
    failed = 0
    results = []

    for name, rel_path, desc in TTL_DIGITAL_CATALOG:
        full_path = os.path.join(lib_base, rel_path)
        if not os.path.exists(full_path):
            results.append((name, desc, "SKIP", f"File not found: {rel_path}", 0, 0))
            continue

        try:
            parsed = parse_file(full_path)
            # Find the main subcircuit
            target_sc = None
            for sc in parsed.subcircuits.values():
                if len(sc.instances) > 0 or len(sc.subckt_instances) > 0:
                    target_sc = sc
                    break

            if target_sc is None:
                results.append((name, desc, "SKIP", "No digital primitives in subcircuit", 0, 0))
                continue

            # Instantiate the subcircuit as a top-level module
            top_inst = SubcktInstance(name="x1", nets=list(target_sc.ports), subckt_name=target_sc.name)
            top_parsed = ParsedNetlist(
                title=f"74-Series Sweep: {name}",
                subckt_instances=[top_inst],
                subcircuits=parsed.subcircuits,
                models=parsed.models,
            )

            mod_name = f"m_{name}"
            circuit = extract_circuit(top_parsed, mod_name)
            verilog = emit_verilog(circuit)

            v_path = os.path.join(out_dir, f"{name}.v")
            with open(v_path, "w", encoding="utf-8") as vf:
                vf.write(verilog)

            # iverilog syntax check
            if has_iverilog:
                rc, out, err = _run_cmd(f"iverilog -tnull {v_path}")
                if rc != 0:
                    results.append((name, desc, "FAIL", f"iverilog: {err.strip()[:40]}", len(circuit.gates), 0))
                    failed += 1
                    continue

            # yosys synthesis check
            cell_count = 0
            if has_yosys:
                ys_cmd = f"yosys -p 'read_verilog {v_path}; synth -top {mod_name}; stat'"
                rc, out, err = _run_cmd(ys_cmd)
                if rc != 0:
                    results.append((name, desc, "FAIL", f"yosys: {err.strip()[:40]}", len(circuit.gates), 0))
                    failed += 1
                    continue
                # Extract cell count from yosys stat output
                for line in out.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[1] == "cells" and parts[0].isdigit():
                        cell_count = int(parts[0])
                        # Keep looking for the last/final stat block


            results.append((name, desc, "PASS", "ok", len(circuit.gates), cell_count))
            passed += 1

        except Exception as e:
            results.append((name, desc, "FAIL", str(e)[:50], 0, 0))
            failed += 1

    # Print summary table
    print("\n" + "=" * 72)
    print("  SWEEP RESULTS TABLE")
    print("=" * 72)
    print(f"  {'IC Name':<14} {'Description':<32} {'Status':<8} {'Gates':<6} {'Cells':<6}")
    print("  " + "-" * 68)
    for name, desc, status, note, gates, cells in results:
        cells_str = str(cells) if cells > 0 else "-"
        print(f"  {name:<14} {desc:<32} {status:<8} {gates:<6} {cells_str:<6}")

    print("=" * 72)
    print(f"  TOTAL: {len(results)}  |  PASS: {passed}  |  FAIL: {failed}")
    print("=" * 72 + "\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
