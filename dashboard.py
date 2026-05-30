#!/usr/bin/env python3
"""
Raspberry Pi OBD-II Dashboard for 2011 Toyota Tacoma
Displays real-time vehicle data and controls accessory lights via GPIO relays.

Features:
  - Live gauges: speed, RPM, coolant temp, engine load, intake temp, instant & avg MPG
  - Distance-to-empty gauge with persistent fuel state (saved every 30 seconds)
  - Trip statistics (duration, distance, avg MPG) with reset
  - Light switch panel (pod lights, amber bar, white bar)
  - Day / night mode
"""

import tkinter as tk
from tkinter import font as tkfont
import obd
from obd import OBDStatus
import RPi.GPIO as GPIO
import threading
import time
import json
import os
from collections import deque
from datetime import datetime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# GPIO pin assignments (BCM numbering) for active-LOW relay board
RELAY_PINS = {
    'pod_lights': 17,
    'amber_bar':  18,
    'white_bar':  27,
}

# Tire size correction factor applied to OBD speed readings
#   Stock tires:   265/70R16 → 30.6" diameter
#   Current tires: 285/70R17 → 32.7" diameter
#   Factor = 32.7 / 30.6
TIRE_CORRECTION_FACTOR = 1.0686

# Fuel tank capacity in gallons (2011 Toyota Tacoma)
TANK_CAPACITY_GALLONS = 21.0

# Path for the persistent fuel/trip state file (same directory as this script)
FUEL_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fuel_state.json')

# How often (seconds) to write the fuel state to disk
FUEL_SAVE_INTERVAL = 30

# ---------------------------------------------------------------------------
# Color palettes
# ---------------------------------------------------------------------------

COLORS = {
    'day': {
        'bg':            '#F0F0F0',
        'text':          '#111111',
        'gauge_bg':      '#FFFFFF',
        'gauge_border':  '#CCCCCC',
        'accent':        '#2196F3',
        'button_bg':     '#E0E0E0',
        'button_active': '#4CAF50',
        'warning':       '#FF5722',
        'dte_low':       '#FF5722',   # red when range is low
        'dte_mid':       '#FF9800',   # orange when range is moderate
        'dte_ok':        '#4CAF50',   # green when range is healthy
    },
    'night': {
        'bg':            '#1A1A1A',
        'text':          '#FFFFFF',
        'gauge_bg':      '#2A2A2A',
        'gauge_border':  '#404040',
        'accent':        '#2196F3',
        'button_bg':     '#333333',
        'button_active': '#4CAF50',
        'warning':       '#FF5722',
        'dte_low':       '#FF5722',
        'dte_mid':       '#FF9800',
        'dte_ok':        '#4CAF50',
    },
}


# ---------------------------------------------------------------------------
# Main application class
# ---------------------------------------------------------------------------

class OBDDashboard:

    # -----------------------------------------------------------------------
    # Initialisation
    # -----------------------------------------------------------------------

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Tacoma Dashboard")
        self.root.attributes('-fullscreen', True)
        self.root.configure(cursor='none')  # hide cursor for touchscreen use

        # ── UI state ──────────────────────────────────────────────────────
        self.night_mode      = False
        self.show_trip_stats = False

        # ── Hardware state ────────────────────────────────────────────────
        self.connection   = None
        self.relay_states = {key: False for key in RELAY_PINS}
        self.running      = True

        # ── Live vehicle data ─────────────────────────────────────────────
        self.vehicle_data = {
            'speed':        0.0,
            'rpm':          0,
            'coolant_temp': 0,
            'engine_load':  0,
            'intake_temp':  0,
            'instant_mpg':  0.0,
            'avg_mpg':      0.0,
        }

        # ── MPG history (all readings in current trip) ────────────────────
        self.mpg_history: deque[float] = deque()

        # ── Trip statistics ───────────────────────────────────────────────
        self.trip_start_time  = datetime.now()
        self.trip_distance_mi = 0.0   # miles driven this trip
        self.last_speed       = 0.0
        self.last_obd_time    = time.time()

        # ── Fuel tracking (loaded from disk on startup) ───────────────────
        self.gas_used_gallons  = 0.0   # cumulative gallons consumed
        self.last_fuel_save    = time.time()

        self._load_fuel_state()

        # ── Setup & launch ────────────────────────────────────────────────
        self._setup_gpio()
        self._create_ui()
        self._connect_obd()

        self.update_thread = threading.Thread(
            target=self._obd_update_loop, daemon=True
        )
        self.update_thread.start()

        # Escape exits (useful during development/testing)
        self.root.bind('<Escape>', lambda _e: self._cleanup_and_exit())

    # -----------------------------------------------------------------------
    # GPIO
    # -----------------------------------------------------------------------

    def _setup_gpio(self):
        """Configure relay GPIO pins. Active-LOW board: HIGH = relay OFF."""
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in RELAY_PINS.values():
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.HIGH)

    # -----------------------------------------------------------------------
    # OBD connection
    # -----------------------------------------------------------------------

    def _connect_obd(self):
        """Attempt to connect to the OBD-II adapter."""
        try:
            print("Connecting to OBD-II adapter…")
            self.connection = obd.OBD()
            status = self.connection.status()
            if status == OBDStatus.CAR_CONNECTED:
                print("Connected to vehicle.")
            else:
                print(f"OBD adapter found but vehicle not detected (status={status}).")
        except Exception as exc:
            print(f"OBD connection error: {exc}")
            self.connection = None

    # -----------------------------------------------------------------------
    # Fuel state persistence
    # -----------------------------------------------------------------------

    def _load_fuel_state(self):
        """
        Read saved fuel state from disk on startup.
        Falls back to a full tank if the file is missing or corrupt.
        """
        try:
            with open(FUEL_STATE_FILE, 'r') as fh:
                data = json.load(fh)
            self.gas_used_gallons = float(data.get('gas_used_gallons', 0.0))
            print(
                f"Loaded fuel state: {self.gas_used_gallons:.3f} gal used "
                f"({self._gallons_remaining:.2f} gal remaining)."
            )
        except FileNotFoundError:
            print("No fuel state file found — assuming a full tank.")
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            print(f"Fuel state file corrupt ({exc}) — assuming a full tank.")

    def _save_fuel_state(self):
        """Write current fuel state to disk (called every FUEL_SAVE_INTERVAL seconds)."""
        try:
            data = {
                'gas_used_gallons':  self.gas_used_gallons,
                'saved_at':          datetime.now().isoformat(),
            }
            # Write atomically via a temp file to avoid corruption on power loss
            tmp_path = FUEL_STATE_FILE + '.tmp'
            with open(tmp_path, 'w') as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_path, FUEL_STATE_FILE)
        except OSError as exc:
            print(f"Warning: could not save fuel state: {exc}")

    def _fill_tank(self):
        """Reset fuel consumption to zero (full tank) and persist immediately."""
        self.gas_used_gallons = 0.0
        self._save_fuel_state()
        print("Tank reset to full.")

    # -----------------------------------------------------------------------
    # Derived fuel properties
    # -----------------------------------------------------------------------

    @property
    def _gallons_remaining(self) -> float:
        return max(0.0, TANK_CAPACITY_GALLONS - self.gas_used_gallons)

    @property
    def _distance_to_empty_mi(self) -> float:
        """Estimated miles remaining based on current average MPG."""
        avg_mpg = self.vehicle_data['avg_mpg']
        if avg_mpg <= 0:
            return 0.0
        return self._gallons_remaining * avg_mpg

    @property
    def _fuel_percent(self) -> float:
        return self._gallons_remaining / TANK_CAPACITY_GALLONS

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _create_ui(self):
        """Build (or rebuild) the complete UI from scratch."""
        colors = COLORS['night' if self.night_mode else 'day']
        self.root.configure(bg=colors['bg'])

        main_frame = tk.Frame(self.root, bg=colors['bg'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        if self.show_trip_stats:
            self._create_trip_stats_view(main_frame, colors)
        else:
            self._create_gauge_view(main_frame, colors)

        self._create_control_bar(main_frame, colors)

    def _create_gauge_view(self, parent: tk.Frame, colors: dict):
        """Standard driving view: speed, RPM, and secondary gauges."""

        # ── Primary gauges: Speed and RPM ────────────────────────────────
        primary_row = tk.Frame(parent, bg=colors['bg'])
        primary_row.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self.speed_frame = self._make_large_gauge(primary_row, "SPEED", "MPH")
        self.speed_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        self.rpm_frame = self._make_large_gauge(primary_row, "RPM", "")
        self.rpm_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        # ── Secondary gauges: row 1 ───────────────────────────────────────
        secondary_row1 = tk.Frame(parent, bg=colors['bg'])
        secondary_row1.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        self.coolant_frame     = self._make_small_gauge(secondary_row1, "COOLANT",  "°F")
        self.coolant_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        self.load_frame        = self._make_small_gauge(secondary_row1, "ENG LOAD", "%")
        self.load_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        self.instant_mpg_frame = self._make_small_gauge(secondary_row1, "INST MPG", "mpg")
        self.instant_mpg_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        # ── Secondary gauges: row 2 ───────────────────────────────────────
        secondary_row2 = tk.Frame(parent, bg=colors['bg'])
        secondary_row2.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        self.intake_frame      = self._make_small_gauge(secondary_row2, "INTAKE",   "°F")
        self.intake_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        self.avg_mpg_frame     = self._make_small_gauge(secondary_row2, "AVG MPG",  "mpg")
        self.avg_mpg_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        self.dte_frame         = self._make_small_gauge(secondary_row2, "RANGE",    "mi")
        self.dte_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

    def _create_trip_stats_view(self, parent: tk.Frame, colors: dict):
        """Trip statistics + distance-to-empty view."""
        outer = tk.Frame(parent, bg=colors['bg'])
        outer.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # Title
        tk.Label(
            outer,
            text="TRIP STATISTICS",
            font=tkfont.Font(size=24, weight='bold'),
            bg=colors['bg'],
            fg=colors['accent'],
        ).pack(pady=(10, 12))

        # ── Trip stat cards ───────────────────────────────────────────────
        cards_frame = tk.Frame(outer, bg=colors['bg'])
        cards_frame.pack(fill=tk.BOTH, expand=True)

        self.trip_duration_frame = self._make_stat_card(cards_frame, "TRIP DURATION",    "00:00:00")
        self.trip_duration_frame.pack(fill=tk.BOTH, expand=True, pady=4)

        self.trip_distance_frame = self._make_stat_card(cards_frame, "DISTANCE TRAVELED","0.0 mi")
        self.trip_distance_frame.pack(fill=tk.BOTH, expand=True, pady=4)

        self.trip_avg_mpg_frame  = self._make_stat_card(cards_frame, "AVERAGE MPG",      "0.0 mpg")
        self.trip_avg_mpg_frame.pack(fill=tk.BOTH, expand=True, pady=4)

        # ── Fuel / Distance-to-empty card ─────────────────────────────────
        self.dte_card_frame = self._make_dte_card(cards_frame, colors)
        self.dte_card_frame.pack(fill=tk.BOTH, expand=True, pady=4)

        # ── Action buttons ────────────────────────────────────────────────
        btn_row = tk.Frame(outer, bg=colors['bg'])
        btn_row.pack(pady=(12, 0))

        tk.Button(
            btn_row,
            text="← BACK",
            command=self._toggle_trip_view,
            font=tkfont.Font(size=16, weight='bold'),
            bg=colors['button_bg'],
            fg=colors['text'],
            activebackground=colors['accent'],
            relief=tk.RAISED,
            bd=3,
            height=2,
            width=12,
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_row,
            text="RESET TRIP",
            command=self._reset_trip,
            font=tkfont.Font(size=16, weight='bold'),
            bg=colors['button_bg'],
            fg=colors['text'],
            activebackground=colors['warning'],
            relief=tk.RAISED,
            bd=3,
            height=2,
            width=12,
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_row,
            text="⛽ FILL TANK",
            command=self._fill_tank_and_refresh,
            font=tkfont.Font(size=16, weight='bold'),
            bg=colors['button_bg'],
            fg=colors['text'],
            activebackground=colors['dte_ok'],
            relief=tk.RAISED,
            bd=3,
            height=2,
            width=12,
        ).pack(side=tk.LEFT, padx=5)

    def _make_dte_card(self, parent: tk.Frame, colors: dict) -> tk.Frame:
        """Create the distance-to-empty summary card for the trip stats view."""
        frame = tk.Frame(
            parent,
            bg=colors['gauge_bg'],
            relief=tk.SOLID,
            bd=2,
            highlightbackground=colors['gauge_border'],
            highlightthickness=2,
        )

        # Two columns side by side
        left  = tk.Frame(frame, bg=colors['gauge_bg'])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=6)
        right = tk.Frame(frame, bg=colors['gauge_bg'])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=6)

        # Left: distance to empty
        tk.Label(left, text="RANGE",
                 font=tkfont.Font(size=13, weight='bold'),
                 bg=colors['gauge_bg'], fg=colors['text']).pack()
        self.dte_value_label = tk.Label(
            left, text="--- mi",
            font=tkfont.Font(size=30, weight='bold'),
            bg=colors['gauge_bg'], fg=colors['dte_ok'],
        )
        self.dte_value_label.pack()

        # Right: fuel remaining
        tk.Label(right, text="FUEL REMAINING",
                 font=tkfont.Font(size=13, weight='bold'),
                 bg=colors['gauge_bg'], fg=colors['text']).pack()
        self.fuel_remaining_label = tk.Label(
            right, text="--- gal",
            font=tkfont.Font(size=30, weight='bold'),
            bg=colors['gauge_bg'], fg=colors['dte_ok'],
        )
        self.fuel_remaining_label.pack()

        return frame

    def _create_control_bar(self, parent: tk.Frame, colors: dict):
        """Bottom row of control buttons, always visible."""
        bar = tk.Frame(parent, bg=colors['bg'])
        bar.pack(fill=tk.X, pady=(8, 0))

        # Light toggle buttons
        self.pod_btn   = self._make_relay_button(bar, "Pod Lights", 'pod_lights')
        self.pod_btn.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        self.amber_btn = self._make_relay_button(bar, "Amber Bar",  'amber_bar')
        self.amber_btn.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        self.white_btn = self._make_relay_button(bar, "White Bar",  'white_bar')
        self.white_btn.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        # Trip stats toggle
        tk.Button(
            bar,
            text="TRIP",
            command=self._toggle_trip_view,
            font=tkfont.Font(size=14, weight='bold'),
            bg=colors['button_bg'],
            fg=colors['text'],
            activebackground=colors['accent'],
            relief=tk.RAISED,
            bd=3,
            height=3,
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

        # Day / night toggle
        mode_label = "🌙 NIGHT" if self.night_mode else "☀️ DAY"
        tk.Button(
            bar,
            text=mode_label,
            command=self._toggle_day_night,
            font=tkfont.Font(size=14, weight='bold'),
            bg=colors['button_bg'],
            fg=colors['text'],
            activebackground=colors['accent'],
            relief=tk.RAISED,
            bd=3,
            height=3,
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)

    # -----------------------------------------------------------------------
    # Widget factories
    # -----------------------------------------------------------------------

    def _make_large_gauge(self, parent: tk.Frame, title: str, unit: str) -> tk.Frame:
        colors = COLORS['night' if self.night_mode else 'day']
        frame = tk.Frame(
            parent, bg=colors['gauge_bg'], relief=tk.SOLID, bd=2,
            highlightbackground=colors['gauge_border'], highlightthickness=2,
        )
        tk.Label(frame, text=title,
                 font=tkfont.Font(size=18, weight='bold'),
                 bg=colors['gauge_bg'], fg=colors['text']).pack(pady=(10, 0))

        value_lbl = tk.Label(frame, text="---",
                             font=tkfont.Font(size=72, weight='bold'),
                             bg=colors['gauge_bg'], fg=colors['accent'])
        value_lbl.pack(expand=True)

        tk.Label(frame, text=unit,
                 font=tkfont.Font(size=16),
                 bg=colors['gauge_bg'], fg=colors['text']).pack(pady=(0, 10))

        frame.value_label = value_lbl
        return frame

    def _make_small_gauge(self, parent: tk.Frame, title: str, unit: str) -> tk.Frame:
        colors = COLORS['night' if self.night_mode else 'day']
        frame = tk.Frame(
            parent, bg=colors['gauge_bg'], relief=tk.SOLID, bd=1,
            highlightbackground=colors['gauge_border'], highlightthickness=1,
        )
        tk.Label(frame, text=title,
                 font=tkfont.Font(size=10, weight='bold'),
                 bg=colors['gauge_bg'], fg=colors['text']).pack(pady=(5, 0))

        value_lbl = tk.Label(frame, text="---",
                             font=tkfont.Font(size=28, weight='bold'),
                             bg=colors['gauge_bg'], fg=colors['accent'])
        value_lbl.pack(expand=True)

        tk.Label(frame, text=unit,
                 font=tkfont.Font(size=9),
                 bg=colors['gauge_bg'], fg=colors['text']).pack(pady=(0, 5))

        frame.value_label = value_lbl
        return frame

    def _make_stat_card(self, parent: tk.Frame, label: str, initial: str) -> tk.Frame:
        colors = COLORS['night' if self.night_mode else 'day']
        frame = tk.Frame(
            parent, bg=colors['gauge_bg'], relief=tk.SOLID, bd=2,
            highlightbackground=colors['gauge_border'], highlightthickness=2,
        )
        tk.Label(frame, text=label,
                 font=tkfont.Font(size=14, weight='bold'),
                 bg=colors['gauge_bg'], fg=colors['text']).pack(pady=(8, 2))

        value_lbl = tk.Label(frame, text=initial,
                             font=tkfont.Font(size=36, weight='bold'),
                             bg=colors['gauge_bg'], fg=colors['accent'])
        value_lbl.pack(pady=(2, 8))

        frame.value_label = value_lbl
        return frame

    def _make_relay_button(self, parent: tk.Frame, label: str, relay_key: str) -> tk.Button:
        """Create a latching ON/OFF button that controls a relay."""
        colors = COLORS['night' if self.night_mode else 'day']
        btn = tk.Button(
            parent,
            text=f"{label}\nOFF",
            font=tkfont.Font(size=14, weight='bold'),
            bg=colors['button_bg'],
            fg=colors['text'],
            activebackground=colors['button_active'],
            relief=tk.RAISED,
            bd=3,
            height=3,
        )
        btn.config(command=lambda: self._toggle_relay(relay_key, btn))
        return btn

    # -----------------------------------------------------------------------
    # UI interactions
    # -----------------------------------------------------------------------

    def _toggle_relay(self, relay_key: str, button: tk.Button):
        """Flip a relay and update the button to reflect the new state."""
        self.relay_states[relay_key] = not self.relay_states[relay_key]
        is_on = self.relay_states[relay_key]

        GPIO.output(RELAY_PINS[relay_key], GPIO.LOW if is_on else GPIO.HIGH)

        colors = COLORS['night' if self.night_mode else 'day']
        name   = button.cget('text').split('\n')[0]   # preserve the label text
        if is_on:
            button.config(text=f"{name}\nON",  bg=colors['button_active'], relief=tk.SUNKEN)
        else:
            button.config(text=f"{name}\nOFF", bg=colors['button_bg'],     relief=tk.RAISED)

    def _toggle_trip_view(self):
        self.show_trip_stats = not self.show_trip_stats
        self._rebuild_ui()

    def _toggle_day_night(self):
        self.night_mode = not self.night_mode
        self._rebuild_ui()

    def _reset_trip(self):
        """Clear trip timer, distance, and MPG history."""
        self.trip_start_time  = datetime.now()
        self.trip_distance_mi = 0.0
        self.mpg_history.clear()
        print("Trip statistics reset.")

    def _fill_tank_and_refresh(self):
        """Reset the tank to full and refresh the UI so the DTE updates immediately."""
        self._fill_tank()
        self._rebuild_ui()

    def _rebuild_ui(self):
        """Destroy and recreate all widgets (used after mode/view changes)."""
        for widget in self.root.winfo_children():
            widget.destroy()
        self._create_ui()

    # -----------------------------------------------------------------------
    # OBD polling loop (background thread)
    # -----------------------------------------------------------------------

    def _obd_update_loop(self):
        """
        Continuously query the OBD-II adapter.
        Runs in a daemon thread; updates vehicle_data then schedules a UI refresh.
        Also handles periodic saving of the fuel state.
        """
        while self.running:
            if self.connection and self.connection.status() == OBDStatus.CAR_CONNECTED:
                try:
                    self._poll_obd()
                except Exception as exc:
                    print(f"OBD polling error: {exc}")

            # Save fuel state every FUEL_SAVE_INTERVAL seconds
            if time.time() - self.last_fuel_save >= FUEL_SAVE_INTERVAL:
                self._save_fuel_state()
                self.last_fuel_save = time.time()

            # Schedule display update on the main (Tkinter) thread
            self.root.after(0, self._update_display)

            time.sleep(0.5)  # poll at ~2 Hz

    def _poll_obd(self):
        """Read all OBD-II sensors and update vehicle_data / fuel tracking."""
        speed_resp   = self.connection.query(obd.commands.SPEED)
        rpm_resp     = self.connection.query(obd.commands.RPM)
        coolant_resp = self.connection.query(obd.commands.COOLANT_TEMP)
        load_resp    = self.connection.query(obd.commands.ENGINE_LOAD)
        intake_resp  = self.connection.query(obd.commands.INTAKE_TEMP)
        maf_resp     = self.connection.query(obd.commands.MAF)

        # Speed (apply tire-size correction)
        if not speed_resp.is_null():
            raw_mph = speed_resp.value.to('mph').magnitude
            self.vehicle_data['speed'] = raw_mph * TIRE_CORRECTION_FACTOR

        if not rpm_resp.is_null():
            self.vehicle_data['rpm'] = rpm_resp.value.magnitude

        if not coolant_resp.is_null():
            self.vehicle_data['coolant_temp'] = coolant_resp.value.to('degF').magnitude

        if not load_resp.is_null():
            self.vehicle_data['engine_load'] = load_resp.value.magnitude

        if not intake_resp.is_null():
            self.vehicle_data['intake_temp'] = intake_resp.value.to('degF').magnitude

        # ── Distance tracking ─────────────────────────────────────────────
        now        = time.time()
        elapsed_hr = (now - self.last_obd_time) / 3600.0
        speed      = self.vehicle_data['speed']

        if speed > 0 and elapsed_hr > 0:
            self.trip_distance_mi += speed * elapsed_hr

        self.last_obd_time = now

        # ── MPG + fuel consumption ────────────────────────────────────────
        if not maf_resp.is_null() and speed > 0:
            maf_g_per_s = maf_resp.value.magnitude   # grams/second

            if maf_g_per_s > 0:
                # Instantaneous MPG:
                #   (speed mph * 7.107 conversion factor) / MAF g/s
                instant_mpg = (speed * 7.107) / maf_g_per_s
                self.vehicle_data['instant_mpg'] = min(instant_mpg, 99.9)
                self.mpg_history.append(instant_mpg)

                # Average MPG over the trip
                self.vehicle_data['avg_mpg'] = (
                    sum(self.mpg_history) / len(self.mpg_history)
                )

                # Fuel consumed this interval:
                #   MAF (g/s) → lb/hr → gal/hr (gasoline ≈ 6.17 lb/gal)
                #   gal/hr × elapsed_hr = gallons used
                lb_per_hr  = maf_g_per_s * 0.002205 * 3600
                gal_per_hr = lb_per_hr / 6.17
                self.gas_used_gallons += gal_per_hr * elapsed_hr

    # -----------------------------------------------------------------------
    # Display update (called on main thread)
    # -----------------------------------------------------------------------

    def _update_display(self):
        try:
            if self.show_trip_stats:
                self._refresh_trip_stats()
            else:
                self._refresh_gauges()
        except AttributeError:
            # Widgets may not exist during a UI rebuild — safe to ignore
            pass

    def _refresh_gauges(self):
        """Push current vehicle_data values into the gauge widgets."""
        d = self.vehicle_data
        self.speed_frame.value_label.config(text=f"{int(d['speed'])}")
        self.rpm_frame.value_label.config(text=f"{int(d['rpm'])}")
        self.coolant_frame.value_label.config(text=f"{int(d['coolant_temp'])}")
        self.load_frame.value_label.config(text=f"{int(d['engine_load'])}")
        self.instant_mpg_frame.value_label.config(text=f"{d['instant_mpg']:.1f}")
        self.avg_mpg_frame.value_label.config(text=f"{d['avg_mpg']:.1f}")
        self.intake_frame.value_label.config(text=f"{int(d['intake_temp'])}")

        # Distance-to-empty gauge with colour coding
        dte = self._distance_to_empty_mi
        dte_color = self._dte_color(dte)
        self.dte_frame.value_label.config(
            text=f"{int(dte)}",
            fg=dte_color,
        )

    def _refresh_trip_stats(self):
        """Update trip statistics cards and the DTE card."""
        # Trip duration
        elapsed = datetime.now() - self.trip_start_time
        total_s = int(elapsed.total_seconds())
        h, rem  = divmod(total_s, 3600)
        m, s    = divmod(rem, 60)

        if hasattr(self, 'trip_duration_frame'):
            self.trip_duration_frame.value_label.config(text=f"{h:02d}:{m:02d}:{s:02d}")

        if hasattr(self, 'trip_distance_frame'):
            self.trip_distance_frame.value_label.config(
                text=f"{self.trip_distance_mi:.1f} mi"
            )

        avg_mpg = self.vehicle_data['avg_mpg']
        if hasattr(self, 'trip_avg_mpg_frame'):
            self.trip_avg_mpg_frame.value_label.config(text=f"{avg_mpg:.1f} mpg")

        # Distance-to-empty card
        dte       = self._distance_to_empty_mi
        gal_left  = self._gallons_remaining
        dte_color = self._dte_color(dte)

        if hasattr(self, 'dte_value_label'):
            self.dte_value_label.config(text=f"{int(dte)} mi", fg=dte_color)

        if hasattr(self, 'fuel_remaining_label'):
            self.fuel_remaining_label.config(text=f"{gal_left:.1f} gal", fg=dte_color)

    def _dte_color(self, dte_miles: float) -> str:
        """Return a colour string based on the estimated range remaining."""
        colors = COLORS['night' if self.night_mode else 'day']
        if dte_miles < 30:
            return colors['dte_low']
        if dte_miles < 75:
            return colors['dte_mid']
        return colors['dte_ok']

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------

    def _cleanup_and_exit(self):
        """Save state, turn off relays, and exit cleanly."""
        print("Shutting down…")
        self.running = False

        self._save_fuel_state()

        for pin in RELAY_PINS.values():
            GPIO.output(pin, GPIO.HIGH)   # active-LOW: HIGH = OFF
        GPIO.cleanup()

        if self.connection:
            self.connection.close()

        self.root.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    OBDDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()