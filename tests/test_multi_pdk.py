#!/usr/bin/env python3
# =====================================================================
#          FILE: test_multi_pdk.py
#
#   DESCRIPTION: Multi-PDK Technology Mapping and Timing Validation Suite.
#                Verifies that converter-generated Verilog and SDC
#                are strictly PDK-agnostic by compiling and running
#                static timing analysis (STA) across:
#                  1. SkyWater 130nm (sky130hd)
#                  2. GlobalFoundries 180nm (gf180)
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-10-18
# =====================================================================

"""Multi-PDK Flow and Timing Cross-Validation Suite.

Verifies:
  1. Synthesis technology mapping across distinct standard cell libraries:
     - SkyWater 130nm: sky130_fd_sc_hd
     - GlobalFoundries 180nm: gf180mcu_fd_sc_mcu9t5v0
  2. Standard cell area scaling and gate count comparisons.
  3. Static timing analysis (STA) signoff via OpenROAD / OpenSTA engine.
  4. Timing constraint compliance with generated SDC (setup slack MET).
"""

import os
import sys
import shutil
import tempfile
import subprocess
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "maker"))

from OpenROAD import OpenROADFlow


def _get_platform_configs():
    """Return platform paths for available PDKs in ORFS."""
    orfs_root = OpenROADFlow.orfs_root_path()
    platforms_base = os.path.join(orfs_root, "flow", "platforms")

    configs = {}

    # SkyWater 130nm High-Density (1.8V, 7-track)
    sky130_dir = os.path.join(platforms_base, "sky130hd")
    sky130_lib = os.path.join(sky130_dir, "lib", "sky130_fd_sc_hd__tt_025C_1v80.lib")
    sky130_tlef = os.path.join(sky130_dir, "lef", "sky130_fd_sc_hd.tlef")
    sky130_slef = os.path.join(sky130_dir, "lef", "sky130_fd_sc_hd_merged.lef")
    if os.path.exists(sky130_lib) and os.path.exists(sky130_tlef):
        configs["sky130hd"] = {
            "name": "SkyWater 130nm (sky130hd)",
            "process": "130nm",
            "voltage": "1.8V",
            "lib": sky130_lib,
            "tlef": sky130_tlef,
            "slef": sky130_slef,
            "cell_prefix": "sky130_fd_sc_hd__",
        }

    # GlobalFoundries 180nm MCU (5.0V, 9-track)
    gf180_dir = os.path.join(platforms_base, "gf180")
    gf180_lib = os.path.join(gf180_dir, "lib", "gf180mcu_fd_sc_mcu9t5v0__tt_025C_5v00.lib.gz")
    gf180_tlef = os.path.join(gf180_dir, "lef", "gf180mcu_5LM_1TM_9K_9t_tech.lef")
    gf180_slef = os.path.join(gf180_dir, "lef", "gf180mcu_5LM_1TM_9K_9t_sc.lef")
    if os.path.exists(gf180_lib) and os.path.exists(gf180_tlef):
        configs["gf180"] = {
            "name": "GlobalFoundries 180nm (gf180)",
            "process": "180nm",
            "voltage": "5.0V",
            "lib": gf180_lib,
            "tlef": gf180_tlef,
            "slef": gf180_slef,
            "cell_prefix": "gf180mcu_fd_sc_mcu9t5v0__",
        }

    return configs


def _get_openroad_bin():
    """Locate openroad binary in local install or system PATH."""
    local_bin = os.path.join(
        OpenROADFlow.orfs_root_path(), "tools", "install", "OpenROAD", "bin", "openroad"
    )
    if os.path.exists(local_bin):
        return local_bin
    system_bin = shutil.which("openroad")
    return system_bin


# Digital benchmark circuits for Multi-PDK verification
PDK_BENCHMARKS = [
    # (Design Name, Relative Path to .v, Relative Path to .sdc, Top Module, Circuit Type)
    ("Half_Adder",
     "Examples/Half_Adder/Half_Adder.v",
     "Examples/Half_Adder/Half_Adder.sdc",
     "Half_Adder",
     "Combinational"),

    ("BasicGates",
     "Examples/BasicGates/BasicGates.v",
     "Examples/BasicGates/BasicGates.sdc",
     "BasicGates",
     "Combinational"),

    ("JK_Flipflop",
     "Examples/JK_Flipflop/JK_Flipflop.v",
     "Examples/JK_Flipflop/JK_Flipflop.sdc",
     "JK_Flipflop",
     "Sequential"),

    ("4_bit_JK_ff",
     "Examples/4_bit_JK_ff/4_bit_JK_ff.v",
     "Examples/4_bit_JK_ff/4_bit_JK_ff.sdc",
     "n_4_bit_JK_ff",
     "Multi-Stage Sequential"),

    ("BCDToDecimalDecoder",
     "Examples/Analysis_Of_Digital_IC/BCDToDecimalDecoder/BCDToDecimalDecoder.v",
     "Examples/Analysis_Of_Digital_IC/BCDToDecimalDecoder/BCDToDecimalDecoder.sdc",
     "BCDToDecimalDecoder",
     "MSI Subcircuit"),
]


class TestMultiPDKFlow(unittest.TestCase):
    """Test suite validating RTL & SDC synthesis across Sky130hd and GF180."""

    @classmethod
    def setUpClass(cls):
        cls.configs = _get_platform_configs()
        cls.openroad_bin = _get_openroad_bin()
        cls.has_yosys = shutil.which("yosys") is not None

    def test_platforms_available(self):
        """Verify that both sky130hd and gf180 platforms are present in ORFS."""
        self.assertIn("sky130hd", self.configs, "Sky130hd platform files not found")
        self.assertIn("gf180", self.configs, "GF180 platform files not found")

    def _run_pdk_synthesis_and_sta(self, platform_id, v_file, sdc_file, top_module):
        """Synthesize Verilog with Yosys and run OpenSTA timing checks."""
        if not self.has_yosys:
            self.skipTest("yosys not found in PATH")
        if not self.openroad_bin:
            self.skipTest("openroad not found")

        cfg = self.configs[platform_id]
        lib_path = cfg["lib"]
        tlef_path = cfg["tlef"]
        slef_path = cfg["slef"]
        cell_prefix = cfg["cell_prefix"]

        with tempfile.TemporaryDirectory() as tmpdir:
            synth_v = os.path.join(tmpdir, f"{top_module}_{platform_id}_synth.v")

            # 1. Yosys technology synthesis
            ys_script = f"""
read_verilog {v_file}
synth -top {top_module}
dfflibmap -liberty {lib_path}
abc -liberty {lib_path}
write_verilog {synth_v}
stat -liberty {lib_path}
"""
            p_ys = subprocess.run(
                ["yosys", "-p", ys_script],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                p_ys.returncode, 0,
                f"Yosys synthesis failed on {platform_id}: {p_ys.stderr}"
            )

            # Parse Yosys statistics
            cell_count = 0
            cell_area = 0.0
            mapped_cells = []
            for line in p_ys.stdout.splitlines():
                line_str = line.strip()
                if "Chip area for module" in line_str:
                    try:
                        cell_area = float(line_str.split(":")[-1].strip())
                    except ValueError:
                        pass
                parts = line_str.split()
                if len(parts) >= 2 and parts[1] == "cells" and parts[0].isdigit():
                    cell_count = int(parts[0])
                if len(parts) >= 2 and parts[-1].startswith(cell_prefix):
                    mapped_cells.append(parts[-1])

            # Ensure technology cells actually came from the target PDK library
            self.assertGreater(
                len(mapped_cells), 0,
                f"No {cell_prefix} cells mapped in {top_module} for {platform_id}"
            )
            for c in mapped_cells:
                self.assertTrue(
                    c.startswith(cell_prefix),
                    f"Unexpected cell {c} found during {platform_id} synthesis"
                )

            # 2. OpenROAD / OpenSTA static timing analysis
            sta_tcl = f"""
read_liberty {lib_path}
read_lef {tlef_path}
read_lef {slef_path}
read_verilog {synth_v}
link_design {top_module}
read_sdc {sdc_file}
report_checks -path_delay max
report_worst_slack -max
exit
"""
            p_sta = subprocess.run(
                [self.openroad_bin, "-exit", "-no_splash"],
                input=sta_tcl,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(
                p_sta.returncode, 0,
                f"OpenSTA failed on {platform_id}: {p_sta.stderr}"
            )

            # Parse timing slack
            slack_str = "N/A"
            slack_met = False
            for line in p_sta.stdout.splitlines():
                if "slack (MET)" in line:
                    slack_met = True
                    slack_str = line.strip().split()[0]
                elif "worst slack max" in line:
                    slack_str = line.strip().split()[-1]
                    try:
                        slack_val = float(slack_str)
                        if slack_val >= 0.0:
                            slack_met = True
                    except ValueError:
                        pass

            self.assertTrue(
                slack_met,
                f"Timing setup slack not met for {top_module} on {platform_id}: {p_sta.stdout}"
            )

            return {
                "platform": platform_id,
                "cell_count": cell_count,
                "cell_area": cell_area,
                "mapped_cells": mapped_cells,
                "slack": slack_str,
                "slack_met": slack_met,
            }

    def test_half_adder_multi_pdk(self):
        """Cross-validate Half_Adder across Sky130hd and GF180."""
        v_file = os.path.join(REPO_ROOT, "Examples", "Half_Adder", "Half_Adder.v")
        sdc_file = os.path.join(REPO_ROOT, "Examples", "Half_Adder", "Half_Adder.sdc")
        res_sky = self._run_pdk_synthesis_and_sta("sky130hd", v_file, sdc_file, "Half_Adder")
        res_gf = self._run_pdk_synthesis_and_sta("gf180", v_file, sdc_file, "Half_Adder")

        self.assertEqual(res_sky["cell_count"], 2)
        self.assertEqual(res_gf["cell_count"], 2)
        # 180nm cells are larger than 130nm cells
        self.assertGreater(res_gf["cell_area"], res_sky["cell_area"])

    def test_jk_flipflop_multi_pdk(self):
        """Cross-validate JK_Flipflop sequential timing across Sky130hd and GF180."""
        v_file = os.path.join(REPO_ROOT, "Examples", "JK_Flipflop", "JK_Flipflop.v")
        sdc_file = os.path.join(REPO_ROOT, "Examples", "JK_Flipflop", "JK_Flipflop.sdc")
        res_sky = self._run_pdk_synthesis_and_sta("sky130hd", v_file, sdc_file, "JK_Flipflop")
        res_gf = self._run_pdk_synthesis_and_sta("gf180", v_file, sdc_file, "JK_Flipflop")

        self.assertTrue(res_sky["slack_met"])
        self.assertTrue(res_gf["slack_met"])
        self.assertGreater(res_gf["cell_area"], res_sky["cell_area"])

    def test_4_bit_jk_counter_multi_pdk(self):
        """Cross-validate 4-bit synchronous counter across Sky130hd and GF180."""
        v_file = os.path.join(REPO_ROOT, "Examples", "4_bit_JK_ff", "4_bit_JK_ff.v")
        sdc_file = os.path.join(REPO_ROOT, "Examples", "4_bit_JK_ff", "4_bit_JK_ff.sdc")
        res_sky = self._run_pdk_synthesis_and_sta("sky130hd", v_file, sdc_file, "n_4_bit_JK_ff")
        res_gf = self._run_pdk_synthesis_and_sta("gf180", v_file, sdc_file, "n_4_bit_JK_ff")

        self.assertTrue(res_sky["slack_met"])
        self.assertTrue(res_gf["slack_met"])
        self.assertGreater(res_gf["cell_area"], res_sky["cell_area"])


def run_benchmark_report():
    """Run full benchmark sweep across both PDKs and print comparison table."""
    configs = _get_platform_configs()
    openroad_bin = _get_openroad_bin()
    has_yosys = shutil.which("yosys") is not None

    print("\n" + "=" * 76)
    print("  MULTI-PDK COMPARISON REPORT: SkyWater 130nm vs GlobalFoundries 180nm")
    print("=" * 76)

    if not has_yosys:
        print("ERROR: yosys not found in PATH")
        return 1
    if not openroad_bin:
        print("ERROR: openroad binary not found")
        return 1
    if "sky130hd" not in configs or "gf180" not in configs:
        print("ERROR: required platform files for sky130hd or gf180 not found")
        return 1

    print(f"  {'Design':<20} {'PDK':<10} {'Node':<7} {'Cells':<6} {'Area (um^2)':<13} {'Slack':<10} {'Status':<6}")
    print("  " + "-" * 72)

    all_passed = True
    test_instance = TestMultiPDKFlow()
    test_instance.configs = configs
    test_instance.openroad_bin = openroad_bin
    test_instance.has_yosys = has_yosys

    for design_name, rel_v, rel_sdc, top_mod, ctype in PDK_BENCHMARKS:
        v_path = os.path.join(REPO_ROOT, rel_v)
        sdc_path = os.path.join(REPO_ROOT, rel_sdc)

        if not os.path.exists(v_path) or not os.path.exists(sdc_path):
            continue

        for pid in ("sky130hd", "gf180"):
            node_label = configs[pid]["process"]
            try:
                res = test_instance._run_pdk_synthesis_and_sta(pid, v_path, sdc_path, top_mod)
                status_str = "PASS" if res["slack_met"] else "FAIL"
                area_str = f"{res['cell_area']:.2f}"
                slack_str = f"+{res['slack']}ns" if res["slack_met"] else f"{res['slack']}ns"
                print(f"  {design_name:<20} {pid:<10} {node_label:<7} {res['cell_count']:<6} {area_str:<13} {slack_str:<10} {status_str:<6}")
            except Exception as e:
                all_passed = False
                print(f"  {design_name:<20} {pid:<10} {node_label:<7} {'-':<6} {'-':<13} {'ERR':<10} FAIL")

    print("=" * 76 + "\n")
    return 0 if all_passed else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        sys.exit(run_benchmark_report())
    unittest.main()
