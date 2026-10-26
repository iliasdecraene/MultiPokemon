"""Platinum (US rev 0) helpers for the raid prototypes: call game functions from Python + addresses found by
pt_find.py (structure, no symbols exist). Throwaway.

CALLING A GAME FUNCTION (no PC writes - DeSmuME has already fetched the instruction when an exec hook runs, so a
write to r15 is ignored; measured): the field calls a small table dispatcher about 1.5x per frame:

    02067DA8 push {r3,r4,r5,lr} ... 02067DBA ldr r4,[r4,r5]   ; r4 = table[arg1]
    02067DBC blx r4                                           ; <- HOOK_CALL
    02067DBE pop {r3,r4,r5,pc}                                ; <- HOOK_RET

  1. HOOK_CALL with a job queued: remember the real r0-r4, put our function in r4 and our arguments in r0-r3.
     Our function runs instead and returns to HOOK_RET.
  2. HOOK_RET (ours returned): r0 = our result. Re-run the real call: the frame's saved PC slot ([sp+12]) is pointed
     back at the dispatcher's entry (02067DA9) and its ENTRY arguments restored (r0, r1 = table index = r5 >> 2,
     r2/r3 = what it moved to r1/r2; r3 also in the frame's r3 slot, which the pop reloads). The pop "returns" into the dispatcher, which does the real call with the real arguments.
  3. HOOK_RET again (the real call returned): the frame's PC slot gets the caller's real return address back. The
     pop returns to the caller with the real result. Net effect: our function ran once, nothing was skipped.
One call per dispatcher invocation (max 4 register arguments).
"""
import os
import sys
from ctypes import c_char_p

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "platinum"))

# --- v2 (works in every field map): the epilogue of ExecuteFieldProcesses (the field app's main, every frame)
EPILOGUE = 0x0203CCE0                     # movs r0,#0 ; >>> pop {r3, pc} <<<  (the normal "not finished" return)
EPILOGUE_CODE = bytes.fromhex("08bd")     # pop {r3, pc}


class EpilogueCaller:
    """Call game functions (<= 4 args) from Python, one per field frame, with only register/memory writes.

    At `pop {r3, pc}` (EPILOGUE, frame = [r3 slot, return address]):
      1. job queued: remember the frame + r0, put our function in the frame's PC slot and our 4th argument in
         its r3 slot, r0-r2 = arguments, LR = EPILOGUE|1. The pop "returns" into our function, which returns to
         EPILOGUE (its own pop/bx lr uses the LR we set).
      2. back at EPILOGUE: r0 = our result; write the original frame back just below the current SP, set
         SP = that address and r0 = the original return value. The pop now returns to the real caller.
    A DeSmuME exec hook cannot redirect execution by writing r15 (the instruction is already fetched), but the
    pop reads the stack and the stack is ours to write."""

    def __init__(self, d):
        self.d = d
        self.r = Regs(d.emu)
        self.queue = []
        self.cur = None
        self.log = []
        d.emu.memory.register_exec(EPILOGUE, self._cb)

    def _cb(self, addr, size):
        try:
            self._on_pop()
        except Exception as e:  # noqa: BLE001
            self.log.append("hook error %r" % e)
        return 0

    def call(self, fn, args, on_result=None):
        self.queue.append((fn, list(args), on_result))

    def busy(self):
        return bool(self.queue) or self.cur is not None

    def _on_pop(self):
        d, r = self.d, self.r
        if d.read(EPILOGUE, 2) != EPILOGUE_CODE:
            return
        sp = r["r13"]
        if self.cur is None:
            if not self.queue:
                return
            fn, args, cb = self.queue.pop(0)
            args = args + [0] * (4 - len(args))
            self.cur = {"cb": cb, "sp": sp, "frame": (d.r32(sp), d.r32(sp + 4)), "r0": r["r0"]}
            d.w32(sp, args[3])                # the pop loads r3 from here
            d.w32(sp + 4, fn | 1)             # ... and PC from here
            for i in range(3):
                r["r%d" % i] = args[i]
            r["r14"] = EPILOGUE | 1
            self.log.append("call %08X %s" % (fn, ["%X" % a for a in args]))
            return
        cur = self.cur
        if sp != cur["sp"] + 8:                # not our function coming back (shouldn't happen)
            self.log.append("unexpected sp %08X" % sp)
            return
        result = r["r0"]
        base = cur["sp"]
        d.w32(base, cur["frame"][0])
        d.w32(base + 4, cur["frame"][1])
        r["r13"] = base
        r["r0"] = cur["r0"]
        self.cur = None
        self.log.append("result %08X" % result)
        if cur["cb"]:
            cur["cb"](result)


# --- v1 (only where the map runs it, e.g. Route 202): a table dispatcher's blx r4
HOOK_CALL = 0x02067DBC
HOOK_RET = 0x02067DBE
DISPATCH_ENTRY = 0x02067DA9
DISPATCH_CODE = bytes.fromhex("a047")        # blx r4 (checked before every use)

# functions (Thumb entry | 1), found from ScrCmd_StartWildBattle (gFieldScriptCommands[292] @ 020EAC58)
FieldBattleDTO_New = 0x02051D8D           # (heapID, battleType) -> dto
FieldBattleDTO_Init = 0x02052315          # (dto, fieldSystem)
CreateWildMon_Scripted = 0x022420D5       # (fieldSystem, species, level, dto)   overlay 6 (field encounters)
Encounter_NewVsWild = 0x02050E11          # (fieldSystem, dto): creates FieldTask_WildEncounter
FieldSystem_CreateTask = 0x02050905
OV6_CODE = (0x022420D4, bytes.fromhex("f8b588b0051c0292"))   # push {r3-r7,lr}; sub sp,#0x20; ...

HEAP_ID_FIELD2 = 0xB
BATTLE_TYPE_WILD_MON = 0
DTO_PARTIES = 0x04
DTO_BATTLE_STATUS = 0x18C
BATTLE_STATUS_LEGENDARY = 0x8
S_FIELD_SYSTEM = 0x021C07DC


class Regs:
    def __init__(self, emu):
        self.lib = emu.lib

    def __getitem__(self, n):
        return self.lib.desmume_memory_read_register(c_char_p(("arm9." + n).encode())) & 0xFFFFFFFF

    def __setitem__(self, n, v):
        self.lib.desmume_memory_write_register(c_char_p(("arm9." + n).encode()), int(v) & 0xFFFFFFFF)


class Caller:
    """Queue game-function calls; each runs inside the field's own frame (see the module docstring)."""

    def __init__(self, d):
        self.d = d
        self.r = Regs(d.emu)
        self.queue = []          # [(fn, args, on_result)]
        self.cur = None          # job being run: dict
        self.log = []
        d.emu.memory.register_exec(HOOK_CALL, self._safe(self._on_call))
        d.emu.memory.register_exec(HOOK_RET, self._safe(self._on_ret))

    def _safe(self, fn):
        def cb(addr, size):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.log.append("hook error %r" % e)
            return 0
        return cb

    def call(self, fn, args, on_result=None):
        self.queue.append((fn, list(args), on_result))

    def busy(self):
        return bool(self.queue) or self.cur is not None

    def _on_call(self):
        r, d = self.r, self.d
        if self.cur is not None or not self.queue:
            return
        if d.read(HOOK_CALL, 2) != DISPATCH_CODE:
            return
        fn, args, cb = self.queue.pop(0)
        # the dispatcher's ENTRY arguments (it has shuffled them by now: entry r1 = table index = r5 >> 2,
        # entry r2/r3 = r1/r2 here; r0 unchanged; stack arguments untouched in the caller's frame)
        self.cur = {"fn": fn, "args": args, "cb": cb, "stage": "ours",
                    "orig": [r["r0"], r["r5"] >> 2, r["r1"], r["r2"]], "sp": r["r13"]}
        for i in range(4):
            r["r%d" % i] = args[i] if i < len(args) else 0
        r["r4"] = fn
        self.log.append("call %08X %s" % (fn, ["%X" % a for a in args]))

    def _on_ret(self):
        cur, r, d = self.cur, self.r, self.d
        if cur is None:
            return
        sp = r["r13"]
        if sp != cur["sp"]:
            return                                   # a nested dispatcher call, not ours
        if cur["stage"] == "ours":
            cur["result"] = r["r0"]
            cur["ret"] = d.r32(sp + 12)              # the caller's return address
            o = cur["orig"]
            for i in range(4):
                r["r%d" % i] = o[i]
            d.w32(sp + 0, o[3])                      # the pop reloads r3 from the frame (entry r3)
            d.w32(sp + 12, DISPATCH_ENTRY)           # "return" into the dispatcher: it redoes the real call
            cur["stage"] = "real"
        elif cur["stage"] == "real":
            d.w32(sp + 12, cur["ret"])               # the real call is done: back to the real caller
            self.cur = None
            self.log.append("result %08X" % cur["result"])
            if cur["cb"]:
                cur["cb"](cur["result"])
