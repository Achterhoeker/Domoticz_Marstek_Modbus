# Domoticz Marstek Modbus

Local Modbus integration for the Marstek Venus E home battery in Domoticz.

The plugin communicates directly with the battery using Modbus TCP.

No Marstek cloud, OpenAPI or MQTT connection is required.


## Origin and credits

This project is based on the original **Domoticz_Marstek_Modbus** plugin by
**Simon Riemersma (hopSilentSimon)**:

https://github.com/hopSilentSimon/Domoticz_Marstek_Modbus

Many thanks to Simon for creating and sharing the original Domoticz plugin.

This repository is my personal fork / continuation of that project, containing
the changes and improvements I use with my own Marstek Venus E V3 setup.

The main goal of this version is to keep the original plugin simple while adding
the functionality and reliability I need in daily use, including:

- Native LAN / Modbus TCP support on the Venus E V3, without an external RS485 gateway
- More efficient grouped Modbus polling
- Native lifetime charge and discharge energy counters
- Domoticz energy history integration
- Rolling 7-day charge/discharge efficiency
- Cell voltage delta monitoring
- Configurable maximum charge and discharge power
- Improved handling of slow-changing registers
- Various fixes and practical improvements discovered while running the plugin

This is therefore not intended to take credit for the original work. It is a
personal, actively used and tested evolution of the original plugin.

## Connection options

### Marstek Venus E V3 LAN port

The easiest setup for a Venus E V3 is the built-in LAN connection.

Connect the battery to your local network and configure the plugin with:

- IP address of the Marstek
- TCP port: `502`
- Modbus Slave ID: `1`

No additional Modbus gateway hardware is required.

### RS485 / Modbus TCP gateway

The plugin can also be used through an RS485-to-Modbus-TCP gateway.

Tested with:

- PUSR-DR134
- TCP Server mode
- Baud rate: `115200`
- Data size: `8 bit`
- Parity: `None`
- Stop bits: `1`
- Modbus Simple Protocol conversion

Other gateways that transparently convert Modbus TCP to Modbus RTU should work as well.

## Requirements

- Marstek Venus home battery with Modbus support
- Domoticz with Python plugin support
- Python `pymodbus`

Install pymodbus if required:

    pip install pymodbus

## Installation

Clone the repository into the Domoticz plugins directory:

    cd ~/domoticz/plugins
    git clone https://github.com/Achterhoeker/Domoticz_Marstek_Modbus.git

Restart Domoticz:

    sudo systemctl restart domoticz

Then add **Marstek Venus Modbus** from the Domoticz Hardware page.

## Configuration

Typical Venus E V3 settings:

| Setting | Value |
|---|---|
| Gateway IP Address | IP address of the battery |
| TCP Port | `502` |
| Modbus Slave ID | `1` |
| Domoticz API URL | `http://127.0.0.1:8080` |
| Poll interval | `10 seconds` |

The Domoticz API URL is only used locally to calculate the rolling 7-day battery efficiency from historical energy data.

## Available sensors and controls

The plugin currently provides:

- State of Charge
- Remaining Energy
- Battery Voltage
- Battery Current
- Battery Power
- AC Power
- Battery Temperature
- Internal Temperature
- MOS1 Temperature
- MOS2 Temperature
- Operating Mode
- Mode Selector
- Battery Capacity
- Connection Status
- RS485 Control Status
- Cycle Count
- 7-Day Efficiency
- Charge / Discharge Direction
- Cell Voltage Delta
- Maximum Cell Voltage
- Minimum Cell Voltage
- Charge Energy
- Discharge Energy
- Maximum Charge Power
- Maximum Discharge Power

## Energy counters

The plugin uses the native cumulative Modbus energy registers:

- `33000` - Lifetime Charging Energy
- `33002` - Lifetime Discharging Energy

Both are 32-bit cumulative energy values.

Domoticz receives these directly as cumulative energy counters and uses the counter history to generate daily, weekly, monthly and yearly energy statistics.

The Marstek daily and monthly Modbus energy registers are therefore not required by this plugin.

This also means no additional counter persistence or reconstruction from daily counters is necessary.

## Battery cell monitoring

The plugin monitors:

- Maximum cell voltage
- Minimum cell voltage
- Cell voltage delta

Cell voltage delta is displayed in millivolts (`mV`).

Example:

    Minimum Cell Voltage : 3.271 V
    Maximum Cell Voltage : 3.276 V
    Cell Voltage Delta   : 5 mV

The cell voltage delta can be useful for monitoring cell balance, especially near the upper and lower ends of the battery SOC range.

A small difference between minimum and maximum cell voltage generally indicates that the cells are well balanced.

## 7-Day Efficiency

Battery efficiency is calculated from the Domoticz energy history over the last seven complete days.

Conceptually:

    Efficiency = discharged energy / charged energy * 100%

For example:

    Charged during 7 days    : 30.0 kWh
    Discharged during 7 days : 27.0 kWh

    Efficiency = 27.0 / 30.0 * 100
               = 90.0%

Using energy over a longer period provides a much more useful indication than comparing instantaneous AC power and battery power.

The efficiency calculation runs together with the slow-read cycle and is therefore not executed on every Modbus poll.

Because battery SOC can differ between the beginning and end of the seven-day period, the displayed value should be considered a rolling energy efficiency indication rather than a laboratory measurement of round-trip efficiency.

## Charge and discharge power limits

The maximum charging and discharging power can be controlled directly from Domoticz.

Registers:

- `44002` - Maximum Charge Power
- `44003` - Maximum Discharge Power

The controls currently allow values from `0` to `2500 W`.

After writing a new value, the plugin immediately reads the register back.

Only a value confirmed by the Marstek is accepted by the plugin.

This makes the power limits useful for external load-management logic in Domoticz.

For example, maximum battery discharge power can be dynamically reduced when solar generation and battery output share the same electrical circuit.

## Charge / discharge direction

Battery power uses a small deadband around zero to prevent measurement noise from continuously switching the displayed direction.

The current thresholds are:

    battery power < -10 W : Discharging
    -10 W ... +10 W       : Idle
    battery power > +10 W : Charging

## Efficient Modbus polling

Registers located next to each other are read as blocks instead of using a separate Modbus request for every value.

Fast-changing values are read every configured polling interval.

These include:

- Battery power
- AC power
- Battery voltage
- Battery current
- SOC
- Lifetime charging energy
- Lifetime discharging energy
- Temperatures
- Minimum cell voltage
- Maximum cell voltage
- Operating mode

Slow-changing values are cached and refreshed once every 30 successful Modbus polls.

These include:

- Battery capacity
- Cycle count
- RS485 control status
- Maximum charge power
- Maximum discharge power

With a 10-second polling interval:

    30 polls * 10 seconds = 300 seconds

So the slow values are refreshed approximately every 5 minutes.

With a 30-second polling interval they are refreshed approximately every 15 minutes.

The 7-day efficiency calculation uses the same slow-read schedule.

## Modbus registers currently used

Some of the main registers used by the plugin are:

| Register | Description |
|---:|---|
| `30001` | Battery power |
| `30006` | AC power |
| `30100` | Battery voltage |
| `30101` | Battery current |
| `32105` | Battery capacity |
| `33000` | Lifetime charging energy |
| `33002` | Lifetime discharging energy |
| `34002` | SOC on V3-style register map |
| `34003` | Cycle count |
| `35000` | Internal temperature |
| `35001` | MOS1 temperature |
| `35002` | MOS2 temperature |
| `37007` | Maximum cell voltage |
| `37008` | Minimum cell voltage |
| `42000` | RS485 control status |
| `43000` | User work mode |
| `44002` | Maximum charge power |
| `44003` | Maximum discharge power |

## Result

![Marstek overview](https://github.com/user-attachments/assets/96949b4f-874e-4d94-b2b3-e69ce949046f)

---

# Changelog

## 1.5.0

- Uses native lifetime energy registers:
  - `33000` - Lifetime Charging Energy
  - `33002` - Lifetime Discharging Energy
- Removed reconstruction of cumulative energy from daily counters.
- Domoticz now receives the native lifetime counters directly.
- Removed unnecessary polling of daily and monthly energy registers.
- Replaced instantaneous power-ratio efficiency with rolling 7-day efficiency based on Domoticz energy history.
- Removed fixed `100%` Estimated SOH value.
- Added real Cell Voltage Delta measurement in `mV`.
- Added Maximum Charge Power control using register `44002`.
- Added Maximum Discharge Power control using register `44003`.
- Charge/discharge power settings are read back after writing for verification.
- Added grouped Modbus reads to reduce Modbus traffic.
- Added slow polling for values that do not need to be refreshed every cycle.
- Slow-changing registers are refreshed once every 30 successful polls.
- Idle power deadband changed to `-10 W ... +10 W`.
- Added native Venus E V3 LAN / Modbus TCP support.
- No external RS485 gateway is required when using the native Venus E V3 LAN interface.

## 1.4.0

- Added Maximum Charge Power control.
- Added Maximum Discharge Power control.
- Improved grouped Modbus polling.
- Slow-changing registers are cached and refreshed every 30 successful polls.
- Improved cumulative energy counter handling across Domoticz/plugin restarts.

## 1.3

- TCP connection failure is distinguished from an RS485/Modbus failure.
- Modbus Exception 02 / Illegal Data Address is identified separately.
- Error reporting distinguishes:
  - Gateway/network unreachable
  - Gateway reachable but battery/RS485 not replying
  - Unsupported register
  - Other Modbus errors
- Added automatic register-map detection.
- Correct SOC scaling:
  - V3-style: register `34002`, raw x `0.1`
  - Venus E V2: register `32104`, raw x `1.0`
- Cycle Count handled gracefully:
  - V3-style attempts register `34003`
  - Unsupported models display `N/A`

## 1.2

- Improved Modbus response handling.
- Detects no-response conditions.
- Detects Modbus exception responses.
- Detects invalid responses without register data.
- Improved communication troubleshooting and diagnostics.

## 1.1

- Supports pymodbus 2.x and 3.x.
- Compatible with older Domoticz installations.
- Added pymodbus version information to diagnostics.

