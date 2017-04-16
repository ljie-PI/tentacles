package com.lab_440.tentacles.common.item;

import io.vertx.core.json.JsonObject;
import org.junit.Assert;
import org.junit.Before;
import org.junit.Test;

public class GeneralResultItemTest {

    private final String jStr = "{\"url\":\"http://www.example.com\", \"title\":\"It's an example\", \"date\": \"2017-01-06\"}";
    private GeneralResultAbstractItem item = new GeneralResultAbstractItem();

    @Before
    public void setUp() {
        JsonObject originObj = new JsonObject(jStr);
        item.fromJsonObject(originObj);
    }

    @Test
    public void testJsonConvert() {
        JsonObject convObj = item.toJsonObject();
        Assert.assertEquals("http://www.example.com", convObj.getString("url"));
        Assert.assertEquals(null, convObj.getString("content"));
        item.setUrl("http://www.example2.com")
                .setTitle("It's another example")
                .setDate("2017-01-01")
                .setContent("It's another example");
        convObj = item.toJsonObject();
        Assert.assertEquals("http://www.example2.com", convObj.getString("url"));
        Assert.assertEquals("It's another example", convObj.getString("content"));
    }

    @Test
    public void testIdentity() {
        Assert.assertEquals("http://www.example.com\u0001It's an example", item.identity());
    }
}
