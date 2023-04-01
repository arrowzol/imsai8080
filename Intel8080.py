#!/usr/bin/python3

import sys
from select import select
import re
import signal

REG_B = 0
REG_C = 1
REG_D = 2
REG_E = 3
REG_H = 4
REG_L = 5
REG_MEM = 6
REG_FLAG = 6
REG_A = 7

FLAG_C = 0x01
FLAG_1 = 0x02
FLAG_P = 0x04
FLAG_A = 0x10
FLAG_Z = 0x40
FLAG_S = 0x80

_OPS = "++--&^|-"
_RS = 'BCDEHLMA'
_RSX_SP = ['B',  'D',  'H',  'SP']
_RSX2_SP= ['BC', 'DE', 'HL', 'SP']
_RSX    = ['B',  'D',  'H',  'PSW']
_RSX2   = ['BC', 'DE', 'HL', 'PSW']
_07_OPS = ["RLC","RRC","RAL","RAR","DAA","CMA","STC","CMC"]
_80_OPS = ["ADD","ADC","SUB","SBB","ANA","XRA","ORA","CMP"]
_C0_OPS = ["ADI","ACI","SUI","SBI","ANI","XRI","ORI","CPI"]
_RJC_OPS = [
    "RNZ", "JNZ", "CNZ", None, "RZ", "JZ", "CZ", None,
    "RNC", "JNC", "CNC", None, "RC", "JC", "CC", None,
    "RPO", "JPO", "CPO", None, "RPE", "JPE", "CPE", None,
    "RP", "JP", "CP", None, "RM", "JM", "CM", None]
_DIRECT_OPS = ["SHLD", "LHLD", "STA", "LDA"]
_LS_EXTENDED_OPS = ["STAX", "LDAX"]

class ScriptedInputDevice:
    """values in self.in_devices"""
    def __init__(self, name, string=None, single_value=0xFF, halt_on_empty=False):
        self.name = name
        self.halt_on_empty = halt_on_empty
        if string:
            self.stack = [ord(c) for c in string]
            self.stack.reverse()
        else:
            self.stack = None
        self.single_value = single_value

    def load_file(self, file_name):
        fh = open(file_name)
        string = "NEW\r\r" + "\r\r".join((line.strip() for line in fh)) + "\r\rRUN\r\r"
        self.stack = [ord(c) for c in string]
        self.stack.reverse()
        fh.close()
        single_value = ord('\r')

    def get_device_input(self):
        if self.stack:
            return self.stack.pop()
        if self.halt_on_empty:
            return -1
        return self.single_value

class InteractiveInputDevice:
    """values in self.in_devices"""
    def __init__(self, name):
        self.name = name
        self.stack = None
        signal.signal(signal.SIGINT, self.sigint_handler)
        self.ctrl_c = 0

    def sigint_handler(self, sig, frame):
        if sig == 2:
            self.ctrl_c += 1
            if self.ctrl_c > 3:
                sys.exit(1)
        else:
            print('UNEXPECTED SIG %d'%sig)

    def get_device_input(self):
        if self.ctrl_c:
            self.ctrl_c = 0
            return 3
        if self.stack:
            return self.stack.pop()
        timeout = 0.01
        rlist, _, _ = select([sys.stdin], [], [], timeout)
        if rlist:
            string = sys.stdin.readline().upper()
            if not string or string.strip() == "EOF":
                # pressing ^D will produce empty string
                return -1
            self.stack = [ord(c) for c in string.strip()]
            self.stack.append(ord('\r'))
            self.stack.reverse()
        return 0xFF

class RecordingOutputDevice:
    def __init__(self, name):
        self.name = name
        self.record = []

    def put_output(self, value):
        self.record.append(value)

    def print(self):
        print("".join((chr(c) for c in self.record if 0 < c < 127)))

class InteractiveOutputDevice:
    def __init__(self, name):
        self.name = name

    def put_output(self, c):
        if 0 < c < 127:
            print(chr(c), end='', flush=True)

    def print(self):
        pass

class Intel8080:
    def __init__(self, mem_size):
        self.sp = 0
        self.pc = 0

        # B C
        # D E
        # H L
        # flags A
        self.rs = [0]*8
        self.rs[REG_FLAG] = FLAG_1
        self.mem = [0]*(mem_size)
        self.halt = False
        self.show_inst = False
        self.show_mem_set = False
        self.show_mem_get = False

        self.dump_instr_addr = set()
        self.dump_mem_addr = set()
        self.start_bp = set()
        self.stop_bp = set()
        self.in_devices = {}
        self.out_devices = {}
        self.call_indent = ""
        self.mem_to_sym = {}
        self.sym_to_mem = {}
        self.sp_fault = True
        self.limit_steps = 0

    def strFlags(self):
        return "".join((
            n if n != '.' and b == '1' else "-"
            for b,n in zip(bin(0x100 + self.rs[6])[3:], "SZ.A.P.C")))

    def dump_one_reg(self, reg_pair):
        h = self.rs[reg_pair*2]
        l = self.rs[reg_pair*2 + 1]
        addr = h * 0x100 + l
        show_symbol = ""
        if addr in self.mem_to_sym:
            show_symbol = self.mem_to_sym[addr]
        s = []
        for i in range(20):
            if addr >= len(self.mem):
                break
            c = self.mem[addr]
            if 0 < c < 127:
                s.append(chr(c))
            else:
                break
            addr += 1
        s = "".join(s)
        print("  %s x%02x:%02x   %s --> %s"%(_RSX2[reg_pair], h, l, show_symbol, repr(s)))
        pass

    def dump_reg(self):
        print("   A x%02x    FLAGS:%s"%(self.rs[7], self.strFlags()))
        for i in range(4):
            self.dump_one_reg(i)
        print("  SP x%04x"%(self.sp))
        print("  PC x%04x"%(self.pc))

    def set_mem(self, addr, value, bits=8, stack=False):
        """
        [addr + 0] <- low
        [addr + 1] <- high
        """
        if not stack and (self.show_mem_set or addr in self.dump_mem_addr):
            if addr in self.mem_to_sym:
                s_addr = self.mem_to_sym.get(addr)
            else:
                s_addr = "x%04x"%addr

            s_value = ("x%%%02dx"%(bits/4))%value
            if bits == 8 and 32 <= value < 127:
                s_value += " chr(%s)"%(chr(value))

            print("          %s mem[%s] <- %s"%(self.call_indent, s_addr, s_value))
        if addr < len(self.mem):
            self.mem[addr] = value & 0xFF
        if bits == 16 and addr + 1 < len(self.mem):
            self.mem[addr + 1] = (value >> 8) & 0xFF

    def reset(self, pc):
        self.pc = pc
        self.rs[REG_FLAG] = FLAG_1
        self.halt = False

    def get_bc(self):
        value = self.rs[REG_B] * 0x100 + self.rs[REG_C]
        if value < 0:
            print("ERROR1 %d %d %d"%(self.rs[REG_C], self.rs[REG_B], value))
            self.halt = True
            return 0
        return value

    def get_de(self):
        value = self.rs[REG_D] * 0x100 + self.rs[REG_E]
        if value < 0:
            print("ERROR1 %d %d %d"%(self.rs[REG_E], self.rs[REG_D], value))
            self.halt = True
            return 0
        return value

    def get_hl(self):
        value = self.rs[REG_H] * 0x100 + self.rs[REG_L]
        if value < 0:
            print("ERROR1 %d %d %d"%(self.rs[REG_H], self.rs[REG_L], value))
            self.halt = True
            return 0
        return value

    def get_mem(self, addr):
        if addr >= len(self.mem):
            value = 0
        else:
            value = self.mem[addr]
        if self.show_mem_get:
            if addr in self.mem_to_sym:
                s_addr = self.mem_to_sym.get(addr)
            else:
                s_addr = "x%04x"%addr

            s_value = "x%02x"%value
            if 32 <= value < 127:
                s_value += " chr(%s)"%(chr(value))

            print("          %s mem[%s] -- %s"%(self.call_indent, s_addr, s_value))
        return value

    def get_by_id(self, instr, shift):
        ident = (instr >> shift) & 0x07
        self.get_ident = ident
        if ident == REG_MEM:
            return self.get_mem(self.get_hl())
        else:
            return self.rs[ident]

    def set_by_id(self, instr, shift, value):
        ident = (instr >> shift) & 0x07
        self.set_ident = ident
        if ident == REG_MEM:
            addr = self.get_hl()
            self.set_mem(addr, value)
        else:
            self.rs[ident] = value

    def get_flag(self, flag):
        if self.rs[REG_FLAG] & flag:
            return 1
        else:
            return 0

    def set_flag(self, flag, value):
        if value:
            self.rs[REG_FLAG] |= flag
        else:
            self.rs[REG_FLAG] &= 0xFF - flag

    def set_flags_not_c(self, value, value4):
        self.set_flag(FLAG_S, value & 0x80)
        self.set_flag(FLAG_Z, value & 0xFF == 0)
        self.set_flag(FLAG_A, not 0 <= value4 < 0x10)
        self.set_flag(FLAG_P, bin(value & 0xFF).count("1") & 0x01)

    def set_all_flags(self, value, value4):
        self.set_flag(FLAG_S, value & 0x80)
        self.set_flag(FLAG_Z, value & 0xFF == 0)
        self.set_flag(FLAG_A, not 0 <= value4 < 0x10)
        self.set_flag(FLAG_P, bin(value & 0xFF).count("1") & 0x01)
        self.set_flag(FLAG_C, not 0 <= value < 0x100)

    def set_most_flags(self, value):
        "set all but A flag"
        self.set_flag(FLAG_S, value & 0x80)
        self.set_flag(FLAG_Z, value & 0xFF == 0)
        self.set_flag(FLAG_P, bin(value & 0xFF).count("1") & 0x01)
        self.set_flag(FLAG_C, not 0 <= value < 0x100)

    def alu(self, op_names, op, value, show_value, pc, instr):
        a = self.rs[REG_A]
        a_start = a
        c_in = self.get_flag(FLAG_C)
        a4 = a & 0xF
        if op < 4:
            # ADD, ADC, SUB, SBB
            value2 = value
            if op & 0x01 == 1 and self.get_flag(FLAG_C):
                # ADC, SBB
                value2 += 1
            if op >= 2:
                # SUB, SBB
                value2 = (-value2) & 0xFF

            a += value2
            a4 += value2 & 0xF
            self.set_all_flags(a, a4)
            if op >= 2:
                # SUB, SBB
                if value2:
                    self.set_flag(FLAG_C, not self.get_flag(FLAG_C))
                if not value2 & 0x0F: # do "else:" to make subtraction work
                    self.set_flag(FLAG_A, not self.get_flag(FLAG_A))

            a &= 0xFF
            self.rs[REG_A] = a
        elif op == 4:
            # ANA
            a &= value
            self.rs[REG_A] = a
            self.set_most_flags(a)
        elif op == 5:
            # XRA
            a ^= value
            self.rs[REG_A] = a
            self.set_all_flags(a, 0)
        elif op == 6:
            # ORA
            a |= value
            self.rs[REG_A] = a
            self.set_most_flags(a)
        elif op == 7:
            # CMP
            self.set_all_flags(a - value, (a4 & 0xF) - (value & 0xF))
            if (a ^ value) & 0x80 == 0x80:
                self.set_flag(FLAG_C, not self.get_flag(FLAG_C))
            a -= value
            a &= 0xFF
        else:
            print("%04x unknown ALU OP %d"%(pc, op))
            self.halt = True

        if self.show_inst:
            s_value = "x%02x"%value
            s_a = "x%02x"%a
            if op < 4:
                c_out = self.get_flag(FLAG_C)
                s_a = "%d:"%c_out + s_a
                if op & 0x01:
                    s_value += "%s%d"%(_OPS[op], c_in)
            print("%04x %02x %s %s %s [x%02x%s%s=%s F=%s]"%(
                pc, instr, self.call_indent,
                op_names[op], show_value,
                a_start, _OPS[op], s_value, s_a,
                self.strFlags()))

    def step(self):
        pc = self.pc
        instr = self.get_instr8()
        family = instr & 0xC0

        if family == 0x00:
            family_op = instr & 0x07
            reg_id = ((instr >> 4) & 0x3) * 2 # 0=BC, 2=DE, 4=HL, 6=SP/PSW
            if family_op == 0:
                if self.show_inst:
                    print("%04x %02x %s NOP"%(pc, instr, self.call_indent))
            elif family_op == 1:
                if instr & 0x08 == 0:
                    # LXI, Load Register Pair Immediate
                    data = self.get_instr16()
                    if reg_id == 6:
                        self.sp = data
                    else:
                        self.rs[reg_id + 1] = data & 0xFF
                        self.rs[reg_id] = (data >> 8) & 0xFF

                    if self.show_inst:
                        s_data = self.mem_to_sym.get(data, "x%04x"%data)
                        print("%04x %02x %s LXI %s %s"%(pc, instr, self.call_indent, _RSX_SP[reg_id // 2], s_data))
                else:
                    # DAD, Double Add
                    h = self.rs[REG_H]
                    l = self.rs[REG_L]
                    if reg_id == 6:
                        h += self.sp >> 8
                        l += self.sp & 0xff
                    else:
                        h += self.rs[reg_id]
                        l += self.rs[reg_id + 1]
                    if l > 0x100:
                        l -= 0x100
                        h += 1
                    if h >= 0x100:
                        h -= 0x100
                        self.set_flag(FLAG_C, True)
                    else:
                        self.set_flag(FLAG_C, False)
                    self.rs[REG_H] = h
                    self.rs[REG_L] = l

                    if self.show_inst:
                        print("%04x %02x %s DAD %s [HL=x%04x]"%(
                            pc, instr, self.call_indent,
                            _RSX_SP[reg_id // 2],
                            h * 0x100 + l))
            elif family_op == 2:
                if instr < 0x20:
                    if instr & 0x10:
                        addr = self.get_de()
                    else:
                        addr = self.get_bc()
                    if instr & 0x08:
                        # LDAX (16-bit-reg), Load Accumulator
                        self.rs[REG_A] = self.get_mem(addr)
                    else:
                        # STAX (16-bit-reg), Store Accumulator
                        self.set_mem(addr, self.rs[REG_A])
                    if self.show_inst:
                        s_addr = self.mem_to_sym.get(addr, "x%04x"%addr)
                        print("%04x %02x %s %s %s [x%02x, %s]"%(
                            pc, instr, self.call_indent,
                            _LS_EXTENDED_OPS[bool(instr & 0x08)],
                            "BD"[bool(instr & 0x10)],
                            self.rs[REG_A],
                            s_addr
                            ))
                else:
                    addr = self.get_instr16()
                    sub_op = (instr >> 3) & 0x3
                    if sub_op == 0:
                        # SHLD, Store H and L Direct
                        value = self.rs[REG_H] * 0x100 + self.rs[REG_L]
                        self.set_mem(addr, value, 16)

                        who = '(x)'
                        hexes = 4
                    elif sub_op == 1:
                        # LHLD, Load H and L Direct
                        value_l = self.rs[REG_L] = self.mem[addr]
                        value_h = self.rs[REG_H] = self.mem[addr + 1]
                        value = value_h * 0x100 + value_l
                        who = 'HL'
                        hexes = 4
                    elif sub_op == 2:
                        # STA, Store Accumulator Direct
                        value = self.rs[REG_A]
                        self.set_mem(addr, value)
                        who = '(HL)'
                        hexes = 2
                    elif sub_op == 3:
                        # LDA, Load Accumulator Direct
                        value = self.rs[REG_A] = self.mem[addr]
                        who = 'A'
                        hexes = 2
                    if self.show_inst:
                        s_addr = self.mem_to_sym.get(addr, "x%04x"%addr)
                        value = ("x%%%02dx"%hexes)%value
                        print("%04x %02x %s %s %s [%s=%s]"%(
                            pc, instr, self.call_indent,
                            _DIRECT_OPS[sub_op], s_addr, who, value))
            elif family_op == 3:
                if instr & 0x08 == 0:
                    # INX, Increment Register Pair
                    if reg_id == 6:
                        self.sp += 1
                        self.sp &= 0xFFFF
                        value = self.sp
                    else:
                        value = self.rs[reg_id + 1]
                        value += 1
                        self.rs[reg_id + 1] = value & 0xFF
                        if value == 0x100:
                            value_h = (self.rs[reg_id] + 1) & 0xFF
                            self.rs[reg_id] = value_h
                            value = 0
                        else:
                            value_h = self.rs[reg_id]
                        value += value_h * 0x100

                    if self.show_inst:
                        s_value = self.mem_to_sym.get(value, "x%04x"%value)
                        print("%04x %02x %s INX %s [%s=%s]"%(
                            pc, instr, self.call_indent,
                            _RSX_SP[reg_id // 2],
                            _RSX2_SP[reg_id // 2], s_value))
                else:
                    # DCX, Decrement Register Pair
                    if reg_id == 6:
                        self.sp -= 1
                        self.sp &= 0xFFFF
                        value = self.sp
                    else:
                        value = self.rs[reg_id + 1]
                        value -= 1
                        self.rs[reg_id + 1] = value & 0xFF
                        if value == -1:
                            value_h = (self.rs[reg_id] - 1) & 0xFF
                            self.rs[reg_id] = value_h
                            value = 0xff
                        else:
                            value_h = self.rs[reg_id]
                        value += value_h * 0x100

                    if self.show_inst:
                        s_value = self.mem_to_sym.get(value, "x%04x"%value)
                        print("%04x %02x %s DCX %s [%s=%s]"%(
                            pc, instr, self.call_indent,
                            _RSX_SP[reg_id // 2],
                            _RSX2_SP[reg_id // 2], s_value))
            elif family_op == 4:
                # INR, Increment Register or Memory
                value = self.get_by_id(instr, 3) + 1
                value4 = (value & 0xF) + 1
                # TODO: INR affacts carry, DCR does not?
                if True:
                    # needed for FDIV
                    self.set_flags_not_c(value, value4)
                else:
                    self.set_all_flags(value, value4)
                value &= 0xFF
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    print("%04x %02x %s INR %s [%s=x%02x F=%s]"%(
                        pc, instr, self.call_indent,
                        _RS[self.get_ident],
                        _RS[self.get_ident], value, self.strFlags()))
            elif family_op == 5:
                # DCR, Decrement Register or Memory
                value = self.get_by_id(instr, 3) - 1
                value4 = (value & 0xF) - 1
                self.set_flags_not_c(value, value4)
                value &= 0xFF
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    print("%04x %02x %s DCR %s [%s=x%02x F=%s]"%(
                        pc, instr, self.call_indent,
                        _RS[self.get_ident],
                        _RS[self.get_ident], value,
                        self.strFlags()
                        ))
            elif family_op == 6:
                # MVI, Move Immediate
                value = self.mem[self.pc]
                self.pc += 1
                self.pc &= 0xFFFF
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    print("%04x %02x %s MVI %s x%02x"%(pc, instr, self.call_indent, _RS[self.set_ident], value))
            elif family_op == 7:
                op = (instr >> 3) & 0x7
                if op < 4:
                    # RAR, Rotate Accumulator Right Through Carry
                    # RAL, Rotate Accumulator Left Through Carry
                    # RRC, Rotate Accumulator Right
                    # RLC, Rotate Accumulator Left
                    right = op & 0x1
                    through_carry = op & 0x2

                    a = self.rs[REG_A]
                    if through_carry:
                        c = self.get_flag(FLAG_C)
                    if right:
                        c_out = a & 0x01
                        if not through_carry:
                            c = c_out
                        a = (c << 7) | (a >> 1)
                    else:
                        c_out = (a & 0x80) >> 7
                        if not through_carry:
                            c = c_out
                        a = ((a << 1) & 0xff) | c
                    self.rs[REG_A] = a
                    self.set_flag(FLAG_C, c_out)
                elif op == 4:
                    # DAA, Decimal Adjust Accumulator
                    a = self.rs[REG_A]
                    carry_in = self.get_flag(FLAG_C)
                    a4 = a & 0xf
                    if a & 0xF > 9 or self.get_flag(FLAG_A):
                        a += 0x06
                        a4 += 0x06
                    if a & 0x100 or (a >> 4) & 0xF > 9 or self.get_flag(FLAG_C):
                        a += 0x60
                    self.set_all_flags(a, a4)
                    if carry_in:
                        self.set_flag(FLAG_C, True)
                    a &= 0xFF
                    self.rs[REG_A] = a
                elif op == 5:
                    # CMA, Complement Accumulator
                    a = (~self.rs[REG_A]) & 0xFF
                    self.rs[REG_A] = a
                elif op == 6:
                    # STC, Set Carry
                    self.set_flag(FLAG_C, True)
                elif op == 7:
                    # CMC, Complement Carry
                    self.set_flag(FLAG_C, not self.get_flag(FLAG_C))
                if self.show_inst:
                    if op <= 5:
                        print("%04x %02x %s %s [A=x%02x F=%s]"%(
                            pc, instr, self.call_indent, _07_OPS[op],
                            a, self.strFlags()))
                    else:
                        print("%04x %02x %s %s [F=%s]"%(
                            pc, instr, self.call_indent, _07_OPS[op], self.strFlags()))
        elif family == 0x40:
                # MOV [8-bit],[8-bit]

                if instr == 0x76:
                    if self.show_inst:
                        print("%04x %02x %s HLT"%(pc, instr, self.call_indent))
                    self.halt = True
                    return

                value = self.get_by_id(instr, 0)
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    print("%04x %02x %s MOV %s,%s [x%02x]"%(
                        pc, instr, self.call_indent,
                        _RS[self.set_ident], _RS[self.get_ident],
                        value))

        elif family == 0x80:
            # ADD,ADC,SUB,SBB,ANA,XRA,ORA,CMP [8-bit]

            family_op = (instr >> 3) & 0x07
            value = self.get_by_id(instr, 0)

            self.alu(_80_OPS, family_op, value, _RS[self.get_ident], pc, instr)

        elif family == 0xC0:
            family_op7 = instr & 0x07
            param7 = (instr >> 3) & 0x7

            family_opF = instr & 0x0F
            paramF = (instr >> 4) & 0x3

            if family_opF & 0x1 == 0 and family_op7 != 6:
                # RNZ, Return If Not Zero
                # RNC, Return If Not Carry
                # RPO, Return If Parity Odd
                # RP, Return If Plus
                # JNZ, Jump If Not Zero
                # JNC, Jump If Not Carry
                # JPO, Jump If Parity Odd
                # JP, Jump If Plus
                # CNZ, Call If Not Zero
                # CNC, Call If Not Carry
                # CPO, Call If Parity Odd
                # CP, Call If Plus
                # RZ, Return If Zero
                # RC, Return If Carry
                # RPE, Return If Parity Even
                # RM, Return If Minus
                # JZ, Jump If Zero
                # JC, Jump If Carry
                # JPE, Jump If Parity Even
                # JM, Jump If Minus
                # CZ, Call If Zero
                # CC, Call If Carry
                # CPE, Call If Parity Even
                # CM, Call If Minus
                flags = self.rs[REG_FLAG]
                condition = bool(flags & [FLAG_Z, FLAG_C, FLAG_P, FLAG_S][paramF])
                if instr & 0x08 == 0:
                    condition = not condition
                sub_op = (instr >> 1) & 0x3
                if sub_op != 0:
                    addr = self.get_instr16()


                if self.show_inst:
                    if sub_op == 0:
                        print("%04x %02x %s %s [%s]"%(
                            pc, instr, self.call_indent, _RJC_OPS[(instr >> 1) & 0x1F], condition))
                    else:
                        s_addr = self.mem_to_sym.get(addr, "x%04x"%addr)
                        print("%04x %02x %s %s %s [%s]"%(
                            pc, instr, self.call_indent, _RJC_OPS[(instr >> 1) & 0x1F], s_addr, condition))

                if condition:
                    if sub_op == 0:
                        # return
                        self.call_indent = self.call_indent[:-2]
                        self.pc = self.pop()
                    else:
                        # jump/call
                        if instr & 0x02 == 0:
                            # call
                            self.call_indent += "  "
                            self.push(self.pc)
                        self.pc = addr
            elif instr | 0x08 == 0xCB:
                # JMP
                addr = self.get_instr16()
                self.pc = addr
                if self.show_inst:
                    s_addr = self.mem_to_sym.get(addr, "x%04x"%addr)
                    print("%04x %02x %s JMP %s"%(pc, instr, self.call_indent, s_addr))
            elif instr | 0x70 == 0xFD:
                # CALL
                addr = self.get_instr16()
                self.push(self.pc)
                self.pc = addr
                if self.show_inst:
                    s_addr = self.mem_to_sym.get(addr, "x%04x"%addr)
                    print("%04x %02x %s CALL %s"%(pc, instr, self.call_indent, s_addr))
                self.call_indent += "  "
            elif family_opF == 1:
                # POP (16-bit-reg), Pop Data Off Stack
                value = self.pop()

                if paramF == 3: # TODO: why POP PSW switched?
                    self.rs[paramF*2 + 1] = (value >> 8) & 0xFF
                    self.rs[paramF*2] = value & 0xFF
                else:
                    self.rs[paramF*2] = (value >> 8) & 0xFF
                    self.rs[paramF*2 + 1] = value & 0xFF

                if self.show_inst:
                    if paramF == 3:
                        s_value = "x%04x"%value
                    else:
                        s_value = self.mem_to_sym.get(value, "x%04x"%value)
                    print("%04x %02x %s POP %s [%s=%s]"%(
                        pc, instr, self.call_indent,
                        _RSX[paramF],
                        _RSX2[paramF], s_value))
            elif family_opF == 5:
                # PUSH (16-bit-reg), Push Data Onto Stack
                value_h = self.rs[paramF*2]
                value_l = self.rs[paramF*2 + 1]

                if paramF == 3: # TODO: why PUSH PSW switched?
                    self.push(value_l * 0x100 + value_h)
                else:
                    self.push(value_h * 0x100 + value_l)

                if self.show_inst:
                    print("%04x %02x %s PUSH %s [x%04x]"%(
                        pc, instr, self.call_indent,
                        _RSX[paramF], value_h * 0x100 + value_l))
            elif family_op7 == 6:
                # ADI, ACI, SUI, SBI, ANI, XRI, ORI, CPI
                value = self.get_instr8()
                self.alu(_C0_OPS, param7, value, "x%02x"%value, pc, instr)

            elif family_op7 == 7:
                # RST (id), Restart
                self.push(self.pc)
                exp = (instr >> 3) & 0x7
                self.pc = exp*0x08

                if self.show_inst:
                    print("%04x %02x %s RST %d"%(pc, instr, self.call_indent, exp))
                self.call_indent += "  "
            elif instr == 0xDB:
                # IN (device)
                device_id = self.get_instr8()

                in_device = self.in_devices.get(device_id, None)
                if in_device:
                    value = in_device.get_device_input()
                    if value == -1:
                        print("DEVICE EMPTY: " + in_device.name)
                        self.halt = True
                        return
                else:
                    value = 0
                self.rs[REG_A] = value

                if self.show_inst:
                    print("%04x %02x %s IN x%02x [x%02x]"%(pc, instr, self.call_indent, device_id, value))
            elif instr == 0xD3:
                # OUT (device)
                device_id = self.get_instr8()
                if not device_id in self.out_devices:
                    self.out_devices[device_id] = RecordingOutputDevice("output device %d"%device_id)
                self.out_devices[device_id].put_output(self.rs[REG_A])

                if self.show_inst:
                    print("%04x %02x %s OUT x%02x [x%02x]"%(pc, instr, self.call_indent, device_id, self.rs[REG_A]))
            elif instr == 0xF9:
                # SPHL, Load SP from H and L
                self.sp = self.get_hl()
                if self.show_inst:
                    print("%04x %02x %s SPHL [x%04x]"%(pc, instr, self.call_indent, self.sp))
            elif instr == 0xC9:
                # RET, Return
                self.pc = self.pop()
                if self.show_inst:
                    print("%04x %02x %s RET [A=x%02x HL=x%04x F=%s]"%(
                        pc, instr, self.call_indent,
                        self.rs[REG_A], self.rs[REG_H] * 0x100 + self.rs[REG_L],
                        self.strFlags()))
                self.call_indent = self.call_indent[:-2]
            elif instr == 0xEB:
                # XCHG, Exchange Registers
                h = self.rs[REG_H]
                l = self.rs[REG_L]
                d = self.rs[REG_D]
                e = self.rs[REG_E]

                self.rs[REG_H] = d
                self.rs[REG_L] = e
                self.rs[REG_D] = h
                self.rs[REG_E] = l

                if self.show_inst:
                    print("%04x %02x %s XCHG"%(pc, instr, self.call_indent))
            elif instr == 0xE3:
                # XTHL, Exchange Stack
                reg_l = self.rs[REG_L]
                self.rs[REG_L] = self.mem[self.sp]

                reg_h = self.rs[REG_H]
                self.rs[REG_H] = self.mem[self.sp + 1]

                self.set_mem(self.sp, reg_h * 0x100 + reg_l, 16)

                if self.show_inst:
                    print("%04x %02x %s XTHL"%(
                        pc, instr, self.call_indent))
            elif instr == 0xE9:
                # PCHL, H & L to PC
                self.pc = self.rs[REG_L] + self.rs[REG_H] * 0x100

                if self.show_inst:
                    print("%04x %02x %s PCHL"%(
                        pc, instr, self.call_indent))
            else:
                print("%04x unknown %2x"%(pc, instr))
                self.halt = True

    def get_instr8(self):
        if self.pc+1 >= len(self.mem):
            return 0
        value = self.mem[self.pc]
        self.pc += 1
        return value

    def get_instr16(self):
        if self.pc+2 >= len(self.mem):
            value = 0
        else:
            value = self.mem[self.pc] | (self.mem[self.pc + 1] * 0x100) 
        self.pc += 2
        return value

    def push(self, value):
        """
        [SP - 1] <- low
        [SP - 2] <- high
        SP <- SP - 2
        """
        self.sp -= 2
        self.sp &= 0xFFFF
        if self.sp_fault and self.sp+1 >= len(self.mem):
            print("STACK FAULT")
            self.halt = True
            return 0
        self.set_mem(self.sp, value, 16, True)

    def pop(self):
        """
        low <- [SP]
        high <- [SP + 1]
        SP <- SP + 2
        """
        value = 0
        if self.sp < len(self.mem):
            value = self.mem[self.sp]
        elif self.sp_fault:
            print("STACK FAULT")
            self.halt = True
            return 0

        self.sp += 1
        self.sp &= 0xFFFF

        if self.sp < len(self.mem):
            value += self.mem[self.sp] * 0x100
        elif self.sp_fault:
            print("STACK FAULT")
            self.halt = True
            return 0

        self.sp += 1
        self.sp &= 0xFFFF

        return value

    def run(self):
        step_count = 0
        bp_on = False
        bp_next = False
        while not self.halt and (self.limit_steps <= 0 or step_count < self.limit_steps):
            if self.show_inst and self.pc in self.mem_to_sym:
                print(":%s:"%(self.mem_to_sym[self.pc]))
            step_count += 1
            if self.pc in self.start_bp:
                bp_on = True
            if self.pc in self.stop_bp:
                bp_on = False
            if bp_next or bp_on or self.pc in self.dump_instr_addr:
                self.dump_reg()
            bp_next = self.pc in self.dump_instr_addr
            self.step()
        print("STEPS %d"%(step_count))

    def dump_at_instr(self, addr):
        if addr in self.sym_to_mem:
            addr = self.sym_to_mem[addr]
        self.dump_instr_addr.add(addr)

    def dump_at_mem(self, addr):
        if addr in self.sym_to_mem:
            addr = self.sym_to_mem[addr]
        self.dump_mem_addr.add(addr)
        self.dump_mem_addr.add(addr+1)

    def dump_instr_start(self, addr):
        if addr in self.sym_to_mem:
            addr = self.sym_to_mem[addr]
        self.start_bp.add(addr)

    def dump_instr_stop(self, addr):
        self.stop_bp.add(addr)

    def read_hex_string(self, addr, string):
        for i in range(len(string) // 2):
            byte = int(string[i*2:i*2+2],16)
            self.mem[addr + i] = byte

    def extend_symbol(self, sym, count):
        addr = self.sym_to_mem.get(sym, None)
        if addr:
            self.add_symbol(addr, sym + '+0')
            if count < 0:
                r = range(-1,count,-1)
            else:
                r = range(1, count)
            for i in r:
                if (addr + i) in self.mem_to_sym:
                    break
                self.add_symbol(addr + i, sym + '+%d'%i)

    def add_symbol(self, addr, sym):
        self.mem_to_sym[addr] = sym
        self.sym_to_mem[sym] = addr

    def read_symbols(self, file_name):
        fh = open(file_name)
        for line in fh:
            addr, sym = line.strip().split('|')
            addr = int(addr,16)
            self.add_symbol(addr, sym)
        fh.close()

    def read_hex(self, file_name):
        hex_file = open(file_name, 'r')
        line_num = 0
        end_count = 0
        for line in hex_file:
            line_num += 1
            line = line.strip()
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
                        self.mem[addr + i] = byte
                    start = 9 + count*2
                    byte = int(line[start:start+2],16)
                    cksum += byte
                    if cksum & 0xFF:
                        print("checksum line %d"%line_num)
            elif end_count == 0:
                num, sym, addr = re.split('  *', line)
                if len(addr) == 5:
                    addr = int(addr[:-1],16)
                    self.mem_to_sym[addr] = sym
                    self.sym_to_mem[sym] = addr
        hex_file.close()

    def add_input_device(self, port, device):
        self.in_devices[port] = device

    def add_output_device(self, port, device):
        self.out_devices[port] = device

def go():
    cpu = Intel8080(16*1024)

    interactive_out = True

    do_run = False
    do_run_file = None
    for arg in sys.argv[1:]:
        if do_run:
            do_run_file = arg
            do_run = False
        elif arg == "-j":
            cpu.show_inst = True
            cpu.show_mem_set = True
            cpu.show_mem_get = True
            interactive_out = False
        elif arg == "-r":
            do_run = True

    if False:
        cpu.read_symbols('IMSAI/basic4k.symbols')
        cpu.read_hex('IMSAI/basic4k.hex')
        cpu.extend_symbol('FACC', 4)
        cpu.extend_symbol('FTEMP', 10)
        cpu.extend_symbol('IMMED', 70)
        cpu.extend_symbol('IOBUF', 40)
        cpu.extend_symbol('IOBUF', -2)
        cpu.extend_symbol('RNDNU', 4)
        cpu.extend_symbol('BEGPR', -2)
    else:
        cpu.read_hex('IMSAI/basic8k.hex')
        cpu.extend_symbol('FACC', 4)
        cpu.extend_symbol('FTEMP', 12)
        cpu.extend_symbol('IMMED', 82)
        cpu.extend_symbol('IOBUFF', 82)
        cpu.extend_symbol('STRIN', 256)
        cpu.extend_symbol('BEGPR', 256)

    # TTY setup
    cpu.add_input_device(3, ScriptedInputDevice("Ready", None, 0xFF))
    print_device = None

    if do_run_file:
        tty_device = ScriptedInputDevice("TTY")
        tty_device.load_file(do_run_file)
        cpu.add_input_device(2, tty_device)
        cpu.show_inst = True
        cpu.show_mem_set = True
        cpu.show_mem_get = True
        interactive_out = False
        cpu.limit_steps = 200000
    else:
        cpu.add_input_device(2, InteractiveInputDevice("TTY"))

    if interactive_out:
        cpu.add_output_device(2, InteractiveOutputDevice("TTY"))
    else:
        print_device = RecordingOutputDevice("PRINT")
        cpu.add_output_device(2, print_device)

    cpu.reset(0)
    cpu.run()
    if print_device:
        print_device.print()

if __name__ == '__main__':
    go()

# see IMSAI/basic4k.hex
# see IMSAI/basic4k.asm
# see IMSAI/basic8k.hex
# see IMSAI/basic8k.asm
# see IMSAI/basic4k.symbols

