package com.lab_440.tentacles.common.item;

import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import org.junit.Assert;
import org.junit.Before;
import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public class RequestItemTest {

    private final String jStr = "{\"url\":\"http://www.example.com\", \"domain\":\"www.example.com\", \"is_repeat\": true}";
    private RequestItem item = new RequestItem();

    @Before
    public void setUp() {
        JsonObject originObj = new JsonObject(jStr);
        item.fromJsonObject(originObj);
    }

    @Test
    public void testJsonConvert() {
        JsonObject convObj = item.toJsonObject();
        Assert.assertEquals("http://www.example.com", convObj.getString("url"));
        Assert.assertEquals(true, convObj.getBoolean("is_repeat"));
        item.setUrl("http://www.example2.com")
                .setDomain("www.example2.com")
                .setForceRepeat(false);
        convObj = item.toJsonObject();
        Assert.assertEquals("http://www.example2.com", convObj.getString("url"));
        Assert.assertEquals(false, convObj.getBoolean("is_repeat", false));
        List<AbstractItem> itemList = new ArrayList<>();
        itemList.add(item);
        JsonArray jArr = new JsonArray(itemList);
    }

    @Test
    public void testIdentity() {
        Assert.assertEquals("http://www.example.com", item.identity());
    }
}
