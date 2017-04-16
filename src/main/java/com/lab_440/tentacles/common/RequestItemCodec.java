package com.lab_440.tentacles.common;

import com.lab_440.tentacles.common.item.RequestItem;
import io.vertx.core.buffer.Buffer;
import io.vertx.core.eventbus.MessageCodec;

public class RequestItemCodec implements MessageCodec<RequestItem, RequestItem> {

    private static RequestItemCodec instance = new RequestItemCodec();

    private RequestItemCodec() {}

    public static RequestItemCodec getInstance() {
        return instance;
    }

    @Override
    public void encodeToWire(Buffer buffer, RequestItem item) {
        buffer.appendString(RequestItem.encode(item));
    }

    @Override
    public RequestItem decodeFromWire(int pos, Buffer buffer) {
        String jStr = buffer.getString(pos, buffer.length());
        RequestItem item = new RequestItem();
        RequestItem.decode(jStr, item);
        return item;
    }

    @Override
    public RequestItem transform(RequestItem item) {
        return item;
    }

    @Override
    public String name() {
        return "RequestItemCodec";
    }

    @Override
    public byte systemCodecID() {
        return -1;
    }
}
