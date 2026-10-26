"""Find Platinum (US rev 0) function addresses for the raid prototype by structure (no symbols exist).
Dumps ARM9 RAM in the field, locates gFieldScriptCommands (840 Thumb pointers), disassembles
ScrCmd_StartWildBattle -> Encounter_NewVsSpeciesAtLevel -> its callees. Prints what it finds."""
import os, sys, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "platinum"))
from ds import DS
import capstone

STATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "states")
d = DS(state=os.path.join(STATES, "pt_sl_route202.dsst"))
d.run(2)
BASE = 0x02000000
ram = d.read(BASE, 0x400000)
open("/tmp/yl-raid-proto/pt_ram.bin", "wb").write(ram)

def u32(a):
    return struct.unpack_from("<I", ram, a - BASE)[0]

# 840 consecutive odd pointers into ARM9 static code
best = None
for off in range(0, 0x110000, 4):
    a = BASE + off
    n = 0
    while n < 840:
        v = u32(a + 4 * n)
        if not (v & 1 and 0x02000000 <= v < 0x02400000):
            break
        n += 1
    if n >= 840:
        best = a
        break
print("gFieldScriptCommands at %08X" % best if best else "table not found")
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

def dis(addr, n=60, show=True):
    addr &= ~1
    code = ram[addr - BASE: addr - BASE + n * 2 + 8]
    out = []
    for ins in md.disasm(code, addr):
        out.append(ins)
        if show:
            print("   %08X %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))
        if ins.mnemonic in ("pop", "bx") and ("pc" in ins.op_str or ins.mnemonic == "bx"):
            break
        if len(out) >= n:
            break
    return out

def bls(ins):
    return [int(i.op_str.strip("#"), 16) for i in ins if i.mnemonic in ("bl", "blx") and i.op_str.startswith("#")]

for idx, name in ((292, "ScrCmd_StartWildBattle"), (709 - 8, "ScrCmd_StartLegendaryBattle")):
    f = u32(best + 4 * idx)
    print(name, "%08X" % f)
    ins = dis(f)
    print("  calls:", ["%08X" % x for x in bls(ins)])
