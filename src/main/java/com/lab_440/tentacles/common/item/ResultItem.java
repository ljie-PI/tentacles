package com.lab_440.tentacles.common.item;

import com.lab_440.tentacles.common.Separators;
import io.vertx.core.json.JsonObject;

public class ResultItem implements IItem {

    private final String TYPE_FIELD = "ITEM_TYPE";
    private final String URL_FIELD = "url";
    private final String TITLE_FIELD = "title";
    private final String DATE_FIELD = "date";
    private final String CONTENT_FIELD = "content";

    private String type = "result";
    private String url;
    private String title;
    private String date;
    private String content;

    @Override
    public String identity() {
        return url + Separators.FIELD_SEP + title;
    }

    @Override
    public JsonObject toJsonObject() {
        JsonObject jobj = new JsonObject();
        jobj.put(TYPE_FIELD, type);
        if (url != null) jobj.put(URL_FIELD, url);
        if (title != null) jobj.put(TITLE_FIELD, title);
        if (date != null) jobj.put(DATE_FIELD, date);
        if (content != null) jobj.put(CONTENT_FIELD, content);
        return jobj;
    }

    @Override
    public IItem fromJsonObject(JsonObject jobj) {
        url = jobj.getString(URL_FIELD, null);
        title = jobj.getString(TITLE_FIELD, null);
        date = jobj.getString(DATE_FIELD, null);
        content = jobj.getString(CONTENT_FIELD, null);
        return this;
    }

    public ResultItem setUrl(String url) {
        this.url = url;
        return this;
    }

    public ResultItem setTitle(String title) {
        this.title = title;
        return this;
    }

    public ResultItem setDate(String date) {
        this.date = date;
        return this;
    }

    public ResultItem setContent(String content) {
        this.content = content;
        return this;
    }
}
