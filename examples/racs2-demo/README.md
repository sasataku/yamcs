# racs2-demo — Upstream cFS XTCE MDB

This XTCE 1.2 mission database defines cFE ES/EVS/SB/TBL/TIME and TO_LAB/CI_LAB
separately for the SC-OBC RPU and APU. Yamcs organizes them under paths such as
`/cFS/RPU/CFE_ES` and `/cFS/APU/CFE_ES`.

## File layout

`src/main/yamcs/mdb/` contains the common root, CPU namespaces, and application XML files.

```text
mdb/
  cfs.xml                  # Common CCSDS primary, telemetry and command headers; cFS root
  cfs.manifest.json        # Source revisions and index of CPU manifests
  rpu.xml                  # RPU SpaceSystem
  apu.xml                  # APU SpaceSystem
  rpu/
    cfe_es.xml
    cfe_evs.xml
    cfe_sb.xml
    cfe_tbl.xml
    cfe_time.xml
    to_lab.xml
    ci_lab.xml
    cfs.manifest.json       # RPU MIDs, function codes, structures, and offsets
  apu/
    cfe_es.xml
    cfe_evs.xml
    cfe_sb.xml
    cfe_tbl.xml
    cfe_time.xml
    to_lab.xml
    ci_lab.xml
    cfs.manifest.json       # APU MIDs, function codes, structures, and offsets
```

Application telemetry containers inherit from `/cFS/CCSDS_Telemetry` and are
identified by `/cFS/MessageId`. Load the common header and select the CPUs and
applications you need through `subLoaders`.

## Supported configuration and coverage

The database was generated from the upstream cFS builds
`native_std/default_cpu1` and `native_std/default_cpu2` on Linux. Their message
ID blocks match the SC-OBC mission (`scobc_msgid_bases.h`): `default_cpu1` is
the RPU (0x18xx/0x08xx) and `default_cpu2` is the APU (0x19xx/0x09xx).
Both CPU builds use little endian byte order in this configuration.

| Source | Commit |
|---|---|
| cFS | `83c403ed8ec273c6de4fe8ca08ade8d7753d80ac` |
| cFE | `5dd74afa85e5ed5e8c1ed4129281d21611f8d699` |
| to_lab | `d27c6014cb5d2979140500f635876ee87b81995b` |
| ci_lab | `edb8ddf53076128a125b373b3b870f75e1d82d04` |

The following telemetry (TM) and telecommands (TC) are defined for **each CPU**.

| Service | TM | TC, including HK requests and internal messages |
|---|---:|---:|
| ES | 3 | 25 |
| EVS | 3 | 22 |
| SB | 4 | 12 |
| TBL | 2 | 11 |
| TIME | 2 | 21 |
| TO_LAB | 2 | 8 |
| CI_LAB | 1 | 4 |
| Total per CPU | **17** | **103** |
| Total for both CPUs | **34** | **206** |

The MDB defines 1356 parameters and 36 telemetry containers, including the common headers.
It also includes one abstract command, `/cFS/CCSDS_Command`, shared by the 206 concrete commands.
Telemetry includes housekeeping, ES application information and memory statistics,
EVS long and short events, SB statistics and subscriptions, the TBL registry,
TIME diagnostics, and TO data type tests.
Telecommands include ground commands, housekeeping requests, SB subscription
report controls, CI ReadUplink, and TIME OneHz/ToneSignal/FakeTone/ToneData.

APU MIDs are obtained from the APU's build configuration. For example:

| Message | RPU | APU |
|---|---|---|
| ES HK | `0x0800` | `0x0900` |
| ES ground command | `0x1806` | `0x1906` |

TIME global commands use MIDs shared by both CPUs. Selecting a CPU namespace
for these commands does not change their global MID.
`CFE_TBL_NotifyCmd_t` is a notification template whose destination application
specifies the MID and function code. It is therefore excluded from the fixed-MID
TBL ground commands. File record definitions are also outside the scope of this MDB.

## Wire format and naming

Telemetry headers are defined as XML fields in two reusable containers:

```text
/cFS/CCSDS_PrimaryHeader
  MessageId (16 bits; complete MID word)
  Version (3 bits), PacketType (1 bit), SecondaryHeaderFlag (1 bit), APID (11 bits)
  Sequence (16 bits; complete sequence word), SequenceFlags (2 bits), SequenceCount (14 bits)
  PacketLength (16 bits)
    └─ /cFS/CCSDS_Telemetry
         Seconds (32 bits), Subseconds (16 bits), Spare (32 bits)
           └─ /cFS/RPU/CFE_ES/CFE_ES_HousekeepingTlm, etc.
```

`MessageId` and `Sequence` expose the complete words as well as their individual
bit fields. The telemetry header inherits the primary header and requires
`PacketType=0` and `SecondaryHeaderFlag=1`.
Each application's telemetry container inherits the common telemetry header and
selects its MID with a hexadecimal comparison. For example, RPU ES HK specifies:

```xml
<BaseContainer containerRef="/cFS/CCSDS_Telemetry">
  <RestrictionCriteria>
    <Comparison parameterRef="/cFS/MessageId" value="0x0800" />
  </RestrictionCriteria>
</BaseContainer>
```

In XTCE, telemetry MIDs are decoding conditions (`Comparison`), while command
MIDs are encoding assignments (`ArgumentAssignment`). Both use named header
fields and readable hexadecimal values.

The common `cfs.xml` defines the command header as individual XML fields in the
abstract `/cFS/CCSDS_Command`: `mid` (16 bits), `sequenceFlags` (2 bits),
`sequenceCount` (14 bits), `packetLength` (16 bits), `functionCode` (8 bits),
and `checksum` (8 bits). The 16-bit `mid` contains the CCSDS version, packet type,
secondary-header flag, and APID, following cFE's MID representation.

Each application command inherits this header and assigns its values explicitly.
For example, RPU ES NOOP specifies:

```xml
<BaseMetaCommand metaCommandRef="/cFS/CCSDS_Command">
  <ArgumentAssignmentList>
    <ArgumentAssignment argumentName="mid" argumentValue="0x1806" />
    <ArgumentAssignment argumentName="packetLength" argumentValue="1" />
    <ArgumentAssignment argumentName="functionCode" argumentValue="0" />
    <ArgumentAssignment argumentName="sequenceFlags" argumentValue="3" />
    <ArgumentAssignment argumentName="sequenceCount" argumentValue="0" />
    <ArgumentAssignment argumentName="checksum" argumentValue="0" />
  </ArgumentAssignmentList>
</BaseMetaCommand>
```

To specify a MID such as `0x1808`, use
`<ArgumentAssignment argumentName="mid" argumentValue="0x1808" />`.
This is standard XTCE syntax; the header is encoded from these fields rather
than from a single opaque `binaryValue`. Telemetry container comparisons also
use hexadecimal MIDs, such as `<Comparison parameterRef="/cFS/MessageId" value="0x0800" />`.

- The CCSDS primary header and telemetry timestamps use big endian byte order.
  Telemetry headers are 16 bytes; command headers are 8 bytes.
- Telemetry stores 32-bit seconds at byte 6, 16-bit subseconds at byte 10,
  and spare bytes at offsets 12–15.
- Commands store the function code at byte 6 and the checksum at byte 7.
- Payloads use the byte order of each CPU's native build. Structure sizes and
  field offsets are obtained through GCC/GDB, including internal and trailing padding.
- `char[N]` is represented as a NULL-terminated string in a fixed N-byte buffer.
- Arrays and structures are expanded into individual fields. Descriptions retain
  their original C field paths.
- Enums, bitmasks, ID wrappers, and booleans are represented numerically.
  Calibrations, alarms, enumeration labels, and command result verifiers are not defined.
- In SB AllSubscriptions telemetry, `Payload.Entries` specifies the number of
  valid entries. The remaining array elements are unused.

Example paths:

```text
/cFS/RPU/CFE_ES/CFE_ES_NoopCmd
/cFS/APU/CFE_ES/CFE_ES_NoopCmd
/cFS/RPU/TO_LAB/TO_LAB_EnableOutputCmd
/cFS/APU/TO_LAB/TO_LAB_EnableOutputCmd
/cFS/RPU/TO_LAB/TO_LAB_HkTlm_Payload_CommandCounter
```

The CPU namespace adds a level to the previous `/cFS/CFE_ES/...` layout.
Update command and parameter references in displays and scripts accordingly.

## Loading the MDB into Yamcs

Copy all XML files into Yamcs's `mdb/` directory, preserving the layout above.
`src/main/yamcs/etc/yamcs.racs2-demo.yaml` contains the configuration for both
CPUs and all applications. A minimal configuration loading only ES for both CPUs is:

```yaml
mdb:
  - type: xtce
    args:
      file: mdb/cfs.xml
    subLoaders:
      - type: xtce
        args:
          file: mdb/rpu.xml
        subLoaders:
          - type: xtce
            args:
              file: mdb/rpu/cfe_es.xml
      - type: xtce
        args:
          file: mdb/apu.xml
        subLoaders:
          - type: xtce
            args:
              file: mdb/apu/cfe_es.xml

streamConfig:
  tm:
    - name: tm_realtime
      processor: realtime
      rootContainer: /cFS/CCSDS_Telemetry
```

The same telemetry stream can distinguish both CPUs by MID.
Commands for both CPUs are sent to the RPU's CI link at `127.0.0.1:1235`.
The RPU forwards commands carrying APU MIDs through SBN to the APU.
The command stream specifies `tcPatterns: ["/cFS/RPU/.*", "/cFS/APU/.*"]`.
APU telemetry also reaches the RPU through SBN and is sent to Yamcs by the RPU's TO.

`CfsCommandPostprocessor` updates the packet length, sequence count, and checksum
immediately before transmission. Configure SBN subscriptions and forwarding,
and TO's output subscriptions, on the cFS side.

## Building and starting Yamcs

For the initial build, run these commands from the repository root:

```sh
mvn -B --no-transfer-progress -pl examples/racs2-demo -am install -DskipTests
cd yamcs-web/src/main/webapp
npm ci --no-audit --no-fund
npm run build
cd ../../../..
./run-example.sh racs2-demo
```

After building, start Yamcs with `./run-example.sh racs2-demo`.
Open `http://localhost:8090` and select `racs2-demo/realtime`.
To stop Yamcs started in your terminal, press Ctrl+C in that terminal.
Start cFS separately; this example does not launch cFS processes automatically.

Yamcs receives telemetry from the RPU's TO on UDP port 2235 and sends commands for
both CPUs to the RPU at `127.0.0.1:1235`. Data is stored in `data/` under the runtime directory.
ci_lab and to_lab offset their ports by the processor ID minus one, and the RPU is
processor 2 (`rpu_PROCESSORID` in the SC-OBC `targets.cmake`). The APU is processor 1,
so its development link uses 1234 and 2234.
To enable the RPU's TO output, issue `/cFS/RPU/TO_LAB/TO_LAB_EnableOutputCmd`
with `Payload_dest_IP=127.0.0.1`.
Add any missing telemetry subscriptions through AddPacket or TO's table configuration.

The cFE epoch depends on the mission, so this example sets
`useLocalGenerationTime: true`. Seconds and subseconds remain available as parameters.
Once the epoch is known, configure `timeEncoding` for `CfsPacketPreprocessor`
and set this flag to false.

## Regeneration and verification

Using the generated XML requires neither cFS sources nor GCC/GDB.
Regeneration requires Python 3.9 or later, GCC, GDB with Python support,
and a cFS build containing both CPUs. The generator leaves existing cFS sources
and build artifacts untouched.

```sh
python3 examples/racs2-demo/tools/generate_xtce.py \
  --cfs-root /path/to/cFS \
  --fixtures-output /tmp/cfs-fixtures.json
python3 examples/racs2-demo/tools/validate_schema.py
```

Use `--build-dir` to select the cFS build directory and
`--output /path/to/mdb/cfs.xml` to select the output location.
The generator writes the common XML, CPU wrapper XML files, and application XML
files and manifests for each CPU together.
It reads `native/default_cpu1` as the RPU and `native/default_cpu2` as the APU.
EDS, other header formats, cross builds, and other ABIs require generator changes.

Schema validation requires Python's `lxml` package.
Verification fixtures are generated by assigning nonzero values to actual
upstream C structures. Fixture generation does not start cFE.

```sh
# YAMCS_CP: Java classpath containing yamcs-core and its dependency JARs
mkdir -p /tmp/cfs-mdb-validation
javac -cp "$YAMCS_CP" -d /tmp/cfs-mdb-validation examples/racs2-demo/tools/VerifyCfsMdb.java
java -cp "/tmp/cfs-mdb-validation:$YAMCS_CP" VerifyCfsMdb \
  examples/racs2-demo/src/main/yamcs/mdb/cfs.xml /tmp/cfs-fixtures.json
```

All XML files have passed XTCE 1.2 schema validation.
With Yamcs 5.13.7-SNAPSHOT, all 206 command packets matched their C structures
byte for byte, and all 34 telemetry packets decoded into the correct CPU paths.
All 12 telemetry header parameters were checked for every fixture, including
additional packets with nonzero sequence counts, timestamps, and spare bytes.
Yamcs 5.13.7-SNAPSHOT and its web interface were built and started on 2026-10-13.
After adding CPU namespaces, Yamcs was restarted and its API confirmed seven
applications per CPU, 206 commands, 35 containers, and a running
`racs2-demo/realtime` processor.
The subsequent header refactoring was verified offline; it has not been applied
to the separate checkout currently serving port 8090.
UDP communication with actual cFS processes has not been verified in this work.
