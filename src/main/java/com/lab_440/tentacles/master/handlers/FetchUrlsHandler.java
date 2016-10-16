package com.lab_440.tentacles.master.handlers;

import com.lab_440.tentacles.common.item.IItem;
import com.lab_440.tentacles.master.scheduler.IScheduler;
import io.vertx.core.Handler;
import io.vertx.core.json.JsonArray;
import io.vertx.ext.web.RoutingContext;

import java.util.List;

public class FetchUrlsHandler implements Handler<RoutingContext> {

    private IScheduler<IItem> scheduler;

    public FetchUrlsHandler(IScheduler<IItem> scheduler) {
        this.scheduler = scheduler;
    }

    @Override
    public void handle(RoutingContext ctx) {
        JsonArray jarr = new JsonArray();
        if (scheduler != null) {
            String bs = ctx.request().getParam("bs");
            int batchSize;
            if (bs == null) {
                batchSize = 0;
            } else {
                batchSize = Integer.parseInt(bs);
            }
            List<IItem> items = scheduler.pollBatch(batchSize);
            for (IItem item: items) {
                jarr.add(item.toJsonObject());
            }
        }
        ctx.response().end(jarr.encode());
    }
}
