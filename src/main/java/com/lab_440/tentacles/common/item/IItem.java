package com.lab_440.tentacles.common.item;

import io.vertx.core.json.JsonObject;

public interface IItem {

    public String identity();

    public JsonObject toJsonObject();

    default public String encode() {
        return toJsonObject().encode();
    }

    public IItem fromJsonObject(JsonObject jobj);

    default public IItem decode(String s) {
        if (s == null) {
            return null;
        }
        return fromJsonObject(new JsonObject(s));
    }

}
