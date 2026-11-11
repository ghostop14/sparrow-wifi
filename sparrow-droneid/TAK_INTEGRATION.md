# TAK / Cursor-on-Target integration

Sparrow DroneID can publish what it hears to the TAK ecosystem (ATAK, WinTAK, iTAK, WebTAK and TAK Server) as Cursor-on-Target (CoT). Everything is configured on the **TAK** tab of the Settings dialog; settings apply on Save with no restart.

Contents: [What is sent](#1-what-is-sent) | [Quick start](#2-quick-start) | [Transports](#3-transports) | [TLS](#4-tls-and-certificates) | [Enrollment](#5-certificate-enrollment) | [Data packages](#6-data-package-import) | [Symbols](#7-symbols) | [Timing and removal](#8-timing-and-removal) | [Clock skew](#9-clock-skew) | [Several receivers](#10-several-receivers-on-one-tak-server) | [Troubleshooting](#11-troubleshooting) | [Upgrading](#12-upgrading-from-the-multicast-only-output)

## 1. What is sent

For every drone currently tracked (and having a position):

| Item | CoT type | Callsign | Notes |
|---|---|---|---|
| Drone | `a-<aff>-A-M-F-Q` (`-r` for multirotor / helicopter) | `UAS-<last 6 of ID>` | Position, altitude, speed, course, full details in the remarks. |
| Operator point | `a-<aff>-G` | `OP-<last 6>` | Live operator position from Remote ID. |
| Takeoff point | `a-<aff>-G` | `HOME-<last 6>` | Used when only a takeoff point is known (French Remote ID). |
| Link line | `u-d-f`, dashed | `UAS-<last 6> link` | Drone to operator, in the affiliation colour. |
| Receiver | `a-f-G-E-S` | the station name | Only with a GPS fix (gpsd or static coordinates). |

`<aff>` is `f` (friendly), `h` (threat) or the configured unknown affiliation (`u` or `n`), following the disposition you set in the app. When a drone leaves the live list the app sends a delete for its drone, operator and line items. Wi-Fi SSID-only detections have no drone position (they carry the receiver's) and are left out unless you opt in.

The identifiers are per receiver: `<prefix>-<station>-drone-<id>`, plus `-op` and `-line`; the receiver point is `<prefix>-<station>-sensor`.

Example drone point (the document is one line on the wire):

```xml
<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019" type="a-h-A-M-F-Q-r"
       time="2026-11-11T14:00:02.000Z" start="2026-11-11T14:00:02.000Z"
       stale="2026-11-11T14:00:31.000Z" how="m-g">
  <point lat="32.7157000" lon="-117.1611000" hae="152.0" ce="10.0" le="15.0"/>
  <detail>
    <contact callsign="UAS-450019"/>
    <track course="240.0" speed="5.25"/>
    <remarks>Remote ID drone (Sparrow DroneID)
ID: 1581F5FHD23450019 (Serial Number (ANSI/CTA-2063-A))
Type: Helicopter / Multirotor
Class: civil
Disposition: threat
Altitude: 152.0 m HAE, 45.0 m AGL
Sensor: rx1</remarks>
    <__droneid serial="1581F5FHD23450019" registration_id="" mac="" key="1581F5FHD23450019"
               ua_type="2" height_agl="45.0" operator_lat="32.7100000" operator_lon="-117.1700000"
               operator_id="" self_id="" rssi="-68" protocol="astm_ble" disposition="threat"
               military="false" law_enforcement="false"/>
  </detail>
</event>
```

Operator point:

```xml
<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019-op" type="a-h-G" ... how="m-g">
  <point lat="32.7100000" lon="-117.1700000" hae="9999999.0" ce="10.0" le="9999999.0"/>
  <detail>
    <contact callsign="OP-450019"/>
    <link uid="sparrow-droneid-rx1-drone-1581F5FHD23450019" type="a-h-A-M-F-Q-r" relation="p-p"/>
    <remarks>Remote ID operator position for UAS-450019
Sensor: rx1</remarks>
  </detail>
</event>
```

Link line:

```xml
<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019-line" type="u-d-f" ... how="h-e">
  <point lat="32.7157000" lon="-117.1611000" hae="9999999.0" ce="9999999.0" le="9999999.0"/>
  <detail>
    <link point="32.7157000,-117.1611000"/><link point="32.7100000,-117.1700000"/>
    <__shapeExtras cpvis="true" editable="false"/><labels_on value="false"/>
    <contact callsign="UAS-450019 link"/><remarks>UAS-450019 to OP-450019</remarks>
    <color value="-65536"/><strokeColor value="-65536"/><strokeWeight value="2.0"/>
    <strokeStyle value="dashed"/>
  </detail>
</event>
```

Receiver point:

```xml
<event version="2.0" uid="sparrow-droneid-rx1-sensor" type="a-f-G-E-S" ... how="m-g">
  <point lat="32.7000000" lon="-117.1500000" hae="12.0" ce="10.0" le="15.0"/>
  <detail><contact callsign="rx1"/>
    <remarks>Sparrow DroneID Remote ID receiver
Drones tracked: 3</remarks></detail>
</event>
```

Delete (sent with `__forcedelete`, naming the type last sent for that item):

```xml
<event version="2.0" uid="sparrow-droneid-rx1-drone-1581F5FHD23450019-delete" type="t-x-d-d" ... how="h-e">
  <point lat="0.0" lon="0.0" hae="0.0" ce="9999999.0" le="9999999.0"/>
  <detail><link uid="sparrow-droneid-rx1-drone-1581F5FHD23450019" relation="none"
                type="a-h-A-M-F-Q-r"/><__forcedelete/></detail>
</event>
```

Text taken from the drone's own broadcast (self-ID, operator ID, serial and so on) is untrusted: it is cleaned of characters XML forbids, cut to 64 characters and escaped before it is sent.

## 2. Quick start

**a) ATAK on the same network (UDP multicast).** Leave the defaults: transport *UDP multicast*, group `239.2.3.1`, port `6969`. Tick *Enable*, Save. ATAK listens on that group by default, so drones appear with no further setup. The multicast TTL defaults to 1 (the local segment); raise it to cross routers. If the receiver has several network interfaces, choose the one facing your TAK clients under *Multicast group*.

**b) TAK Server with an enrollment data package.** In *Data Package*, import the enrollment `.zip` your TAK administrator gave you. The server, port, truststore and enrollment mode are filled in. Enter the enrollment user name and password for this server, click *Test connection* (the first test enrolls and fetches a certificate), tick *Enable* and Save.

**c) TAK Server with a certificate package or an iTAK package.** Import the `.zip` in *Data Package*: the client certificate and truststore are stored and the connection settings filled in. Test, tick *Enable*, Save.

If you have no package, choose *TLS (TAK Server)*, enter the host and port (default 8089), upload the client certificate and the truststore (or choose *Enroll with TAK Server*), then test.

## 3. Transports

| Transport | Typical port | Destination | Notes |
|---|---|---|---|
| `udp_multicast` | 6969 | IPv4 group in 224.0.0.0/4, default `239.2.3.1` | Optional interface, TTL 1-255. |
| `udp_broadcast` | 6969 | IPv4 address, e.g. the subnet broadcast `192.168.1.255` | `SO_BROADCAST` is set. |
| `udp_unicast` | 4242 | host name or IP | 4242 is ATAK's default UDP input. |
| `tcp` | 8087 | host name or IP | TAK Server's plain input. |
| `tls` | 8089 | host name or IP | TAK Server's TLS input; client certificate required. |

UDP is connectionless: "connected" in the status means the socket is open, not that anyone received anything. TCP and TLS reconnect automatically, retrying after 1 s and doubling up to 30 s; a link that stays up for 10 s resets the backoff. After a reconnect everything still on the map is sent again. TCP and TLS can send an `<auth><cot username=".." password=".." uid=".."/></auth>` message once after connecting when an input user name is set. Anything the server sends back is read and discarded so the connection never stalls. The send queue is bounded (at least 1000 messages); on overflow the oldest position update is dropped, never a delete if an update can go instead.

Network I/O never happens on the capture thread: detections only signal the emit thread, which hands messages to a separate sender thread.

## 4. TLS and certificates

Client certificate, *three ways*:

- **Upload**: a `.p12`/`.pfx`, a certificate plus a key file, or a combined PEM. The format is detected from the content, not the file name. The key is decrypted at upload and stored unencrypted next to the certificate in `<data dir>/tak_certs/` (directory mode 0700, files 0600). The password you typed is used once and never stored. Data packages already carry their passwords in plain text; protect the data directory accordingly.
- **File on this host**: give the path to a `.p12`, PEM or combined PEM (plus a key path when separate) and, if needed, a password. Files are re-read when they change.
- **Enroll with TAK Server**: see the next section.

*CA truststore*: *System trust store* (default), an uploaded truststore (`.p12`, PEM bundle or a single certificate) or a file on this host. A truststore password is separate from the client certificate password (TAK packages commonly use `atakatak` for the truststore).

*Verification*: *Verify server certificate* and *Check server hostname* are on by default. With verification off, the connection is encrypted but the server is not authenticated.

Wrong passwords are reported as "Wrong password for the .p12 / key"; a key that does not belong to the certificate is reported as "The private key does not match the certificate".

## 5. Certificate enrollment

In *Enroll with TAK Server* mode the app requests its own client certificate:

1. `GET https://<host>:<enrollment port>/Marti/api/tls/config` (HTTP Basic with the enrollment user) for the subject template (`C, ST, L, O, OU`).
2. It generates a 4096-bit RSA key and a CSR whose common name is the user name (TAK Server checks the CN against the logged-in user), with the template fields before it.
3. `POST /Marti/api/tls/signClient/v2?clientUid=<client UID>` returns the signed certificate and the CA chain.

The certificate, key and chain are cached under `tak_certs/enroll/<identity>/`, one directory per identity (host, enrollment port, user name and client UID; the password is not part of it), so testing a different server or user never replaces the certificate the running connection uses. A certificate cached by an earlier version directly under `tak_certs/enroll/` is adopted automatically when it matches the saved identity and ignored otherwise. If the running connection finds no usable certificate it asks the enrollment thread to check at once (still honouring the failure backoff) rather than waiting for the daily check. It is renewed automatically 14 days before it expires (checked daily) and requested again when the host, enrollment port, user name or client UID changes. A failed attempt is not repeated for 5 minutes, then 10, 20, 40 and so on up to 1 hour. This is deliberate: repeated wrong passwords can lock the account on the server. Correcting the password or any other enrollment setting is retried at once. A certificate that is still valid keeps being used while a renewal fails.

A user that was just created on TAK Server may get HTTP 401 for a few seconds; wait and try again.

## 6. Data package import

*Data Package* accepts the `.zip` files TAK administrators hand out:

- ATAK enrollment packages (`config.pref` with `enrollForCertificateWithTrust0=true`, plus a truststore),
- certificate packages (a client `.p12` as well),
- iTAK packages (the same keys without the index suffix; the client password falls back to the truststore password).

Files are found by their base name, wherever they sit in the zip. What is filled in: transport (`ssl` becomes TLS), host, port, the truststore, and the client mode (enrollment or upload). Verification is switched on. What you still enter yourself: the enrollment user name and password (never in packages), and the Enable switch is never touched.

Safety: the zip is read in memory and nothing is extracted. Rejected: more than 2 MiB, more than 32 members, encrypted members, a member over 1 MiB or all members over 4 MiB unpacked, a compression ratio over 100, a DTD or entity declaration in the XML, a missing referenced file, ambiguous duplicate names. Entries with `..`, absolute or drive-letter paths are ignored (with a warning). A package with several connections imports the first. Everything is checked before anything is written; a bad package changes nothing. **Importing never enables the output**, and when the import points at a different host, port or transport than the saved one (a switch from TLS to plain TCP on the same server counts), the saved secrets of the old server (enrollment password, input password, client key password) are cleared in the same write and listed in the response (`cleared_secrets`), so they can never be sent to a server named by an untrusted package; the TAK tab shows which ones to re-enter.

## 7. Symbols

| Drone class | Default `uav` family | Optional `civil` family |
|---|---|---|
| Multirotor, gyroplane, tethered (UA types 2, 3, 13) | `a-<aff>-A-M-F-Q-r` | `a-<aff>-A-C-H` |
| Everything else | `a-<aff>-A-M-F-Q` | `a-<aff>-A-C-F-q` |

Affiliation letters: `f` friendly, `h` threat, unknown drones use `u` (yellow, default) or `n` (green, a Settings choice).

Measured on ATAK-CIV 5.8.0.8 through TAK Server 5.8 (2026-11-11):

- The `A-M` types draw the UAV glyph and **take the affiliation colour** (yellow unknown, red hostile, blue friend, green neutral). `a-h-A-M-F-Q-r` shows the red UAV glyph with the rotary-wing "R" mark.
- The `A-C` civil types draw a civil aeroplane or helicopter glyph but are **always filled purple**, whatever the affiliation, so a threat drone does not turn red.
- `a-?-A-M-H-Q` draws a military helicopter bow-tie, not a drone glyph, so it is not used.

That is why `uav` is the default: seeing disposition at a glance matters more than the "M" in the type, which formally means military, so with `uav` even a hobbyist's drone is a "military UAV" track. The civil/military distinction travels in the remarks (`Class: civil` or `Class: military (operator flag)`) and in the `__droneid` detail. Choose `civil` if another system filters on `A-C` types and accept purple icons. The operator-set military and law-enforcement flags never change the type; they appear in the `Flags:` remark line and the `__droneid` attributes.

The operator point is `a-<aff>-G`. It renders as a bare ground frame in the affiliation colour (unknown yellow quatrefoil, hostile red diamond, friend blue rectangle, neutral green square). The link line is `u-d-f` with `strokeStyle` `dashed`, drawn dashed in the stroke colour (red, cyan, green or yellow by affiliation).

Callsigns use the last six characters of the drone ID because ANSI/CTA-2063-A serials share their leading manufacturer code; the full ID is in the remarks.

## 8. Timing and removal

Defaults: updates at most every 2 s per drone while new frames arrive; an unchanged drone is re-sent every 10 s while it is still fresh; the CoT stale time is the drone's last-seen time plus 30 s (the moment the app itself calls the drone "aging"); a delete is sent after 180 s without a frame (when the app drops it from the live list). A change of disposition (affiliation) is sent immediately.

| Setting | Default | Meaning |
|---|---|---|
| Send interval | 2 s | Minimum spacing between updates of one drone. |
| Refresh interval | 10 s | Re-send of an unchanged, not yet stale drone. |
| Stale after | 30 s | Stale = last seen + this; at least 3 x the larger of the two intervals. |
| Delete after | 180 s | Delete when the drone is this old; at most 300 s. |

Timeline for a Bluetooth drone first heard at t=0 with frames every second: t=0 drone, operator and line are sent; t=2, 4, ... updates every 2 s, each with stale = last seen + 30 s; if the Wi-Fi beacon later reveals the serial number, the MAC-based items are deleted and the serial-based ones are drawn in the same batch; after the last frame (say t=60) refreshes at 70 and 80, then nothing (stale at 90); at 241 s (age 181) the three items are deleted. Turning the output off sends deletes for every drawn drone, operator and line at once (waiting up to 2 s for them to go out); the receiver point is never deleted and stales out by itself. Any change to the effective settings does the same, then starts again with the new settings; saving without changes does nothing.

**How clients handle it** (measured on TAK Server 5.8):

| Client | On the delete (`t-x-d-d` with `__forcedelete`) | On stale alone |
|---|---|---|
| ATAK-CIV 5.8.0.8 | Removes any item within about 3-5 s | Darkens a point at its stale time, then removes it on its own about 5-6 minutes later (a UAV point with stale = send + 10 s was still drawn at stale + 4 min 56 s and gone at stale + 5 min 57 s, default settings). Stale `u-d-f` shapes (the link line) are never removed on stale, even across an ATAK restart. |
| WebTAK 4.10.5 | Ignored for map items (it only removes contacts) | Removes the item 0.5-2.5 minutes after the stale time |
| iTAK 2.12.3 | Ignored | Kept (updates are applied) |

So without deletes a vanished drone lingers about five minutes in ATAK as a dark icon and its line lingers indefinitely; with the delete everything goes in seconds. TAK Server does not replay deletes to a client that connects later: a client that was offline when a delete went out keeps the item (in ATAK and iTAK until it is removed by hand). No iTAK workaround is attempted.

## 9. Clock skew

Stale times are stamped from this host's clock. A receiver clock ahead of the TAK clients makes items linger by the difference; behind makes them expire early. Keep NTP or GPS time running on the receiver.

## 10. Several receivers on one TAK Server

Identifiers include the station name, so two receivers that hear the same drone draw two co-located icons rather than fighting over one (one receiver's delete would otherwise remove the other's live drone). Give every receiver its own station name and its own client UID (the UID identifies the receiver to TAK Server and is used for enrollment).

## 11. Troubleshooting

- **Connection refused**: wrong port, server down, or a firewall. TAK Server's TLS input is 8089, plain TCP 8087 (off by default), enrollment 8446.
- **Server certificate verification failed**: the truststore does not contain the server's CA, or the server name does not match its certificate. Upload the right truststore, or (knowing the risk) turn off hostname checking or verification.
- **Server rejected our client certificate**: the Test connection waits two seconds after the handshake because under TLS 1.3 the server refuses a client certificate only afterwards. TAK Server 5.8 answers an untrusted client certificate with a bare "internal error" alert. Use a certificate issued by the CA the server trusts, or enroll.
- **Wrong password for the .p12 / key**: the client certificate and the truststore have different passwords; check you used the right one for each file.
- **Enrollment 401 or "backed off until ..."**: wrong user name or password, or the account is brand new (wait a few seconds). Retries are spaced 5 min, 10 min, ... up to 1 h to protect the account; fixing a setting retries immediately.
- **Nothing on the map**: is the output enabled? Does the drone have a position (a Wi-Fi SSID detection does not, and is filtered unless opted in)? The receiver point needs a GPS fix. Check *Live Status* for errors and queue drops.
- **Items linger**: see the client table in section 8, and clock skew in section 9.

## 12. Upgrading from the multicast-only output

Existing settings keep working: `cot_enabled`, `cot_address` and `cot_port` are the enable, host and port of the new output, and the transport defaults to UDP multicast. An upgraded install keeps its multicast TTL of 32 (new installs start at 1). `GET /api/v1/cot/status` and `PUT /api/v1/cot/config` still exist with their old shapes (the PUT selects UDP multicast when an address or port is given). What changed on the wire: UIDs now contain the station name and the drone ID, the type is `a-<aff>-A-M-F-Q(-r)` instead of `a-n-A-C-F-q`, the callsign is `UAS-<last 6>` instead of `DRONE-<first 4>`, and the `__droneid` element keeps its attribute names and gains `registration_id`, `mac`, `key`, `disposition`, `military` and `law_enforcement`. See [sparrow_drone_id_api.md](sparrow_drone_id_api.md) for the API.
