"""Deadline Dragon - a desktop dragon that gets bigger and angrier as deadlines close in.

Run:  python deadline_dragon.py

- Drag the dragon with the left mouse button; click it (without dragging) to poke it.
- Right-click for the menu (add / manage deadlines, quit).
- Dates can be YYYY-MM-DD, DD-MM-YYYY, or just a number of days from today (e.g. 5).
"""

import math
import random
import sqlite3
import sys
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

DB_PATH = Path(__file__).with_name("deadline_dragon.db")
W, H = 340, 300
KEY = "#010203"      # colour treated as see-through on Windows
BONE = "#f5e6c8"
INK = "#1e1e2e"

# urgency 0..1 -> colour ramp (green -> yellow -> orange -> red)
RAMP = [
    (0.0, (76, 175, 80)),
    (0.4, (242, 194, 0)),
    (0.7, (255, 122, 0)),
    (1.0, (229, 57, 53)),
]
SLEEP_RGB = (96, 125, 139)
OVERDUE_RGB = (140, 30, 70)

QUIPS = {
    "sleeping": ["zzz... no deadlines...", "Add a deadline. I'm bored.", "Feed me a due date."],
    "calm": ["Plenty of time. Don't get cocky.", "I'm watching you.", "Enjoy it while it lasts."],
    "alert": ["Start revising.", "Less scrolling, more studying.", "I can feel it getting closer."],
    "angry": ["This is getting close!", "Why are you on Instagram?", "Open the notes. Now."],
    "furious": ["STUDY. NOW.", "I WILL BURN YOUR NOTES.", "Do you even see the date?!"],
    "overdue": ["...you missed one.", "I smell smoke. It's me.", "Slay it or face me."],
}


# ---------------------------------------------------------------- helpers
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_rgb(a, b, t):
    return tuple(lerp(x, y, t) for x, y in zip(a, b))


def hexc(rgb):
    r, g, b = (int(clamp(v, 0, 255)) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def shade(rgb, k):
    return hexc(tuple(v * k for v in rgb))


def ramp(u):
    u = clamp(u, 0.0, 1.0)
    for (u0, c0), (u1, c1) in zip(RAMP, RAMP[1:]):
        if u <= u1:
            return lerp_rgb(c0, c1, (u - u0) / (u1 - u0))
    return RAMP[-1][1]


def parse_due(text):
    text = text.strip()
    if text.isdigit():
        return date.today() + timedelta(days=int(text))
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("Use YYYY-MM-DD, DD-MM-YYYY, or a number of days from today.")


def when_text(days):
    if days < 0:
        return f"{-days}d overdue"
    if days == 0:
        return "today!"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def days_left(due_iso):
    return (date.fromisoformat(due_iso) - date.today()).days


# ---------------------------------------------------------------- storage
class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS deadlines (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   title TEXT NOT NULL,
                   due TEXT NOT NULL,
                   done INTEGER NOT NULL DEFAULT 0,
                   created_at TEXT NOT NULL,
                   done_at TEXT
               )"""
        )
        self.conn.commit()

    def add(self, title, due_iso):
        self.conn.execute(
            "INSERT INTO deadlines (title, due, created_at) VALUES (?, ?, ?)",
            (title, due_iso, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def pending(self):
        cur = self.conn.execute(
            "SELECT id, title, due FROM deadlines WHERE done = 0 ORDER BY due, id"
        )
        return cur.fetchall()

    def mark_done(self, rid):
        self.conn.execute(
            "UPDATE deadlines SET done = 1, done_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), rid),
        )
        self.conn.commit()

    def delete(self, rid):
        self.conn.execute("DELETE FROM deadlines WHERE id = ?", (rid,))
        self.conn.commit()

    def slain(self):
        return self.conn.execute("SELECT COUNT(*) FROM deadlines WHERE done = 1").fetchone()[0]


# ---------------------------------------------------------------- the dragon
class DragonApp:
    def __init__(self, root, db_path=DB_PATH):
        self.root = root
        self.store = Store(db_path)
        self.manager = None

        # state driven by the nearest deadline
        self.mood = "sleeping"
        self.urgency = 0.0
        self.label = ""
        self.scale = 0.5
        self.target_scale = 0.5
        self.rgb = SLEEP_RGB
        self.target_rgb = SLEEP_RGB

        # animation state
        self.t0 = time.time()
        self.roar_until = 0.0
        self.quip = ""
        self.quip_until = 0.0

        self._setup_window()
        self._bind_events()
        self.refresh()
        self.rgb, self.scale = self.target_rgb, self.target_scale
        self.tick()
        self.refresh_loop()

    # ---- window ----
    def _setup_window(self):
        root = self.root
        root.title("Deadline Dragon")
        root.overrideredirect(True)
        root.attributes("-topmost", True)

        bg = INK
        try:
            if sys.platform.startswith("win"):
                root.wm_attributes("-transparentcolor", KEY)
                bg = KEY
            elif sys.platform == "darwin":
                root.wm_attributes("-transparent", True)
                root.config(bg="systemTransparent")
                bg = "systemTransparent"
            else:
                root.wm_attributes("-transparentcolor", KEY)
                bg = KEY
        except tk.TclError:
            bg = INK  # no transparency support: fall back to a solid backdrop

        self.canvas = tk.Canvas(root, width=W, height=H, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack()

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{sw - W - 40}+{sh - H - 80}")

    def _bind_events(self):
        cv = self.canvas
        cv.bind("<ButtonPress-1>", self.on_press)
        cv.bind("<B1-Motion>", self.on_drag)
        cv.bind("<ButtonRelease-1>", self.on_release)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Add / manage deadlines...", command=self.open_manager)
        self.menu.add_command(label="Poke the dragon", command=self.poke)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.root.destroy)

        for seq in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            cv.bind(seq, self.popup)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def popup(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_press(self, e):
        self.press_xy = (e.x_root, e.y_root)
        self.drag_off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self.moved = False

    def on_drag(self, e):
        if abs(e.x_root - self.press_xy[0]) + abs(e.y_root - self.press_xy[1]) > 4:
            self.moved = True
        if self.moved:
            self.root.geometry(f"+{e.x_root - self.drag_off[0]}+{e.y_root - self.drag_off[1]}")

    def on_release(self, _e):
        if not getattr(self, "moved", False):
            self.poke()

    # ---- behaviour ----
    def say(self, text, secs=4.0):
        self.quip = text
        self.quip_until = time.time() + secs

    def poke(self):
        self.say(random.choice(QUIPS[self.mood]), 3.0)

    def roar(self, secs=1.4):
        self.roar_until = time.time() + secs

    def slay(self, rid):
        self.store.mark_done(rid)
        self.roar()
        self.refresh()
        self.say(f"ROAR! Slain: {self.store.slain()}", 3.5)

    def refresh(self):
        rows = self.store.pending()
        old_mood = self.mood
        if not rows:
            self.mood, self.urgency = "sleeping", 0.0
            self.label = "No deadlines. Feed me one."
            self.target_rgb = SLEEP_RGB
        else:
            _rid, title, due = rows[0]
            d = days_left(due)
            if d < 0:
                self.mood, self.urgency = "overdue", 1.0
            else:
                self.urgency = clamp(1 - d / 21, 0.0, 1.0)
                if d <= 2:
                    self.mood = "furious"
                elif d <= 6:
                    self.mood = "angry"
                elif d <= 13:
                    self.mood = "alert"
                else:
                    self.mood = "calm"
            short = title if len(title) <= 20 else title[:19] + "…"
            self.label = f"{short} · {when_text(d)}"
            self.target_rgb = OVERDUE_RGB if self.mood == "overdue" else ramp(self.urgency)
        self.target_scale = 0.5 + 0.55 * self.urgency
        if self.mood != old_mood and time.time() > self.quip_until:
            self.say(random.choice(QUIPS[self.mood]), 4.0)

    def refresh_loop(self):
        self.refresh()
        self.root.after(30_000, self.refresh_loop)

    def tick(self):
        self.scale += (self.target_scale - self.scale) * 0.06
        self.rgb = lerp_rgb(self.rgb, self.target_rgb, 0.06)
        self.draw()
        self.root.after(40, self.tick)

    # ---- drawing ----
    def draw(self):
        cv = self.canvas
        cv.delete("all")
        now = time.time()
        t = now - self.t0
        s = self.scale
        mood = self.mood
        roaring = now < self.roar_until
        fire = roaring or mood in ("furious", "overdue")
        angry = mood in ("angry", "furious", "overdue")

        rgb = self.rgb
        body, dark, light = hexc(rgb), shade(rgb, 0.65), shade(rgb, 1.3)
        lw = max(1, round(2 * s))

        bob = math.sin(t * 2.2) * 4 * s
        shake = random.uniform(-4, 4) if roaring else 0
        cx, cy = 146 + shake, 175 + bob

        def p(x, y):
            return cx + x * s, cy + y * s

        def flat(xy):
            out = []
            for i in range(0, len(xy), 2):
                out.extend(p(xy[i], xy[i + 1]))
            return out

        def oval(x1, y1, x2, y2, **kw):
            cv.create_oval(*p(x1, y1), *p(x2, y2), **kw)

        def poly(*xy, **kw):
            cv.create_polygon(*flat(xy), **kw)

        def line(*xy, **kw):
            cv.create_line(*flat(xy), **kw)

        # wings (behind everything)
        flap = math.sin(t * (3 + 5 * self.urgency)) * (10 + 8 * self.urgency)
        if mood == "sleeping":
            flap = math.sin(t * 1.2) * 3
        poly(-10, -35, -62, -100 + flap, -38, -72 + flap * 0.5, -8, -118 + flap,
             8, -74 + flap * 0.4, 40, -98 + flap, 25, -35,
             fill=dark, outline=shade(rgb, 0.45), width=lw)

        # tail
        sway = math.sin(t * 2) * 8
        ty = 34 + sway * 1.5
        line(-45, 15, -80, 32, -105, 20 + sway, -126, ty,
             fill=body, width=max(2, int(16 * s)), smooth=True, capstyle=tk.ROUND)
        poly(-122, ty - 10, -138, ty, -122, ty + 10, fill=dark, outline="")

        # legs
        for bx in (-42, 8):
            oval(bx, 36, bx + 30, 62, fill=body, outline=dark, width=lw)
            for dx in (4, 12, 20):
                poly(bx + dx, 60, bx + dx + 4, 67, bx + dx + 8, 60, fill=BONE, outline="")

        # body + belly
        oval(-55, -45, 45, 50, fill=body, outline=dark, width=lw)
        oval(-22, -18, 38, 46, fill=light, outline="")
        for i in range(3):
            y = -2 + i * 15
            line(-12, y, 14, y + 4, 30, y, fill=shade(rgb, 1.0), width=lw, smooth=True)

        # back spikes
        for i in range(5):
            x = -45 + i * 17
            y = 2.5 - 47.5 * math.sqrt(max(0.0, 1 - ((x + 5) / 50) ** 2))
            poly(x - 6, y + 4, x, y - 14, x + 6, y + 4, fill=dark, outline="")

        # neck, head, snout
        line(22, -25, 46, -55, fill=body, width=max(2, int(30 * s)), capstyle=tk.ROUND)
        oval(25, -90, 80, -35, fill=body, outline=dark, width=lw)
        oval(58, -76, 110, -48, fill=body, outline=dark, width=lw)
        oval(56, -74, 78, -50, fill=body, outline="")

        # horns
        poly(36, -84, 30, -114, 50, -88, fill=BONE, outline=dark, width=lw)
        poly(52, -88, 54, -118, 68, -84, fill=BONE, outline=dark, width=lw)

        # eye
        ex, ey = 64, -70
        closed = mood == "sleeping" or (t % 4.0) < 0.12
        if closed:
            line(55, ey, 64, ey + 3, 73, ey, fill=dark, width=lw + 1, smooth=True)
        else:
            iris = "#ffeb3b" if mood in ("furious", "overdue") else "#ffffff"
            oval(ex - 8, ey - 7, ex + 8, ey + 7, fill=iris, outline=dark, width=lw)
            if angry:
                oval(ex, ey - 6, ex + 4, ey + 6, fill="black", outline="")
                line(ex - 12, ey - 13, ex + 12, ey - 2, fill=dark, width=lw + 2)
            else:
                oval(ex - 1, ey - 4, ex + 5, ey + 4, fill="black", outline="")

        # nostril + mouth
        oval(100, -66, 105, -61, fill=dark, outline="")
        if fire:
            poly(100, -50, 68, -46, 76, -32, 98, -40, fill=body, outline=dark, width=lw)
            poly(104, -52, 70, -49, 78, -40, 98, -44, fill="#7a1020", outline="")
        else:
            line(106, -52, 70, -47, fill=dark, width=lw)

        # fire breath
        if fire:
            length = 40 + random.random() * 20 + (12 if roaring else 0)
            for col, k in (("#ff5722", 1.0), ("#ffb300", 0.72), ("#fff59d", 0.45)):
                l, hw, j = length * k, 12 * k, random.uniform(-6, 6)
                poly(108, -53 - hw, 108 + l * 0.5, -53 - hw * 1.4 + j, 108 + l, -53 + j * 0.5,
                     108 + l * 0.55, -53 + hw * 1.2, 108, -53 + hw, fill=col, outline="")

        # smoke when annoyed
        if mood in ("angry", "furious", "overdue"):
            n = 2 if mood == "angry" else 4
            for i in range(n):
                age = (t * 0.7 + i / n) % 1
                x = 103 + age * 18 + math.sin(age * 6 + i) * 4
                y = -66 - age * 55
                r = 3 + age * 9
                col = hexc(lerp_rgb((70, 70, 70), (170, 170, 170), age))
                oval(x - r, y - r, x + r, y + r, fill=col, outline="")

        # sleeping z's
        if mood == "sleeping":
            for i in range(3):
                age = (t * 0.4 + i / 3) % 1
                x, y = 95 + age * 25, -95 - age * 45
                cv.create_text(*p(x, y), text="z", fill=BONE,
                               font=("Helvetica", int((8 + i * 3) * max(s, 0.7)), "bold"))

        # label / quip bubble
        text = self.quip if now < self.quip_until else self.label
        tid = cv.create_text(W / 2, 22, text=text, fill="white", font=("Helvetica", 10, "bold"))
        x1, y1, x2, y2 = cv.bbox(tid)
        box = cv.create_rectangle(x1 - 10, y1 - 5, x2 + 10, y2 + 5, fill=INK, outline=body, width=2)
        cv.tag_lower(box, tid)

    # ---- deadline manager window ----
    def open_manager(self):
        if self.manager is not None and self.manager.winfo_exists():
            self.manager.lift()
            self.manager.focus_force()
            return

        win = tk.Toplevel(self.root)
        self.manager = win
        win.title("Deadline Dragon")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.geometry(f"+{max(0, self.root.winfo_x() - 480)}+{max(0, self.root.winfo_y() - 60)}")
        win.bind("<Escape>", lambda e: win.destroy())

        frm = ttk.Frame(win, padding=12)
        frm.grid()

        title_var, due_var = tk.StringVar(), tk.StringVar()
        ttk.Label(frm, text="What's due?").grid(row=0, column=0, sticky="w")
        ttk.Label(frm, text="When? (YYYY-MM-DD or days)").grid(row=0, column=1, sticky="w", padx=(8, 0))
        title_e = ttk.Entry(frm, textvariable=title_var, width=26)
        title_e.grid(row=1, column=0)
        due_e = ttk.Entry(frm, textvariable=due_var, width=22)
        due_e.grid(row=1, column=1, padx=(8, 0))

        tree = ttk.Treeview(frm, columns=("title", "due", "left"), show="headings", height=8)
        for col, text, width in (("title", "Deadline", 200), ("due", "Due", 90), ("left", "Time left", 110)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="w")
        tree.grid(row=2, column=0, columnspan=3, pady=(10, 6), sticky="ew")
        tree.tag_configure("late", foreground="#c62828")

        status = ttk.Label(frm, text="")
        status.grid(row=3, column=0, sticky="w")

        def reload():
            tree.delete(*tree.get_children())
            for rid, title, due in self.store.pending():
                d = days_left(due)
                tree.insert("", "end", iid=str(rid),
                            values=(title, due, when_text(d)), tags=("late",) if d < 0 else ())
            status.config(text=f"Slain so far: {self.store.slain()}")

        def selected():
            sel = tree.selection()
            return int(sel[0]) if sel else None

        def add(_event=None):
            title = title_var.get().strip()
            if not title:
                messagebox.showwarning("Deadline Dragon", "Give the deadline a name.", parent=win)
                return
            try:
                due = parse_due(due_var.get())
            except ValueError as err:
                messagebox.showwarning("Deadline Dragon", str(err), parent=win)
                return
            self.store.add(title, due.isoformat())
            title_var.set("")
            due_var.set("")
            title_e.focus_set()
            reload()
            self.refresh()
            self.say("Fresh meat. I'll be watching.")

        def slay():
            rid = selected()
            if rid is not None:
                self.slay(rid)
                reload()

        def remove():
            rid = selected()
            if rid is not None:
                self.store.delete(rid)
                reload()
                self.refresh()

        ttk.Button(frm, text="Add", width=8, command=add).grid(row=1, column=2, padx=(8, 0))
        btns = ttk.Frame(frm)
        btns.grid(row=3, column=1, columnspan=2, sticky="e")
        ttk.Button(btns, text="Slay (done)", command=slay).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Delete", command=remove).pack(side="left")

        title_e.bind("<Return>", add)
        due_e.bind("<Return>", add)
        reload()
        title_e.focus_set()


def main():
    root = tk.Tk()
    DragonApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()