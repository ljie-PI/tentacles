package com.lab_440.tentacles.common.item;

import com.sun.org.apache.regexp.internal.RE;
import io.vertx.core.json.JsonObject;

public class RequestItem extends AbstractItem {

    private final String URL_FIELD = "url";
    private final String DOMAIN_FIELD = "domain";
    private final String FORCE_REPEAT_FIELD = "is_repeat";  // force to download repeatly
    private final String RETRIED_FIELD = "retried";  // already retried times

    private String url;
    private String domain;
    private boolean forceRepeat;
    private int retried;

    public RequestItem(String url) {
        forceRepeat = false;
        String[] segs = url.split("/");
        if (segs.length < 3) {
            domain = null;
        } else {
            domain = segs[2];
        }
    }

    public RequestItem() {
        forceRepeat = false;
    }

    @Override
    public String identity() {
        return url;
    }

    @Override
    public JsonObject toJsonObject() {
        JsonObject jObj = new JsonObject();
        if (url != null) jObj.put(URL_FIELD, url);
        if (domain != null) jObj.put(DOMAIN_FIELD, domain);
        if (forceRepeat) jObj.put(FORCE_REPEAT_FIELD, true);
        if (retried > 0) jObj.put(RETRIED_FIELD, retried);
        return jObj;
    }

    @Override
    public void fromJsonObject(JsonObject jObj) {
        url = jObj.getString(URL_FIELD, null);
        domain = jObj.getString(DOMAIN_FIELD, null);
        if (domain == null) {
            String[] segs = url.split("/");
            if (segs.length < 3) {
                domain = null;
            } else {
                domain = segs[2];
            }
        }
        forceRepeat = jObj.getBoolean(FORCE_REPEAT_FIELD, false);
        retried = jObj.getInteger(RETRIED_FIELD, 0);
    }

    public String getUrl() {
        return url;
    }

    public RequestItem setUrl(String url) {
        this.url = url;
        return this;
    }

    public boolean getForceRepeat() {
        return forceRepeat;
    }

    public RequestItem setForceRepeat(boolean forceRepeat) {
        this.forceRepeat = forceRepeat;
        return this;
    }

    public String getDomain() {
        return domain;
    }

    public RequestItem setDomain(String domain) {
        this.domain = domain;
        return this;
    }

    public int getRetried() {
        return retried;
    }

    public RequestItem setRetried(int retried) {
        this.retried = retried;
        return this;
    }

}
