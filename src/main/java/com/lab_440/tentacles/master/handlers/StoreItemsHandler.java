package com.lab_440.tentacles.master.handlers;

import com.lab_440.tentacles.master.datastore.IDatastore;
import io.vertx.core.Handler;
import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import io.vertx.ext.web.RoutingContext;

public class StoreItemsHandler implements Handler<RoutingContext> {

    private IDatastore dataStore;

    public StoreItemsHandler(IDatastore datastore) {
        this.dataStore = datastore;
    }

    @Override
    public void handle(RoutingContext ctx) {
        JsonObject jObj = new JsonObject();
        if (dataStore == null) {
            jObj.put("status", "FAIL");
            jObj.put("msg", "Datastore not initilized");
        } else {
            JsonArray items = ctx.getBodyAsJsonArray();
            int expected = items.size();
            int actual = 0;
            for (int i = 0; i < expected; i++) {
                if (dataStore.store(items.getJsonObject(i))) {
                    actual++;
                }
            }
            if (expected != actual) {
                jObj.put("status", "INCOMPLETE");
                jObj.put("msg", "Expect to store " + expected + " items, actually stored " + actual + " items");
            } else {
                jObj.put("status", "OK");
            }
        }
        ctx.response().end(jObj.encode());
    }
}
