package com.lab_440.tentacles.master.handlers;

import com.lab_440.tentacles.common.item.AbstractItem;
import com.lab_440.tentacles.common.item.RequestItem;
import com.lab_440.tentacles.master.scheduler.IScheduler;
import io.vertx.core.Handler;
import io.vertx.core.json.JsonArray;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;
import io.vertx.ext.web.RoutingContext;

public class FollowLinksHandler implements Handler<RoutingContext> {

    private Logger logger = LoggerFactory.getLogger(getClass());

    private IScheduler<AbstractItem> scheduler;

    public FollowLinksHandler(IScheduler<AbstractItem> scheduler) {
        this.scheduler = scheduler;
    }

    @Override
    public void handle(RoutingContext ctx) {
        int added = 0;
        if (scheduler != null) {
            JsonArray jArr = ctx.getBodyAsJsonArray();
            for (int i = 0; i < jArr.size(); i++) {
                RequestItem item = new RequestItem();
                item.fromJsonObject(jArr.getJsonObject(i));
                if (scheduler.add(item.getDomain(), item)) {
                    added++;
                }
            }
        }
        ctx.response().end(String.valueOf(added));
    }
}
