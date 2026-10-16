package org.yamcs.tctm.cfs;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.yamcs.StandardTupleDefinitions;
import org.yamcs.YConfiguration;
import org.yamcs.events.MockupEventProducer;
import org.yamcs.protobuf.Event.EventSeverity;
import org.yamcs.yarch.Tuple;
import org.yamcs.yarch.protobuf.Db.Event;

class CfsEventDecoderTest {
    @Test
    void keepsTwelveByteHeaderDefault() throws Exception {
        var config = new CfsEventDecoder().getSpec().validate(YConfiguration.wrap(Map.of()));
        assertEquals(12, config.getInt("headerLength"));
        verifyEvent(new CfsEventDecoder(), 12, ByteOrder.BIG_ENDIAN, 0x0808, 1, 3);
    }

    @Test
    void supportsSixteenByteHeadersWithBothByteOrdersAndCpus() {
        for (var order : new ByteOrder[] {ByteOrder.LITTLE_ENDIAN, ByteOrder.BIG_ENDIAN}) {
            for (int cpu = 1; cpu <= 2; cpu++) {
                for (int severity = 1; severity <= 4; severity++) {
                    var decoder = new CfsEventDecoder();
                    decoder.headerLength = 16;
                    verifyEvent(decoder, 16, order, cpu == 1 ? 0x0808 : 0x0908, cpu, severity);
                }
            }
        }
    }

    private static void verifyEvent(CfsEventDecoder decoder, int headerLength, ByteOrder order,
            int mid, int cpu, int severity) {
        var queue = new ArrayDeque<Event>();
        decoder.byteOrder = order;
        decoder.charset = StandardCharsets.US_ASCII;
        decoder.appNameMax = 20;
        decoder.eventMsgMax = 122;
        decoder.msgIds.add(mid);
        decoder.eventProducer = new MockupEventProducer(queue);

        byte[] packet = new byte[headerLength + 20 + 2 + 2 + 4 + 4 + 122];
        ByteBuffer.wrap(packet).putShort((short) mid);
        var body = ByteBuffer.wrap(packet).order(order);
        body.position(headerLength);
        body.put("CFE_ES".getBytes(StandardCharsets.US_ASCII));
        body.position(headerLength + 20);
        body.putShort((short) 42).putShort((short) severity).putInt(0x42).putInt(cpu);
        body.put("Application started".getBytes(StandardCharsets.US_ASCII));
        var tuple = new Tuple(StandardTupleDefinitions.TM,
                new Object[] {123L, 0, 456L, 0, packet, null, null, null, null});
        decoder.onTuple(null, tuple);

        assertEquals(1, queue.size());
        var event = queue.remove();
        assertEquals("/CFS/CPU" + cpu + "/CFE_ES", event.getSource());
        assertEquals("EVID42", event.getType());
        assertEquals("Application started", event.getMessage());
        assertEquals(123L, event.getGenerationTime());
        assertEquals(456L, event.getReceptionTime());
        assertTrue(event.hasSeverity());
        assertEquals(switch (severity) {
            case 3 -> EventSeverity.ERROR;
            case 4 -> EventSeverity.CRITICAL;
            default -> EventSeverity.INFO;
        }, event.getSeverity());
    }
}
