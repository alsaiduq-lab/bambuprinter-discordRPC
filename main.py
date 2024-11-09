import argparse
import random
import time
import json
from pypresence import Presence
from icecream import ic
import paho.mqtt.client as mqtt
import ssl


class BambuLabPresence:
    def __init__(self, ip, access_code, serial, client_id):
        self.ip = ip
        self.access_code = access_code
        self.serial = serial
        self.client_id = client_id
        self.RPC = None
        self.mqtt_client = None
        self.printer_status = None
        self.last_update_time = 0
        self.update_interval = 15
        self.start_time = int(time.time())
        self.idle_messages = [
            "Printer Online - Ready to Print",
            "Waiting for Next Print Job",
            "Bambu Lab Printer Standing By",
            "System Ready"
        ]
        self.selected_idle_message = None
        self.status_mapping = {
            "IDLE": "🟢 Ready",
            "RUNNING": "🖨️ Printing",
            "PAUSE": "⏸️ Paused",
            "FINISH": "✅ Print Complete",
            "PREPARE": "🔄 Preparing",
            "HEAT": "♨️ Preheating",
            "HOME": "🏠 Homing",
            "CLEAN": "🧹 Auto-Cleaning",
            "CALIBRATE": "📏 Auto-Leveling",
            "FILAMENT": "🎯 Loading Filament",
            "UNLOAD_FILAMENT": "⏏️ Unloading Filament",
            "CHANGE_FILAMENT": "🔄 Changing Filament",
            "MANUAL_LEVELING": "🔧 Manual Leveling",
            "OFFLINE": "❌ Printer Offline",
            "UNKNOWN": "⚪ Status Unknown"
        }

        self.bed_temper = "???"
        self.nozzle_temper = "???"
        self.current_status = "IDLE"
        self.current_progress = 0
        self.last_known_file = None
        self.is_printing = False
        self.current_layer = 0
        self.total_layers = 0
        self.remaining_time = 0
        self.last_line_number = 0
        self.print_sub_stage = None
        self.print_stage = None
        self.last_idle_message_time = 0
        self.idle_message_interval = 600

    def update_idle_message(self):
        current_time = time.time()
        if (current_time - self.last_idle_message_time >= self.idle_message_interval or
                self.selected_idle_message is None):
            self.selected_idle_message = random.choice(self.idle_messages)
            self.last_idle_message_time = current_time
            ic(f"Updated idle message to: {self.selected_idle_message}")

    @staticmethod
    def format_temperature(temp):
        try:
            if temp is None:
                return "??? "
            temp_float = float(temp)
            return f"{int(round(temp_float))}"
        except (ValueError, TypeError):
            return "??? "

    def on_connect(self, client, userdata, flags, rc):
        ic(f"Connected with result code {rc}")
        client.subscribe(f"device/{self.serial}/report")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            ic("Received printer status")
            ic(payload)

            if 'print' in payload:
                print_data = payload['print']

                if 'mc_print_line_number' in print_data:
                    current_line = int(print_data['mc_print_line_number'])

                    if current_line > self.last_line_number:
                        self.is_printing = True
                        self.last_line_number = current_line

                if 'bed_temper' in print_data:
                    self.bed_temper = self.format_temperature(print_data['bed_temper'])
                    ic(f"Bed plate temperature: {self.bed_temper}")

                if 'nozzle_temper' in print_data:
                    self.nozzle_temper = self.format_temperature(print_data['nozzle_temper'])
                    ic(f"Nozzle temperature: {self.nozzle_temper}")

                if 'gcode_file' in print_data and print_data['gcode_file']:
                    self.last_known_file = print_data['gcode_file']

                if 'mc_percent' in print_data:
                    self.current_progress = float(print_data.get('mc_percent', 0))
                    ic(f"Progress: {self.current_progress}%")

                if 'layer_num' in print_data:
                    self.current_layer = print_data['layer_num']
                    ic(f"Current layer: {self.current_layer}")

                if 'mc_remaining_time' in print_data:
                    self.remaining_time = print_data['mc_remaining_time']
                    ic(f"Remaining time: {self.remaining_time} minutes")
                if 'gcode_state' in print_data:
                    self.current_status = print_data['gcode_state']
                    if print_data['gcode_state'] not in ['RUNNING', 'PAUSE']:
                        self.is_printing = False

        except Exception as e:
            ic(f"Error processing message: {e}")
            ic(f"Message payload: {msg.payload}")

    def connect_to_printer(self):
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.username_pw_set("bblp", password=self.access_code)
        self.mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)

        try:
            self.mqtt_client.connect(self.ip, 8883, 60)
            self.mqtt_client.loop_start()
            ic("Connected to printer MQTT")
        except Exception as e:
            ic(f"Failed to connect to printer: {e}")
            raise

    def initialize_discord(self):
        for pipe in range(10):
            try:
                self.RPC = Presence(self.client_id, pipe=pipe)
                self.RPC.connect()
                ic(f"Connected to Discord on pipe {pipe}")
                break
            except Exception as e:
                ic(f"Failed to connect on pipe {pipe}: {e}")
                continue

        if not self.RPC:
            raise Exception("Could not connect to Discord on any pipe")

    def update_status(self, print_data):
        if 'gcode_state' in print_data:
            self.current_status = print_data['gcode_state']
        if 'mc_print_stage' in print_data:
            self.print_stage = print_data['mc_print_stage']
        if 'mc_print_sub_stage' in print_data:
            self.print_sub_stage = print_data['mc_print_sub_stage']
        if 'ams_status' in print_data:
            ams_status = print_data['ams_status']
            if ams_status in [1, 2]:
                self.current_status = "CHANGE_FILAMENT"
        if self.current_status == "RUNNING":
            if self.print_stage == "1":  # Preparing
                if self.print_sub_stage == 1:
                    self.current_status = "HEAT"
                elif self.print_sub_stage == 2:
                    self.current_status = "HOME"
                elif self.print_sub_stage == 3:
                    self.current_status = "CLEAN"
                elif self.print_sub_stage == 4:
                    self.current_status = "CALIBRATE"
                elif self.print_sub_stage == 5:
                    self.current_status = "FILAMENT"
                else:
                    self.current_status = "PREPARE"
        if 'online' in print_data:
            online_status = print_data.get('online', {}).get('status', True)
            if not online_status:
                self.current_status = "OFFLINE"

    @staticmethod
    def create_progress_bar(progress, width=10):
        filled = int(width * (progress / 100))
        empty = width - filled
        return "█" * filled + "▒" * empty

    def update_presence(self):
        current_time = time.time()
        if current_time - self.last_update_time < self.update_interval:
            return

        try:
            status = self.status_mapping.get(self.current_status.upper(), "Status Unknown")

            if not self.is_printing:
                self.update_idle_message()
                details = self.selected_idle_message or "Printer Ready"
            else:
                if self.last_known_file:
                    filename = self.last_known_file.rsplit('.', 1)[0]
                    progress_bar = self.create_progress_bar(self.current_progress)
                    details = f"{filename} [{progress_bar}] {self.current_progress:.1f}%"
                else:
                    details = "Print in Progress"

            if not self.is_printing:
                state = f"🔧 {self.bed_temper}°C 🔥 {self.nozzle_temper}°C"
            else:
                state = f"🔧 {self.bed_temper}°C 🔥 {self.nozzle_temper}°C"
                if self.remaining_time > 0:
                    state += f" ⏱️ {self.remaining_time}min"

            ic(f"Updating Discord presence - Details: {details}, State: {state}")
            self.RPC.update(
                details=details,
                state=state,
                large_image="bambulab_logo",
                large_text=status,
                start=self.start_time,
            )

            self.last_update_time = current_time

        except Exception as e:
            ic(f"Error updating presence: {e}")

    def check_initial_state(self):
        try:
            # Request full printer status
            request = {
                "pushing": {
                    "sequence_id": "0",
                    "command": "pushall",
                    "version": 1,
                    "push_target": 1
                }
            }

            topic = f"device/{self.serial}/request"
            self.mqtt_client.publish(topic, json.dumps(request))
            time.sleep(2)

            ic("Requested initial printer state")

        except Exception as e:
            ic(f"Error checking initial state: {e}")

    def run(self):
        try:
            print("\n⚡ Initializing connection to printer...")
            self.connect_to_printer()
            self.initialize_discord()
            print("\n🔄 Requesting full printer status...")
            self.check_initial_state()

            print("\n🔒 Connection active - Press Ctrl+C to terminate")

            while True:
                self.update_presence()
                time.sleep(2)

        except KeyboardInterrupt:
            print("\n💫 Connection terminated...")
        except Exception as e:
            ic(f"Fatal error: {e}")
        finally:
            if self.RPC:
                self.RPC.close()
            if self.mqtt_client:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()


def main():
    print("""
╔═══════════════════════════════════════╗
║        BAMBU LAB PRINTER BRIDGE       ║
║         INITIALIZATION SEQUENCE       ║
╚═══════════════════════════════════════╝
    """)

    parser = argparse.ArgumentParser(description='Bambu Lab Presence')
    parser.add_argument('--ip', required=True, help='Your Bambu Lab printer IP address')
    parser.add_argument('--code', required=True, help='Your Bambu Lab printer access code')
    parser.add_argument('--serial', required=True, help='Your Bambu Lab printer serial number')
    parser.add_argument('--client', required=True,
                        help='Your Discord app client ID (from the discord developer portal)')

    args = parser.parse_args()

    presence = BambuLabPresence(args.ip, args.code, args.serial, args.client)
    presence.run()


if __name__ == "__main__":
    main()
