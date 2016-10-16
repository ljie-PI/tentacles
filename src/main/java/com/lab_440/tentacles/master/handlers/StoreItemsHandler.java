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
        JsonObject jobj = new JsonObject();
        if (dataStore == null) {
            jobj.put("status", "FAIL");
            jobj.put("msg", "Datastore not initilized");
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
                jobj.put("status", "INCOMPLETE");
                jobj.put("msg", "Expect to store " + expected + " items, actually stored " + actual + " items");
            } else {
                jobj.put("status", "OK");
            }
        }
        ctx.response().end(jobj.encode());
    }
}
