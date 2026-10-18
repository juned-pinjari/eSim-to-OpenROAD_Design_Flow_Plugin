# =====================================================================
#          FILE: test_ripple_clocks.py
#
#   DESCRIPTION: Unit and STA verification test suite for Task 2:
#                Multi-Clock & Ripple Counter SDC Constraints.
#                Tests ripple clock detection, divide ratio propagation,
#                SDC create_generated_clock emission, and validates
#                with OpenSTA / OpenROAD timing reports.
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-10-18
# =====================================================================

import os
import sys
import shutil
import tempfile
import subprocess
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "maker"))

from spice_parser import parse_string, parse_file, SubcktInstance, ParsedNetlist
from circuit_graph import extract_circuit
from verilog_emitter import emit_verilog
from sdc_emitter import emit_sdc


class TestRippleClockDetection(unittest.TestCase):
    """Test algorithmic detection and SDC generation for ripple counters."""

    def test_synthetic_3stage_ripple_counter(self):
        """Test a 3-stage ripple counter netlist with master pulse clock."""
        netlist_text = """* 3-Stage Ripple Counter Test
v1 clk gnd pulse(0 5 0 1n 1n 10n 20n)
v4 net-_u8-pad1_ gnd 0
a1 [clk ] [d_clk ] u1
a4 [net-_u8-pad1_ ] [net-_u8-pad2_ ] u8
a2 qb0 d_clk net-_u8-pad2_ net-_u8-pad2_ q0 qb0 u9
a3 qb1 q0 net-_u8-pad2_ net-_u8-pad2_ q1 qb1 u10
a6 qb2 q1 net-_u8-pad2_ net-_u8-pad2_ q2 qb2 u11
a5 [q0 q1 q2] [out0 out1 out2] u7
.model u1 adc_bridge
.model u8 adc_bridge
.model u7 dac_bridge
.model u9 d_dff
.model u10 d_dff
.model u11 d_dff
"""
        parsed = parse_string(netlist_text)
        circuit = extract_circuit(parsed, "ripple3_test")

        self.assertEqual(circuit.clock_net, "clk")
        self.assertEqual(len(circuit.generated_clocks), 2)

        # Stage 1: clocked by q0 (mapped to port out0), divide-by 2
        gc1 = circuit.generated_clocks[0]
        self.assertEqual(gc1.target_net, "out0")
        self.assertEqual(gc1.divide_by, 2)
        self.assertEqual(gc1.master_port, "clk")

        # Stage 2: clocked by q1 (mapped to port out1), divide-by 4
        gc2 = circuit.generated_clocks[1]
        self.assertEqual(gc2.target_net, "out1")
        self.assertEqual(gc2.divide_by, 4)
        self.assertEqual(gc2.master_port, "clk")

        # Verify emitted SDC contains create_generated_clock commands
        sdc = emit_sdc(circuit)
        self.assertIn("create_clock [get_ports clk]", sdc)
        self.assertIn("create_generated_clock -name gen_clk_out0 -source [get_ports clk] -divide_by 2 [get_nets out0]", sdc)
        self.assertIn("create_generated_clock -name gen_clk_out1 -source [get_ports clk] -divide_by 4 [get_nets out1]", sdc)

    def test_74hc4020_14stage_asynchronous_counter(self):
        """Test the 14-stage 74HC4020 binary ripple counter model from library."""
        full_path = os.path.join(REPO_ROOT, "library", "SubcircuitLibrary", "74HC4020", "74HC4020.sub")
        if not os.path.exists(full_path):
            self.skipTest("74HC4020.sub not found in library")

        parsed = parse_file(full_path)
        target_sc = None
        for sc in parsed.subcircuits.values():
            if len(sc.instances) > 0 or len(sc.subckt_instances) > 0:
                target_sc = sc
                break

        self.assertIsNotNone(target_sc)
        top_inst = SubcktInstance(name="x1", nets=list(target_sc.ports), subckt_name=target_sc.name)
        top_parsed = ParsedNetlist(
            title="74HC4020 Ripple Counter Test",
            subckt_instances=[top_inst],
            subcircuits=parsed.subcircuits,
            models=parsed.models,
        )
        circuit = extract_circuit(top_parsed, "m_74HC4020")

        # Out of 14 flip-flops, 13 are downstream ripple stages
        self.assertEqual(len(circuit.generated_clocks), 13)

        # Expected divide ratios: 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192
        expected_divides = [2 ** i for i in range(1, 14)]
        actual_divides = [gc.divide_by for gc in circuit.generated_clocks]
        self.assertEqual(actual_divides, expected_divides)

        # Verify SDC emission
        sdc = emit_sdc(circuit)
        for d in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192):
            self.assertIn(f"-divide_by {d}", sdc)

    def test_synchronous_counter_no_false_generated_clocks(self):
        """Verify that synchronous counter (4_bit_JK_ff) produces 0 generated clocks."""
        cir_path = os.path.join(REPO_ROOT, "Examples", "4_bit_JK_ff", "4_bit_JK_ff.cir.out")
        if not os.path.exists(cir_path):
            self.skipTest("4_bit_JK_ff.cir.out not found")

        parsed = parse_file(cir_path)
        circuit = extract_circuit(parsed, "n_4_bit_JK_ff")
        self.assertEqual(len(circuit.generated_clocks), 0)

    def test_opensta_timing_report_validation(self):
        """Validate synthesized ripple counter with OpenSTA / OpenROAD timing engine."""
        openroad_bin = os.path.join(
            REPO_ROOT, "orfs", "OpenROAD-flow-scripts", "tools", "install", "OpenROAD", "bin", "openroad"
        )
        if not os.path.exists(openroad_bin):
            if not shutil.which("openroad"):
                self.skipTest("OpenROAD binary not found, skipping STA report validation")
            openroad_bin = "openroad"

        if not shutil.which("yosys"):
            self.skipTest("yosys binary not found, skipping synthesis check")

        sky130_lib = os.path.join(
            REPO_ROOT, "orfs", "OpenROAD-flow-scripts", "flow", "platforms", "sky130hd", "lib", "sky130_fd_sc_hd__tt_025C_1v80.lib"
        )
        sky130_tlef = os.path.join(
            REPO_ROOT, "orfs", "OpenROAD-flow-scripts", "flow", "platforms", "sky130hd", "lef", "sky130_fd_sc_hd.tlef"
        )
        sky130_lef = os.path.join(
            REPO_ROOT, "orfs", "OpenROAD-flow-scripts", "flow", "platforms", "sky130hd", "lef", "sky130_fd_sc_hd_merged.lef"
        )

        if not os.path.exists(sky130_lib) or not os.path.exists(sky130_tlef):
            self.skipTest("Sky130hd platform files not found")

        netlist_text = """* 2-Stage Ripple Counter
v1 clk gnd pulse(0 5 0 1n 1n 10n 20n)
v4 net-_u8-pad1_ gnd 0
a1 [clk ] [d_clk ] u1
a4 [net-_u8-pad1_ ] [net-_u8-pad2_ ] u8
a2 qb0 d_clk net-_u8-pad2_ net-_u8-pad2_ q0 qb0 u9
a3 qb1 q0 net-_u8-pad2_ net-_u8-pad2_ q1 qb1 u10
a5 [q0 q1] [out0 out1] u7
.model u1 adc_bridge
.model u8 adc_bridge
.model u7 dac_bridge
.model u9 d_dff
.model u10 d_dff
"""
        parsed = parse_string(netlist_text)
        circuit = extract_circuit(parsed, "ripple2_sta")
        verilog_code = emit_verilog(circuit)
        sdc_code = emit_sdc(circuit)

        with tempfile.TemporaryDirectory() as tmpdir:
            v_file = os.path.join(tmpdir, "ripple2.v")
            synth_v = os.path.join(tmpdir, "ripple2_synth.v")
            sdc_file = os.path.join(tmpdir, "ripple2.sdc")

            with open(v_file, "w", encoding="utf-8") as f:
                f.write(verilog_code)
            with open(sdc_file, "w", encoding="utf-8") as f:
                f.write(sdc_code)

            # Synthesize with Yosys
            ys_cmd = f"""
read_verilog {v_file}
synth -top ripple2_sta
dfflibmap -liberty {sky130_lib}
abc -liberty {sky130_lib}
write_verilog {synth_v}
"""
            res_ys = subprocess.run(["yosys", "-p", ys_cmd], capture_output=True, text=True)
            self.assertEqual(res_ys.returncode, 0, f"Yosys failed: {res_ys.stderr}")

            # Run OpenROAD / OpenSTA timing check
            sta_tcl = f"""
read_liberty {sky130_lib}
read_lef {sky130_tlef}
read_lef {sky130_lef}
read_verilog {synth_v}
link_design ripple2_sta
read_sdc {sdc_file}
report_checks
exit
"""
            p_sta = subprocess.run(
                [openroad_bin, "-exit", "-no_splash"],
                input=sta_tcl,
                text=True,
                capture_output=True,
            )
            self.assertEqual(p_sta.returncode, 0, f"OpenROAD STA error: {p_sta.stderr}")
            # Ensure path group for generated clock exists and reports slack MET
            self.assertIn("clock gen_clk_out0", p_sta.stdout)
            self.assertIn("slack (MET)", p_sta.stdout)


if __name__ == "__main__":
    unittest.main()
