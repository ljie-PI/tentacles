package com.lab_440.tentacles.common.item;

import com.lab_440.tentacles.common.Separators;
import io.vertx.core.json.JsonObject;

/**
 * General result item
 */
public class GeneralResultAbstractItem extends AbstractItem {
    private final String URL_FIELD = "url";
    private final String TITLE_FIELD = "title";
    private final String DATE_FIELD = "date";
    private final String CONTENT_FIELD = "content";

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
        JsonObject jObj = new JsonObject();
        if (url != null) jObj.put(URL_FIELD, url);
        if (title != null) jObj.put(TITLE_FIELD, title);
        if (date != null) jObj.put(DATE_FIELD, date);
        if (content != null) jObj.put(CONTENT_FIELD, content);
        return jObj;
    }

    @Override
    public void fromJsonObject(JsonObject jObj) {
        url = jObj.getString(URL_FIELD, null);
        title = jObj.getString(TITLE_FIELD, null);
        date = jObj.getString(DATE_FIELD, null);
        content = jObj.getString(CONTENT_FIELD, null);
    }

    public GeneralResultAbstractItem setUrl(String url) {
        this.url = url;
        return this;
    }

    public GeneralResultAbstractItem setTitle(String title) {
        this.title = title;
        return this;
    }

    public GeneralResultAbstractItem setDate(String date) {
        this.date = date;
        return this;
    }

    public GeneralResultAbstractItem setContent(String content) {
        this.content = content;
        return this;
    }
}
