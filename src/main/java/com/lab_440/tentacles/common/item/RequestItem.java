package com.lab_440.tentacles.common.item;

import io.vertx.core.json.JsonObject;

public class RequestItem implements IItem {

    private final String URL_FIELD = "url";
    private final String IS_RETRY_FIELD = "is_retry";

    private String url;
    private boolean isRetry = false;

    @Override
    public String identity() {
        return url;
    }

    @Override
    public JsonObject toJsonObject() {
        JsonObject jobj = new JsonObject();
        if (url != null) jobj.put(URL_FIELD, url);
        jobj.put(IS_RETRY_FIELD, isRetry);
        return jobj;
    }

    @Override
    public RequestItem fromJsonObject(JsonObject jobj) {
        url = jobj.getString(URL_FIELD, null);
        isRetry = jobj.getBoolean(IS_RETRY_FIELD, false);
        return this;
    }

    @Override
    public RequestItem decode(String s) {
        return fromJsonObject(new JsonObject(s));
    }

    public String getUrl() {
        return url;
    }

    public RequestItem setUrl(String url) {
        this.url = url;
        return this;
    }

    public boolean getIsRetry() {
        return isRetry;
    }

    public RequestItem setIsRetry(boolean isRetry) {
        this.isRetry = isRetry;
        return this;
    }
}
