# =====================================================================
#          FILE: test_tb_generator.py
#
#   DESCRIPTION: Test suite for Automated Verilog Testbench Generator.
#                Verifies that testbenches are correctly emitted from
#                SPICE netlists, compiled with iverilog, and executed
#                with vvp to produce valid waveform (.vcd) files.
#
#        AUTHOR: Juned Pinjari, juned.m.pinjari@gmail.com
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: 2026-10-18
# =====================================================================

"""Verification suite for Automated Verilog Testbench Generation.

Tests:
  1. Combinational circuit with truth table sweep (Half_Adder)
  2. Pulse stimulus parameter extraction (BasicGates)
  3. Sequential clock and DC control stimulus (JK_Flipflop)
  4. Multi-stage synchronous counter simulation (4_bit_JK_ff)
  5. MSI subcircuit decoder simulation (BCDToDecimalDecoder)
"""

import os
import sys
import shutil
import tempfile
import subprocess
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "maker"))

from spice_parser import parse_file
from circuit_graph import extract_circuit
from verilog_emitter import emit_verilog
from tb_generator import emit_testbench, parse_spice_eng_value, normalize_to_ns


class TestTestbenchGenerator(unittest.TestCase):
    """Test suite for automated Verilog testbench generation and simulation."""

    @classmethod
    def setUpClass(cls):
        cls.has_iverilog = shutil.which("iverilog") is not None
        cls.has_vvp = shutil.which("vvp") is not None

    def _compile_and_simulate(self, v_code, tb_code, top_module):
        """Compile with iverilog and run simulation with vvp."""
        if not self.has_iverilog:
            self.skipTest("iverilog not installed")
        if not self.has_vvp:
            self.skipTest("vvp not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            v_path = os.path.join(tmpdir, f"{top_module}.v")
            tb_path = os.path.join(tmpdir, f"{top_module}_tb.v")
            out_bin = os.path.join(tmpdir, "sim.out")

            with open(v_path, "w", encoding="utf-8") as f:
                f.write(v_code)
            with open(tb_path, "w", encoding="utf-8") as f:
                f.write(tb_code)

            # Compile with iverilog
            compile_cmd = ["iverilog", "-o", out_bin, v_path, tb_path]
            p_iv = subprocess.run(compile_cmd, capture_output=True, text=True)
            self.assertEqual(
                p_iv.returncode, 0,
                f"iverilog compilation failed for {top_module}:\n{p_iv.stderr}"
            )

            # Run with vvp
            p_vvp = subprocess.run(["vvp", out_bin], capture_output=True, text=True, cwd=tmpdir)
            self.assertEqual(
                p_vvp.returncode, 0,
                f"vvp simulation failed for {top_module}:\n{p_vvp.stderr}"
            )

            # Verify $finish message and verification report
            self.assertIn("Simulation completed", p_vvp.stdout)
            self.assertIn("ALL CHECKS PASSED [OK]", p_vvp.stdout)
            self.assertIn("Total Assertion Errors : 0", p_vvp.stdout)

            # Verify VCD file was created
            vcd_name = f"{top_module}_tb.vcd"
            vcd_path = os.path.join(tmpdir, vcd_name)
            self.assertTrue(
                os.path.exists(vcd_path),
                f"VCD waveform file not generated: {vcd_name}"
            )
            self.assertGreater(os.path.getsize(vcd_path), 0)

            return p_vvp.stdout

    def test_eng_value_parser(self):
        """Test engineering unit parser."""
        self.assertAlmostEqual(parse_spice_eng_value("5"), 5.0)
        self.assertAlmostEqual(parse_spice_eng_value("10n"), 10e-9)
        self.assertAlmostEqual(parse_spice_eng_value("0.1u"), 0.1e-6)
        self.assertAlmostEqual(parse_spice_eng_value("20m"), 20e-3)
        self.assertAlmostEqual(parse_spice_eng_value("100p"), 100e-12)
        self.assertAlmostEqual(parse_spice_eng_value("1k"), 1e3)

    def test_half_adder_tb_simulation(self):
        """Test testbench generation and simulation for Half_Adder."""
        cir_path = os.path.join(REPO_ROOT, "Examples", "Half_Adder", "Half_Adder.cir.out")
        parsed = parse_file(cir_path)
        circuit = extract_circuit(parsed, "Half_Adder")
        v_code = emit_verilog(circuit)
        tb_code = emit_testbench(circuit, parsed)

        self.assertIn("module Half_Adder_tb;", tb_code)
        self.assertIn("Half_Adder dut (", tb_code)
        self.assertIn("$dumpfile", tb_code)
        self.assertIn("$monitor", tb_code)

        stdout = self._compile_and_simulate(v_code, tb_code, "Half_Adder")
        self.assertIn("Time |", stdout)

    def test_basic_gates_pulse_tb_simulation(self):
        """Test pulse stimulus extraction and simulation for BasicGates."""
        cir_path = os.path.join(REPO_ROOT, "Examples", "BasicGates", "BasicGates.cir.out")
        parsed = parse_file(cir_path)
        circuit = extract_circuit(parsed, "BasicGates")
        v_code = emit_verilog(circuit)
        tb_code = emit_testbench(circuit, parsed)

        self.assertIn("module BasicGates_tb;", tb_code)
        self.assertIn("Periodic Pulse Stimulus", tb_code)

        stdout = self._compile_and_simulate(v_code, tb_code, "BasicGates")
        self.assertIn("BasicGates_tb.vcd opened", stdout)

    def test_jk_flipflop_clock_tb_simulation(self):
        """Test clock generator and sequential simulation for JK_Flipflop."""
        cir_path = os.path.join(REPO_ROOT, "Examples", "JK_Flipflop", "JK_Flipflop.cir.out")
        parsed = parse_file(cir_path)
        circuit = extract_circuit(parsed, "JK_Flipflop")
        v_code = emit_verilog(circuit)
        tb_code = emit_testbench(circuit, parsed)

        self.assertIn("module JK_Flipflop_tb;", tb_code)
        self.assertIn("Clock Generator: clk", tb_code)

        stdout = self._compile_and_simulate(v_code, tb_code, "JK_Flipflop")
        self.assertIn("Simulation completed", stdout)

    def test_4_bit_jk_counter_tb_simulation(self):
        """Test multi-stage counter testbench for 4_bit_JK_ff."""
        cir_path = os.path.join(REPO_ROOT, "Examples", "4_bit_JK_ff", "4_bit_JK_ff.cir.out")
        parsed = parse_file(cir_path)
        circuit = extract_circuit(parsed, "n_4_bit_JK_ff")
        v_code = emit_verilog(circuit)
        tb_code = emit_testbench(circuit, parsed)

        self.assertIn("module n_4_bit_JK_ff_tb;", tb_code)
        self.assertIn("n_4_bit_JK_ff dut (", tb_code)
        self.assertIn("Power-on Register State Initialization", tb_code)

        stdout = self._compile_and_simulate(v_code, tb_code, "n_4_bit_JK_ff")
        self.assertIn("Simulation completed", stdout)
        self.assertIn("Signal Activity: d1 toggles =", stdout)
        self.assertIn("Signal Activity: d2 toggles =", stdout)
        self.assertIn("Signal Activity: d3 toggles =", stdout)
        self.assertIn("Signal Activity: d4 toggles =", stdout)

    def test_assertion_monitor_catches_fault(self):
        """Verify that unknown/floating states cause assertion failure with fatal exit code."""
        if not self.has_iverilog or not self.has_vvp:
            self.skipTest("iverilog/vvp not installed")

        faulty_v = (
            "module FaultyDesign (input in, output out);\n"
            "    wire floating_net;\n"
            "    assign out = in ? 1'b0 : floating_net;\n"
            "endmodule\n"
        )
        faulty_tb = (
            "`timescale 1ns / 1ps\n"
            "module FaultyDesign_tb;\n"
            "    reg in;\n"
            "    wire out;\n"
            "    FaultyDesign dut (.in(in), .out(out));\n"
            "    integer error_count = 0;\n"
            "    always @(out) begin\n"
            "        if ($time > 1) begin\n"
            "            if (out === 1'bx || out === 1'bz) error_count = error_count + 1;\n"
            "        end\n"
            "    end\n"
            "    initial begin\n"
            "        in = 1'b1;\n"
            "        #10 in = 1'b0;\n"
            "        #10;\n"
            "        if (error_count > 0) $fatal(1, \"Verification assertions violated.\");\n"
            "        else $finish;\n"
            "    end\n"
            "endmodule\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            v_p = os.path.join(tmpdir, "dut.v")
            tb_p = os.path.join(tmpdir, "dut_tb.v")
            out_bin = os.path.join(tmpdir, "sim.out")
            with open(v_p, "w", encoding="utf-8") as f:
                f.write(faulty_v)
            with open(tb_p, "w", encoding="utf-8") as f:
                f.write(faulty_tb)
            subprocess.run(["iverilog", "-o", out_bin, v_p, tb_p], check=True)
            res = subprocess.run(["vvp", out_bin], capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("FATAL", res.stdout)

    def test_bcd_decoder_msi_subcircuit_tb_simulation(self):
        """Test MSI subcircuit BCD decoder testbench simulation."""
        cir_path = os.path.join(
            REPO_ROOT, "Examples", "Analysis_Of_Digital_IC", "BCDToDecimalDecoder", "BCDToDecimalDecoder.cir.out"
        )
        parsed = parse_file(cir_path)
        circuit = extract_circuit(parsed, "BCDToDecimalDecoder")
        v_code = emit_verilog(circuit)
        tb_code = emit_testbench(circuit, parsed)

        self.assertIn("module BCDToDecimalDecoder_tb;", tb_code)
        self.assertIn("BCDToDecimalDecoder dut (", tb_code)

        stdout = self._compile_and_simulate(v_code, tb_code, "BCDToDecimalDecoder")
        self.assertIn("Simulation completed", stdout)

    def test_all_15_digital_benchmarks_simulation(self):
        """Cross-validate automated testbench generation across all 15 digital benchmarks."""
        all_circuits = [
            ("Half_Adder",          "Examples/Half_Adder/Half_Adder.cir.out"),
            ("BasicGates",          "Examples/BasicGates/BasicGates.cir.out"),
            ("FullAdder",           "Examples/FullAdder/FullAdder.cir.out"),
            ("JK_Flipflop",         "Examples/JK_Flipflop/JK_Flipflop.cir.out"),
            ("4_bit_JK_ff",         "Examples/4_bit_JK_ff/4_bit_JK_ff.cir.out"),
            ("4002_test",           "Examples/Analysis_Of_Digital_IC/4002_test/4002_test.cir.out"),
            ("4012_test",           "Examples/Analysis_Of_Digital_IC/4012_test/4012_test.cir.out"),
            ("4023_test",           "Examples/Analysis_Of_Digital_IC/4023_test/4023_test.cir.out"),
            ("4073_test",           "Examples/Analysis_Of_Digital_IC/4073_test/4073_test.cir.out"),
            ("3_Input_NAND",        "Examples/Analysis_Of_Digital_IC/3_Input_NAND_Characteristics/3_Input_NAND_Characteristics.cir.out"),
            ("4_Input_NAND",        "Examples/Analysis_Of_Digital_IC/4_Input_NAND_Charcateristics/4_Input_NAND_Charcateristics.cir.out"),
            ("4_Input_NOR",         "Examples/Analysis_Of_Digital_IC/4_Input_NOR_Characteristics/4_Input_NOR_Characteristics.cir.out"),
            ("BCDToDecimalDecoder",  "Examples/Analysis_Of_Digital_IC/BCDToDecimalDecoder/BCDToDecimalDecoder.cir.out"),
            ("SN74LS138_test",      "Examples/Analysis_Of_Digital_IC/SN74LS138_test/SN74LS138_test.cir.out"),
            ("74151_test",          "Examples/Analysis_Of_Digital_IC/74151_test/74151_test.cir.out"),
        ]

        for name, rel_path in all_circuits:
            full_path = os.path.join(REPO_ROOT, rel_path)
            if not os.path.exists(full_path):
                continue
            parsed = parse_file(full_path)
            circuit = extract_circuit(parsed, name)
            v_code = emit_verilog(circuit)
            tb_code = emit_testbench(circuit, parsed)
            stdout = self._compile_and_simulate(v_code, tb_code, circuit.module_name)
            self.assertIn("Simulation completed", stdout, f"Simulation failed to complete for {name}")


if __name__ == "__main__":
    unittest.main()
