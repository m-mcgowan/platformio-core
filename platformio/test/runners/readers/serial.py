# Copyright (c) 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
from time import sleep

import click
import serial

from platformio.device.finder import SerialPortFinder
from platformio.exception import UserSideException


class SerialTestOutputReader:
    SERIAL_TIMEOUT = 600

    def __init__(self, test_runner):
        self.test_runner = test_runner
        self.ser = None

    def _create_serial(self):
        ser = serial.serial_for_url(
            self.resolve_test_port(),
            do_not_open=True,
            baudrate=self.test_runner.get_test_speed(),
            timeout=self.SERIAL_TIMEOUT,
        )
        ser.rts = self.test_runner.options.monitor_rts
        ser.dtr = self.test_runner.options.monitor_dtr
        return ser

    def reset_serial(self, ser):
        ser.flushInput()
        ser.setDTR(False)
        ser.setRTS(False)
        sleep(0.1)
        ser.setDTR(True)
        ser.setRTS(True)
        sleep(0.1)

    def begin(self):
        click.echo(
            "If you don't see any output for the first 10 secs, "
            "please reset board (press reset button)"
        )
        click.echo()

        try:
            ser = self._create_serial()
            ser.open()
        except serial.SerialException as exc:
            click.secho(str(exc), fg="red", err=True)
            return

        if not self.test_runner.options.no_reset:
            self.reset_serial(ser)

        self.ser = ser
        try:
            while not self.test_runner.test_suite.is_finished():
                self.test_runner.on_testing_data_output(ser.read(ser.in_waiting or 1))
            ser.close()
        finally:
            self.ser = None

    def on_testing_line_output(self, line):
        args = line.strip().split()
        if len(args) >= 1:
            cmd = args[0]
            if self.test_runner.options.allow_disconnect and cmd == "disconnect:":
                reconnect_ms = (
                    float(args[1]) if len(args) >= 2 else self.SERIAL_TIMEOUT * 1000.0
                )
                self.handle_reconnect((reconnect_ms / 1000))
                return ""
        return line

    def loop_until_condition_or_timeout(
        self, condition, timeout_seconds, condition_check_interval=0.1
    ):
        start_time = time.time()
        end_time = start_time + timeout_seconds

        while time.time() < end_time:
            # Replace this with your actual condition check
            if condition():
                return True

            # Add a small delay to prevent busy-waiting
            time.sleep(condition_check_interval)
        return condition()

    def handle_reconnect(self, reconnect_timeout):
        ser = self.ser
        ser.write(b"disconnected:\n")
        ser.flush()
        ser.close()
        last_exception = None

        def try_open(ser):
            try:
                ser.open()
                return True
            except serial.SerialException as exc:
                nonlocal last_exception
                last_exception = exc
                return False

        def is_unavailable(ser):
            if try_open(ser):
                ser.close()
                return False
            nonlocal last_exception
            last_exception = None  # don't care about this
            return True

        # wait for the port to be closed by the device
        start_time = time.time()
        self.loop_until_condition_or_timeout(
            lambda: is_unavailable(ser), reconnect_timeout
        )
        remaining = reconnect_timeout - (time.time() - start_time) + 1
        remaining = max(remaining, 2)
        if not self.loop_until_condition_or_timeout(lambda: try_open(ser), remaining):
            raise last_exception or serial.SerialTimeoutException(
                "Timeout waiting for serial to reconnect."
            )
        ser.write(b"connect:\n")  # tell the device we are ready to receive
        ser.flush()

    def resolve_test_port(self):
        project_options = self.test_runner.project_config.items(
            env=self.test_runner.test_suite.env_name, as_dict=True
        )
        port = SerialPortFinder(
            board_config=self.test_runner.platform.board_config(
                project_options["board"]
            ),
            upload_protocol=project_options.get("upload_protocol"),
            ensure_ready=True,
            verbose=self.test_runner.options.verbose,
        ).find(initial_port=self.test_runner.get_test_port())
        if port:
            return port
        raise UserSideException(
            "Please specify `test_port` for environment or use "
            "global `--test-port` option."
        )
