package com.lab_440.tentacles.common.item;

import io.vertx.core.json.JsonObject;

public abstract class AbstractItem {

    public abstract String identity();

    public abstract JsonObject toJsonObject();

    public static String encode(AbstractItem item) {
        if (item == null) {
            return null;
        }
        return item.toJsonObject().encode();
    }

    public abstract void fromJsonObject(JsonObject jObj);

    public static void decode(String s, AbstractItem item) {
        if (s == null) {
            return;
        }
        item.fromJsonObject(new JsonObject(s));
    }

}
