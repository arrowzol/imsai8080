import re
import os

class HexLoader:
    def __init__(self, hex_file):
        self.hex_file = hex_file
        self.sym_file = hex_file[:-3] + 'symbols'
        self.asm_file = hex_file[:-3] + 'asm'

    def boot(self, cpu):
        if os.path.exists(self.hex_file):
            if os.path.exists(self.asm_file):
                self.read_asm(cpu)
            if os.path.exists(self.sym_file):
                self.read_symbols(cpu)
            self.read_hex(cpu)
            return True

    def read_symbols(self, cpu):
        fh = open(self.sym_file)
        for line in fh:
            addr, sym = line.strip().split('|')
            addr = int(addr,16)
            cpu.add_symbol(sym, addr)
        fh.close()

    def read_asm(self, cpu):
        asm_const_sym = set()
        with open(self.asm_file, 'r') as asm_file :
            for line in asm_file:
                tokens = re.split('  *', line.strip())
                if len(tokens) >= 1 and not line.startswith(' '):
                    sym_bytes = 0
                    if tokens[0].endswith(':'):
                        sym = tokens[0][:-1]
                        if len(tokens) >= 3 and tokens[1] == 'DS':
                            try:
                                sym_bytes = int(tokens[2])
                            except Exception:
                                sym_bytes = 1
                        else:
                            sym_bytes = 1
                    elif len(tokens) >= 3 and tokens[1] == 'DEFS':
                        sym = tokens[0]
                        try:
                            sym_bytes = int(tokens[2])
                        except Exception:
                            sym_bytes = 1
                    elif len(tokens) >= 3 and tokens[1] == 'DEFW':
                        sym = tokens[0]
                        sym_bytes = 2
                    elif len(tokens) >= 3 and tokens[1] == 'DEFM':
                        sym = tokens[0]
                        sym_bytes = line.index("'") + 1
                        sym_bytes = line.index("'", sym_bytes) - sym_bytes
                    elif len(tokens) >= 3 and tokens[1] == 'EQU':
                        if tokens[2] == '$':
                            sym = tokens[0]
                            sym_bytes = 1
                        else:
                            asm_const_sym.add(tokens[0])
                    elif len(tokens) >= 4 and not tokens[0] and tokens[2] == 'EQU':
                        asm_const_sym.add(tokens[1])
                    else:
                        sym = tokens[0]
                        sym_bytes = 1
                    if sym_bytes:
                        cpu.add_symbol_signature(sym, sym_bytes)

    def read_hex(self, cpu):
        with open(self.hex_file, 'r') as hex_file:
            line_num = 0
            end_count = 0
            for line in hex_file:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                if line == "$":
                    end_count += 1
                    continue
                if line[0] == ':':
                    count = int(line[1:3],16)
                    addr = int(line[3:7],16)
                    tp = int(line[7:9],16)
                    if tp == 0:
                        cksum = count + tp + addr + (addr >> 8)
                        for i in range(count):
                            start = 9 + i*2
                            byte = int(line[start:start+2],16)
                            cksum += byte
                            cpu.mem[addr + i] = byte
                        start = 9 + count*2
                        byte = int(line[start:start+2],16)
                        cksum += byte
                        if cksum & 0xFF:
                            print("checksum line %d"%line_num)
                elif end_count == 0:
                    num, sym, addr = re.split('  *', line)
                    if len(addr) == 5:
                        addr = int(addr[:-1],16)
                        cpu.add_symbol(sym, addr)

