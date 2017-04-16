package com.lab_440.tentacles.master.handlers;

import com.lab_440.tentacles.common.Domains;
import com.lab_440.tentacles.common.item.AbstractItem;
import com.lab_440.tentacles.master.scheduler.IScheduler;
import io.vertx.core.Handler;
import io.vertx.core.json.JsonArray;
import io.vertx.core.json.JsonObject;
import io.vertx.ext.web.RoutingContext;

import java.util.*;

public class FetchUrlsHandler implements Handler<RoutingContext> {

    private IScheduler<AbstractItem> scheduler;

    public FetchUrlsHandler(IScheduler<AbstractItem> scheduler) {
        this.scheduler = scheduler;
    }

    @Override
    public void handle(RoutingContext ctx) {
        List<AbstractItem> itemList = new ArrayList<>();
        if (scheduler != null) {
            Set<String> exclude = new HashSet<>();
            JsonObject jObj = ctx.getBodyAsJson();
            for (Map.Entry<String, Object> entry : jObj) {
                String domain = entry.getKey();
                if (!domain.equals(Domains.DEFAULT_DOMAIN)) {
                    exclude.add(domain);
                    int batchSize = (Integer) entry.getValue();
                    itemList.addAll(scheduler.pollBatch(domain, batchSize));
                }
            }
            int batchSize = jObj.getInteger(Domains.DEFAULT_DOMAIN);
            itemList.addAll(scheduler.pollBatch(exclude, batchSize));
        }
        JsonArray jArr = new JsonArray();
        for (AbstractItem item : itemList) {
            jArr.add(item.toJsonObject());
        }
        ctx.response().end(jArr.encode());
    }
}
