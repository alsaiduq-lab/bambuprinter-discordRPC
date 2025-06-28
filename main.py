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
        self.last_update_time = 0
        self.update_interval = 15
        self.start_time = int(time.time())
        self.idle_messages = [
            "Printer Online - Ready to Print",
            "Waiting for Next Print Job",
            "Bambu Lab Printer Standing By",
            "System Ready",
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
            "UNKNOWN": "⚪ Status Unknown",
            "QUEUED": "📋 Job Queued",
            "SLICING": "✂️ Preparing Print",
            "UPLOADING": "⬆️ Uploading File",
            "FAILED": "❌ Print Failed",
        }

        self.sequence_id = 0
        self.bed_temper = None
        self.bed_target_temper = None
        self.nozzle_temper = None
        self.nozzle_target_temper = None
        self.chamber_temper = None
        self.current_status = "IDLE"
        self.current_progress = 0
        self.last_known_file = None
        self.is_printing = False
        self.current_layer = 0
        self.total_layer_num = 0
        self.remaining_time = 0
        self.print_stage = None
        self.print_sub_stage = None
        self.print_error = 0
        self.last_temp_update = 0
        self.temp_timeout = 5

        self.ams_status = 0
        self.ams_rfid_status = 0
        self.current_tray = None

        self.gcode_state = "IDLE"
        self.gcode_start_time = "0"
        self.print_error = 0
        self.fail_reason = "0"

        self.upload_progress = 0
        self.upload_status = "idle"

        self.last_idle_message_time = 0
        self.idle_message_interval = 600

    def get_next_sequence_id(self):
        """Get next sequence ID and increment counter"""
        current_id = self.sequence_id
        self.sequence_id += 1
        return str(current_id)

    def update_idle_message(self):
        current_time = time.time()
        if (
            current_time - self.last_idle_message_time >= self.idle_message_interval
            or self.selected_idle_message is None
        ):
            self.selected_idle_message = random.choice(self.idle_messages)
            self.last_idle_message_time = current_time
            ic(f"Updated idle message to: {self.selected_idle_message}")

    def update_status(self, print_data):
        if "upload" in print_data:
            upload_data = print_data["upload"]
            if upload_data.get("status") != "idle":
                self.current_status = "UPLOADING"
                return

        if self.print_error != 0:
            self.current_status = f"ERROR_{self.print_error}"
            return

        if "ams_status" in print_data:
            ams_status = int(print_data.get("ams_status", 0))
            if ams_status in [1, 2]:
                self.current_status = "CHANGE_FILAMENT"
                return

        if self.gcode_state == "RUNNING":
            if self.print_stage == "1":
                stage_mapping = {1: "HEAT", 2: "HOME", 3: "CLEAN", 4: "CALIBRATE", 5: "FILAMENT"}
                self.current_status = stage_mapping.get(self.print_sub_stage, "PREPARE")
                return

        if "online" in print_data:
            online_data = print_data.get("online", {})
            if not online_data.get("status", True):
                self.current_status = "OFFLINE"
                return

        self.current_status = self.gcode_state

    @staticmethod
    def format_temperature(temp):
        try:
            if temp is None:
                return "??"
            temp_float = float(temp)
            return f"{int(round(temp_float))}"
        except (ValueError, TypeError):
            return "??"

    def handle_report_message(self, payload):
        if "print" not in payload:
            return

        print_data = payload["print"]
        current_time = time.time()

        if "gcode_state" in print_data:
            self.gcode_state = print_data.get("gcode_state", "UNKNOWN")
            self.current_status = self.gcode_state

        if "bed_temper" in print_data:
            self.bed_temper = float(print_data["bed_temper"])
            self.last_temp_update = current_time
        if "bed_target_temper" in print_data:
            self.bed_target_temper = float(print_data["bed_target_temper"])
        if "nozzle_temper" in print_data:
            self.nozzle_temper = float(print_data["nozzle_temper"])
            self.last_temp_update = current_time
        if "nozzle_target_temper" in print_data:
            self.nozzle_target_temper = float(print_data["nozzle_target_temper"])

        if "mc_percent" in print_data:
            self.current_progress = float(print_data.get("mc_percent", 0))
        if "mc_remaining_time" in print_data:
            self.remaining_time = int(print_data.get("mc_remaining_time", 0))
        if "layer_num" in print_data:
            self.current_layer = int(print_data.get("layer_num", 0))
        if "total_layer_num" in print_data:
            self.total_layer_num = int(print_data.get("total_layer_num", 0))

        if "print_error" in print_data:
            self.print_error = int(print_data.get("print_error", 0))

        if "gcode_file" in print_data:
            new_file = print_data["gcode_file"]
            if new_file:
                self.last_known_file = new_file

        if self.gcode_state in ["RUNNING", "PAUSE", "PREPARE"]:
            self.is_printing = True
        elif self.gcode_state in ["IDLE", "FAILED", "FINISH"]:
            self.is_printing = False

        if "mc_print_stage" in print_data:
            self.print_stage = print_data.get("mc_print_stage")
        if "mc_print_sub_stage" in print_data:
            self.print_sub_stage = print_data.get("mc_print_sub_stage", 0)

        self.update_status(print_data)

    def on_connect(self, client, userdata, flags, rc):
        ic(f"Connected with result code {rc}")
        client.subscribe(f"device/{self.serial}/report")

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

            bed_temp = self.format_temperature(self.bed_temper) if self.bed_temper is not None else "??"
            nozzle_temp = self.format_temperature(self.nozzle_temper) if self.nozzle_temper is not None else "??"

            if self.bed_target_temper is not None and float(self.bed_target_temper) > 0:
                bed_temp = f"{bed_temp}/{int(float(self.bed_target_temper))}"
            if self.nozzle_target_temper is not None and float(self.nozzle_target_temper) > 0:
                nozzle_temp = f"{nozzle_temp}/{int(float(self.nozzle_target_temper))}"

            state = f"🔧 {bed_temp}°C 🔥 {nozzle_temp}°C"

            if self.is_printing:
                if self.last_known_file:
                    filename = self.last_known_file.rsplit(".", 1)[0]
                    progress_bar = self.create_progress_bar(self.current_progress)
                    details = f"{filename} [{progress_bar}] {self.current_progress:.1f}%"
                else:
                    details = "Print in Progress"
            else:
                self.update_idle_message()
                if self.print_error != 0:
                    details = f"Error: {self.print_error}"
                else:
                    details = self.selected_idle_message or "Printer Ready"

            if self.is_printing and self.remaining_time > 0:
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
            request = {
                "pushing": {
                    "sequence_id": self.get_next_sequence_id(),
                    "command": "pushall",
                    "version": 1,
                    "push_target": 1,
                }
            }

            topic = f"device/{self.serial}/request"
            self.mqtt_client.publish(topic, json.dumps(request))
            ic("Requested initial printer state")

            time.sleep(2)

        except Exception as e:
            ic(f"Error checking initial state: {e}")

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

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            ic("Received message on topic:", msg.topic)
            ic(payload)

            if msg.topic.endswith("/task"):
                self.handle_task_message(payload)
            elif msg.topic.endswith("/report"):
                self.handle_report_message(payload)

        except Exception as e:
            ic(f"Error processing message: {e}")
            ic(f"Message payload: {msg.payload}")

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

    parser = argparse.ArgumentParser(description="Bambu Lab Presence")
    parser.add_argument("--ip", required=True, help="Your Bambu Lab printer IP address")
    parser.add_argument("--code", required=True, help="Your Bambu Lab printer access code")
    parser.add_argument("--serial", required=True, help="Your Bambu Lab printer serial number")
    parser.add_argument(
        "--client", required=True, help="Your Discord app client ID (from the discord developer portal)"
    )

    args = parser.parse_args()

    presence = BambuLabPresence(args.ip, args.code, args.serial, args.client)
    presence.run()


if __name__ == "__main__":
    main()
