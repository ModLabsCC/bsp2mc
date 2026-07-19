#!/usr/bin/env python3
import math
import os
import subprocess
import sys
from pathlib import Path
from collections import defaultdict, deque

HERE = Path(__file__).resolve().parent


def _relaunch_with_venv():
    if os.environ.get("BSP2MC_RELAUNCHED"):
        sys.exit(
            "srctools is still not available after relaunching with the venv "
            "Python. Install it there with:\n"
            "    <venv>/bin/pip install srctools"
        )
    candidates = [
        HERE / ".venv",
        HERE / "venv",
        HERE.parent / "srctools-venv",
        Path.home() / "Desktop" / "srctools-venv",
    ]
    for venv in candidates:
        python = venv / "bin" / "python3"
        if python.exists():
            env = dict(os.environ, BSP2MC_RELAUNCHED="1")
            result = subprocess.run(
                [str(python), str(Path(__file__).resolve())] + sys.argv[1:],
                env=env,
            )
            sys.exit(result.returncode)
    sys.exit(
        "srctools is not installed in this Python environment, and no venv "
        "containing it was found (looked for BSP2MC/.venv, "
        "~/Desktop/srctools-venv). Create one with:\n"
        "    python3 -m venv ~/Desktop/srctools-venv\n"
        "    ~/Desktop/srctools-venv/bin/pip install srctools"
    )


try:
    from srctools.bsp import BSP
    from srctools import Vec, Matrix, Angle
except ImportError:
    _relaunch_with_venv()
    from srctools.bsp import BSP
    from srctools import Vec, Matrix, Angle

SCALE = 64.0
NS = "portalmod"
ANCHOR_TAG = "bsp2mc_anchor"
ANCHOR_SELECTOR = f"@e[tag={ANCHOR_TAG},limit=1]"


def src_to_mc(vec):
    return (vec.x / SCALE, vec.z / SCALE, -vec.y / SCALE)


GRID_EPSILON = 1e-4


def src_to_mc_cell(vec):
    x, y, z = src_to_mc(vec)
    return (
        math.floor(x + GRID_EPSILON),
        math.floor(y + GRID_EPSILON),
        math.floor(z + GRID_EPSILON),
    )


def origin_of(ent):
    return Vec.from_str(ent["origin"])


DIRS = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "east": (1, 0, 0),
    "west": (-1, 0, 0),
    "up": (0, 1, 0),
    "down": (0, -1, 0),
}


def yaw_to_facing(yaw):
    rad = math.radians(yaw)
    dx = math.cos(rad)
    dz = -math.sin(rad)  # source +Y -> minecraft -Z (see src_to_mc)
    if abs(dx) >= abs(dz):
        return "east" if dx > 0 else "west"
    return "south" if dz > 0 else "north"


def perp_of(facing):
    return {
        "north": "west", "south": "east",
        "east": "north", "west": "south",
    }[facing]


def add(pos, d, n=1):
    dv = DIRS[d]
    return (pos[0] + dv[0] * n, pos[1] + dv[1] * n, pos[2] + dv[2] * n)


OPPOSITE_DIR = {
    "north": "south", "south": "north",
    "east": "west", "west": "east",
    "up": "down", "down": "up",
}


def mount_facing(ent):
    angles = ent["angles"] if "angles" in ent else "0 0 0"
    pitch, yaw, roll = (float(a) for a in angles.split())
    matrix = Matrix.from_angle(Angle(pitch, yaw, roll))
    up_src = Vec(0, 0, 1) @ matrix
    mc_vec = (up_src.x, up_src.z, -up_src.y)
    axis = max(range(3), key=lambda i: abs(mc_vec[i]))
    sign = 1 if mc_vec[axis] > 0 else -1
    positive_names = ("east", "up", "south")
    negative_names = ("west", "down", "north")
    return positive_names[axis] if sign > 0 else negative_names[axis]


QUAD_AXIS_DIRS = {"x": ("north", "up"), "z": ("east", "up"), "y": ("east", "north")}
QUAD_POSITIVE_FACINGS = {"east", "south", "up"}


def quad_corners(base, facing):
    axis = "y" if facing in ("up", "down") else ("x" if facing in ("east", "west") else "z")
    right_dir, up_dir = QUAD_AXIS_DIRS[axis]
    if facing not in QUAD_POSITIVE_FACINGS:
        right_dir = OPPOSITE_DIR[right_dir]
    down_dir = OPPOSITE_DIR[up_dir]
    up_right = add(base, right_dir)
    down_left = add(base, down_dir)
    down_right = add(up_right, down_dir)
    return {"up_left": base, "up_right": up_right, "down_left": down_left, "down_right": down_right}



SKIP_SUBSTRINGS = (
    "nodraw", "skip", "trigger", "hint", "invisible", "clip",
    "areaportal", "toolsblockbullets", "toolsblocklight", "sky",
    "toolsplayerclip", "toolsnpcclip", "toolsinvisible", "toolsorigin",
    "squarebeam",
    "backpanel",
    "maps/preview",
    "dev/",
)

GLASS_SUBSTRINGS = ("glass",)
LUNECAST_SUBSTRINGS = ("white", "panel_glow")
GOO_SUBSTRINGS = ("goo", "toxic_slime", "slime")
BLACKPLATE_SUBSTRINGS = ("black", "metal", "noportal", "concrete")


EXACT_SKIP_MATERIALS = {
    "tools/toolsblack",
}


def classify_material(mat):
    m = mat.lower()
    if any(s in m for s in GOO_SUBSTRINGS):
        return f"{NS}:goo"
    if any(s in m for s in GLASS_SUBSTRINGS):
        return "minecraft:glass"
    if m in EXACT_SKIP_MATERIALS:
        return None
    if any(s in m for s in SKIP_SUBSTRINGS):
        return None
    if any(s in m for s in LUNECAST_SUBSTRINGS):
        return f"{NS}:lunecast"
    if any(s in m for s in BLACKPLATE_SUBSTRINGS):
        return f"{NS}:blackplate"
    return f"{NS}:blackplate"



def voxelize_world(bsp):
    vmf = bsp.ents
    worldspawn = list(vmf.by_class["worldspawn"])[0]
    model = bsp.bmodels[worldspawn]

    voxels = {}
    for face in model.faces:
        if face.texinfo is None:
            continue
        block = classify_material(face.texinfo.mat)
        if block is None:
            continue

        verts = []
        for e in face.edges:
            verts.append(e.a)
        if len(verts) < 3:
            continue

        normal = face.plane.normal
        axis = max(range(3), key=lambda i: abs(normal[i]))
        sign = 1 if normal[axis] > 0 else -1

        const_src = sum(v[axis] for v in verts) / len(verts)
        boundary_cell = round(const_src / SCALE)
        solid_cell = boundary_cell - 1 if sign > 0 else boundary_cell

        def span_range(lo, hi):
            start, stop = round(lo / SCALE), round(hi / SCALE)
            if stop <= start:
                stop = start + 1
            return range(start, stop)

        other_axes = [i for i in range(3) if i != axis]
        mins = [min(v[i] for v in verts) for i in other_axes]
        maxs = [max(v[i] for v in verts) for i in other_axes]
        c0 = span_range(mins[0], maxs[0])
        c1 = span_range(mins[1], maxs[1])

        for u in c0:
            for v in c1:
                cell_src = [0, 0, 0]
                cell_src[axis] = solid_cell
                cell_src[other_axes[0]] = u
                cell_src[other_axes[1]] = v
                mc_cell = (cell_src[0], cell_src[2], -cell_src[1] - 1)
                voxels[mc_cell] = block
    return voxels


def fill_pinholes(voxels, max_size=8):
    candidates = set()
    for (x, y, z) in voxels:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y, z + dz)
            if n not in voxels:
                candidates.add(n)

    visited = set()
    to_fill = {}
    for start in candidates:
        if start in visited:
            continue
        comp = []
        queue = deque([start])
        visited.add(start)
        too_big = False
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            if len(comp) > max_size:
                too_big = True
                break
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (cur[0] + dx, cur[1], cur[2] + dz)
                if n in voxels or n in visited:
                    continue
                visited.add(n)
                queue.append(n)
        if too_big:
            continue

        comp_set = set(comp)
        boundary_blocks = []
        enclosed = True
        for cell in comp:
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (cell[0] + dx, cell[1], cell[2] + dz)
                if n in comp_set:
                    continue
                if n not in voxels:
                    enclosed = False
                    break
                boundary_blocks.append(voxels[n])
            if not enclosed:
                break
        if enclosed and boundary_blocks:
            fill_block = max(set(boundary_blocks), key=boundary_blocks.count)
            for cell in comp:
                to_fill[cell] = fill_block

    voxels.update(to_fill)
    return voxels



class Placement:
    __slots__ = ("pos", "block", "states", "summon", "nbt")

    def __init__(self, pos, block, states=None, summon=False, nbt=None):
        self.pos = pos
        self.block = block
        self.states = states or {}
        self.summon = summon
        self.nbt = nbt

    def command(self):
        if self.summon:
            x, y, z = self.pos
            return f"summon {self.block} ~{x:.2f} ~{y:.2f} ~{z:.2f}"
        state_str = ""
        if self.states:
            parts = ",".join(f"{k}={v}" for k, v in self.states.items())
            state_str = f"[{parts}]"
        nbt_str = self.nbt or ""
        x, y, z = self.pos
        return f"setblock ~{x} ~{y} ~{z} {self.block}{state_str}{nbt_str} replace"


class EntityResult:

    def __init__(self):
        self.placements = []
        self.io_role = None  # 'button' | 'door' | 'fizzler' | None
        self.io_cells = []  # list of (pos, block, base_states)
        self.targetnames = []


def handle_button(ent, super_button):
    pos = src_to_mc_cell(origin_of(ent))
    facing = mount_facing(ent)
    block = f"{NS}:super_button" if super_button else f"{NS}:standing_button"

    result = EntityResult()
    result.io_role = "button"
    result.targetnames = [ent["targetname"]] if "targetname" in ent else []

    if not super_button:
        lower_states = {"facing": facing, "half": "lower"}
        upper_states = {"facing": facing, "half": "upper"}
        result.placements.append(Placement(pos, block, lower_states))
        result.placements.append(Placement(add(pos, "up"), block, upper_states))
        result.io_cells.append((pos, block, lower_states))
        return result

    axis = "y" if facing in ("up", "down") else ("x" if facing in ("east", "west") else "z")
    right_dir, up_dir = QUAD_AXIS_DIRS[axis]
    if facing not in QUAD_POSITIVE_FACINGS:
        right_dir = OPPOSITE_DIR[right_dir]
    down_dir = OPPOSITE_DIR[up_dir]
    origin2d = add(add(pos, right_dir, -1), down_dir, -1)
    for corner, cell in quad_corners(origin2d, facing).items():
        states = {"facing": facing, "corner": corner}
        result.placements.append(Placement(cell, block, states))
        result.io_cells.append((cell, block, states))
    return result


def handle_door(ent):
    pos = src_to_mc_cell(origin_of(ent))
    yaw = float(ent["angles"].split()[1])
    facing = yaw_to_facing(yaw)
    perp = perp_of(facing)
    block = f"{NS}:chamber_door"

    result = EntityResult()
    result.io_role = "door"
    result.targetnames = [ent["targetname"]] if "targetname" in ent else []

    left = add(pos, perp, -1)
    cells = {
        ("lower", "left"): left,
        ("lower", "right"): pos,
        ("upper", "left"): add(left, "up"),
        ("upper", "right"): add(pos, "up"),
    }
    for (half, side), cell in cells.items():
        states = {"facing": facing, "half": half, "side": side, "open": "false"}
        result.placements.append(Placement(cell, block, states))
        result.io_cells.append((cell, block, states))
    return result


def handle_cube(ent):
    cube_type = ent["CubeType"] if "CubeType" in ent else "0"
    entity_id = f"{NS}:companion_cube" if cube_type == "1" else f"{NS}:storage_cube"
    x, y, z = src_to_mc(origin_of(ent))
    result = EntityResult()
    result.placements.append(Placement((x, y, z), entity_id, summon=True))
    return result


def handle_cube_dropper(ent):
    pos = src_to_mc_cell(origin_of(ent))
    block = f"{NS}:cube_dropper"

    result = EntityResult()
    corners = {
        (0, 0): "up_left",
        (1, 0): "up_right",
        (0, 1): "down_left",
        (1, 1): "down_right",
    }
    upper = add(pos, "up")
    origin2d = add(add(pos, "east", -1), "south", -1)
    upper2d = add(add(upper, "east", -1), "south", -1)
    for (du, dv), corner in corners.items():
        for half in ("upper", "lower"):
            cell = add(add(upper2d if half == "upper" else origin2d, "east", du), "south", dv)
            states = {"corner": corner, "half": half}
            result.placements.append(Placement(cell, block, states))
    return result


def handle_indicator_panel(ent):
    pos = src_to_mc_cell(origin_of(ent))
    yaw = float(ent["angles"].split()[1]) if "angles" in ent else 0.0
    facing = yaw_to_facing(yaw)
    block = f"{NS}:antline_indicator"
    states = {"facing": facing, "face": "wall"}
    result = EntityResult()
    result.io_role = "indicator"
    result.placements.append(Placement(pos, block, states))
    result.io_cells.append((pos, block, states))
    return result


def handle_faithplate(ent, bsp):
    pos = src_to_mc_cell(origin_of(ent))
    yaw = float(ent["angles"].split()[1]) if "angles" in ent else 0.0
    facing = yaw_to_facing(yaw)
    block = f"{NS}:faithplate"

    result = EntityResult()
    result.io_role = "faithplate"
    result.targetnames = [ent["targetname"]] if "targetname" in ent else []

    upper_pos = pos
    lower_pos = add(pos, facing)
    lower_states = {"facing": facing, "half": "lower", "face": "floor"}
    result.placements.append(Placement(lower_pos, block, lower_states))
    result.io_cells.append((lower_pos, block, lower_states))

    nbt = None
    target_name = ent["launchTarget"] if "launchTarget" in ent else None
    if target_name:
        target_ent = None
        for e in bsp.ents.entities:
            if ("targetname" in e) and e["targetname"] == target_name:
                target_ent = e
                break
        if target_ent is not None and "origin" in target_ent:
            plate_src = origin_of(ent)
            target_src = origin_of(target_ent)
            dx = (target_src.x - plate_src.x) / SCALE
            dy = (target_src.z - plate_src.z) / SCALE
            dz = -(target_src.y - plate_src.y) / SCALE
            height = max(1.0, (abs(dx) + abs(dy) + abs(dz)) / 4.0)
            nbt = (
                f"{{target:{{x:{dx:.4f}d,y:{dy:.4f}d,z:{dz:.4f}d,"
                f"side:1b,height:{height:.4f}f}},enabled:1b}}"
            )

    result.placements.append(
        Placement(upper_pos, block, {"facing": facing, "half": "upper", "face": "floor"}, nbt=nbt)
    )
    return result


def handle_fizzler_pair(prop_a, prop_b, voxels):
    pos_a = src_to_mc_cell(origin_of(prop_a))
    pos_b = src_to_mc_cell(origin_of(prop_b))

    diffs = [abs(pos_a[i] - pos_b[i]) for i in range(3)]
    axis_idx = max(range(3), key=lambda i: diffs[i])
    axis_name = ("x", "y", "z")[axis_idx]
    if pos_a[axis_idx] > pos_b[axis_idx]:
        pos_a, pos_b, prop_a, prop_b = pos_b, pos_a, prop_b, prop_a

    result = EntityResult()
    result.io_role = "fizzler"
    tn = prop_a["targetname"] if "targetname" in prop_a else None
    result.targetnames = [tn] if tn else []

    positive_facing = {"x": "east", "y": "up", "z": "south"}[axis_name]
    negative_facing = {"x": "west", "y": "down", "z": "north"}[axis_name]

    def emitter_halves(pos):
        if axis_name == "y":
            return pos, add(pos, "south")
        return add(pos, "up", -1), pos

    step_dir = {"x": "east", "y": "up", "z": "south"}[axis_name]
    for _ in range(6):
        if not any(c in voxels for c in emitter_halves(pos_a)):
            break
        if pos_a[axis_idx] + 1 >= pos_b[axis_idx]:
            break
        pos_a = add(pos_a, step_dir, 1)
    for _ in range(6):
        if not any(c in voxels for c in emitter_halves(pos_b)):
            break
        if pos_b[axis_idx] - 1 <= pos_a[axis_idx]:
            break
        pos_b = add(pos_b, step_dir, -1)

    for pos, facing in ((pos_a, positive_facing), (pos_b, negative_facing)):
        lower_pos, upper_pos = emitter_halves(pos)
        for half, cell in (("lower", lower_pos), ("upper", upper_pos)):
            states = {"facing": facing, "half": half, "active": "false"}
            result.placements.append(Placement(cell, f"{NS}:fizzler_emitter", states))
        result.io_cells.append((lower_pos, f"{NS}:fizzler_emitter", {"facing": facing, "half": "lower", "active": "false"}))

    span = pos_b[axis_idx] - pos_a[axis_idx]
    step_dir = {"x": "east", "y": "up", "z": "south"}[axis_name]
    for step in range(1, span):
        base = add(pos_a, step_dir, step)
        lower_cell, upper_cell = emitter_halves(base)
        for half, cell in (("lower", lower_cell), ("upper", upper_cell)):
            states = {"axis": axis_name, "half": half}
            result.placements.append(Placement(cell, f"{NS}:fizzler_field", states))
    return result


def collect_fizzlers(vmf, voxels):
    groups = defaultdict(list)
    for e in vmf.by_class["prop_dynamic"]:
        model = e["model"] if "model" in e else ""
        if model.lower().endswith("/fizzler.mdl"):
            tn = e["targetname"] if "targetname" in e else f"_unnamed_{id(e)}"
            groups[tn].append(e)

    def yaw_of(ent):
        return float(ent["angles"].split()[1]) if "angles" in ent else 0.0

    def opposite_yaw(a, b, tol=1.0):
        return abs(((yaw_of(a) - yaw_of(b) - 180) % 360) - 0) < tol or \
               abs(((yaw_of(a) - yaw_of(b) + 180) % 360) - 0) < tol

    results = []
    for tn, props in groups.items():
        remaining = list(props)
        while len(remaining) >= 2:
            best_pair, best_dist = None, None
            for i in range(len(remaining)):
                for j in range(i + 1, len(remaining)):
                    if not opposite_yaw(remaining[i], remaining[j]):
                        continue
                    a, b = origin_of(remaining[i]), origin_of(remaining[j])
                    d = (a - b).mag()
                    if best_dist is None or d < best_dist:
                        best_pair, best_dist = (i, j), d
            if best_pair is None:
                best_dist = None
                for i in range(len(remaining)):
                    for j in range(i + 1, len(remaining)):
                        a, b = origin_of(remaining[i]), origin_of(remaining[j])
                        d = (a - b).mag()
                        if best_dist is None or d < best_dist:
                            best_pair, best_dist = (i, j), d
            i, j = best_pair
            prop_b = remaining.pop(j)
            prop_a = remaining.pop(i)
            try:
                results.append((prop_a, handle_fizzler_pair(prop_a, prop_b, voxels)))
            except Exception as exc:
                print(f"  ! skipped fizzler pair ({tn}): {exc}")
    return results


ENTITY_HANDLERS = {
    "prop_floor_button": lambda e, bsp: handle_button(e, super_button=True),
    "prop_floor_cube_button": lambda e, bsp: handle_button(e, super_button=True),
    "prop_button": lambda e, bsp: handle_button(e, super_button=False),
    "prop_testchamber_door": lambda e, bsp: handle_door(e),
    "prop_weighted_cube": lambda e, bsp: handle_cube(e),
    "prop_indicator_panel": lambda e, bsp: handle_indicator_panel(e),
    "trigger_catapult": lambda e, bsp: handle_faithplate(e, bsp),
}


def collect_entities(bsp, voxels):
    vmf = bsp.ents
    results = []  # list of (entity, EntityResult)
    for classname, handler in ENTITY_HANDLERS.items():
        for ent in vmf.by_class[classname]:
            try:
                res = handler(ent, bsp)
            except Exception as exc:
                print(f"  ! skipped {classname} ({ent.get('hammerid','?')}): {exc}")
                continue
            results.append((ent, res))

    for ent in vmf.by_class["prop_dynamic"]:
        model = ent["model"] if "model" in ent else ""
        if "item_dropper" in model:
            try:
                res = handle_cube_dropper(ent)
                results.append((ent, res))
            except Exception as exc:
                print(f"  ! skipped cube dropper ({ent.get('hammerid','?')}): {exc}")

    results.extend(collect_fizzlers(vmf, voxels))
    return results



RELEVANT_INPUTS = {"open", "close", "enable", "disable"}



def _next_output_names(ent, input_name, opens):
    cls = ent["classname"]
    inp = input_name.lower()
    if cls == "logic_relay":
        return {"ontrigger"}
    if cls == "func_instance_io_proxy":
        return {inp}
    if cls == "math_counter":
        if inp == "add":
            return {"onhitmax"}
        if inp == "subtract":
            return {"onchangedfrommax"}
        return set()
    if cls == "logic_branch":
        return {"ontrue"} if opens else {"onfalse"}
    return set()


def resolve_io(bsp, entity_results):
    vmf = bsp.ents

    by_targetname = defaultdict(list)
    for ent in vmf.entities:
        if "targetname" in ent:
            by_targetname[ent["targetname"]].append(ent)

    role_by_targetname = {}
    cells_by_targetname = {}
    for ent, res in entity_results:
        if res.io_role in ("door", "fizzler", "faithplate"):
            for tn in res.targetnames:
                role_by_targetname[tn] = res.io_role
                cells_by_targetname[tn] = res.io_cells

    edges = []  # (button_res, opens: bool, cell_group, role)
    button_results = [(e, r) for e, r in entity_results if r.io_role == "button"]

    for button_ent, button_res in button_results:
        for start_output, opens in (("onpressed", True), ("onunpressed", False)):
            seen = set()
            queue = deque()
            for out in button_ent.outputs:
                if out.output.lower() == start_output:
                    queue.append((out.target, out.input))
            while queue:
                target_name, input_name = queue.popleft()
                key = (target_name, input_name.lower())
                if key in seen:
                    continue
                seen.add(key)
                if target_name in role_by_targetname and input_name.lower() in RELEVANT_INPUTS:
                    edges.append((button_res, opens, cells_by_targetname[target_name],
                                   role_by_targetname[target_name]))
                    continue
                for target_ent in by_targetname.get(target_name, []):
                    wanted_outputs = _next_output_names(target_ent, input_name, opens)
                    if not wanted_outputs:
                        continue
                    for out in target_ent.outputs:
                        if out.output.lower() in wanted_outputs:
                            queue.append((out.target, out.input))
    return edges


def antline_path(a, b):
    ax, ay, az = a
    bx, by, bz = b
    cells = []
    x, z = ax, az
    while x != bx:
        x += 1 if bx > x else -1
        cells.append((x, ay, z))
    while z != bz:
        z += 1 if bz > z else -1
        cells.append((x, ay, z))
    if cells and cells[-1] == (bx, ay, bz):
        cells.pop()
    return cells


def _closest_pair(cells_a, cells_b):
    best, best_dist = None, None
    for a in cells_a:
        for b in cells_b:
            d = abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
            if best_dist is None or d < best_dist:
                best, best_dist = (a, b), d
    return best


def generate_antlines(entity_results, io_edges):
    placements = []
    seen_pairs = set()

    for button_res, opens, cell_group, role in io_edges:
        if not opens:
            continue  # one trace per button->target pair is enough
        a, b = _closest_pair(
            [c[0] for c in button_res.io_cells], [c[0] for c in cell_group]
        )
        key = (a, b)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        for cell in antline_path(a, b):
            placements.append(Placement(cell, f"{NS}:antline"))

    button_results = [r for _, r in entity_results if r.io_role == "button"]
    indicator_results = [r for _, r in entity_results if r.io_role == "indicator"]
    for button_res in button_results:
        for indicator_res in indicator_results:
            a, b = _closest_pair(
                [c[0] for c in button_res.io_cells], [c[0] for c in indicator_res.io_cells]
            )
            key = (a, b)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            for cell in antline_path(a, b):
                placements.append(Placement(cell, f"{NS}:antline"))
    return placements



def write_datapack(out_dir, voxels, entity_results, io_edges):
    fn_dir = out_dir / "data" / "bsp2mc" / "functions"
    fn_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "pack.mcmeta").write_text(
        '{"pack": {"pack_format": 7, "description": "BSP2MC generated chamber"}}\n'
    )

    build_chunks = []
    chunk = []
    for pos, block in voxels.items():
        x, y, z = pos
        chunk.append(f"setblock ~{x} ~{y} ~{z} {block} replace")
        if len(chunk) >= 20000:
            build_chunks.append(chunk)
            chunk = []
    if chunk:
        build_chunks.append(chunk)

    build_calls = []
    for i, lines in enumerate(build_chunks):
        name = f"build_geometry_{i}"
        (fn_dir / f"{name}.mcfunction").write_text("\n".join(lines) + "\n")
        build_calls.append(f"execute at {ANCHOR_SELECTOR} run function bsp2mc:{name}")

    entity_lines = []
    summon_lines = []
    for ent, res in entity_results:
        for p in res.placements:
            (summon_lines if p.summon else entity_lines).append(p.command())
    for p in generate_antlines(entity_results, io_edges):
        entity_lines.append(p.command())
    (fn_dir / "build_entities.mcfunction").write_text("\n".join(entity_lines) + "\n")
    build_calls.append(f"execute at {ANCHOR_SELECTOR} run function bsp2mc:build_entities")

    (fn_dir / "build.mcfunction").write_text(
        "\n".join([
            f"kill @e[tag={ANCHOR_TAG}]",
            f"summon minecraft:armor_stand ~ ~ ~ "
            f'{{Tags:["{ANCHOR_TAG}"],Marker:1b,Invisible:1b,NoGravity:1b,Invulnerable:1b}}',
            "say [BSP2MC] Building chamber...",
        ] + build_calls + [
            "say [BSP2MC] Build complete. Run /function bsp2mc:build_summons once to spawn cubes.",
        ]) + "\n"
    )

    summon_lines = summon_lines or ["# no cubes/entities to summon in this chamber"]
    (fn_dir / "build_summons_body.mcfunction").write_text("\n".join(summon_lines) + "\n")
    (fn_dir / "build_summons.mcfunction").write_text(
        f"execute at {ANCHOR_SELECTOR} run function bsp2mc:build_summons_body\n"
    )




def find_bsp():
    bsps = list(HERE.glob("*.bsp"))
    if not bsps:
        sys.exit(f"No .bsp file found in {HERE}. Put one there and re-run.")
    if len(bsps) > 1:
        sys.exit(f"Multiple .bsp files found in {HERE}: {[b.name for b in bsps]}. Keep only one.")
    return bsps[0]


def main():
    bsp_path = find_bsp()
    print(f"Loading {bsp_path.name} ...")
    bsp = BSP(str(bsp_path))
    bsp.read()

    print("Voxelizing world geometry ...")
    voxels = voxelize_world(bsp)
    voxels = fill_pinholes(voxels)
    print(f"  {len(voxels)} blocks")

    print("Classifying gameplay entities ...")
    entity_results = collect_entities(bsp, voxels)
    print(f"  {len(entity_results)} entities placed")

    print("Resolving button/door/fizzler I/O ...")
    io_edges = resolve_io(bsp, entity_results)
    print(f"  {len(io_edges)} I/O edges resolved")

    out_dir = HERE / f"{bsp_path.stem}_datapack"
    print(f"Writing datapack to {out_dir} ...")
    write_datapack(out_dir, voxels, entity_results, io_edges)

    print("Done. Copy the datapack folder into your world's `datapacks/` folder, run")
    print("`/reload`, then stand where you want the chamber and run:")
    print("    /function bsp2mc:build")
    print("followed once by:")
    print("    /function bsp2mc:build_summons")


if __name__ == "__main__":
    main()
