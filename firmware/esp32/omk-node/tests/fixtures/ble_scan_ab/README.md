# Timestamp fixtures

Source-equivalent `%j` output examples (synthetic message/time; not captures from the operational Gateway). The timestamp templates and six-digit fraction match these official release sources, both WITH_CJSON and fallback branches:

- [Mosquitto 2.0.11 json_print](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.11/client/sub_client_output.c#L244): literal Z followed by numeric offset.
- [Mosquitto 2.0.18 format_time_8601](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.18/client/sub_client_output.c#L234): numeric offset without literal Z.

The actual installed Gateway build remains unverified; record its version and a real line before deployment. Tests additionally cover UTC Z, colon offset, numeric UNIX seconds and sensor-collector received_at.
