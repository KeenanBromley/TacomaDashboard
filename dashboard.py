#!/usr/bin/env python3
"""Console dashboard for Raspberry Pi OS Lite.

Shows live OBD-II data, trip statistics, fuel range, and relay controls in a
terminal-only interface so the Pi can boot without a desktop environment.
"""

from __future__ import annotations

import curses
import json
import os
import signal
import threading
import time
from collections import deque
from contextlib import suppress
from datetime import datetime

import obd
from obd import OBDStatus

try:
    import RPi.GPIO as GPIO
except Exception:  # pragma: no cover - keeps the file importable off-Pi
    class _DummyGPIO:
        BCM = OUT = HIGH = LOW = None

        def setmode(self, *_args, **_kwargs):
            pass

        def setwarnings(self, *_args, **_kwargs):
            pass

        def setup(self, *_args, **_kwargs):
            pass

        def output(self, *_args, **_kwargs):
            pass

        def cleanup(self):
            pass

    GPIO = _DummyGPIO()


RELAY_PINS = {
    'pod_lights': 17,
    'amber_bar': 18,
    'white_bar': 27,
}

RELAY_LABELS = {
    'pod_lights': 'Pod Lights',
    'amber_bar': 'Amber Bar',
    'white_bar': 'White Bar',
}

TIRE_CORRECTION_FACTOR = 1.0686
TANK_CAPACITY_GALLONS = 21.0
FUEL_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fuel_state.json')
FUEL_SAVE_INTERVAL = 30


def _format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


class OBDDashboardConsole:
    def __init__(self):
        self.running = True
        self.state_lock = threading.Lock()

        self.night_mode = False
        self.show_trip_stats = False

        self.connection = None
        self.connection_status = 'Connecting...'
        self.last_connection_attempt = 0.0

        self.relay_states = {key: False for key in RELAY_PINS}
        self.gpio_available = True

        self.vehicle_data = {
            'speed': 0.0,
            'rpm': 0,
            'coolant_temp': 0,
            'engine_load': 0,
            'intake_temp': 0,
            'instant_mpg': 0.0,
            'avg_mpg': 0.0,
        }

        self.mpg_history: deque[float] = deque()
        self.trip_start_time = datetime.now()
        self.trip_distance_mi = 0.0
        self.last_obd_time = time.time()

        self.gas_used_gallons = 0.0
        self.last_fuel_save = time.time()

        self.status_message = 'Starting dashboard...'
        self._load_fuel_state()
        self._setup_gpio()
        self._connect_obd()

        self.update_thread = threading.Thread(target=self._obd_update_loop, daemon=True)
        self.update_thread.start()

    def _setup_gpio(self):
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in RELAY_PINS.values():
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.HIGH)
        except Exception as exc:
            self.gpio_available = False
            self.status_message = f'GPIO unavailable: {exc}'

    def _connect_obd(self):
        self.last_connection_attempt = time.time()
        try:
            self.connection = obd.OBD()
            status = self.connection.status()
            if status == OBDStatus.CAR_CONNECTED:
                self.connection_status = 'Vehicle connected'
                self.status_message = 'OBD-II adapter connected.'
            else:
                self.connection_status = f'Adapter ready, vehicle not detected ({status})'
                self.status_message = 'OBD-II adapter found, waiting for vehicle.'
        except Exception as exc:
            self.connection = None
            self.connection_status = 'OBD connection failed'
            self.status_message = f'OBD connection error: {exc}'

    def _load_fuel_state(self):
        try:
            with open(FUEL_STATE_FILE, 'r') as handle:
                data = json.load(handle)
            self.gas_used_gallons = float(data.get('gas_used_gallons', 0.0))
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            self.gas_used_gallons = 0.0

    def _save_fuel_state(self):
        try:
            data = {
                'gas_used_gallons': self.gas_used_gallons,
                'saved_at': datetime.now().isoformat(),
            }
            tmp_path = FUEL_STATE_FILE + '.tmp'
            with open(tmp_path, 'w') as handle:
                json.dump(data, handle, indent=2)
            os.replace(tmp_path, FUEL_STATE_FILE)
        except OSError as exc:
            self.status_message = f'Fuel save failed: {exc}'

    def _fill_tank(self):
        self.gas_used_gallons = 0.0
        self._save_fuel_state()

    @property
    def _gallons_remaining(self) -> float:
        return max(0.0, TANK_CAPACITY_GALLONS - self.gas_used_gallons)

    @property
    def _distance_to_empty_mi(self) -> float:
        avg_mpg = self.vehicle_data['avg_mpg']
        if avg_mpg <= 0:
            return 0.0
        return self._gallons_remaining * avg_mpg

    def _toggle_relay(self, relay_key: str):
        if not self.gpio_available:
            self.status_message = 'GPIO is unavailable on this system.'
            return

        self.relay_states[relay_key] = not self.relay_states[relay_key]
        is_on = self.relay_states[relay_key]
        GPIO.output(RELAY_PINS[relay_key], GPIO.LOW if is_on else GPIO.HIGH)
        self.status_message = f"{RELAY_LABELS[relay_key]} {'ON' if is_on else 'OFF'}"

    def _reset_trip(self):
        self.trip_start_time = datetime.now()
        self.trip_distance_mi = 0.0
        self.mpg_history.clear()
        self.status_message = 'Trip statistics reset.'

    def _toggle_trip_view(self):
        self.show_trip_stats = not self.show_trip_stats

    def _toggle_night_mode(self):
        self.night_mode = not self.night_mode

    def _obd_update_loop(self):
        while self.running:
            try:
                if self.connection and self.connection.status() == OBDStatus.CAR_CONNECTED:
                    self._poll_obd()
                elif time.time() - self.last_connection_attempt >= 10:
                    self._connect_obd()
            except Exception as exc:
                self.connection_status = 'OBD polling error'
                self.status_message = f'OBD polling error: {exc}'

            if time.time() - self.last_fuel_save >= FUEL_SAVE_INTERVAL:
                self._save_fuel_state()
                self.last_fuel_save = time.time()

            time.sleep(0.5)

    def _poll_obd(self):
        speed_resp = self.connection.query(obd.commands.SPEED)
        rpm_resp = self.connection.query(obd.commands.RPM)
        coolant_resp = self.connection.query(obd.commands.COOLANT_TEMP)
        load_resp = self.connection.query(obd.commands.ENGINE_LOAD)
        intake_resp = self.connection.query(obd.commands.INTAKE_TEMP)
        maf_resp = self.connection.query(obd.commands.MAF)

        with self.state_lock:
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

            now = time.time()
            elapsed_hr = (now - self.last_obd_time) / 3600.0
            speed = self.vehicle_data['speed']

            if speed > 0 and elapsed_hr > 0:
                self.trip_distance_mi += speed * elapsed_hr

            self.last_obd_time = now

            if not maf_resp.is_null() and speed > 0:
                maf_g_per_s = maf_resp.value.magnitude
                if maf_g_per_s > 0:
                    instant_mpg = (speed * 7.107) / maf_g_per_s
                    self.vehicle_data['instant_mpg'] = min(instant_mpg, 99.9)
                    self.mpg_history.append(instant_mpg)
                    self.vehicle_data['avg_mpg'] = sum(self.mpg_history) / len(self.mpg_history)

                    lb_per_hr = maf_g_per_s * 0.002205 * 3600
                    gal_per_hr = lb_per_hr / 6.17
                    self.gas_used_gallons += gal_per_hr * elapsed_hr

    def _snapshot(self):
        with self.state_lock:
            return {
                'vehicle_data': dict(self.vehicle_data),
                'relay_states': dict(self.relay_states),
                'trip_distance_mi': self.trip_distance_mi,
                'trip_duration': datetime.now() - self.trip_start_time,
                'gas_used_gallons': self.gas_used_gallons,
                'gallons_remaining': self._gallons_remaining,
                'distance_to_empty_mi': self._distance_to_empty_mi,
                'night_mode': self.night_mode,
                'show_trip_stats': self.show_trip_stats,
                'connection_status': self.connection_status,
                'status_message': self.status_message,
            }

    def _safe_add(self, window, y, x, text, attr=0):
        height, width = window.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        try:
            window.addstr(y, x, str(text)[: max(0, width - x - 1)], attr)
        except curses.error:
            pass

    def _draw_horizontal_rule(self, window, y, width):
        if y < window.getmaxyx()[0]:
            try:
                window.hline(y, 0, curses.ACS_HLINE, max(0, width))
            except curses.error:
                pass

    def _render_metric_line(self, window, y, metrics, width, label_attr, value_attr):
        column_width = max(18, width // max(1, len(metrics)))
        x = 0
        for label, value in metrics:
            text = f'{label}: '
            self._safe_add(window, y, x, text, label_attr)
            self._safe_add(window, y, x + len(text), value, value_attr)
            x += column_width

    def _render(self, window):
        snapshot = self._snapshot()
        window.erase()
        height, width = window.getmaxyx()

        if width < 80 or height < 18:
            self._safe_add(window, 0, 0, 'TacomaDashboard', curses.A_BOLD)
            self._safe_add(window, 1, 0, 'Terminal too small. Resize for full dashboard.')
            self._safe_add(window, 3, 0, 'q quit | 1/2/3 relay toggle | t trip view | r reset trip | f fill tank | n theme')
            window.refresh()
            return

        header_attr = curses.A_REVERSE if snapshot['night_mode'] else curses.A_BOLD
        self._safe_add(
            window,
            0,
            0,
            f"TacomaDashboard | {snapshot['connection_status']} | Mode: {'TRIP' if snapshot['show_trip_stats'] else 'GAUGES'} | {snapshot['status_message']}",
            header_attr,
        )
        self._draw_horizontal_rule(window, 1, width)

        vehicle = snapshot['vehicle_data']
        if snapshot['show_trip_stats']:
            self._safe_add(window, 2, 0, 'TRIP STATISTICS', curses.A_BOLD)
            self._render_metric_line(
                window,
                4,
                [
                    ('Trip Time', _format_duration(int(snapshot['trip_duration'].total_seconds()))),
                    ('Trip Dist', f"{snapshot['trip_distance_mi']:.1f} mi"),
                    ('Avg MPG', f"{vehicle['avg_mpg']:.1f} mpg"),
                ],
                width,
                curses.A_BOLD,
                curses.A_NORMAL,
            )
            self._render_metric_line(
                window,
                6,
                [
                    ('Fuel Used', f"{snapshot['gas_used_gallons']:.2f} gal"),
                    ('Fuel Left', f"{snapshot['gallons_remaining']:.2f} gal"),
                    ('Range', f"{snapshot['distance_to_empty_mi']:.0f} mi"),
                ],
                width,
                curses.A_BOLD,
                curses.A_NORMAL,
            )
        else:
            self._safe_add(window, 2, 0, 'LIVE DATA', curses.A_BOLD)
            self._render_metric_line(
                window,
                4,
                [
                    ('Speed', f"{vehicle['speed']:.0f} mph"),
                    ('RPM', f"{vehicle['rpm']:.0f}"),
                    ('Coolant', f"{vehicle['coolant_temp']:.0f} F"),
                ],
                width,
                curses.A_BOLD,
                curses.A_NORMAL,
            )
            self._render_metric_line(
                window,
                6,
                [
                    ('Load', f"{vehicle['engine_load']:.0f}%"),
                    ('Intake', f"{vehicle['intake_temp']:.0f} F"),
                    ('Inst MPG', f"{vehicle['instant_mpg']:.1f}"),
                ],
                width,
                curses.A_BOLD,
                curses.A_NORMAL,
            )
            self._render_metric_line(
                window,
                8,
                [
                    ('Avg MPG', f"{vehicle['avg_mpg']:.1f}"),
                    ('Range', f"{snapshot['distance_to_empty_mi']:.0f} mi"),
                    ('Fuel Left', f"{snapshot['gallons_remaining']:.2f} gal"),
                ],
                width,
                curses.A_BOLD,
                curses.A_NORMAL,
            )

        self._draw_horizontal_rule(window, 10, width)
        relay_text = '  '.join(
            f"[{index + 1} {RELAY_LABELS[key]}: {'ON' if snapshot['relay_states'][key] else 'OFF'}]"
            for index, key in enumerate(RELAY_PINS)
        )
        self._safe_add(window, 11, 0, f'Relays: {relay_text}')
        self._safe_add(window, 13, 0, 'Controls: 1 pod lights | 2 amber bar | 3 white bar | t trip view | r reset trip | f fill tank | n theme | q quit')
        self._safe_add(window, 14, 0, 'Fuel state is saved automatically every 30 seconds.')
        window.refresh()

    def _handle_key(self, key):
        if key in (ord('q'), 27):
            self.request_exit()
        elif key == ord('1'):
            self._toggle_relay('pod_lights')
        elif key == ord('2'):
            self._toggle_relay('amber_bar')
        elif key == ord('3'):
            self._toggle_relay('white_bar')
        elif key in (ord('t'), ord('T')):
            self._toggle_trip_view()
            self.status_message = 'Trip view toggled.'
        elif key in (ord('r'), ord('R')):
            self._reset_trip()
        elif key in (ord('f'), ord('F')):
            self._fill_tank()
            self.status_message = 'Tank marked full.'
        elif key in (ord('n'), ord('N')):
            self._toggle_night_mode()
            self.status_message = 'Theme toggled.'

    def request_exit(self):
        self.running = False

    def _cleanup(self):
        self.running = False
        self._save_fuel_state()
        try:
            for pin in RELAY_PINS.values():
                GPIO.output(pin, GPIO.HIGH)
            GPIO.cleanup()
        except Exception:
            pass
        if self.connection:
            with suppress(Exception):
                self.connection.close()

    def run(self):
        signal.signal(signal.SIGINT, lambda _signum, _frame: self.request_exit())
        signal.signal(signal.SIGTERM, lambda _signum, _frame: self.request_exit())
        curses.wrapper(self._curses_main)
        self._cleanup()

    def _curses_main(self, window):
        with suppress(curses.error):
            curses.curs_set(0)
        window.nodelay(True)
        window.keypad(True)
        try:
            curses.use_default_colors()
        except curses.error:
            pass

        while self.running:
            self._render(window)
            key = window.getch()
            if key != -1:
                self._handle_key(key)
            time.sleep(0.1)


def main():
    dashboard = OBDDashboardConsole()
    dashboard.run()


if __name__ == '__main__':
    main()