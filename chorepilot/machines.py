"""What each appliance can do: device types, and exact profiles for specific models.

A *machine* is a device type (dishwasher / washer / dryer) plus, optionally, an exact model.
It answers the questions the bridge and the backends need:

  - which programs exist on this model, what they are called on the machine, which Home
    Connect key starts them, and how long they run (for the simulator);
  - which options exist (temperature, spin, dryness, finish-in, booleans), their allowed
    values, and the Home Connect key/enum each one maps to;
  - how remote start works on this model and what disarms it.

Sources (read 2026-10-21; full list in docs/SETUP-bosch-home-connect.md):
  [SG-DW]  https://www.bosch-home.com.sg/en/product/SMV6ZCX42E          (spec sheet)
  [MAN-DW] https://media3.bosch-home.com/Documents/9001910568_B.pdf       (SMV6ZCX42E manual)
  [QRG-DW] https://media3.bosch-home.com/Documents/9001618009_B.pdf       (quick ref: durations)
  [SG-WM]  https://www.bosch-home.com.sg/en/product/WAT286H9SG           (spec sheet)
  [MAN-WM] https://media3.bosch-home.com/Documents/9001259425_D.pdf       (WAT... manual)
  [SG-TD]  https://www.bosch-home.com.sg/en/product/WTW876H8SG           (spec sheet)
  [MAN-TD] https://media3.bosch-home.com/Documents/9001393265_A.pdf       (WTW876H8SG manual)
  [HC-PO]  https://api-docs.home-connect.com/programs-and-options/
  [HC-ST]  https://api-docs.home-connect.com/states/
  [AIOHC]  aiohomeconnect ProgramKey enum (the library Home Assistant uses): program keys
           seen on real devices that the public docs don't list (WoolFinish, Super30, ...)

What "verified" means here: the program/option is on the machine per Bosch's own spec sheet or
manual. The *Home Connect key* for it is a separate claim: where the key's API description
matches the manual's description one-to-one it has no note; otherwise `hc_note` says why it is a
best guess. `chorepilot control programs --appliance <name>` asks the real appliance
(GET /programs/available) and prints which mappings it confirmed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HC_TEMP = "LaundryCare.Washer.EnumType.Temperature."
HC_SPIN = "LaundryCare.Washer.EnumType.SpinSpeed."
HC_DRY = "LaundryCare.Dryer.EnumType.DryingTarget."
HC_VARIO = "LaundryCare.Common.EnumType.VarioPerfect."


@dataclass
class Program:
    name: str                    # canonical name the model sees, e.g. "cotton"
    label: str                   # what the machine / manual calls it
    hc: str | None               # Home Connect program key (None: can't be started remotely)
    minutes: int                 # simulator run time (from the manual where we have it)
    hc_note: str | None = None   # why the key is a best guess, if it is
    note: str | None = None      # anything else the user should know
    max_temp: str | None = None  # washer: highest temperature this program allows (if known)
    estimate: bool = False       # minutes is an estimate, not from the manual


@dataclass
class Option:
    name: str
    kind: str                    # "bool" | "enum" | "minutes"
    desc: str
    hc: str | None = None        # Home Connect option key
    values: tuple = ()           # enum: allowed canonical values
    hc_values: dict = field(default_factory=dict)   # enum: canonical -> HC enum key
    aliases: dict = field(default_factory=dict)     # enum: what people say -> canonical
    lo: int = 0                  # minutes: range
    hi: int = 1440
    step: int = 1
    programs: tuple = ()         # only valid with these programs (empty = all)
    hc_note: str | None = None


@dataclass
class Machine:
    kind: str                    # dishwasher | washer | dryer
    model: str | None            # e.g. "SMV6ZCX42E"; None = generic device of this kind
    title: str
    programs: dict
    options: dict
    default_program: str
    delay_option: str            # which option delays: delay_minutes (start) or finish_in_minutes
    remote_start: str            # how to arm it, in one or two sentences
    remote_start_disarms: tuple  # what turns it off again
    child_lock: bool | None      # the model has a child lock (None = unknown/none)
    hc_type: str                 # Home Connect appliance "type"
    sources: tuple = ()
    done_message: str = "The program is done."
    uses: str = "water and heat"

    # ---- helpers
    def program(self, name):
        return self.programs.get(name)

    def option_names(self):
        return list(self.options)

    def describe(self):
        return f"Bosch {self.model}" if self.model else f"generic {self.kind}"

    def run_minutes(self, program, options=None):
        """Simulator run time: the manual's figure, adjusted for options that change it."""
        p = self.programs[program]
        mins = p.minutes
        o = options or {}
        if self.kind == "dryer" and "dryness" in o:
            mins = int(mins * {"iron": 0.77, "cupboard": 1.0, "cupboard_plus": 1.13}
                       .get(o["dryness"], 1.0))
        if o.get("duration_minutes"):
            mins = int(o["duration_minutes"])
        if o.get("speed_perfect") or o.get("speed_boost"):
            mins = max(15, int(mins * (0.5 if self.kind == "dishwasher" else 0.6)))
        if o.get("eco_perfect"):
            mins = int(mins * 1.3)
        if o.get("intensive_zone"):
            mins += 10
        return mins

    # ---- validation (strict: anything unknown is rejected with the list of valid values)
    PROGRAM_ALIASES = {
        "dishwasher": {"normal": "auto", "standard": "auto", "daily": "auto", "economy": "eco",
                       "eco_50": "eco", "eco50": "eco", "auto_45_65": "auto", "intensive_70": "intensive",
                       "heavy": "intensive", "pots": "intensive", "night": "silence", "quiet": "silence",
                       "silent": "silence", "express_60": "short", "express60": "short",
                       "short_60": "short", "express_45": "quick", "express": "quick",
                       "quick_wash": "quick", "quick_45": "quick", "glass_40": "glass",
                       "glasses": "glass", "machine_clean": "machine_care", "speed": "quick"},
        "washer": {"cottons": "cotton", "whites": "cotton", "coloureds": "cotton", "colors": "cotton",
                   "colours": "cotton", "normal": "cotton", "standard": "cotton",
                   "synthetics": "easy_care", "synthetic": "easy_care", "easycare": "easy_care",
                   "mixed": "mix", "daily": "mix", "silk": "delicates", "delicate": "delicates",
                   "wool_hand_wash": "wool", "woollens": "wool", "hand_wash": "wool",
                   "hygiene": "allergy_plus", "allergy": "allergy_plus", "allergyplus": "allergy_plus",
                   "quick": "super_quick", "super_15": "super_quick", "super_30": "super_quick",
                   "super15": "super_quick", "super30": "super_quick", "express": "super_quick",
                   "spin": "spin_drain", "drain": "spin_drain", "drum_clean_90": "drum_clean"},
        "dryer": {"cottons": "cotton", "normal": "cotton", "standard": "cotton",
                  "synthetics": "synthetic", "easy_care": "synthetic", "mixed": "mix",
                  "daily_wash": "mix", "daily": "mix", "delicate": "delicates", "wool": "wool_finish",
                  "time_program_cold": "time_cold", "cold": "time_cold", "air": "time_cold",
                  "time_program_warm": "time_warm", "warm": "time_warm", "shirts_blouses": "shirts",
                  "blouses": "shirts", "down_wear": "down", "outdoor": "sportswear",
                  "sports": "sportswear", "towel": "towels", "quick": "super40",
                  "super_quick": "super40", "super_quick_40": "super40", "express": "super40",
                  "hygiene": "allergy_plus", "allergy": "allergy_plus", "allergyplus": "allergy_plus"},
    }

    def resolve_program(self, name):
        """Canonical program name, plus anything parsed out of it ("cotton_40" -> 40 C)."""
        n = norm(name)
        if n in self.programs:
            return n, {}
        extra = {}
        m = re.match(r"^([a-z_]+?)_?(\d{2})(?:_?c|_?degrees?)?$", n)
        if m and "temperature" in self.options:
            n, extra = m.group(1).rstrip("_"), {"temperature": m.group(2)}
        n = self.PROGRAM_ALIASES.get(self.kind, {}).get(n, n)
        if n in self.programs:
            return n, extra
        raise ValueError(f"the {self.kind} ({self.describe()}) has no program {name!r}; "
                         f"choose from {', '.join(self.programs)}")

    def clean_options(self, raw, program):
        """Validate and normalise option values for `program`. Raises ValueError."""
        out = {}
        for k, v in (raw or {}).items():
            if v is None or v == "":
                continue
            name = norm(k)
            if name not in self.options:
                name = OPTION_ALIASES.get(name, name)
            if name not in self.options and name in ("extra_dry", "extra") and "dryness" in self.options:
                if _boolish(v):
                    out["dryness"] = self._enum(self.options["dryness"], "extra")
                continue
            if name not in self.options:
                raise ValueError(f"the {self.kind} ({self.describe()}) has no option {k!r}; "
                                 f"known: {', '.join(self.options)}")
            o = self.options[name]
            if o.programs and program not in o.programs:
                if o.kind == "bool" and not _boolish(v):
                    continue
                raise ValueError(f"{name} only works with {', '.join(o.programs)}")
            if o.kind == "bool":
                out[name] = _boolish(v)
            elif o.kind == "enum":
                out[name] = self._enum(o, v)
            else:
                out[name] = self._minutes(o, v)
        if out.get("speed_perfect") and out.get("eco_perfect"):
            raise ValueError("speed_perfect and eco_perfect can't both be on")
        p = self.programs[program]
        if p.max_temp and out.get("temperature") not in (None, "cold"):
            if int(out["temperature"]) > int(p.max_temp):
                raise ValueError(f"{program} goes up to {p.max_temp} C on the {self.model or self.kind}")
        return {k: v for k, v in out.items() if v not in (False, 0) or k in ("spin",)}

    def _enum(self, o, v):
        s = norm(v).replace("°", "").replace("rpm", "").strip("_")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            s = str(int(v))
        s = o.aliases.get(s, s)
        if s.endswith("c") and s[:-1].isdigit():
            s = s[:-1]
        if s in o.values:
            return s
        raise ValueError(f"{o.name} must be one of {', '.join(o.values)} on the "
                         f"{self.model or self.kind} (got {v!r})")

    def _minutes(self, o, v):
        if isinstance(v, str):
            m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?|m|min|mins|minutes?)?\s*$", v.lower())
            if not m:
                raise ValueError(f"{o.name} must be a number of minutes (got {v!r})")
            v = float(m.group(1)) * (60 if (m.group(2) or "m").startswith("h") else 1)
        v = int(round(float(v)))
        if v and not o.lo <= v <= o.hi:
            raise ValueError(f"{o.name} must be {o.lo}..{o.hi} minutes")
        if v and o.step > 1:
            v = int(-(-v // o.step) * o.step)   # round up to the machine's step
        return v

    def compact(self):
        """One short line per appliance for a small model's system prompt."""
        progs = "|".join(self.programs)
        opts = []
        for o in self.options.values():
            if o.kind == "enum":
                opts.append(f"{o.name}={'|'.join(o.values)}")
            elif o.kind == "minutes":
                opts.append(f"{o.name}=minutes")
            else:
                opts.append(o.name)
        return f"programs {progs}; options {', '.join(opts)}"


def norm(s):
    return re.sub(r"[\s\-/]+", "_", str(s).strip().lower()).strip("_")


def _boolish(v):
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on", "y")
    return bool(v)


# ------------------------------------------------------------------ generic device types
_BOOL = "bool"


def _generic_dishwasher():
    progs = {
        "auto": Program("auto", "Auto", "Dishcare.Dishwasher.Program.Auto2", 150),
        "eco": Program("eco", "Eco 50", "Dishcare.Dishwasher.Program.Eco50", 225),
        "quick": Program("quick", "Quick 45", "Dishcare.Dishwasher.Program.Quick45", 45),
        "intensive": Program("intensive", "Intensive 70", "Dishcare.Dishwasher.Program.Intensiv70", 140),
        "normal": Program("normal", "Normal 65", "Dishcare.Dishwasher.Program.Normal65", 120),
        "glass": Program("glass", "Glass 40", "Dishcare.Dishwasher.Program.Glas40", 110),
        "night": Program("night", "Night wash", "Dishcare.Dishwasher.Program.NightWash", 260),
        "prerinse": Program("prerinse", "Pre-rinse", "Dishcare.Dishwasher.Program.PreRinse", 15),
        "machine_care": Program("machine_care", "Machine care",
                                "Dishcare.Dishwasher.Program.MachineCare", 120),
    }
    opts = {
        "half_load": Option("half_load", _BOOL, "half-load / reduced water",
                            "Dishcare.Dishwasher.Option.HalfLoad"),
        "extra_dry": Option("extra_dry", _BOOL, "extra drying", "Dishcare.Dishwasher.Option.ExtraDry"),
        "speed_boost": Option("speed_boost", _BOOL, "VarioSpeed-style shortened run",
                              "Dishcare.Dishwasher.Option.VarioSpeedPlus"),
        "hygiene": Option("hygiene", _BOOL, "higher final rinse temperature",
                          "Dishcare.Dishwasher.Option.HygienePlus"),
        "delay_minutes": Option("delay_minutes", "minutes", "start this many minutes from now",
                                "BSH.Common.Option.StartInRelative"),
    }
    return Machine("dishwasher", None, "dishwasher", progs, opts, "auto", "delay_minutes",
                   "Press the Remote Start button on the machine.", ("door opened",), None,
                   "Dishwasher", done_message="The dishes are done.")


def _generic_washer():
    temps = ("cold", "20", "30", "40", "60", "90")
    progs = {
        "cotton": Program("cotton", "Cottons", "LaundryCare.Washer.Program.Cotton", 170, estimate=True),
        "easy_care": Program("easy_care", "Easy-care", "LaundryCare.Washer.Program.EasyCare", 120,
                             estimate=True),
        "mix": Program("mix", "Mix", "LaundryCare.Washer.Program.Mix", 90, estimate=True),
        "delicates": Program("delicates", "Delicates/Silk", "LaundryCare.Washer.Program.DelicatesSilk",
                             45, estimate=True),
        "wool": Program("wool", "Wool", "LaundryCare.Washer.Program.Wool", 40, estimate=True),
        "quick": Program("quick", "Super 15", "LaundryCare.Washer.Program.Super153045.Super15", 15),
        "rinse": Program("rinse", "Rinse", "LaundryCare.Washer.Program.Rinse.RinseSpinDrain", 25,
                         estimate=True),
    }
    return Machine("washer", None, "washing machine", progs, _washer_options(temps, (0, 400, 800, 1200, 1400)),
                   "cotton", "finish_in_minutes", "Press the Remote Start button on the machine.",
                   ("door opened", "program ended"), None, "Washer",
                   done_message="The washing is done.", uses="water and heat")


def _generic_dryer():
    progs = {
        "cotton": Program("cotton", "Cottons", "LaundryCare.Dryer.Program.Cotton", 128),
        "synthetic": Program("synthetic", "Synthetics", "LaundryCare.Dryer.Program.Synthetic", 55),
        "mix": Program("mix", "Mix", "LaundryCare.Dryer.Program.Mix", 70, estimate=True),
        "delicates": Program("delicates", "Delicates", "LaundryCare.Dryer.Program.Delicates", 45,
                             estimate=True),
        "towels": Program("towels", "Towels", "LaundryCare.Dryer.Program.Towels", 110, estimate=True),
    }
    return Machine("dryer", None, "tumble dryer", progs,
                   _dryer_options(("iron", "cupboard", "cupboard_plus", "extra")), "cotton",
                   "finish_in_minutes", "Press the Remote Start button on the machine.",
                   ("door opened", "program ended"), None, "Dryer",
                   done_message="The laundry is dry.", uses="heat")


def _washer_options(temps, spins):
    hc_t = {t: HC_TEMP + ("Cold" if t == "cold" else f"GC{t}") for t in temps}
    hc_s = {str(s): HC_SPIN + ("Off" if s == 0 else f"RPM{s}") for s in spins}
    return {
        "temperature": Option(
            "temperature", "enum", "wash temperature in C (cold = no heating)",
            "LaundryCare.Washer.Option.Temperature", tuple(temps), hc_t,
            aliases={"cool": "cold", "0": "cold", "20c": "20", "30c": "30", "40c": "40",
                     "60c": "60", "90c": "90", "hot": "60"}),
        "spin": Option(
            "spin", "enum", "spin speed in rpm (0 = no spin)", "LaundryCare.Washer.Option.SpinSpeed",
            tuple(str(s) for s in spins), hc_s,
            aliases={"off": "0", "none": "0", "no_spin": "0", "max": str(max(spins))}),
        "finish_in_minutes": Option(
            "finish_in_minutes", "minutes", "the program should END this many minutes from now "
            "(0 = start now)", "BSH.Common.Option.FinishInRelative", lo=0, hi=1440, step=30),
        "speed_perfect": Option("speed_perfect", _BOOL, "SpeedPerfect: shorter run, more energy",
                                "LaundryCare.Common.Option.VarioPerfect"),
        "eco_perfect": Option("eco_perfect", _BOOL, "EcoPerfect: saves energy, runs longer",
                              "LaundryCare.Common.Option.VarioPerfect"),
    }


def _dryer_options(targets):
    hc = {"iron": HC_DRY + "IronDry", "cupboard": HC_DRY + "CupboardDry",
          "cupboard_plus": HC_DRY + "CupboardDryPlus", "extra": HC_DRY + "ExtraDry",
          "gentle": HC_DRY + "GentleDry"}
    al = {"iron_dry": "iron", "ironing": "iron", "damp": "iron", "slightly_damp": "iron",
          "cupboard_dry": "cupboard", "normal": "cupboard", "dry": "cupboard", "standard": "cupboard",
          "cupboard_dry_plus": "cupboard_plus", "cupboardplus": "cupboard_plus",
          "very_dry": "cupboard_plus", "extra_dry": "extra", "extra": "extra",
          "bone_dry": "cupboard_plus"}
    if "extra" not in targets:      # e.g. WTW876H8SG: "extra dry" = its driest target
        al.update(extra="cupboard_plus", extra_dry="cupboard_plus", extradry="cupboard_plus")
    return {
        "dryness": Option("dryness", "enum", "how dry: " + ", ".join(targets),
                          "LaundryCare.Dryer.Option.DryingTarget", tuple(targets),
                          {t: hc[t] for t in targets}, aliases=al),
        "finish_in_minutes": Option(
            "finish_in_minutes", "minutes", "the program should END this many minutes from now "
            "(0 = start now)", "BSH.Common.Option.FinishInRelative", lo=0, hi=1440, step=1),
    }


# ------------------------------------------------------------------ the exact models
def _smv6zcx42e():
    dw = "Dishcare.Dishwasher.Program."
    progs = {
        # durations: quick reference guide [QRG-DW]; names: manual [MAN-DW] + SG spec [SG-DW]
        "auto": Program("auto", "Auto 45-65 C", dw + "Auto2", 160,
                        note="sensor-controlled, 1:40-2:40 h depending on soiling"),
        "eco": Program("eco", "Eco 50 C", dw + "Eco50", 235, note="most economical, 3:55 h"),
        "intensive": Program("intensive", "Intensive 70 C", dw + "Intensiv70", 150,
                             note="pots and pans, 2:25-2:30 h"),
        "silence": Program("silence", "Silence 50", dw + "NightWash", 240,
                           hc_note="Silence 50 = the quiet program; the API's quiet program is "
                                   "NightWash (descriptions match, not confirmed on this model)",
                           note="reduced noise, 4:00 h"),
        "glass": Program("glass", "Glass 40 C", dw + "Glas40", 105, note="delicate glasses, 1:40-1:45 h"),
        "short": Program("short", "Express 60 C (SG spec: 'Short 60 C')", dw + "Kurz60", 89,
                         hc_note="Kurz60 is 'Short 60' and its API text ('time-optimised, normally "
                                 "soiled') matches the manual's Express 60; not confirmed on this model",
                         note="time-optimised, 1:29 h"),
        "quick": Program("quick", "Express 45 C (SG spec: 'Quick Wash 45 C')", dw + "Quick45", 35,
                         hc_note="Quick45 = 'particularly quick ... lightly soiled'; the manual's "
                                 "Express 45 matches; not confirmed on this model",
                         note="lightly soiled, 0:35 h"),
        "machine_care": Program("machine_care", "Machine Care 70 C", dw + "MachineCare", 130,
                                note="EMPTY machine only, with machine-care product"),
    }
    opts = {
        "extra_dry": Option("extra_dry", _BOOL, "ExtraDry: hotter final rinse, longer drying",
                            "Dishcare.Dishwasher.Option.ExtraDry"),
        "intensive_zone": Option("intensive_zone", _BOOL,
                                 "IntensiveZone: stronger spray in the bottom basket for pots",
                                 "Dishcare.Dishwasher.Option.IntensivZone"),
        "speed_perfect": Option("speed_perfect", _BOOL, "SpeedPerfect+: 15-75% shorter run",
                                "Dishcare.Dishwasher.Option.VarioSpeedPlus"),
        "delay_minutes": Option("delay_minutes", "minutes", "start this many minutes from now "
                                "(timer programming, up to 24 h)", "BSH.Common.Option.StartInRelative",
                                lo=0, hi=1440),
    }
    return Machine(
        "dishwasher", "SMV6ZCX42E", "Bosch Serie 6 fully-integrated dishwasher, 14 place settings",
        progs, opts, "auto", "delay_minutes",
        remote_start=("Select a program on the machine, then press its Remote Start button "
                      "(basic setting rc:01, the default). With basic setting rc:02 remote start "
                      "stays on permanently; rc:00 disables it."),
        remote_start_disarms=("door opened (with rc:01)",),
        child_lock=None,       # SG spec: "Child safety devices: No"
        hc_type="Dishwasher",
        sources=("https://www.bosch-home.com.sg/en/product/SMV6ZCX42E",
                 "https://media3.bosch-home.com/Documents/9001910568_B.pdf",
                 "https://media3.bosch-home.com/Documents/9001618009_B.pdf"),
        done_message="The dishes are done.")


def _wat286h9sg():
    w = "LaundryCare.Washer.Program."
    temps = ("cold", "20", "30", "40", "60", "90")          # [MAN-WM] p.21 temperature row
    spins = (0, 400, 600, 800, 1200, 1400)                   # [MAN-WM] p.21 "1400*, 1200, 800, 600, 400, 0"
    est = dict(estimate=True)
    progs = {
        # names: [SG-WM] spec ("Cottons cold-90", "Easy-care cold-60", Delicates/silk, Wool,
        # AllergyPlus/Hygiene, Curtains, Mix, Duvet, Super 15/30) + [MAN-WM] (Rinse, Spin/Drain,
        # Drum Clean 90). Durations: Bosch prints them only in the per-model programme
        # supplement, which isn't online, so these are estimates for the simulator.
        "cotton": Program("cotton", "Cottons", w + "Cotton", 170, max_temp="90", **est),
        "easy_care": Program("easy_care", "Easy-care (synthetics)", w + "EasyCare", 120,
                             max_temp="60", **est),
        "mix": Program("mix", "Mix", w + "Mix", 90, **est),
        "delicates": Program("delicates", "Delicates/Silk", w + "DelicatesSilk", 45, **est),
        "wool": Program("wool", "Wool / hand wash", w + "Wool", 40, **est),
        "allergy_plus": Program("allergy_plus", "AllergyPlus / Hygiene", w + "Sensitive", 180,
                                hc_note="the API's 'Sensitive' is 'optimised for allergy sufferers, "
                                        "removes pollen and allergens'; real devices also report "
                                        "HygienePlus. Check with `control programs`", **est),
        "curtains": Program("curtains", "Curtains", w + "Curtains", 80, **est),
        "duvet": Program("duvet", "Duvet", w + "DownDuvet.Duvet", 110, **est),
        "super_quick": Program("super_quick", "Super 15'/30'", w + "Super153045.Super1530", 30,
                               hc_note="Super 15/30 is one dial position; devices report Super15, "
                                       "Super30 or Super1530. Check with `control programs`"),
        "rinse": Program("rinse", "Rinse", w + "Rinse.RinseSpinDrain", 25,
                         hc_note="devices report Rinse or Rinse.RinseSpinDrain", **est),
        "spin_drain": Program("spin_drain", "Spin / Drain", w + "Spin.SpinDrain", 15,
                              hc_note="key from aiohomeconnect (real devices), not the public docs",
                              **est),
        "drum_clean": Program("drum_clean", "Drum Clean 90 C", w + "DrumClean", 120,
                              note="EMPTY drum only", **est),
    }
    opts = _washer_options(temps, spins)
    opts["finish_in_minutes"].desc += "; the machine sets it in 30-minute steps, max 24 h"
    opts["i_dos"] = Option("i_dos", _BOOL, "i-DOS 1: automatic detergent dosing",
                           "LaundryCare.Washer.Option.IDos1Active")
    opts["i_dos_softener"] = Option("i_dos_softener", _BOOL, "i-DOS 2: automatic softener dosing",
                                    "LaundryCare.Washer.Option.IDos2Active")
    return Machine(
        "washer", "WAT286H9SG", "Bosch Serie 6 front-loading washing machine, 9 kg, 1400 rpm, i-DOS",
        progs, opts, "cotton", "finish_in_minutes",
        remote_start=("Close the door AND the detergent drawer, make sure the Wi-Fi symbol is lit, "
                      "then briefly press Remote Start. (Hold it 3 s for Home Connect settings.)"),
        remote_start_disarms=("door opened", "detergent drawer opened", "power failure",
                              "program ended", "Start/Pause pressed on the machine",
                              "Remote Start pressed again", "machine switched off"),
        child_lock=True,       # SG spec "Child safety devices: Yes"; manual: hold the lock key 3 s
        hc_type="Washer",
        sources=("https://www.bosch-home.com.sg/en/product/WAT286H9SG",
                 "https://media3.bosch-home.com/Documents/9001259425_D.pdf"),
        done_message="The washing is done.")


def _wtw876h8sg():
    d = "LaundryCare.Dryer.Program."
    est = dict(estimate=True)
    progs = {
        # names + max loads: [MAN-TD] pp.23-24; durations: [MAN-TD] p.56 (cottons 8 kg at
        # 1400 rpm: cupboard dry 128 min, iron dry 98 min; synthetics 3.5 kg at 800 rpm: 55 min)
        "cotton": Program("cotton", "Cottons (8 kg)", d + "Cotton", 128),
        "synthetic": Program("synthetic", "Synthetics (3.5 kg)", d + "Synthetic", 55),
        "mix": Program("mix", "Daily Wash / Mix (3 kg)", d + "Mix", 70, **est),
        "delicates": Program("delicates", "Delicates (2 kg)", d + "Delicates", 45, **est),
        "wool_finish": Program("wool_finish", "Wool Finish (0.5 kg)", d + "WoolFinish", 20,
                               hc_note="key from aiohomeconnect (real devices), not the public docs",
                               note="fluffs wool up, does not dry it", **est),
        "time_cold": Program("time_cold", "Time Progr. Cold (3 kg)", d + "TimeCold", 20,
                             note="set duration_minutes (20-210)"),
        "time_warm": Program("time_warm", "Time Progr. Warm (3 kg)", d + "TimeWarm", 40,
                             note="set duration_minutes (20-210)"),
        "shirts": Program("shirts", "Shirts/Blouses (1.5 kg)", d + "BusinessShirts", 45, **est),
        "duvet": Program("duvet", "Duvet (2.5 kg, synthetic filling)", d + "Blankets", 100,
                         hc_note="the API's Blankets ('synthetic filling', 2.5 kg) matches the "
                                 "manual's Duvet; not confirmed on this model", **est),
        "down": Program("down", "Down Wear (1.5 kg)", d + "DownFeathers", 100, **est),
        "sportswear": Program("sportswear", "Sportswear (1.5 kg, membrane/outdoor)", d + "Outdoor", 60,
                              hc_note="the API's Outdoor text matches word for word; real devices "
                                      "may report Outdoor.Sportswear", **est),
        "towels": Program("towels", "Towels (6 kg)", d + "Towels", 110, **est),
        "super40": Program("super40", "Super Quick 40' (2 kg)", d + "Super40", 40),
        "allergy_plus": Program("allergy_plus", "AllergyPlus (4 kg, high temperature)", d + "Hygiene",
                                120, hc_note="the API's Hygiene ('higher temperature ... highest "
                                             "hygiene requirements', 4 kg) matches", **est),
    }
    opts = _dryer_options(("iron", "cupboard", "cupboard_plus"))
    opts["dryness"].desc = ("how dry: iron (still damp, for ironing), cupboard (normal), "
                            "cupboard_plus (driest; use it for 'extra dry')")
    opts["dryness"].programs = ("cotton", "synthetic", "mix", "towels", "shirts", "delicates")
    opts["finish_in_minutes"].desc += "; up to 24 h"
    opts["duration_minutes"] = Option(
        "duration_minutes", "minutes", "time_cold / time_warm only: run time, 20-210 min in 10-min steps",
        "BSH.Common.Option.Duration", lo=20, hi=210, step=10, programs=("time_cold", "time_warm"))
    return Machine(
        "dryer", "WTW876H8SG", "Bosch Serie 6 heat-pump tumble dryer, 8 kg, AutoDry, self-cleaning condenser",
        progs, opts, "cotton", "finish_in_minutes",
        remote_start=("Load it, close the door, make sure the Wi-Fi symbol is lit, then briefly press "
                      "Remote Start."),
        remote_start_disarms=("door opened", "power failure", "program ended",
                              "Start/Pause pressed on the machine", "Remote Start pressed again",
                              "machine switched off"),
        child_lock=True,       # manual p.33: childproof lock, hold 3 s
        hc_type="Dryer",
        sources=("https://www.bosch-home.com.sg/en/product/WTW876H8SG",
                 "https://media3.bosch-home.com/Documents/9001393265_A.pdf"),
        done_message="The laundry is dry.", uses="heat")


GENERIC = {"dishwasher": _generic_dishwasher, "washer": _generic_washer, "dryer": _generic_dryer}
MODELS = {"SMV6ZCX42E": _smv6zcx42e, "WAT286H9SG": _wat286h9sg, "WTW876H8SG": _wtw876h8sg}
KIND_ALIASES = {"dishwasher": ("dishes", "dish washer", "dish_washer"),
                "washer": ("washing machine", "washing_machine", "washer"),
                "dryer": ("tumble dryer", "tumble_dryer", "drier", "dryer")}

#: not available over the Home Connect API on these models (operate at the machine):
LOCAL_ONLY = {
    "SMV6ZCX42E": ["Favourite program", "basic settings (rc, d, r, SE, A)"],
    "WAT286H9SG": ["Water&Rinse+", "Easy-Iron", "Prewash", "Rinse Stop", "Memory program",
                   "childproof lock on/off (readable; writable via the API's ChildLock setting "
                   "[unverified on this model])"],
    "WTW876H8SG": ["Anti-Crease 60/120 min", "Low Heat", "dryness fine-tune +1/+2/+3",
                   "quiet mode", "Favourite program"],
}


#: what models (and people) call an option -> our option name (used only when the machine has
#: no option of that exact name)
OPTION_ALIASES = {
    "speed_boost": "speed_perfect", "speedperfect": "speed_perfect", "vario_speed": "speed_perfect",
    "ecoperfect": "eco_perfect", "eco": "eco_perfect",
    "temp": "temperature", "temperature_c": "temperature", "degrees": "temperature",
    "spin_speed": "spin", "rpm": "spin",
    "drying_target": "dryness", "dry_level": "dryness", "dryness_level": "dryness",
    "drying_level": "dryness", "level": "dryness",
    "delay": "delay_minutes", "start_in_minutes": "delay_minutes", "start_in": "delay_minutes",
    "finish_in": "finish_in_minutes", "ready_in": "finish_in_minutes",
    "ready_in_minutes": "finish_in_minutes", "end_in_minutes": "finish_in_minutes",
    "duration": "duration_minutes",
    "intensive": "intensive_zone", "intensivzone": "intensive_zone",
    "extra_drying": "extra_dry", "extradry": "extra_dry",
}


def machine_for(kind=None, model=None):
    """A fresh Machine for an exact model, or a generic one for a device kind."""
    if model:
        key = str(model).upper().strip()
        if key not in MODELS:
            raise KeyError(f"no profile for model {model!r}; known: {', '.join(MODELS)} "
                           f"(or leave model out for a generic {kind or 'dishwasher'})")
        m = MODELS[key]()
        if kind and kind != m.kind:
            raise KeyError(f"{model} is a {m.kind}, not a {kind}")
        return m
    kind = kind or "dishwasher"
    if kind not in GENERIC:
        raise KeyError(f"unknown appliance type {kind!r}; choose from {', '.join(GENERIC)}")
    return GENERIC[kind]()


def kind_from_name(name):
    n = norm(name)
    for k, al in KIND_ALIASES.items():
        if n == k or n in (norm(a) for a in al):
            return k
    return None
