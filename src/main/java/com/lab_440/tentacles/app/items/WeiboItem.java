package com.lab_440.tentacles.app.items;

import com.lab_440.tentacles.common.item.IItem;
import io.vertx.core.json.JsonObject;

public class WeiboItem implements IItem {

    private final String TYPE_FIELD = "ITEM_TYPE";
    private final String ID_FIELD = "id";
    private final String CREATED_TS_FIELD = "created_ts";
    private final String USER_ID_FIELD = "user_id";
    private final String USER_DISP_NAME_FIELD = "user_disp_name";
    private final String USER_VERIFIED_FIELD = "user_verified";
    private final String USER_FANS_NUM_FIELD = "user_fans_num";
    private final String TEXT_FIELD = "text";
    private final String RETWEET_FIELD = "retweet";

    private String type = "weibo";
    private String id;
    private long createdTs;
    private long userId;
    private String userDispName;
    private boolean userVerified;
    private int userFansNum;
    private String text;
    private WeiboItem retweet;

    @Override
    public String identity() {
        return type + ":" + id;
    }

    @Override
    public JsonObject toJsonObject() {
        JsonObject jobj = new JsonObject();
        jobj.put(TYPE_FIELD, type);
        if (id != null) jobj.put(ID_FIELD, id);
        jobj.put(CREATED_TS_FIELD, createdTs);
        jobj.put(USER_ID_FIELD, userId);
        if (userDispName != null) jobj.put(USER_DISP_NAME_FIELD, userDispName);
        jobj.put(USER_VERIFIED_FIELD, userVerified);
        jobj.put(USER_FANS_NUM_FIELD, userFansNum);
        if (text != null) jobj.put(TEXT_FIELD, text);
        if (retweet != null) jobj.put(RETWEET_FIELD, retweet.toJsonObject());
        return jobj;
    }

    @Override
    public WeiboItem fromJsonObject(JsonObject jobj) {
        id = jobj.getString(ID_FIELD, null);
        createdTs = jobj.getLong(CREATED_TS_FIELD, 0l);
        userId = jobj.getLong(USER_ID_FIELD, 0l);
        userDispName = jobj.getString(USER_DISP_NAME_FIELD, null);
        userVerified = jobj.getBoolean(USER_VERIFIED_FIELD, false);
        userFansNum = jobj.getInteger(USER_FANS_NUM_FIELD, 0);
        text = jobj.getString(TEXT_FIELD, null);
        retweet = new WeiboItem()
                .fromJsonObject(jobj.getJsonObject(RETWEET_FIELD, null));
        return this;
    }

    @Override
    public WeiboItem decode(String s) {
        return fromJsonObject(new JsonObject(s));
    }

    public WeiboItem setId(String id) {
        this.id = id;
        return this;
    }

    public WeiboItem setUserId(long userId) {
        this.userId = userId;
        return this;
    }

    public WeiboItem setCreatedTs(long createdTs) {
        this.createdTs = createdTs;
        return this;
    }

    public WeiboItem setUserDispName(String userDispName) {
        this.userDispName = userDispName;
        return this;
    }

    public WeiboItem setUserVerified(boolean userVerified) {
        this.userVerified = userVerified;
        return this;
    }

    public WeiboItem setUserFansNum(int userFansNum) {
        this.userFansNum = userFansNum;
        return this;
    }

    public WeiboItem setText(String text) {
        this.text = text;
        return this;
    }

    public WeiboItem setRetweet(WeiboItem retweet) {
        this.retweet = retweet;
        return this;
    }
}
