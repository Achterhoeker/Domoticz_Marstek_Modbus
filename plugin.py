"""
<plugin key="Marstek_modbus"
        name="Marstek Venus Modbus"
        author="Simon Riemersma"
        version="1.5.0">
    <params>
        <param field="Address" label="Gateway IP Address" width="200px" required="true"/>
        <param field="Port" label="TCP Port" width="60px" default="502"/>
        <param field="Mode1" label="Modbus Slave ID" width="60px" default="1"/>
        <param field="Mode2" label="Domoticz API URL" width="250px" default="http://127.0.0.1:8080"/>
        <param field="Mode6" label="Poll interval (seconds)" width="75px" default="30"/>
    </params>
</plugin>
"""

import json
from datetime import datetime, timedelta
from urllib.request import urlopen

import Domoticz
from pymodbus.client import ModbusTcpClient


MODE_NAMES = {0: "Manual", 1: "Anti-feed", 2: "Trade"}
MODE_LEVELS = {0: 0, 1: 10, 2: 20}
LEVEL_TO_MODE = {0: 0, 10: 1, 20: 2}

IDLE_THRESHOLD_W = 10
SLOW_READ_INTERVAL = 30
EFFICIENCY_DAYS = 7
EFFICIENCY_MIN_CHARGE_KWH = 2.0


class BasePlugin:
    def __init__(self):
        self.counter = 0
        self.register_profile = None
        self.successful_reads = 0

        # Slowly changing values. None forces an initial read.
        self.cached_capacity = None
        self.cached_rs485 = None
        self.cached_cycle_count = None
        self.cached_max_charge_power = None
        self.cached_max_discharge_power = None
        self.cached_week_efficiency = None

    def onStart(self):
        # Units 22/23 must be real cumulative kWh devices.
        for unit in (22, 23):
            if unit in Devices and Devices[unit].SubType != 29:
                Domoticz.Log(
                    "Marstek Modbus: recreating Unit {} as kWh energy meter "
                    "(was Custom Sensor)".format(unit)
                )
                Devices[unit].Delete()

        # Units 14 and 16 previously contained calculated/fixed placeholder
        # values. Recreate them once with their new purpose and correct type.
        if 14 in Devices and Devices[14].Name != "7-Day Efficiency":
            Domoticz.Log(
                "Marstek Modbus: recreating Unit 14 as 7-Day Efficiency"
            )
            Devices[14].Delete()

        if 16 in Devices:
            is_cell_delta = (
                Devices[16].Name == "Cell Voltage Delta" and
                Devices[16].Type == 243 and
                Devices[16].SubType == 31
            )
            if not is_cell_delta:
                Domoticz.Log(
                    "Marstek Modbus: recreating Unit 16 as Cell Voltage Delta"
                )
                Devices[16].Delete()

        defs = [
            (1, "SOC", "Percentage"),
            (2, "Remaining Energy", "kWh"),
            (3, "Battery Voltage", "Voltage"),
            (4, "Battery Current", "CustomA"),
            (5, "Battery Power", "Usage"),
            (6, "Battery Temperature", "Temperature"),
            (7, "Current Mode", "Text"),
            (8, "Mode Selector", "Selector"),
            (9, "Battery Capacity", "kWh"),
            (10, "AC Power", "Usage"),
            (11, "Connection Status", "Text"),
            (12, "RS485 Control Status", "Text"),
            (13, "Cycle Count", "Custom"),
            (14, "7-Day Efficiency", "Percentage"),
            (15, "Charge/Discharge Direction", "Text"),
            (16, "Cell Voltage Delta", "CustomMV"),
            (17, "Internal Temperature", "Temperature"),
            (18, "MOS1 Temperature", "Temperature"),
            (19, "MOS2 Temperature", "Temperature"),
            (20, "Max Cell Voltage", "Voltage"),
            (21, "Min Cell Voltage", "Voltage"),
            (22, "Charge Energy", "EnergyCounter"),
            (23, "Discharge Energy", "EnergyCounter"),
            (24, "Max Charge Power", "PowerSetpoint"),
            (25, "Max Discharge Power", "PowerSetpoint"),
        ]

        for unit, name, typ in defs:
            if unit in Devices:
                continue
            if typ == "Text":
                Domoticz.Device(Name=name, Unit=unit, Type=243, Subtype=19).Create()
            elif typ == "Selector":
                Domoticz.Device(
                    Name=name, Unit=unit, TypeName="Selector Switch",
                    Options={
                        "LevelActions": "|||",
                        "LevelNames": "Manual|Anti-feed|Trade",
                        "LevelOffHidden": "false",
                        "SelectorStyle": "0"
                    }
                ).Create()
            elif typ == "CustomA":
                Domoticz.Device(
                    Name=name, Unit=unit, Type=243, Subtype=31,
                    Options={"Custom": "A"}
                ).Create()
            elif typ == "CustomMV":
                Domoticz.Device(
                    Name=name, Unit=unit, Type=243, Subtype=31,
                    Options={"Custom": "mV"}
                ).Create()
            elif typ == "Custom":
                Domoticz.Device(Name=name, Unit=unit, Type=243, Subtype=31).Create()
            elif typ == "kWh":
                Domoticz.Device(
                    Name=name, Unit=unit, Type=243, Subtype=31,
                    Options={"Custom": "kWh"}
                ).Create()
            elif typ == "EnergyCounter":
                Domoticz.Device(Name=name, Unit=unit, Type=243, Subtype=29).Create()
            elif typ == "PowerSetpoint":
                Domoticz.Device(
                    Name=name, Unit=unit, Type=242, Subtype=1,
                    Options={
                        "ValueStep": "100",
                        "ValueMin": "0",
                        "ValueMax": "2500",
                        "ValueUnit": "W"
                    }
                ).Create()
            elif typ == "Percentage":
                Domoticz.Device(Name=name, Unit=unit, TypeName="Percentage").Create()
            else:
                Domoticz.Device(Name=name, Unit=unit, TypeName=typ).Create()

        Domoticz.Heartbeat(10)
        Domoticz.Log(
            "Marstek Modbus: v1.5.0 started; grouped reads enabled; slow "
            "registers and 7-day efficiency update every {} successful polls".format(
                SLOW_READ_INTERVAL
            )
        )

    def client(self):
        return ModbusTcpClient(
            Parameters["Address"], port=int(Parameters["Port"])
        )

    def read_block(self, c, address, count):
        result = c.read_holding_registers(
            address=address,
            count=count,
            slave=int(Parameters["Mode1"])
        )
        if result.isError():
            raise RuntimeError(
                "Failed reading registers {}-{}: {}".format(
                    address, address + count - 1, result
                )
            )
        if len(result.registers) != count:
            raise RuntimeError(
                "Registers {}-{} returned {} values, expected {}".format(
                    address, address + count - 1,
                    len(result.registers), count
                )
            )
        return result.registers

    def read_u16(self, c, address):
        return self.read_block(c, address, 1)[0]

    @staticmethod
    def s16(value):
        return value - 65536 if value > 32767 else value

    @staticmethod
    def u32(registers, offset=0):
        return (registers[offset] << 16) | registers[offset + 1]

    def _is_illegal_address_error(self, error):
        text = str(error).lower()
        return (
            "exception_code=2" in text or
            "code=2" in text or
            "illegal data address" in text
        )

    def _detect_register_profile(self, c):
        try:
            raw = self.read_u16(c, 34002)
            if not 0 <= raw <= 1000:
                raise Exception(
                    "Implausible SOC raw value {} at 34002".format(raw)
                )
            self.register_profile = {
                "name": "V3-style",
                "soc_register": 34002,
                "soc_scale": 0.1,
                "cycle_register": 34003
            }
            Domoticz.Log(
                "Marstek Modbus: detected V3-style map (SOC 34002 x0.1)"
            )
            return
        except Exception as error:
            if not self._is_illegal_address_error(error):
                raise Exception(
                    "Register-map detection stopped: 34002 did not return a "
                    "valid value or explicit Illegal Data Address (02). "
                    "Treating this as a communication problem, not as Venus "
                    "E V2. Details: {}".format(error)
                )
            Domoticz.Log(
                "Marstek Modbus: 34002 returned Illegal Data Address (02); "
                "trying V2 SOC 32104."
            )

        raw = self.read_u16(c, 32104)
        if not 0 <= raw <= 100:
            raise Exception(
                "V2 SOC 32104 returned {}, expected 0..100".format(raw)
            )
        self.register_profile = {
            "name": "Venus E V2",
            "soc_register": 32104,
            "soc_scale": 1.0,
            "cycle_register": None
        }
        Domoticz.Log(
            "Marstek Modbus: detected Venus E V2 map "
            "(SOC 32104 x1.0; cycle count unavailable)"
        )

    def _ensure_register_profile(self, c):
        if self.register_profile is None:
            self._detect_register_profile(c)
        return self.register_profile

    def _read_slow_registers(self, c, profile):
        self.cached_capacity = self.read_u16(c, 32105) * 0.001
        self.cached_rs485 = self.read_u16(c, 42000)

        power_limit_regs = self.read_block(c, 44002, 2)
        self.cached_max_charge_power = power_limit_regs[0]
        self.cached_max_discharge_power = power_limit_regs[1]

        cycle_register = profile.get("cycle_register")
        if cycle_register is None:
            self.cached_cycle_count = None
        else:
            try:
                self.cached_cycle_count = self.read_u16(c, cycle_register)
            except Exception as error:
                Domoticz.Log(
                    "Marstek Modbus: Cycle Count unavailable: {}".format(error)
                )
                profile["cycle_register"] = None
                self.cached_cycle_count = None

    def _domoticz_api_url(self):
        base_url = (Parameters["Mode2"] or "").strip()
        return (base_url or "http://127.0.0.1:8080").rstrip("/")

    def _read_counter_history(self, unit):
        idx = int(Devices[unit].ID)
        url = (
            "{}/json.htm?type=graph&sensor=counter&idx={}&range=year"
        ).format(self._domoticz_api_url(), idx)

        with urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if payload.get("status") != "OK":
            raise RuntimeError(
                "Domoticz graph API returned status {} for Unit {}".format(
                    payload.get("status"), unit
                )
            )

        result = payload.get("result")
        if not isinstance(result, list):
            raise RuntimeError(
                "Domoticz graph API returned no history for Unit {}".format(unit)
            )

        history = {}
        for entry in result:
            date_text = entry.get("d")
            value_text = entry.get("v")
            if date_text is None or value_text is None:
                continue
            try:
                day = datetime.strptime(date_text[:10], "%Y-%m-%d").date()
                value = float(str(value_text).replace(",", "."))
            except (TypeError, ValueError):
                continue
            history[day] = value

        return history

    def _update_week_efficiency(self):
        try:
            charge_history = self._read_counter_history(22)
            discharge_history = self._read_counter_history(23)

            today = datetime.now().date()
            days = [
                today - timedelta(days=offset)
                for offset in range(1, EFFICIENCY_DAYS + 1)
            ]

            missing_days = [
                day for day in days
                if day not in charge_history or day not in discharge_history
            ]
            if missing_days:
                raise RuntimeError(
                    "not enough history; missing {} complete day(s)".format(
                        len(missing_days)
                    )
                )

            charge_kwh = sum(charge_history[day] for day in days)
            discharge_kwh = sum(discharge_history[day] for day in days)

            if charge_kwh < EFFICIENCY_MIN_CHARGE_KWH:
                raise RuntimeError(
                    "only {:.2f} kWh charged; minimum is {:.2f} kWh".format(
                        charge_kwh, EFFICIENCY_MIN_CHARGE_KWH
                    )
                )

            efficiency = discharge_kwh / charge_kwh * 100.0
            if not 0.0 <= efficiency <= 110.0:
                raise RuntimeError(
                    "implausible result {:.1f}% from {:.2f} kWh charge and "
                    "{:.2f} kWh discharge".format(
                        efficiency, charge_kwh, discharge_kwh
                    )
                )

            self.cached_week_efficiency = round(efficiency, 1)
            Devices[14].Update(0, str(self.cached_week_efficiency))

            Domoticz.Log(
                "Marstek Modbus: 7-day efficiency {:.1f}% from {:.2f} kWh "
                "charge and {:.2f} kWh discharge".format(
                    self.cached_week_efficiency,
                    charge_kwh,
                    discharge_kwh
                )
            )

        except Exception as error:
            Domoticz.Log(
                "Marstek Modbus: 7-day efficiency unavailable: {}".format(error)
            )

    def onCommand(self, Unit, Command, Level, Hue):
        if Unit not in (8, 24, 25):
            return

        c = self.client()
        if not c.connect():
            Domoticz.Error(
                "Marstek Modbus: unable to connect for command on Unit {}".format(Unit)
            )
            return

        try:
            if Unit == 8:
                register = 43000
                value = LEVEL_TO_MODE.get(Level, 0)
                description = "mode"
            else:
                register = 44002 if Unit == 24 else 44003
                value = int(round(float(Level)))
                value = max(0, min(2500, value))
                description = (
                    "maximum charge power" if Unit == 24
                    else "maximum discharge power"
                )

            result = c.write_register(
                address=register,
                value=value,
                slave=int(Parameters["Mode1"])
            )
            if result.isError():
                raise RuntimeError(
                    "Writing {} register {} failed: {}".format(
                        description, register, result
                    )
                )

            confirmed = self.read_u16(c, register)
            if confirmed != value:
                raise RuntimeError(
                    "{} write verification failed: requested {}, read back {}".format(
                        description, value, confirmed
                    )
                )

            if Unit == 8:
                Devices[8].Update(
                    nValue=1,
                    sValue=str(MODE_LEVELS.get(confirmed, 0))
                )
            else:
                Devices[Unit].Update(0, str(confirmed))
                if Unit == 24:
                    self.cached_max_charge_power = confirmed
                else:
                    self.cached_max_discharge_power = confirmed

            Domoticz.Log(
                "Marstek Modbus: {} set to {}{}".format(
                    description, confirmed, " W" if Unit in (24, 25) else ""
                )
            )

        except Exception as error:
            Domoticz.Error(
                "Marstek Modbus: command failed: {}".format(error)
            )
        finally:
            c.close()

    def onHeartbeat(self):
        self.counter += 10
        interval = max(10, int(Parameters["Mode6"] or 10))
        if self.counter < interval:
            return
        self.counter = 0

        c = self.client()
        if not c.connect():
            Devices[11].Update(0, "Disconnected")
            return

        try:
            profile = self._ensure_register_profile(c)

            # Seven grouped Modbus requests per normal poll.
            power_regs = self.read_block(c, 30001, 6)
            electrical_regs = self.read_block(c, 30100, 2)
            soc_raw = self.read_u16(c, profile["soc_register"])
            energy_regs = self.read_block(c, 33000, 4)
            temperature_regs = self.read_block(c, 35000, 3)
            cell_regs = self.read_block(c, 37007, 2)
            mode = self.read_u16(c, 43000)

            slow_read_due = (
                self.cached_capacity is None or
                self.cached_rs485 is None or
                self.cached_max_charge_power is None or
                self.cached_max_discharge_power is None or
                self.successful_reads % SLOW_READ_INTERVAL == 0
            )
            if slow_read_due:
                self._read_slow_registers(c, profile)

            battery_power = self.s16(power_regs[0])
            ac_power = self.s16(power_regs[5])
            voltage = electrical_regs[0] / 100.0
            current = self.s16(electrical_regs[1]) / 10.0
            soc = soc_raw * profile["soc_scale"]

            lifetime_charge_kwh = self.u32(energy_regs, 0) * 0.01
            lifetime_discharge_kwh = self.u32(energy_regs, 2) * 0.01

            temperature = self.s16(temperature_regs[0]) / 10.0
            mos1_temperature = self.s16(temperature_regs[1]) / 10.0
            mos2_temperature = self.s16(temperature_regs[2]) / 10.0

            max_cell = cell_regs[0] / 1000.0
            min_cell = cell_regs[1] / 1000.0
            cell_delta_mv = round((max_cell - min_cell) * 1000.0)
            if not 0 <= cell_delta_mv <= 1000:
                raise RuntimeError(
                    "Implausible cell voltage delta: {} mV".format(cell_delta_mv)
                )

            capacity = self.cached_capacity
            remaining = capacity * soc / 100.0
            rs485 = self.cached_rs485
            cycle_count = self.cached_cycle_count

            if battery_power < -IDLE_THRESHOLD_W:
                direction = "Discharging"
            elif battery_power > IDLE_THRESHOLD_W:
                direction = "Charging"
            else:
                direction = "Idle"

            rs485_status = (
                "Enabled" if rs485 == 21930 else
                "Disabled" if rs485 == 21947 else str(rs485)
            )

            Devices[1].Update(0, str(round(soc, 1)))
            Devices[2].Update(0, str(round(remaining, 3)))
            Devices[3].Update(0, str(round(voltage, 2)))
            Devices[4].Update(0, str(round(current, 1)))
            Devices[5].Update(0, str(battery_power))
            Devices[6].Update(0, str(round(temperature, 1)))
            Devices[7].Update(0, MODE_NAMES.get(mode, str(mode)))
            Devices[8].Update(nValue=1, sValue=str(MODE_LEVELS.get(mode, 0)))
            Devices[9].Update(0, str(round(capacity, 3)))
            Devices[10].Update(0, str(-ac_power))
            Devices[11].Update(0, "Connected ({})".format(profile["name"]))
            Devices[12].Update(0, rs485_status)

            if cycle_count is not None:
                Devices[13].Update(0, str(cycle_count))
            elif Devices[13].sValue != "N/A":
                Devices[13].Update(0, "N/A")

            Devices[15].Update(0, direction)
            Devices[16].Update(0, str(cell_delta_mv))
            Devices[17].Update(0, str(round(temperature, 1)))
            Devices[18].Update(0, str(round(mos1_temperature, 1)))
            Devices[19].Update(0, str(round(mos2_temperature, 1)))
            Devices[20].Update(0, str(round(max_cell, 3)))
            Devices[21].Update(0, str(round(min_cell, 3)))

            charge_power = battery_power if direction == "Charging" else 0
            discharge_power = (
                abs(battery_power) if direction == "Discharging" else 0
            )

            total_charge_wh = round(lifetime_charge_kwh * 1000.0)
            total_discharge_wh = round(lifetime_discharge_kwh * 1000.0)

            Devices[22].Update(
                nValue=0,
                sValue="{};{}".format(charge_power, total_charge_wh)
            )
            Devices[23].Update(
                nValue=0,
                sValue="{};{}".format(discharge_power, total_discharge_wh)
            )

            Devices[24].Update(0, str(self.cached_max_charge_power))
            Devices[25].Update(0, str(self.cached_max_discharge_power))

            # Reuse the existing slow-read cadence. At 10-second polling this
            # recalculates efficiency every 5 minutes; at 30 seconds every
            # 15 minutes. The value itself covers seven complete days.
            if slow_read_due:
                self._update_week_efficiency()

            self.successful_reads += 1

        except Exception as error:
            Domoticz.Error("Marstek Modbus: {}".format(error))
        finally:
            c.close()


global _plugin
_plugin = BasePlugin()


def onStart():
    _plugin.onStart()


def onHeartbeat():
    _plugin.onHeartbeat()


def onCommand(Unit, Command, Level, Hue):
    _plugin.onCommand(Unit, Command, Level, Hue)


