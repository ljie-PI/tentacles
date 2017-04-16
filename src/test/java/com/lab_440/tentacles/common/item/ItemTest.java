package com.lab_440.tentacles.common.item;

import io.vertx.core.json.JsonObject;
import org.junit.Assert;
import org.junit.Test;

public class ItemTest {

    public class TestItem extends AbstractItem {
        private String id;
        private String name;

        @Override
        public String identity() {
            return id;
        }

        @Override
        public JsonObject toJsonObject() {
            JsonObject jObj = new JsonObject();
            jObj.put("id", id);
            jObj.put("name", name);
            return jObj;
        }

        @Override
        public void fromJsonObject(JsonObject jObj) {
            id = jObj.getString("id", "");
            name = jObj.getString("name", "");
        }
    }

    private final String jStr = "{\"id\":\"aaa\",\"name\":\"bbb\"}";

    @Test
    public void testEncodeDecode() {
        TestItem item = new TestItem();
        TestItem.decode(jStr, item);
        Assert.assertEquals("aaa", item.identity());
        Assert.assertEquals(jStr, TestItem.encode(item));
    }
}
