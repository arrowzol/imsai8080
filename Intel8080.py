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

_RS = 'BCDEHLMA'
_RSX_SP = ['B', 'D', 'H', 'SP']
_RSX    = ['B', 'D', 'H', 'PSW']
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
        self.bp = set()
        self.start_bp = set()
        self.stop_bp = set()
        self.in_devices = {}
        self.out_devices = {}
        self.call_indent = ""
        self.symbols = {}

    def dump(self):
        print("   A %02x    FLAGS:%s"%(
            self.rs[7],
            "".join((
                n if n != '.' and b == '1' else "-"
                for b,n in zip(bin(0x100 + self.rs[6])[3:], "SZ.A.P.C")))))
        print("  BC %02x-%02x"%(self.rs[0], self.rs[1]))
        print("  DE %02x-%02x"%(self.rs[2], self.rs[3]))
        print("  HL %02x-%02x"%(self.rs[4], self.rs[5]))
        print("  SP %04x"%(self.sp))
        print("  PC %04x"%(self.pc))

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

    def get_by_id(self, instr, shift):
        ident = (instr >> shift) & 0x07
        self.get_ident = ident
        if ident == REG_MEM:
            addr = self.get_hl()
            if addr >= len(self.mem):
                return 0
            return self.mem[addr]
        else:
            return self.rs[ident]

    def set_by_id(self, instr, shift, value):
        ident = (instr >> shift) & 0x07
        self.set_ident = ident
        if ident == REG_MEM:
            addr = self.get_hl()
            if addr < len(self.mem):
                self.mem[addr] = value
            if self.show_inst:
                print("  [%04x] = %02x"%(addr, value))
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

    def set_all_flags(self, value, value4):
        self.set_flag(FLAG_S, value & 0x80)
        self.set_flag(FLAG_Z, value == 0)
        self.set_flag(FLAG_A, not 0 <= value4 < 0x10)
        self.set_flag(FLAG_P, bin(value & 0xFF).count("1") & 0x01)
        self.set_flag(FLAG_C, not 0 <= value < 0x100)

    def set_most_flags(self, value):
        self.set_flag(FLAG_S, value & 0x80)
        self.set_flag(FLAG_Z, value == 0)
        self.set_flag(FLAG_P, bin(value & 0xFF).count("1") & 0x01)
        self.set_flag(FLAG_C, not 0 <= value < 0x100)

    def alu(self, op, value):
        a = self.rs[REG_A]
        a4 = a & 0xF
        if op == 0 or op == 1:
            # ADD, ADC
            a += value
            a4 += value & 0xF
            if op == 1 and self.get_flag(FLAG_C):
                a += 1

            self.rs[REG_A] = a & 0xFF
            self.set_all_flags(a, a4)
        elif op == 2 or op == 3:
            # SUB, SBB
            a -= value
            a4 -= value & 0xF
            if op == 3 and self.get_flag(FLAG_S):
                a -= 1

            self.rs[REG_A] = a & 0xFF
            self.set_all_flags(a, a4)
            self.set_flag(FLAG_A, not self.get_flag(FLAG_A))
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
        else:
            print("%04x unknown ALU OP %d"%(pc, op))
            self.halt = True

    def step(self):
        pc = self.pc
        instr = self.get_instr8()
        family = instr & 0xC0

        if family == 0x00:
            family_op = instr & 0x07
            reg_id = ((instr >> 4) & 0x7) * 2
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
                        s_data = self.symbols.get(data, "%04x"%data)
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
                        print("%04x %02x %s DAD %s"%(pc, instr, self.call_indent, _RSX_SP[reg_id // 2]))
            elif family_op == 2:
                if instr < 0x20:
                    if instr & 0x10:
                        addr = self.get_de()
                    else:
                        addr = self.get_bc()
                    if instr & 0x08:
                        # LDAX (16-bit-reg), Load Accumulator
                        if addr >= len(self.mem):
                            value = 0
                        else:
                            value = self.mem[addr]
                        self.rs[REG_A] = value
                    else:
                        # STAX (16-bit-reg), Store Accumulator
                        if addr < len(self.mem):
                            self.mem[addr] = self.rs[REG_A]
                    if self.show_inst:
                        s_addr = self.symbols.get(addr, "%04x"%addr)
                        print("  [%s] = %02x"%(s_addr, self.rs[REG_A]))
                        print("%04x %02x %s %s %s [%02x -> %s]"%(
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
                        self.mem[addr] = self.rs[REG_L]
                        self.mem[addr + 1] = self.rs[REG_H]
                    elif sub_op == 1:
                        # LHLD, Load H and L Direct
                        self.rs[REG_L] = self.mem[addr]
                        self.rs[REG_H] = self.mem[addr + 1]
                    elif sub_op == 2:
                        # STA, Store Accumulator Direct
                        self.mem[addr] = self.rs[REG_A]
                    elif sub_op == 3:
                        # LDA, Load Accumulator Direct
                        self.rs[REG_A] = self.mem[addr]
                    if self.show_inst:
                        s_addr = self.symbols.get(addr, "%04x"%addr)
                        print("%04x %02x %s %s %s"%(pc, instr, self.call_indent, _DIRECT_OPS[sub_op], s_addr))
            elif family_op == 3:
                if instr & 0x08 == 0:
                    # INX, Increment Register Pair
                    if reg_id == 6:
                        self.sp += 1
                    else:
                        value = self.rs[reg_id]
                        value += 1
                        self.rs[reg_id] = value & 0xFF
                        if value == 0x100:
                            value_h = (self.rs[reg_id + 1] + 1) & 0xFF
                            self.rs[reg_id + 1] = value_h
                            value = 0
                        else:
                            value_h = self.rs[reg_id + 1]

                    if self.show_inst:
                        print("%04x %02x %s INX %s [%04x]"%(pc, instr, self.call_indent, _RSX_SP[reg_id // 2], value_h * 0x100 + value))
                else:
                    # DCX, Decrement Register Pair
                    if reg_id == 7:
                        self.sp -= 1
                    else:
                        value = self.rs[reg_id - 1]
                        value -= 1
                        self.rs[reg_id] = value & 0xFF
                        if value == -1:
                            self.rs[reg_id] -= 1

                    if self.show_inst:
                        print("%04x %02x %s DCX %s"%(pc, instr, self.call_indent, _RSX_SP[reg_id // 2]))
            elif family_op == 4:
                # INR, Increment Register or Memory
                value = self.get_by_id(instr, 0) + 1
                value4 = (value & 0xF) + 1
                self.set_by_id(instr, 3, value)
                self.set_all_flags(value, value4)

                if self.show_inst:
                    print("%04x %02x %s INR %s [%02x]"%(pc, instr, self.call_indent, _RS[self.get_ident], value))
            elif family_op == 5:
                # DCR, Decrement Register or Memory
                value = self.get_by_id(instr, 3) - 1
                value4 = (value & 0xF) - 1
                self.set_by_id(instr, 3, value)
                self.set_all_flags(value, value4)

                if self.show_inst:
                    print("%04x %02x %s DCR %s"%(pc, instr, self.call_indent, _RS[self.get_ident]))
            elif family_op == 6:
                # MVI, Move Immediate
                value = self.mem[self.pc]
                self.pc += 1
                self.set_by_id(instr, 3, value)

                if self.show_inst:
                    print("%04x %02x %s MVI %s %02x"%(pc, instr, self.call_indent, _RS[self.set_ident], value))
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
                        a = (c << 8) | (a >> 1)
                    else:
                        c_out = (a & 0x80) >> 7
                        if not through_carry:
                            c = c_out
                        a = (a << 1) | c
                    self.rs[REG_A] = a
                    self.set_flag(FLAG_C, c_out)
                else:
                    if op == 4:
                        # DDA, Decimal Adjust Accumulator
                        a = self.rs[REG_A]
                        a4 = a & 0xf
                        if a & 0xF > 9 or self.get_flag(FLAG_A):
                            a += 0x06
                            a4 += 0x06
                        if (a >> 4) & 0xF > 9 or self.get_flag(FLAG_C):
                            a += 0x60
                        self.rs[REG_A] = a
                        self.set_all_flags(a, a4)
                    elif op == 5:
                        # CMA, Complement Accumulator
                        self.rs[REG_A] = (~self.rs[REG_A]) & 0xFF
                    elif op == 6:
                        # STC, Set Carry
                        self.set_flag(FLAG_C, True)
                    elif op == 7:
                        # CMC, Complement Carry
                        self.set_flag(FLAG_C, not self.get_flag(FLAG_C))
                if self.show_inst:
                    print("%04x %02x %s %s"%(pc, instr, self.call_indent, _07_OPS[op]))
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
                    print("%04x %02x %s MOV %s,%s"%(pc, instr, self.call_indent, _RS[self.set_ident], _RS[self.get_ident]))

        elif family == 0x80:
            # ADD,ADC,SUB,SBB,ANA,XRA,ORA,CMP [8-bit]

            family_op = (instr >> 3) & 0x07
            value = self.get_by_id(instr, 0)

            self.alu(family_op, value)

            if self.show_inst:
                print("%04x %02x %s %s %s"%(pc, instr, self.call_indent, _80_OPS[family_op], _RS[self.get_ident]))

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
                        s_addr = self.symbols.get(addr, "%04x"%addr)
                        print("%04x %02x %s %s %s [%s]"%(
                            pc, instr, self.call_indent, _RJC_OPS[(instr >> 1) & 0x1F], s_addr, condition))

                if condition:
                    if sub_op == 0:
                        # return
                        self.call_indent = self.call_indent[:-2]
                        self.pc = self.pop16()
                    else:
                        # jump/call
                        if instr & 0x02 == 0:
                            # call
                            self.call_indent += "  "
                            self.push16(self.pc)
                        self.pc = addr
            elif instr | 0x08 == 0xCB:
                # JMP
                addr = self.get_instr16()
                self.pc = addr
                if self.show_inst:
                    s_addr = self.symbols.get(addr, "%04x"%addr)
                    print("%04x %02x %s JMP %s"%(pc, instr, self.call_indent, s_addr))
            elif instr | 0x70 == 0xFD:
                # CALL
                addr = self.get_instr16()
                self.push16(self.pc)
                self.pc = addr
                if self.show_inst:
                    s_addr = self.symbols.get(addr, "%04x"%addr)
                    print("%04x %02x %s CALL %s"%(pc, instr, self.call_indent, s_addr))
                self.call_indent += "  "
            elif family_opF == 1:
                # POP (16-bit-reg), Pop Data Off Stack
                self.rs[paramF*2] = self.pop8()
                self.rs[paramF*2 + 1] = self.pop8()

                if self.show_inst:
                    print("%04x %02x %s POP %s"%(pc, instr, self.call_indent, _RSX[paramF]))
            elif family_opF == 5:
                # PUSH (16-bit-reg), Push Data Onto Stack
                self.push8(self.rs[paramF*2 + 1])
                self.push8(self.rs[paramF*2])

                if self.show_inst:
                    print("%04x %02x %s PUSH %s"%(pc, instr, self.call_indent, _RSX[paramF]))
            elif family_op7 == 6:
                # ADI, ACI, SUI, SBI, ANI, XRI, ORI, CPI
                value = self.get_instr8()
                self.alu(param7, value)

                if self.show_inst:
                    print("%04x %02x %s %s %02x"%(pc, instr, self.call_indent, _C0_OPS[param7], value))
            elif family_op7 == 7:
                # RST (id), Restart
                self.push16(self.pc)
                exp = (instr >> 3) & 0x7
                self.pc = exp*0x08

                if self.show_inst:
                    print("%04x %02x %s RST %d"%(pc, instr, self.call_indent, exp))
                self.call_indent += "  "
            elif instr == 0xDB:
                # IN (device)
                device_id = self.get_instr8()

                device = self.in_devices.get(device_id, None)
                if device:
                    value = device.pop()
                else:
                    value = 0
                self.rs[REG_A] = value

                if self.show_inst:
                    print("%04x %02x %s IN %02x [%02x]"%(pc, instr, self.call_indent, device_id, value))
            elif instr == 0xD3:
                # OUT (device)
                device_id = self.get_instr8()
                if not device_id in self.out_devices:
                    self.out_devices[device_id] = []
                self.out_devices[device_id].append(self.rs[REG_A])

                if self.show_inst:
                    print("------- %02x -> %02x"%(self.rs[REG_A], device_id))
                    print("%04x %02x %s OUT %02x"%(pc, instr, self.call_indent, device_id))
            elif instr == 0xF9:
                # SPHL, Load SP from H and L
                self.sp = self.get_hl()
                if self.show_inst:
                    print("%04x %02x %s SPHL [%04x]"%(pc, instr, self.call_indent, self.sp))
            elif instr == 0xC9:
                # RET, Return
                self.pc = self.pop16()
                if self.show_inst:
                    print("%04x %02x %s RET"%(pc, instr, self.call_indent))
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
                popped = self.mem[self.sp]
                reg = self.rs[REG_L]
                self.mem[self.sp] = reg
                self.rs[REG_L] = popped

                popped = self.mem[self.sp + 1]
                reg = self.rs[REG_H]
                self.mem[self.sp + 1] = reg
                self.rs[REG_H] = popped

                if self.show_inst:
                    print("%04x %02x %s XTHL"%(pc, instr, self.call_indent))
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

    def push16(self, value):
        self.sp -= 1
        self.sp &= 0xFFFF
        self.mem[self.sp] = value & 0xFF
        self.sp -= 1
        self.sp &= 0xFFFF
        self.mem[self.sp] = (value >> 8) & 0xFF

    def pop16(self):
        value = self.mem[self.sp] * 0x100
        self.sp += 1
        self.sp &= 0xFFFF
        value += self.mem[self.sp]
        self.sp += 1
        self.sp &= 0xFFFF
        return value

    def push8(self, value):
        self.sp -= 1
        self.mem[self.sp] = value

    def pop8(self):
        value = self.mem[self.sp]
        self.sp += 1
        return value

    def run(self):
        count_down = 100000
        bp_on = False
        while not self.halt and count_down > 0:
            count_down -= 1
            if self.pc in self.start_bp:
                bp_on = True
            if self.pc in self.stop_bp:
                bp_on = False
            if bp_on or self.pc in self.bp:
                self.dump()
            self.step()
        print(repr(self.out_devices))
        print("OUT: " + "".join((chr(c) for c in self.out_devices[2] if 0 < c < 128)))

    def add_break_point(addr):
        self.bp.add(addr)

    def break_point_start(self, addr):
        self.start_bp.add(addr)

    def break_point_stop(self, addr):
        self.stop_bp.add(addr)

    def read_hex(self, file_name):
        hex_file = open(file_name, 'r')
        line_num = 0
        for line in hex_file:
            line_num += 1
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
        tape = hex_file.read();
        hex_file.close()

if __name__ == '__main__':
    cpu = Intel8080(16*1024)

    # see IMSAI/basic4k.asm
    cpu.symbols[0x0d9d] = 'SYM:TESTI'
    cpu.symbols[0x0e32] = 'SYM:GETCH'
    cpu.symbols[0x0db9] = 'SYM:CRLF'
    cpu.symbols[0x0da5] = 'SYM:TESTO'
    cpu.symbols[0x0e57] = 'SYM:COPYH'
    cpu.symbols[0x0e60] = 'SYM:COPYD'
    cpu.symbols[0x008d] = 'SYM:GETCM' # start here
    cpu.symbols[0x0d59] = 'SYM:TERMI'
    cpu.symbols[0x10cf] = 'SYM:PROMP'
    cpu.symbols[0x1001] = 'SYM:IOBUF-1'
    cpu.symbols[0x1002] = 'SYM:IOBUF'
    cpu.symbols[0x0d73] = 'SYM:TREAD'
    cpu.symbols[0x0e44] = 'SYM:CONTO'
    cpu.symbols[0x1063] = 'SYM:COLUM'
    cpu.symbols[0x1061] = 'SYM:OUTSW'
    cpu.symbols[0x0db7] = 'SYM:TOUT2'
    cpu.symbols[0x0dad] = 'SYM:TOUT1'
    cpu.symbols[0x0dca] = 'SYM:DELAY'
    cpu.symbols[0x106c] = 'SYM:STACK'
    cpu.symbols[0x0dd5] = 'SYM:NOTCR'
    cpu.symbols[0x0e8d] = 'SYM:PACK'
    cpu.symbols[0x0083] = 'SYM:GENRN'
    cpu.symbols[0x0ad1] = 'SYM:RND'
#    cpu.symbols[0x1000] = 'SYM:RAM'
    cpu.symbols[0x0123] = 'SYM:EXEC'
    cpu.symbols[0x0f63] = 'SYM:NEWLI'
    cpu.symbols[0x013c] = 'SYM:NOTSC'
    cpu.symbols[0x0f5f] = 'SYM:LISTL'
    cpu.symbols[0x01d3] = 'SYM:LIST'
    cpu.symbols[0x0f67] = 'SYM:RUNLI'
    cpu.symbols[0x016d] = 'SYM:RUNIT'
    cpu.symbols[0x1062] = 'SYM:RUNSW'
    cpu.symbols[0x10d0] = 'SYM:IMMED'
    cpu.symbols[0x0159] = 'SYM:IMED'
    cpu.symbols[0x106a] = 'SYM:LINE'
    cpu.symbols[0x01a2] = 'SYM:IMMD'
    cpu.symbols[0x1057] = 'SYM:ADDR1'
    cpu.symbols[0x0e2d] = 'SYM:TSTCH'
    cpu.symbols[0x0f7b] = 'SYM:JMPTB'


    cpu.read_hex('IMSAI/basic4k.hex')


    # TTY ready
    cpu.in_devices[3] = [
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        ]

    # the TTY
    l = [ord(c) for c in 'PRINT 1+5\r PRINT "FISH"\r']
    l.reverse()
    cpu.in_devices[2] = l

    cpu.reset(0)
    cpu.run()
