#!/usr/bin/env python3
# =====================================================================
#          FILE: test_converter.py
#
#   DESCRIPTION: Automated regression suite for the Netlist-to-RTL
#                converter.  Runs each digital example through:
#                  parse → emit .v + .sdc → iverilog check → yosys synth
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-10-03
# =====================================================================

"""Regression test for the netlist converter.

Usage:
    python3 tests/test_converter.py
"""

import os
import sys
import subprocess

# ── Configuration ─────────────────────────────────────────────────

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

# Add src/maker to Python path so imports work
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "maker"))

from netlist2rtl import NetlistToRTL

# All known digital test cases.
# Format: (display_name, relative path to .cir.out from REPO_ROOT)
TEST_CASES = [
    # Phase 1: Combinational
    ("Half_Adder",         "Examples/Half_Adder/Half_Adder.cir.out"),
    ("BasicGates",         "Examples/BasicGates/BasicGates.cir.out"),
    ("FullAdder",          "Examples/FullAdder/FullAdder.cir.out"),

    # Phase 2: Sequential
    ("JK_Flipflop",        "Examples/JK_Flipflop/JK_Flipflop.cir.out"),
    ("4_bit_JK_ff",        "Examples/4_bit_JK_ff/4_bit_JK_ff.cir.out"),

    # Phase 4: Digital IC Analysis
    ("4002_test",          "Examples/Analysis_Of_Digital_IC/4002_test/4002_test.cir.out"),
    ("4012_test",          "Examples/Analysis_Of_Digital_IC/4012_test/4012_test.cir.out"),
    ("4023_test",          "Examples/Analysis_Of_Digital_IC/4023_test/4023_test.cir.out"),
    ("4073_test",          "Examples/Analysis_Of_Digital_IC/4073_test/4073_test.cir.out"),
    ("3_Input_NAND",       "Examples/Analysis_Of_Digital_IC/3_Input_NAND_Characteristics/3_Input_NAND_Characteristics.cir.out"),
    ("4_Input_NAND",       "Examples/Analysis_Of_Digital_IC/4_Input_NAND_Charcateristics/4_Input_NAND_Charcateristics.cir.out"),
    ("4_Input_NOR",        "Examples/Analysis_Of_Digital_IC/4_Input_NOR_Characteristics/4_Input_NOR_Characteristics.cir.out"),
    ("74LS04-test",        "Examples/Analysis_Of_Digital_IC/74LS04-test/74LS04-test.cir.out"),
    ("BCDToDecimalDecoder", "Examples/Analysis_Of_Digital_IC/BCDToDecimalDecoder/BCDToDecimalDecoder.cir.out"),
]

# Circuits known to be unsupported (transistor-level, mixed-signal, etc.)
# These should fail gracefully with "No digital gates found".
EXPECTED_FAILURES = {
    "74LS04-test",   # transistor-level subcircuit (q-devices, not XSPICE)
}


# ── Helpers ───────────────────────────────────────────────────────

def _run_cmd(cmd, cwd=REPO_ROOT, timeout=30):
    """Run a shell command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


def _check_iverilog(verilog_path):
    """Syntax-check a Verilog file with iverilog."""
    rc, out, err = _run_cmd(f"iverilog -tnull {verilog_path}")
    return rc == 0, err.strip()


def _check_yosys(verilog_path, module_name):
    """Synthesize a Verilog file with yosys and return cell count."""
    cmd = (
        f"yosys -p '"
        f"read_verilog {verilog_path}; "
        f"synth -top {module_name}; "
        f"stat'"
    )
    rc, out, err = _run_cmd(cmd)
    if rc != 0:
        return False, err.strip(), 0

    # Extract cell count from yosys output
    cells = 0
    for line in out.split("\n"):
        line = line.strip()
        if "cells" in line and "Number" not in line:
            # Lines like "   7 cells" or "      7   cells"
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "cells":
                try:
                    cells = int(parts[0])
                except ValueError:
                    pass

    return True, "", cells


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 70)
    print("  NETLIST-TO-RTL CONVERTER — REGRESSION TEST SUITE")
    print("=" * 70 + "\n")

    results = []
    pass_count = 0
    fail_count = 0
    skip_count = 0

    for name, rel_path in TEST_CASES:
        cir_path = os.path.join(REPO_ROOT, rel_path)
        print(f"  [{name}]", end=" ... ", flush=True)

        # Check if input file exists
        if not os.path.exists(cir_path):
            print("SKIP (file not found)")
            results.append((name, "SKIP", "Input file not found", "", ""))
            skip_count += 1
            continue

        # Step 1: Convert
        try:
            converter = NetlistToRTL(cir_path)
            output_file = converter.convert()
            module_name = converter.module_name
        except Exception as e:
            if name in EXPECTED_FAILURES:
                print(f"XFAIL (expected: {e})")
                results.append((name, "XFAIL", str(e), "", ""))
                skip_count += 1
                continue
            print(f"FAIL (converter: {e})")
            results.append((name, "FAIL", f"Converter error: {e}", "", ""))
            fail_count += 1
            continue

        # Check .v file was created
        if not os.path.exists(output_file):
            print("FAIL (.v not created)")
            results.append((name, "FAIL", ".v file not created", "", ""))
            fail_count += 1
            continue

        # Check .sdc file was created
        sdc_file = output_file.replace(".v", ".sdc")
        sdc_ok = os.path.exists(sdc_file)

        # Step 2: iverilog check
        iv_ok, iv_err = _check_iverilog(output_file)
        if not iv_ok:
            print(f"FAIL (iverilog: {iv_err[:80]})")
            results.append((name, "FAIL", f"iverilog: {iv_err}", "", ""))
            fail_count += 1
            continue

        # Step 3: yosys synthesis
        ys_ok, ys_err, cells = _check_yosys(output_file, module_name)
        if not ys_ok:
            print(f"FAIL (yosys: {ys_err[:80]})")
            results.append((name, "FAIL", f"yosys: {ys_err}", "", ""))
            fail_count += 1
            continue

        sdc_status = "✅" if sdc_ok else "❌"
        print(f"PASS (cells={cells}, sdc={sdc_status})")
        results.append((name, "PASS", "", str(cells), sdc_status))
        pass_count += 1

    # ── Summary Table ─────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    print(f"\n  {'Circuit':<30} {'Status':<8} {'Cells':<8} {'SDC':<5} Notes")
    print(f"  {'-'*30} {'-'*8} {'-'*8} {'-'*5} {'-'*30}")

    for name, status, notes, cells, sdc in results:
        status_icon = "✅" if status == "PASS" else \
                      "⚠️ " if status in ("SKIP", "XFAIL") else "❌"
        short_notes = notes[:40] if notes else ""
        print(f"  {name:<30} {status_icon:<8} {cells:<8} {sdc:<5} "
              f"{short_notes}")

    total = len(TEST_CASES)
    print(f"\n  Total: {total}  |  "
          f"Pass: {pass_count}  |  "
          f"Fail: {fail_count}  |  "
          f"Skip: {skip_count}")
    print("=" * 70 + "\n")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
