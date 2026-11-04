"""mitosis_loader.py — the "Node Mitosis" animation from test.html, as a widget.

A faithful port, not a look-alike: the same seeded growth, force-directed
layout, reparenting and short-lived cross links, the same add-a-wave /
prune-and-regrow turnover once the tree is full, the unwind back to the root
and the fresh seed that grows again.  The only things dropped are the page
chrome (title, counters, buttons) and the logo bitmap.

Differences the widget size forces, nothing else:
  * node/edge sizes are derived from the widget, so world zoom (which still
    fits the tree into the box) never shrinks the dots to nothing;
  * every node is born red, orange or yellow (the agent pointer's ramp) and
    keeps that colour, edges take the colour of the node they feed;
  * the root is just the central node, drawn a little bigger — no logo;
  * `finish()` recolours everything cyan and plays the growth backwards
    (leaves peel off) until only the central node is left, then emits
    `finished` so the host can drop the widget;
  * size=None makes it fill whatever area it is given (the host paints it
    behind the chat), an int keeps it a fixed square;
  * the simulation runs on fixed 1/60 s steps - exactly the rate test.html
    was tuned at - a bit faster, and repainting at ~30 fps.

No web view, no file to load: a QTimer plus one paint event.
"""

import math
import random

from PyQt5.QtCore import QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

STEP = 1.0 / 60.0       # fixed simulation step (the rate test.html runs at)
TICK_MS = 33            # repaint interval
STEPS_PER_TICK = 3      # 1.5x the page's speed: a loader must read at once
MAXZ = 1.4              # camera zoom cap (same as the page)
K = 46.0                # base spacing between nodes, world units
RR = 44.0               # root keep-out radius
# The agent pointer's warm ramp: red, orange, yellow.
PALETTE = (QColor('#ef4444'), QColor('#f97316'), QColor('#fbbf24'))
# Same ramp in cyan, for the reverse run that ends the turn.
PALETTE_CYAN = (QColor('#22d3ee'), QColor('#06b6d4'), QColor('#67e8f9'))
TIER = (2.6, 1.8, 1.3, 0.9)   # settled edge width per depth tier
ROOT_R = 6.5            # the central node: fixed size, it never scales


def _mulberry32(seed):
    """The page's seeded RNG: one seed is one tree."""
    a = seed & 0xFFFFFFFF

    def _next():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        x = a
        x = ((x ^ (x >> 15)) * (1 | x)) & 0xFFFFFFFF
        x = (x + (((x ^ (x >> 7)) * (61 | x)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        x ^= (x >> 14) & 0xFFFFFFFF
        return (x & 0xFFFFFFFF) / 4294967296.0

    return _next


def _tier_of(depth):
    return 0 if depth <= 1 else 1 if depth <= 3 else 2 if depth <= 6 else 3


class MitosisLoader(QWidget):
    """Growing / rewiring / unwinding node tree — the runtime is working."""

    finished = pyqtSignal()      # the reverse run reached the central node

    def __init__(self, parent=None, size=None, max_nodes=None):
        super().__init__(parent)
        self._max_nodes = max_nodes
        self._fixed = int(size) if size else 0
        self._box = self._fixed or 240     # cap unit until a real size exists
        self._rng = random.Random()
        self._mul = _mulberry32(0)
        self._cap = 24
        self._arm_cap = 6
        self._finishing = False
        self._unwind_dt = 0.08
        self._dot_t = 0.0

        if self._fixed:
            self.setFixedSize(self._fixed, self._fixed)
        else:
            self.setMinimumSize(160, 140)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._nodes = []
        self._edges = []
        self._adj = []
        self._keys = {}
        self._free = []
        self._act = []
        self._alive = 0
        self._live = 0
        self._cross_live = 0
        self._rewired = 0
        self._t = 0.0
        self._state = 'grow'
        self._next = 0.0
        self._span = 1.0
        self._hold_until = 0.0
        self._rep_acc = 0.0
        self._cross_acc = 0.0
        self._depth_t = 0.0
        self._vis = 0.0
        self._cam = [0.0, 0.0, MAXZ, MAXZ]
        self._reset_tree()

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ── public ───────────────────────────────────────────────────────

    def start(self):
        if not self._timer.isActive():
            self._finishing = False
            self._reset_tree()
            self._timer.start()

    def finish(self):
        """Turn ended: recolour cyan and collapse the tree back to the centre."""
        if self._finishing:
            return
        self._finishing = True
        self._state = 'shrink'
        # One node leaves at a time, newest first, spread over ~1.6 s.
        self._unwind_dt = max(0.03, min(0.12, 1.6 / max(1, self._alive)))
        self._next = self._t + self._unwind_dt
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._timer.stop()
        self.update()

    # ── seeded growth ────────────────────────────────────────────────

    def _r(self, a, b):
        return a + self._mul() * (b - a)

    def _new_seed(self):
        self._mul = _mulberry32(int(self._rng.random() * 4294967296))

    def _key(self, a, b):
        return a * 65536 + b if a < b else b * 65536 + a

    def _rebuild_act(self):
        self._act = [i for i, n in enumerate(self._nodes) if n['state'] == 1]

    def _add_node(self, x, y, parent, depth):
        nd = {'x': x, 'y': y, 'vx': 0.0, 'vy': 0.0, 'fx': 0.0, 'fy': 0.0,
              'born': self._t, 'state': 1, 'parent': parent, 'depth': depth,
              'kids': 0, 'die': 0.0,
              # the root is orange; the rest are born red / orange / yellow
              'c': 1 if parent < 0 else int(self._mul() * len(PALETTE))}
        if self._free:
            idx = self._free.pop()
            self._nodes[idx] = nd
            self._adj[idx] = set()
        else:
            idx = len(self._nodes)
            self._nodes.append(nd)
            self._adj.append(set())
        self._alive += 1
        return idx

    def _add_edge(self, a, b, kind):
        if a == b:
            return False
        k = self._key(a, b)
        if k in self._keys:
            return False
        e = {
            'a': a, 'b': b, 'kind': kind, 'born': self._t, 'al': 0.0,
            'dying': False,
            'rest': (K * (0.7 + self._mul() ** 1.6 * 1.9)) if kind == 0
                    else (K * (1.2 + self._mul() * 1.8)),
            'k': 1.2 if kind == 0 else 0.3,
            'expire': 0.0 if kind == 0 else self._t + self._r(1.2, 3.5),
        }
        self._edges.append(e)
        self._keys[k] = e
        self._adj[a].add(b)
        self._adj[b].add(a)
        self._live += 1
        if kind == 1:
            self._cross_live += 1
        return True

    def _remove_edge(self, e):
        e['dying'] = True
        self._keys.pop(self._key(e['a'], e['b']), None)
        self._adj[e['a']].discard(e['b'])
        self._adj[e['b']].discard(e['a'])
        self._live -= 1
        if e['kind'] == 1:
            self._cross_live -= 1

    def _schedule(self):
        if self._state == 'hold':
            self._span = self._r(0.6, 1.6)
        elif self._state == 'shrink':
            self._span = self._r(0.35, 1.2)
        else:
            self._span = self._r(0.8, 2.4)
        self._next = self._t + self._span

    def _reset_tree(self, keep_cam=False):
        self._new_seed()
        # A bigger box gets a bigger tree; the O(n^2) repulsion is capped at 60
        # so a full chat panel still runs a few thousand pairs per step.
        unit = min(self.width() or self._box, self.height() or self._box)
        self._cap = max(6, int(self._max_nodes or min(60, 18 + unit // 4)))
        self._nodes = []
        self._edges = []
        self._adj = []
        self._free = []
        self._keys = {}
        self._alive = 0
        self._live = 0
        self._cross_live = 0
        self._rewired = 0
        self._rep_acc = 0.0
        self._cross_acc = 0.0
        self._depth_t = 0.0
        root = self._add_node(0.0, 0.0, -1, 0)
        self._nodes[root]['born'] = self._t - 10.0
        self._state = 'grow'
        if not keep_cam:
            self._cam[0] = 0.0
            self._cam[1] = 0.0
            self._cam[2] = MAXZ
            self._cam[3] = MAXZ
        self._span = self._r(0.6, 1.1)
        self._next = self._t + self._span
        self._rebuild_act()

    @staticmethod
    def _ang_diff(a, b):
        d = abs(a - b) % 6.2832
        return 6.2832 - d if d > 3.1416 else d

    def _pick_root_angle(self):
        angs = []
        for nb in self._adj[0]:
            q = self._nodes[nb]
            if q['parent'] == 0:
                angs.append(math.atan2(q['y'], q['x']))
        best, bs = self._mul() * 6.2832, -1.0
        for _ in range(6):
            a = self._mul() * 6.2832
            md = 7.0
            for other in angs:
                d = self._ang_diff(a, other)
                if d < md:
                    md = d
            if md > bs:
                bs, best = md, a
        return best

    def _sprout(self, pid):
        p = self._nodes[pid]
        if pid == 0:
            # the first node of an arm: as far as possible from the other arms
            ang = self._pick_root_angle()
            dist = RR + 6 + self._r(4, 22)
            x = math.cos(ang) * dist
            y = math.sin(ang) * dist
        else:
            # arms: keep growing outwards along the parent's radius, tiny wobble
            out = math.atan2(p['y'], p['x'])
            ang = out + (self._mul() - 0.5) * 0.45
            dist = self._r(16, 42)
            x = p['x'] + math.cos(ang) * dist
            y = p['y'] + math.sin(ang) * dist
        idx = self._add_node(x, y, pid, p['depth'] + 1)
        p['kids'] += 1
        self._add_edge(idx, pid, 0)
        return idx

    def _wither(self, idx):
        nd = self._nodes[idx]
        for nb in list(self._adj[idx]):
            e = self._keys.get(self._key(idx, nb))
            if e is not None:
                self._remove_edge(e)
        if nd['parent'] >= 0:
            self._nodes[nd['parent']]['kids'] -= 1
        nd['state'] = 2
        nd['die'] = self._t
        self._alive -= 1

    def _grow_wave(self):
        listed = list(self._act)
        root_kids = self._nodes[0]['kids']
        # Arms: one new one per wave until the fan is full, then the wave
        # only grows the arms it already has.
        if root_kids < self._arm_cap:
            if self._alive < self._cap:
                self._sprout(0)
        for idx in listed:
            if idx == 0 or self._alive >= self._cap:
                continue
            nd = self._nodes[idx]
            if nd['state'] != 1:
                continue
            p = 0.8 if nd['kids'] == 0 else 0.16
            if nd['depth'] > 8:
                p *= 0.2
            if self._mul() < p:
                count = 1 + (1 if self._mul() < 0.28 else 0)
                for _ in range(count):
                    if self._alive < self._cap:
                        self._sprout(idx)
        if self._alive >= self._cap * 0.97:
            self._state = 'hold'
            self._hold_until = self._t + 4.5
        self._schedule()

    def _turnover(self):
        """Full tree keeps living: leaves wither, same count sprouts elsewhere."""
        m = max(2, int(round(self._alive * 0.04)))
        leaves = [i for i in self._act
                  if i != 0 and self._nodes[i]['state'] == 1
                  and self._nodes[i]['kids'] == 0
                  and self._t - self._nodes[i]['born'] > 1.5]
        cut = 0
        for _ in range(m):
            if not leaves:
                break
            li = int(self._mul() * len(leaves)) % len(leaves)
            self._wither(leaves.pop(li))
            cut += 1
        alive = [i for i in self._act
                 if i != 0 and self._nodes[i]['state'] == 1
                 and self._nodes[i]['depth'] < 9]
        for _ in range(cut):
            if not alive:
                break
            pid = alive[int(self._mul() * len(alive)) % len(alive)]
            if self._nodes[pid]['state'] == 1:
                self._sprout(pid)
        self._schedule()

    def _shrink_wave(self):
        """Growth played backwards: newest growth (leaves) peels off first."""
        leaves = [i for i in self._act
                  if i != 0 and self._nodes[i]['state'] == 1
                  and self._nodes[i]['kids'] == 0]
        if leaves:
            p = 1.0 if self._alive <= 4 else 0.7
            cut = 0
            for idx in leaves:
                if self._mul() < p:
                    self._wither(idx)
                    cut += 1
            if not cut:
                self._wither(leaves[int(self._mul() * len(leaves)) % len(leaves)])
        self._schedule()

    def _no_dying(self):
        return not any(n['state'] == 2 for n in self._nodes)

    def _unwind_one(self):
        """Drop the newest leaf: growth played back one node at a time."""
        newest, born = -1, -1e9
        for idx in self._act:
            nd = self._nodes[idx]
            if idx == 0 or nd['kids'] or nd['state'] != 1:
                continue
            if nd['born'] > born:
                born, newest = nd['born'], idx
        if newest >= 0:
            self._wither(newest)
            self._rebuild_act()

    def _is_desc(self, q, c):
        x, guard = q, 0
        while x >= 0 and guard < 80:
            if x == c:
                return True
            x = self._nodes[x]['parent']
            guard += 1
        return False

    def _reparent(self):
        """Move a branch to another parent (never under itself: stays a tree)."""
        if len(self._act) < 4:
            return
        for _ in range(10):
            c = self._act[int(self._mul() * len(self._act)) % len(self._act)]
            if c == 0:
                continue
            nd = self._nodes[c]
            if nd['state'] != 1 or nd['parent'] < 0:
                continue
            far = self._mul() < 0.22
            best, bd = -1, 1e18
            for _ in range(9):
                q = self._act[int(self._mul() * len(self._act)) % len(self._act)]
                if q == c or q == nd['parent'] or self._nodes[q]['state'] != 1:
                    continue
                if self._nodes[q]['depth'] > nd['depth'] or self._nodes[q]['depth'] > 9:
                    continue
                if q == 0 and self._nodes[0]['kids'] >= 16:
                    continue
                if self._key(c, q) in self._keys or self._is_desc(q, c):
                    continue
                dx = self._nodes[q]['x'] - nd['x']
                dy = self._nodes[q]['y'] - nd['y']
                d2 = dx * dx + dy * dy
                if far:
                    best = q
                    break
                if d2 < bd:
                    bd, best = d2, q
            if best < 0:
                continue
            old = self._keys.get(self._key(c, nd['parent']))
            if old is None:
                continue
            self._remove_edge(old)
            self._nodes[nd['parent']]['kids'] -= 1
            self._add_edge(c, best, 0)
            self._nodes[best]['kids'] += 1
            nd['parent'] = best
            self._rewired += 1
            return

    def _cross_link(self):
        """Short-lived link between two nearby nodes on different branches."""
        if self._cross_live > self._alive * 0.3 or len(self._act) < 4:
            return
        a = self._act[int(self._mul() * len(self._act)) % len(self._act)]
        if a == 0 or self._nodes[a]['state'] != 1:
            return
        best, bd = -1, 1e18
        lim = (K * 4) ** 2
        for _ in range(10):
            b = self._act[int(self._mul() * len(self._act)) % len(self._act)]
            if b == a or b == 0 or self._nodes[b]['state'] != 1 or b in self._adj[a]:
                continue
            dx = self._nodes[b]['x'] - self._nodes[a]['x']
            dy = self._nodes[b]['y'] - self._nodes[a]['y']
            d2 = dx * dx + dy * dy
            if d2 < bd and d2 < lim:
                bd, best = d2, b
        if best >= 0 and self._add_edge(a, best, 1):
            self._rewired += 1

    def _refresh_depth(self):
        for idx in self._act:
            d, x, guard = 0, idx, 0
            while self._nodes[x]['parent'] >= 0 and guard < 80:
                d += 1
                x = self._nodes[x]['parent']
                guard += 1
            self._nodes[idx]['depth'] = d

    # ── physics (force-directed, world space) ────────────────────────

    def _physics(self, dt60):
        act = self._act
        nodes = self._nodes
        n = len(act)
        k, k2 = K, K * K
        cut = k2 * 36
        for idx in act:
            nodes[idx]['fx'] = 0.0
            nodes[idx]['fy'] = 0.0
        for i in range(n):
            a = nodes[act[i]]
            ax, ay = a['x'], a['y']
            for j in range(i + 1, n):
                b = nodes[act[j]]
                dx = ax - b['x']
                dy = ay - b['y']
                d2 = dx * dx + dy * dy
                if d2 > cut:
                    continue
                if d2 < 0.04:
                    dx = self._rng.random() - 0.5
                    dy = self._rng.random() - 0.5
                    d2 = dx * dx + dy * dy + 0.05
                f = k2 / d2
                if i == 0:
                    f *= 2.5
                a['fx'] += dx * f
                a['fy'] += dy * f
                b['fx'] -= dx * f
                b['fy'] -= dy * f
        for e in self._edges:
            if e['dying']:
                continue
            a = nodes[e['a']]
            b = nodes[e['b']]
            dx = b['x'] - a['x']
            dy = b['y'] - a['y']
            d = math.sqrt(dx * dx + dy * dy) + 0.01
            s = d - e['rest']
            if s > 3 * k:
                s = 3 * k
            elif s < -2 * k:
                s = -2 * k
            f = e['k'] * s / d * e['al']
            a['fx'] += dx * f
            a['fy'] += dy * f
            b['fx'] -= dx * f
            b['fy'] -= dy * f
            if e['kind'] == 0:
                # arm alignment: hold the child on the parent's radius line
                bl = math.sqrt(b['x'] * b['x'] + b['y'] * b['y'])
                if bl > 1.0:
                    ux, uy = b['x'] / bl, b['y'] / bl
                    ox, oy = a['x'] - b['x'], a['y'] - b['y']
                    dot = ox * ux + oy * uy
                    tx, ty = ox - dot * ux, oy - dot * uy
                    fa = e['al'] * 0.06
                    a['fx'] -= tx * fa
                    a['fy'] -= ty * fa
        max_v = k * 0.3
        damp = 0.5 ** dt60
        rr = RR + 6
        for idx in act:
            a = nodes[idx]
            if idx == 0:
                a['x'] = a['y'] = a['vx'] = a['vy'] = 0.0
                continue
            a['fx'] -= a['x'] * 0.002
            a['fy'] -= a['y'] * 0.002
            a['vx'] = a['vx'] * damp + a['fx'] * 0.035 * dt60
            a['vy'] = a['vy'] * damp + a['fy'] * 0.035 * dt60
            vx, vy = a['vx'], a['vy']
            sp = math.sqrt(vx * vx + vy * vy)
            if sp > max_v:
                a['vx'] = vx * max_v / sp
                a['vy'] = vy * max_v / sp
            a['x'] += a['vx'] * dt60
            a['y'] += a['vy'] * dt60
            rd = math.sqrt(a['x'] * a['x'] + a['y'] * a['y'])
            if rd < rr:
                if rd < 0.01:
                    a['x'], a['y'] = float(rr), 0.0
                else:
                    a['x'] *= rr / rd
                    a['y'] *= rr / rd

    def _camera(self, dt):
        """The central node never moves: it stays at the middle of the widget.

        Only the zoom follows the tree — the farthest arm in each axis decides
        how much of the panel the arms may use.
        """
        cam = self._cam
        hx = hy = RR * 1.1
        for idx in self._act:
            nd = self._nodes[idx]
            ax = abs(nd['x'])
            ay = abs(nd['y'])
            if ax > hx:
                hx = ax
            if ay > hy:
                hy = ay
        w = float(max(8, self.width()))
        h = float(max(8, self.height()))
        tx = min(MAXZ, max(4.0, w / 2.0 - 10.0) / hx)
        ty = min(MAXZ, max(4.0, h / 2.0 - 10.0) / hy)
        shrinking = tx < cam[2] or ty < cam[3]
        rate = 3.5 if shrinking else (1.8 if self._state == 'shrink' else 0.7)
        k = 1.0 - math.exp(-dt * rate)
        cam[2] += (tx - cam[2]) * k
        cam[3] += (ty - cam[3]) * k
        cam[0] = 0.0
        cam[1] = 0.0

    # ── one simulation step ──────────────────────────────────────────

    def _step(self, dt):
        self._t += dt
        self._rebuild_act()

        if self._finishing:
            # Reverse playback: the newest node leaves first, so the growth
            # sequence runs backwards until one cyan dot is all that is left.
            if self._alive <= 1 and self._live == 0 and self._no_dying():
                self._dot_t += dt
                if self._dot_t >= 0.45:
                    self._timer.stop()
                    self.finished.emit()
                return
            if self._t >= self._next:
                self._unwind_one()
                self._next = self._t + self._unwind_dt
        elif self._state == 'grow':
            if self._t >= self._next:
                self._grow_wave()
                self._rebuild_act()
        elif self._state == 'hold':
            if self._t >= self._next:
                self._turnover()
                self._rebuild_act()
            if self._t >= self._hold_until:
                self._state = 'shrink'
                self._schedule()
        elif self._state == 'shrink':
            if self._t >= self._next:
                self._shrink_wave()
                self._rebuild_act()
            # Only the root left: fresh seed, red cleared, grow again.
            if self._alive == 1 and self._live == 0 and self._no_dying():
                self._reset_tree(keep_cam=True)

        self._vis = min(1.0, self._vis + dt / 0.8)

        self._rep_acc += min(90.0, self._alive * 0.2) * dt
        while self._rep_acc >= 1.0:
            self._reparent()
            self._rep_acc -= 1.0
        self._cross_acc += min(120.0, self._alive * 0.4) * dt
        while self._cross_acc >= 1.0:
            self._cross_link()
            self._cross_acc -= 1.0

        keep = []
        for e in self._edges:
            if not e['dying'] and e['kind'] == 1 and self._t >= e['expire']:
                self._remove_edge(e)
            if e['dying']:
                e['al'] -= dt * 2.5
                if e['al'] <= 0:
                    continue
            else:
                e['al'] = min(1.0, (self._t - e['born']) / 0.5)
            keep.append(e)
        self._edges = keep

        for i, nd in enumerate(self._nodes):
            if nd['state'] == 2 and self._t - nd['die'] > 0.5:
                nd['state'] = 0
                self._free.append(i)

        self._rebuild_act()
        self._depth_t += dt
        if self._depth_t > 0.25:
            self._depth_t = 0.0
            self._refresh_depth()

        self._physics(dt * 60.0)
        self._camera(dt)

    def _tick(self):
        for _ in range(STEPS_PER_TICK):
            self._step(STEP)
        self.update()

    # ── drawing (same layering as the page) ──────────────────────────

    def paintEvent(self, event):
        nodes = self._nodes
        if not nodes:
            return
        w = float(self.width())
        h = float(self.height())
        u = min(w, h) / 48.0                # one size unit, so strokes scale
        # Arms may reach wider than tall: separate x/y scales keep the panel
        # full while each dot stays a circle.
        zx = self._cam[2]
        zy = self._cam[3]
        ox = w / 2.0 - self._cam[0] * zx
        oy = h / 2.0 - self._cam[1] * zy
        vis = self._vis
        t = self._t
        pal = PALETTE_CYAN if self._finishing else PALETTE

        node_r = max(1.0, min(6.0, 1.3 * u))
        base = 0.55
        zf = max(0.55, min(1.1, min(zx, zy) * 1.1))

        def tint(rgb, alpha):
            c = QColor(rgb)
            c.setAlphaF(max(0.0, min(1.0, alpha)))
            return c

        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(Qt.NoBrush)

            # settled tree links, thicker near the root, colour of the node they feed
            paths = {}
            for e in self._edges:
                if e['kind'] != 0 or e['dying'] or t - e['born'] < 1.2:
                    continue
                child = nodes[e['a']]
                key = (_tier_of(child['depth']), child['c'])
                path = paths.get(key)
                if path is None:
                    path = paths[key] = QPainterPath()
                parent = nodes[e['b']]
                path.moveTo(child['x'] * zx + ox, child['y'] * zy + oy)
                path.lineTo(parent['x'] * zx + ox, parent['y'] * zy + oy)
            for (tier, ci), path in paths.items():
                pen = QPen(tint(pal[ci], base * vis),
                           max(0.5, min(2.0, TIER[tier] * 0.5 * u * zf)))
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawPath(path)

            # cross links: thinner and dimmer
            for e in self._edges:
                if e['kind'] != 1 or e['dying'] or t - e['born'] < 1.2:
                    continue
                a, b = nodes[e['a']], nodes[e['b']]
                p.setPen(QPen(tint(pal[a['c']], base * 0.5 * vis),
                              max(0.4, min(1.2, 0.35 * u * zf))))
                p.drawLine(QPointF(a['x'] * zx + ox, a['y'] * zy + oy),
                           QPointF(b['x'] * zx + ox, b['y'] * zy + oy))

            # new and fading links (bright flash while they are born / die)
            for e in self._edges:
                a, b = nodes[e['a']], nodes[e['b']]
                if e['dying']:
                    col = tint(pal[a['c']], base * max(0.0, e['al']) * vis)
                else:
                    age = t - e['born']
                    if age >= 1.2:
                        continue
                    fade = 1.0 - age / 1.2
                    col = tint(pal[a['c']],
                               (base + (0.95 - base) * fade) * e['al'] * vis)
                p.setPen(QPen(col, max(0.6, min(1.6, 0.7 * u * zf))))
                p.drawLine(QPointF(a['x'] * zx + ox, a['y'] * zy + oy),
                           QPointF(b['x'] * zx + ox, b['y'] * zy + oy))

            # settled nodes, one path per colour
            done = {}
            for nd in nodes[1:]:
                if nd['state'] != 1 or t - nd['born'] < 1.5:
                    continue
                path = done.get(nd['c'])
                if path is None:
                    path = done[nd['c']] = QPainterPath()
                path.addEllipse(QPointF(nd['x'] * zx + ox, nd['y'] * zy + oy),
                                node_r, node_r)
            for ci, path in done.items():
                p.setPen(Qt.NoPen)
                p.setBrush(tint(pal[ci], 0.95 * vis))
                p.drawPath(path)

            # newborn (scale-in + blush + pulse ring) and withering
            for nd in nodes[1:]:
                if nd['state'] == 0:
                    continue
                rgb = pal[nd['c']]
                qx, qy = nd['x'] * zx + ox, nd['y'] * zy + oy
                p.setPen(Qt.NoPen)
                if nd['state'] == 2:
                    w = min(1.0, (t - nd['die']) / 0.5)
                    p.setBrush(tint(rgb, (1.0 - w) * vis))
                    p.drawEllipse(QPointF(qx, qy), node_r * (1 - w * 0.6),
                                  node_r * (1 - w * 0.6))
                    continue
                age = t - nd['born']
                if age >= 1.5:
                    continue
                uu = min(1.0, age / 0.35)
                rr = node_r * (1 - (1 - uu) ** 3)
                p.setBrush(tint(rgb, 0.95 * vis))
                p.drawEllipse(QPointF(qx, qy), rr, rr)
                p.setBrush(tint(rgb, (1.0 - age / 1.5) * vis))
                p.drawEllipse(QPointF(qx, qy), rr * 1.15, rr * 1.15)
                if age < 0.9:
                    p.setPen(QPen(tint(rgb, (1 - age / 0.9) * 0.7 * vis),
                                  max(0.6, 0.5 * u)))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(QPointF(qx, qy),
                                  node_r * (1 + (age / 0.9) * 4),
                                  node_r * (1 + (age / 0.9) * 4))
                    p.setPen(Qt.NoPen)

            # the central node: fixed, it never moves and never scales
            p.setPen(Qt.NoPen)
            p.setBrush(tint(pal[nodes[0]['c']], vis))
            p.drawEllipse(QPointF(ox, oy), ROOT_R, ROOT_R)
        finally:
            p.end()
