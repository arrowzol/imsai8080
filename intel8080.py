#!/usr/bin/python3

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

class CPU8080:
    def set_mem_device(self, mem_device, start, end):
        self.mem_devices[mem_device.name] = (start, end, mem_device)

    def unset_mem_device(self, mem_device):
        del self.mem_devices[mem_device.name]

    def __init__(self, device_factory, mem_size=16*1024):
        self.mem_devices = {}

        ########################################
        # Internal State
        ########################################

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

        self.instr_count = 0

        ########################################
        # I/O devices
        ########################################

        self.device_factory = device_factory

        ########################################
        # configuration
        ########################################

        # guard rails
        self.sp_fault = True
        self.limit_steps = 0
        self.read_only_end = 0

        # show debug info
        self.show_inst = False
        self.show_mem_set = False
        self.show_mem_get = False
        self.dump_instr_addr = set()
        self.debug_fh = None

        # CALL/RET tracking, for debug info
        self.return_stack = []
        self.call_indent = ""

        # symbols
        self.mem_to_sym = {}
        self.sym_to_mem = {}
        self.asm_mem_sym = {}
        self.sym5 = {}

    ########################################
    # 
    ########################################

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
        print(
            "  %s x%02x:%02x   %s --> %s"%(_RSX2[reg_pair], h, l, show_symbol, repr(s)),
            file=self.debug_fh)

    def dump_reg(self):
        print(
            "   A x%02x    FLAGS:%s"%(self.rs[7], self.strFlags()),
            file=self.debug_fh)
        for i in range(4):
            self.dump_one_reg(i)
        print("  SP x%04x"%(self.sp), file=self.debug_fh)
        print("  PC x%04x"%(self.pc), file=self.debug_fh)

    def set_mem(self, addr, value, bits=8, stack=False):
        """
        [addr + 0] <- low
        [addr + 1] <- high
        """
        if addr < self.read_only_end:
            if self.debug_fh:
                print("change read-only memory", file=self.debug_fh)
            self.halt = True
        if not stack and self.show_mem_set:
            s_addr = self.addr_to_str(addr)

            s_value = ("x%%%02dx"%(bits/4))%value
            if bits == 8 and 32 <= value < 127:
                s_value += " chr(%s)"%(chr(value))

            print(
                "          %s mem[%s] <- %s"%(self.call_indent, s_addr, s_value),
                file=self.debug_fh)

        if addr < len(self.mem):
            old_value = self.mem[addr]
            new_value = value & 0xFF
            self.mem[addr] = new_value
            for start, end, mem_device in self.mem_devices.values():
                if start <= addr < end:
                    mem_device.set_mem(addr, old_value, new_value)

        if bits == 16:
            addr += 1
            if addr < len(self.mem):
                old_value = self.mem[addr]
                new_value = (value >> 8) & 0xFF
                self.mem[addr] = new_value
                for start, end, mem_device in self.mem_devices.values():
                    if start <= addr < end:
                        mem_device.set_mem(addr, old_value, new_value)

    def reset(self, pc):
        self.pc = pc
        self.rs[REG_FLAG] = FLAG_1
        self.halt = False

    def get_bc(self):
        return self.rs[REG_B] * 0x100 + self.rs[REG_C]

    def get_de(self):
        return self.rs[REG_D] * 0x100 + self.rs[REG_E]

    def get_hl(self):
        return self.rs[REG_H] * 0x100 + self.rs[REG_L]

    def get_mem(self, addr):
        if addr >= len(self.mem):
            value = 0
        else:
            value = self.mem[addr]
        if self.show_mem_get:
            s_addr = self.addr_to_str(addr)

            s_value = "x%02x"%value
            if 32 <= value < 127:
                s_value += " chr(%s)"%(chr(value))

            print(
                "          %s is_mem[%s] -- %s"%(self.call_indent, s_addr, s_value),
                file=self.debug_fh)
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

    def call(self, to_addr):
        self.push(self.pc)
        if self.show_inst:
#            print("%sCP-CAL %04x"%(self.call_indent, self.pc))
            self.call_indent += "  "
            self.return_stack.append((self.sp, self.pc))
        self.pc = to_addr

    def ret(self, addr=-1):
        sp = self.sp
        if addr < 0:
            self.pc = self.pop()
        else:
            self.pc = addr

        if self.show_inst and self.return_stack:
            ex = self.return_stack.pop()
            ex_sp, ex_pc = ex

            # if the expected stack address where the pc is pulled matches
            if ex_sp == sp:
                # but the return pc is way off from expected
                if not ex_pc <= self.pc <= ex_pc+2:

                    # if the next expected stack pc matches, then pop 2
                    if self.return_stack:
                        stack_sp, stack_pc = self.return_stack[-1]
                        if stack_pc <= self.pc <= stack_pc+2:
                            self.return_stack.pop();
                            self.call_indent = self.call_indent[:-4]
                            return
                    # this was a PCHL or RET instr that did GOTO not RET
                    if addr == -1:
                        pass
#                        print("WARN ex:%04x act:%04x"%(ex, self.pc))
                    self.return_stack.append(ex)
                    return
            else:
                pass
#                print("WARN2 %04x  %04x ?= %04x  %04x ?= %04x"%(addr, ex[0], sp, ex[1], self.pc))
            self.call_indent = self.call_indent[:-2]
#            print("%sCP-RET %04x"%(self.call_indent, self.pc))

    def alu(self, op_names, op, value, show_value, pc, instr):
        a = self.rs[REG_A]
        a_start = a
        c_in = self.get_flag(FLAG_C)
        a4 = a & 0xF
        if op < 4 or op == 7:
            # 0:ADD, 1:ADC, 2:SUB, 3:SBB
            value2 = value
            if op != 7 and op & 0x01 == 1 and self.get_flag(FLAG_C):
                # ADC, SBB
                value2 += 1
            if op >= 2:
                # SUB, SBB, CMP
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
            if op != 7:
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

        if self.show_inst:
            s_value = "x%02x"%value
            s_a = "x%02x"%a
            if op < 4:
                c_out = self.get_flag(FLAG_C)
                s_a = "%d:"%c_out + s_a
                if op & 0x01:
                    s_value += "%s%d"%(_OPS[op], c_in)
            print(
                "%06x %04x %02x %s %s %s [x%02x%s%s=%s F=%s]"%(
                    self.instr_count, pc, instr, self.call_indent, op_names[op], show_value,
                    a_start, _OPS[op], s_value, s_a, self.strFlags()),
                file=self.debug_fh)

    def step(self):
        self.instr_count += 1
        pc = self.pc
        instr = self.get_instr8()
        family = instr & 0xC0

        if family == 0x00:
            family_op = instr & 0x07
            reg_id = ((instr >> 4) & 0x3) * 2 # 0=BC, 2=DE, 4=HL, 6=SP/PSW
            if family_op == 0:
                if self.show_inst:
                    print("%06x %04x %02x %s NOP"%(self.instr_count, pc, instr, self.call_indent), file=self.debug_fh)
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
                        s_data = self.addr_to_str(data)
                        print(
                            "%06x %04x %02x %s LXI %s %s"%(
                                self.instr_count, pc, instr, self.call_indent, _RSX_SP[reg_id // 2], s_data),
                            file=self.debug_fh)
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
                        print(
                            "%06x %04x %02x %s DAD %s [HL=x%04x]"%(
                                self.instr_count, pc, instr, self.call_indent, _RSX_SP[reg_id // 2],
                                h * 0x100 + l),
                            file=self.debug_fh)
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
                        s_addr = self.addr_to_str(addr)
                        print(
                            "%06x %04x %02x %s %s %s [x%02x, %s]"%(
                                self.instr_count, pc, instr, self.call_indent,
                                _LS_EXTENDED_OPS[bool(instr & 0x08)],
                                "BD"[bool(instr & 0x10)],
                                self.rs[REG_A], s_addr),
                            file=self.debug_fh)
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
                        s_addr = self.addr_to_str(addr)
                        value = ("x%%%02dx"%hexes)%value
                        print(
                            "%06x %04x %02x %s %s %s [%s=%s]"%(
                                self.instr_count, pc, instr, self.call_indent, _DIRECT_OPS[sub_op], s_addr,
                                who, value),
                            file=self.debug_fh)
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
                        s_value = self.addr_to_str(value)
                        r_name = _RSX2_SP[reg_id // 2]
                        print(
                            "%06x %04x %02x %s INX %s [%s=%s]"%(
                                self.instr_count, pc, instr, self.call_indent, _RSX_SP[reg_id // 2],
                                _RSX2_SP[reg_id // 2], s_value),
                            file=self.debug_fh)
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
                        s_value = self.addr_to_str(value)
                        print(
                            "%06x %04x %02x %s DCX %s [%s=%s]"%(
                                self.instr_count, pc, instr, self.call_indent, _RSX_SP[reg_id // 2],
                                _RSX2_SP[reg_id // 2], s_value),
                            file=self.debug_fh)
            elif family_op == 4:
                # INR, Increment Register or Memory
                value = self.get_by_id(instr, 3) + 1
                value4 = (value & 0xF) + 1
                self.set_flags_not_c(value, value4)
                value &= 0xFF
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    r_name = _RS[self.get_ident]
                    print(
                        "%06x %04x %02x %s INR %s [%s=x%02x F=%s]"%(
                            self.instr_count, pc, instr, self.call_indent, r_name,
                            r_name, value, self.strFlags()),
                        file=self.debug_fh)
            elif family_op == 5:
                # DCR, Decrement Register or Memory
                value = self.get_by_id(instr, 3) - 1
                value4 = (value & 0xF) - 1
                self.set_flags_not_c(value, value4)
                value &= 0xFF
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    r_name = _RS[self.get_ident]
                    print(
                        "%06x %04x %02x %s DCR %s [%s=x%02x F=%s]"%(
                            self.instr_count, pc, instr, self.call_indent, r_name,
                            r_name, value, self.strFlags()),
                        file=self.debug_fh)
            elif family_op == 6:
                # MVI, Move Immediate
                value = self.mem[self.pc]
                self.pc += 1
                self.pc &= 0xFFFF
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    print(
                        "%06x %04x %02x %s MVI %s x%02x"%(
                            self.instr_count, pc, instr, self.call_indent, _RS[self.set_ident], value),
                        file=self.debug_fh)
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
                        print(
                            "%06x %04x %02x %s %s [A=x%02x F=%s]"%(
                                self.instr_count, pc, instr, self.call_indent, _07_OPS[op],
                                a, self.strFlags()),
                            file=self.debug_fh)
                    else:
                        print(
                            "%06x %04x %02x %s %s [F=%s]"%(
                                self.instr_count, pc, instr, self.call_indent, _07_OPS[op],
                                self.strFlags()),
                            file=self.debug_fh)
        elif family == 0x40:
                # MOV [8-bit],[8-bit]

                if instr == 0x76:
                    if self.debug_fh:
                        print(
                            "%06x %04x %02x %s HLT"%(
                                self.instr_count, pc, instr, self.call_indent),
                            file=self.debug_fh)
                    self.halt = True
                    return

                value = self.get_by_id(instr, 0)
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    r1_name = _RS[self.set_ident]
                    r2_name = _RS[self.get_ident]
                    print(
                        "%06x %04x %02x %s MOV %s,%s [%s=x%02x]"%(
                            self.instr_count, pc, instr, self.call_indent, r1_name, r2_name,
                            r1_name, value),
                        file=self.debug_fh)

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
                #
                # JNZ, Jump If Not Zero
                # JNC, Jump If Not Carry
                # JPO, Jump If Parity Odd
                # JP, Jump If Plus
                #
                # CNZ, Call If Not Zero
                # CNC, Call If Not Carry
                # CPO, Call If Parity Odd
                # CP, Call If Plus
                #
                # RZ, Return If Zero
                # RC, Return If Carry
                # RPE, Return If Parity Even
                # RM, Return If Minus
                #
                # JZ, Jump If Zero
                # JC, Jump If Carry
                # JPE, Jump If Parity Even
                # JM, Jump If Minus
                #
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
                    op_name = _RJC_OPS[(instr >> 1) & 0x1F]
                    if sub_op == 0:
                        print(
                            "%06x %04x %02x %s %s [%s]"%(
                                self.instr_count, pc, instr, self.call_indent, op_name,
                                condition),
                            file=self.debug_fh)
                    else:
                        s_addr = self.addr_to_str(addr)
                        print(
                            "%06x %04x %02x %s %s %s [%s]"%(
                                self.instr_count, pc, instr, self.call_indent, op_name, s_addr,
                                condition),
                            file=self.debug_fh)

                if condition:
                    if sub_op == 0:
                        # return
                        self.ret(-2)
                    else:
                        # jump/call
                        if instr & 0x02 == 0:
                            # call
                            self.call(addr)
                        else:
                            self.pc = addr
            elif instr | 0x08 == 0xCB:
                # JMP
                addr = self.get_instr16()
                self.pc = addr
                if self.show_inst:
                    s_addr = self.addr_to_str(addr)
                    print(
                        "%06x %04x %02x %s JMP %s"%(
                            self.instr_count, pc, instr, self.call_indent, s_addr),
                        file=self.debug_fh)
            elif instr | 0x70 == 0xFD:
                # CALL
                prev_call_indent = self.call_indent
                addr = self.get_instr16()
                self.call(addr)
                if self.show_inst:
                    s_addr = self.addr_to_str(addr)
                    print(
                        "%06x %04x %02x %s CALL %s"%(
                            self.instr_count, pc, instr, prev_call_indent, s_addr),
                        file=self.debug_fh)
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
                        s_value = self.addr_to_str(value)
                    print(
                        "%06x %04x %02x %s POP %s [%s=%s]"%(
                            self.instr_count, pc, instr, self.call_indent, _RSX[paramF],
                            _RSX2[paramF], s_value),
                        file=self.debug_fh)
            elif family_opF == 5:
                # PUSH (16-bit-reg), Push Data Onto Stack
                value_h = self.rs[paramF*2]
                value_l = self.rs[paramF*2 + 1]

                if paramF == 3: # TODO: why PUSH PSW switched?
                    self.push(value_l * 0x100 + value_h)
                else:
                    self.push(value_h * 0x100 + value_l)

                if self.show_inst:
                    print(
                        "%06x %04x %02x %s PUSH %s [x%04x]"%(
                            self.instr_count, pc, instr, self.call_indent, _RSX[paramF],
                            value_h * 0x100 + value_l),
                        file=self.debug_fh)
            elif family_op7 == 6:
                # ADI, ACI, SUI, SBI, ANI, XRI, ORI, CPI
                value = self.get_instr8()
                self.alu(_C0_OPS, param7, value, "x%02x"%value, pc, instr)

            elif family_op7 == 7:
                # RST (id), Restart
                prev_call_indent = self.call_indent
                exp = (instr >> 3) & 0x7
                self.call(exp*0x08)

                if self.show_inst:
                    print(
                        "%06x %04x %02x %s RST %d"%(
                            self.instr_count, pc, instr, prev_call_indent, exp),
                        file=self.debug_fh)
            elif instr == 0xDB:
                # IN (device)
                device_id = self.get_instr8()

                in_device = self.device_factory.get_in_device(device_id)
                if in_device:
                    value = in_device.get_device_input(self)
                    if value == -1:
                        if self.debug_fh:
                            print("DEVICE EMPTY x%02x %s"%(device_id, in_device.name), file=self.debug_fh)
                        print("DEVICE EMPTY x%02x %s"%(device_id, in_device.name))
                        self.halt = True
                        return
                else:
                    value = 0
                self.rs[REG_A] = value

                if self.show_inst:
                    s_value = "x%02x"%value
                    if 32 <= value < 127:
                        s_value += " chr(%s)"%(chr(value))
                    print(
                        "%06x %04x %02x %s IN x%02x [%s]"%(
                            self.instr_count, pc, instr, self.call_indent, device_id,
                            s_value),
                        file=self.debug_fh)
            elif instr == 0xD3:
                # OUT (device)
                device_id = self.get_instr8()
                out_device = self.device_factory.get_out_device(device_id)
                if out_device:
                    out_device.put_output(self.rs[REG_A])

                if self.show_inst:
                    value = self.rs[REG_A]
                    s_value = "x%02x"%value
                    if 32 <= value < 127:
                        s_value += " chr(%s)"%(chr(value))
                    print(
                        "%06x %04x %02x %s OUT x%02x [%s]"%(
                            self.instr_count, pc, instr, self.call_indent, device_id,
                            s_value),
                        file=self.debug_fh)
            elif instr == 0xF9:
                # SPHL, Load SP from H and L
                self.sp = self.get_hl()
                if self.show_inst:
                    print(
                        "%06x %04x %02x %s SPHL [SP=x%04x]"%(
                            self.instr_count, pc, instr, self.call_indent, self.sp),
                        file=self.debug_fh)
            elif instr == 0xC9:
                # RET, Return
                prev_call_indent = self.call_indent
                self.ret()
                if self.show_inst:
                    print(
                        "%06x %04x %02x %s RET [A=x%02x HL=x%04x F=%s]"%(
                            self.instr_count, pc, instr, prev_call_indent,
                            self.rs[REG_A], self.rs[REG_H] * 0x100 + self.rs[REG_L], self.strFlags()),
                        file=self.debug_fh)
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
                    print(
                        "%06x %04x %02x %s XCHG [HL=%02x%02x DE=%02x%02x]"%(
                            self.instr_count, pc, instr, self.call_indent,
                            d, e, h, l),
                        file=self.debug_fh)
            elif instr == 0xE3:
                # XTHL, Exchange Stack
                reg_l = self.rs[REG_L]
                self.rs[REG_L] = self.mem[self.sp]

                reg_h = self.rs[REG_H]
                self.rs[REG_H] = self.mem[self.sp + 1]

                self.set_mem(self.sp, reg_h * 0x100 + reg_l, 16)

                if self.show_inst:
                    print("%06x %04x %02x %s XTHL"%(self.instr_count, pc, instr, self.call_indent), file=self.debug_fh)
            elif instr == 0xE9:
                # TODO: can be used as a "RET"
                # PCHL, H & L to PC
                addr = self.rs[REG_L] + self.rs[REG_H] * 0x100
                self.ret(addr)

                if self.show_inst:
                    print("%06x %04x %02x %s PCHL"%(self.instr_count, pc, instr, self.call_indent), file=self.debug_fh)
            else:
                if self.debug_fh:
                    print("%06x %04x unknown instruction %2x"%(self.instr_count, pc, instr), file=self.debug_fh)
                print("%06x %04x unknown instruction %2x"%(self.instr_count, pc, instr))
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
            if self.debug_fh:
                print("STACK FAULT", file=self.debug_fh)
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
            if self.debug_fh:
                print("STACK FAULT", file=self.debug_fh)
            print("STACK FAULT")
            self.halt = True
            return 0

        self.sp += 1
        self.sp &= 0xFFFF

        if self.sp < len(self.mem):
            value += self.mem[self.sp] * 0x100
        elif self.sp_fault:
            if self.debug_fh:
                print("STACK FAULT", file=self.debug_fh)
            print("STACK FAULT")
            self.halt = True
            return 0

        self.sp += 1
        self.sp &= 0xFFFF

        return value

    def tron(self):
        if not self.debug_fh:
            self.debug_fh = open('dbg.txt', 'w')
        self.show_inst = True
        self.show_mem_set = True
        self.show_mem_get = True

    def troff(self):
        self.show_inst = False
        self.show_mem_set = False
        self.show_mem_get = False

    def run(self):
        if self.show_inst or self.show_mem_set or self.show_mem_get:
            self.debug_fh = open('dbg.txt', 'w')
        bp_next = False
        while not self.halt and (self.limit_steps <= 0 or self.instr_count < self.limit_steps):
            if self.show_inst and self.pc in self.mem_to_sym:
                print(":%s:"%(self.mem_to_sym[self.pc]), file=self.debug_fh)
            if bp_next or self.pc in self.dump_instr_addr:
                self.dump_reg()
            bp_next = self.pc in self.dump_instr_addr
            self.step()
        if self.debug_fh:
            print("STEPS %d"%(self.instr_count), file=self.debug_fh)
            self.debug_fh.close()

    def set_read_only_end(self, addr):
        self.read_only_end = self.addr_to_number(addr)

    def dump_at_instr(self, addr):
        self.dump_instr_addr.add(self.addr_to_number(addr))

    ########################################
    # use symbols
    ########################################

    def addr_to_number(self, addr):
        if type(addr) == str:
            if addr in self.sym_to_mem:
                addr = self.sym_to_mem[addr]
            else:
                try:
                    addr = int(addr, 16)
                except Exception:
                    raise Exception("bad address %s"%(addr))
        if type(addr) != int:
            raise Exception("bad address %s"%(addr))
        return addr

    def addr_to_str(self, addr):
        sym = self.mem_to_sym.get(addr, None)
        if sym:
            return sym
        return "x%04x"%addr

    ########################################
    # load symbols
    ########################################

    def extend_symbol(self, sym, count):
        addr = self.sym_to_mem.get(sym, None)
        if True: # addr and addr >= 0x40:
            self.mem_to_sym[addr] = sym + '+0'
            if count < 0:
                r = range(-1,count,-1)
            else:
                r = range(1, count)
            for i in r:
                if (addr + i) in self.mem_to_sym:
                    break
                self.mem_to_sym[addr + i] = sym + '+%d'%i

    def add_symbol_signature(self, sym, sym_bytes):
        self.asm_mem_sym[sym] = sym_bytes
        if len(sym) > 5:
            self.sym5[sym[:5]] = sym

    def add_symbol(self, sym, addr):
        # convert truncated symbols from the hex file into those from the asm file
        if sym in self.sym5:
            sym = self.sym5[sym]

        # check if this symbol is known to be the right type, and find it's size
        if self.asm_mem_sym:
            sym_bytes = self.asm_mem_sym.get(sym, None)
        else:
            sym_bytes = 1

        # record this symbol
        if sym_bytes:
            self.mem_to_sym[addr] = sym
            self.sym_to_mem[sym] = addr
            if sym_bytes > 1:
                self.extend_symbol(sym, sym_bytes)

