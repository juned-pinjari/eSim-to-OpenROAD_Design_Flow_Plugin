# =====================================================================
#          FILE: netlist2rtl.py
#
#   DESCRIPTION: This file is used to convert netlist to verilog
#
#        AUTHOR: Rishabh Jain, 2r10j5@gmail.com
#    MAINTAINED: Sumanto Kar, sumantokar@iitb.ac.in
#  ORGANIZATION: eSim Team at FOSSEE, IIT Bombay
#       CREATED: Monday 2 March 2026
#      REVISION: 2026-09-30 — Rewritten to use parser-based conversion
#                (Juned Pinjari, FOSSEE Autumn 2026 Intern)
# =====================================================================

import os
import sys


class NetlistToRTL:

    def __init__(self, cir_file):

        self.cir_file = cir_file

        # Handle both .cir and .cir.out inputs.
        # The frontend passes .cir but the actual netlist file is .cir.out.
        self.cir_out_file = self._resolve_cir_out(cir_file)

        basename = os.path.basename(cir_file)
        # Strip .cir.out first, then .cir (for frontend compatibility)
        if basename.endswith(".cir.out"):
            self.module_name = basename[:-len(".cir.out")]
        elif basename.endswith(".cir"):
            self.module_name = basename[:-len(".cir")]
        else:
            self.module_name = basename

    @staticmethod
    def _resolve_cir_out(cir_file):
        """Find the actual netlist file.

        The frontend may pass ``foo.cir`` but the simulation output
        is ``foo.cir.out``.  Try both paths.
        """
        if os.path.exists(cir_file):
            # If it ends with .cir, check if .cir.out exists
            if cir_file.endswith(".cir"):
                cir_out = cir_file + ".out"
                if os.path.exists(cir_out):
                    return cir_out
            return cir_file

        # File doesn't exist — try adding .out
        if cir_file.endswith(".cir"):
            cir_out = cir_file + ".out"
            if os.path.exists(cir_out):
                return cir_out

        return cir_file  # let it fail later with a clear message

    def convert(self):
        """Convert the XSPICE netlist to synthesizable Verilog.

        Returns the path to the generated ``.v`` file.
        """
        try:
            from .spice_parser import parse_file
            from .circuit_graph import extract_circuit
            from .verilog_emitter import emit_verilog
        except ImportError:
            # Fallback when executed directly as a script by the eSim GUI
            from spice_parser import parse_file
            from circuit_graph import extract_circuit
            from verilog_emitter import emit_verilog

        if not os.path.exists(self.cir_out_file):
            raise FileNotFoundError(
                f"\nNetlist file not found:\n{self.cir_out_file}\n"
            )

        print(f"\nParsing: {self.cir_out_file}")

        parsed = parse_file(self.cir_out_file)

        print(f"  Models found   : {len(parsed.models)}")
        print(f"  Instances (a)  : {len(parsed.instances)}")
        print(f"  Subckt inst (x): {len(parsed.subckt_instances)}")
        print(f"  V-sources      : {len(parsed.voltage_sources)}")
        print(f"  Includes       : {parsed.includes}")

        circuit = extract_circuit(parsed, self.module_name)

        print(f"\nExtracted circuit: {circuit.module_name}")
        print(f"  Ports  : {len(circuit.ports)}")
        print(f"  Wires  : {len(circuit.wires)}")
        print(f"  Gates  : {len(circuit.gates)}")

        if circuit.clock_net:
            print(f"  Clock  : {circuit.clock_net}"
                  f" (period={circuit.clock_period})")

        verilog = emit_verilog(circuit)

        # Write output alongside the input file
        project_dir = os.path.dirname(self.cir_out_file)

        output_file = os.path.join(
            project_dir,
            self.module_name + ".v"
        )

        with open(output_file, "w") as f:
            f.write(verilog)

        return output_file


# ----------------------------------------
# MAIN
# ----------------------------------------

def main():

    if len(sys.argv) < 2:

        print(
            "\nUsage:\n"
            "python3 netlist2rtl.py file.cir.out\n"
        )

        sys.exit(1)

    cir_file = sys.argv[1]

    print(
        "\n========== NETLIST TO RTL =========="
    )

    try:
        converter = NetlistToRTL(cir_file)
        output_file = converter.convert()

        print(
            "\n========== COMPLETED =========="
        )
        print(
            f"\nGenerated : {output_file}\n"
        )

    except Exception as e:
        print(f"\nERROR: {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":

    main()
