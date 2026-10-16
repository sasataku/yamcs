import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import com.google.gson.JsonParser;
import org.yaml.snakeyaml.Yaml;
import org.yamcs.archive.XtceTmRecorder;
import org.yamcs.ProcessorConfig;
import org.yamcs.YConfiguration;
import org.yamcs.mdb.MdbFactory;
import org.yamcs.mdb.MetaCommandProcessor;
import org.yamcs.mdb.ProcessorData;
import org.yamcs.mdb.XtceTmExtractor;
import org.yamcs.utils.TimeEncoding;

/** Compare XTCE command encoding and telemetry decoding to real upstream C structs. */
public class VerifyCfsMdb {
    public static void main(String[] args) throws Exception {
        if (args.length < 2 || args.length > 3) {
            throw new IllegalArgumentException("Usage: VerifyCfsMdb MDB_XML C_FIXTURES_JSON [INSTANCE_YAML]");
        }
        TimeEncoding.setUp();
        YConfiguration.setupTest(null);
        var modules = List.of("cfe_es", "cfe_evs", "cfe_sb", "cfe_tbl", "cfe_time", "to_lab", "ci_lab");
        var children = new ArrayList<Map<String, Object>>();
        var directory = Path.of(args[0]).toAbsolutePath().getParent();
        for (var cpu : List.of("rpu", "apu")) {
            var apps = new ArrayList<Map<String, Object>>();
            for (var module : modules) {
                apps.add(Map.of("type", "xtce", "args",
                        Map.of("file", directory.resolve(cpu + "/" + module + ".xml").toString())));
            }
            children.add(Map.of("type", "xtce", "args", Map.of("file", directory.resolve(cpu + ".xml").toString()),
                    "subLoaders", apps));
        }
        var config = YConfiguration.wrap(Map.of("type", "xtce", "args", Map.of("file", args[0]),
                "subLoaders", children));
        var mdb = MdbFactory.createInstance(List.of(config), false, false);
        var instanceFile = args.length == 3 ? Path.of(args[2])
                : directory.getParent().resolve("etc/yamcs.racs2-demo.yaml");
        Map<String, Object> instanceMap = new Yaml().load(Files.readString(instanceFile));
        var instanceConfig = YConfiguration.wrap(instanceMap);
        var stream = instanceConfig.getConfig("streamConfig").getConfigList("tm").get(0);
        var rootName = stream.getString("rootContainer");
        var startContainer = mdb.getSequenceContainer(rootName);
        if (startContainer == null) {
            throw new AssertionError("Unknown stream root container " + rootName);
        }
        var processor = new MetaCommandProcessor(new ProcessorData("verify-cfs", mdb, new ProcessorConfig()));
        var extractor = new XtceTmExtractor(mdb);
        extractor.provideAll();
        var recorderExtractor = new XtceTmExtractor(mdb);
        for (var container : mdb.getSequenceContainers()) {
            if (container.useAsArchivePartition()) {
                recorderExtractor.startProviding(container);
            }
        }
        int commands = 0;
        int telemetry = 0;
        var fixtures = JsonParser.parseString(Files.readString(Path.of(args[1]))).getAsJsonArray();
        for (var item : fixtures) {
            var fixture = item.getAsJsonObject();
            var name = fixture.get("name").getAsString();
            var module = modules.stream().map(String::toUpperCase)
                    .filter(m -> name.startsWith(m + "_")).findFirst().orElseThrow();
            var prefix = "/cFS/" + fixture.get("cpu").getAsString() + "/" + module + "/";
            byte[] expected = HexFormat.of().parseHex(fixture.get("hex").getAsString());
            var values = fixture.getAsJsonObject("values");
            if (fixture.get("kind").getAsString().equals("command")) {
                var assignment = new HashMap<String, Object>();
                for (var entry : values.entrySet()) {
                    assignment.put(entry.getKey(), entry.getValue().getAsString());
                }
                var command = mdb.getMetaCommand(prefix + name);
                byte[] actual = processor.buildCommand(command, assignment, 0).getCmdPacket();
                if (!Arrays.equals(expected, actual)) {
                    throw new AssertionError(name + " C bytes=" + HexFormat.of().formatHex(expected)
                            + " XTCE bytes=" + HexFormat.of().formatHex(actual));
                }
                commands++;
            } else {
                var result = extractor.processPacket(expected, 0, 0, 0, startContainer);
                if (result.getParameterResult().getFirstInserted(mdb.getParameter("/cFS/MessageId")) == null) {
                    throw new AssertionError("Stream root " + rootName
                            + " did not decode the MID; start at /cFS/CCSDS_PrimaryHeader");
                }
                verifyHeader(mdb, result, expected);
                if (result.getContainerResult().stream()
                        .noneMatch(c -> c.getContainer().getQualifiedName().equals(prefix + name))) {
                    throw new AssertionError("Wrong telemetry container for " + name);
                }
                if (!(prefix + name).equals(XtceTmRecorder.deriveArchivePartition(result))) {
                    throw new AssertionError("Wrong archive partition for " + prefix + name);
                }
                var recorded = recorderExtractor.processPacket(expected, 0, 0, 0, startContainer);
                if (!(prefix + name).equals(XtceTmRecorder.deriveArchivePartition(recorded))) {
                    throw new AssertionError("Wrong recorder partition for " + prefix + name);
                }
                for (var entry : values.entrySet()) {
                    var parameter = mdb.getParameter(prefix + name + "_" + entry.getKey());
                    var pv = result.getParameterResult().getFirstInserted(parameter);
                    if (pv == null) {
                        throw new AssertionError("Missing parameter " + parameter.getName());
                    }
                    var wanted = entry.getValue().getAsJsonPrimitive();
                    var actual = pv.getEngValue();
                    if (wanted.isString()) {
                        if (!wanted.getAsString().equals(actual.getStringValue())) {
                            throw new AssertionError(parameter.getName() + ": " + actual);
                        }
                    } else if (Double.parseDouble(actual.toString()) != wanted.getAsDouble()) {
                        throw new AssertionError(parameter.getName() + ": " + actual);
                    }
                }
                // Exercise nonzero sequence, timestamp and spare fields while
                // preserving the packet MID and payload.
                byte[] variant = expected.clone();
                byte[] headerTail = HexFormat.of().parseHex("123456789ABC1234ABCD");
                System.arraycopy(headerTail, 0, variant, 6, headerTail.length);
                variant[2] = (byte) 0xA3;
                variant[3] = 0x45;
                verifyHeader(mdb, extractor.processPacket(variant, 0, 0, 0, startContainer), variant);
                telemetry++;
            }
        }
        long concreteCommands = mdb.getMetaCommands().stream().filter(c -> !c.isAbstract()).count();
        if (commands != concreteCommands || telemetry + 2 != mdb.getSequenceContainers().size()) {
            throw new AssertionError("Fixture coverage does not match MDB contents");
        }
        System.out.printf("PASS: %d command packets match C byte for byte; %d telemetry packets decode correctly%n",
                commands, telemetry);
        System.out.println("Stream root verified from instance configuration: " + rootName);
    }

    private static void verifyHeader(org.yamcs.mdb.Mdb mdb,
            org.yamcs.mdb.ContainerProcessingResult result, byte[] packet) {
        var fields = Map.ofEntries(
                Map.entry("MessageId", new int[] {0, 16}), Map.entry("Version", new int[] {0, 3}),
                Map.entry("PacketType", new int[] {3, 1}), Map.entry("SecondaryHeaderFlag", new int[] {4, 1}),
                Map.entry("APID", new int[] {5, 11}), Map.entry("Sequence", new int[] {16, 16}),
                Map.entry("SequenceFlags", new int[] {16, 2}), Map.entry("SequenceCount", new int[] {18, 14}),
                Map.entry("PacketLength", new int[] {32, 16}), Map.entry("Seconds", new int[] {48, 32}),
                Map.entry("Subseconds", new int[] {80, 16}), Map.entry("Spare", new int[] {96, 32}));
        for (var entry : fields.entrySet()) {
            long wanted = 0;
            int start = entry.getValue()[0];
            int bits = entry.getValue()[1];
            for (int bit = start; bit < start + bits; bit++) {
                wanted = (wanted << 1) | ((packet[bit / 8] >> (7 - bit % 8)) & 1);
            }
            var parameter = mdb.getParameter("/cFS/" + entry.getKey());
            var value = result.getParameterResult().getFirstInserted(parameter);
            if (value == null || Long.parseLong(value.getEngValue().toString()) != wanted) {
                throw new AssertionError("Wrong header field " + entry.getKey());
            }
        }
    }
}
